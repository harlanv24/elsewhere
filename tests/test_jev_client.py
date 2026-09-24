import io
import json
import urllib.error

import pytest

from worldsim.director import LocalLLMDirector, MockDirector, director_from_env
from worldsim.jev_client import JevClient, JevClientError, JevConfig


def context():
    return {"action": "pocket the journal", "operations": [
        {"id": "take:journal", "action": "take journal", "label": "Take journal"},
        {"id": "open:box", "action": "open box", "label": "Open box"},
    ], "available_actions": [
        {"id": "take-journal", "operation_id": "take:journal", "label": "Take journal"}]}


def payload(choice="edge_0", confidence=0.95):
    return {"model": "jev-test", "answers": {"action": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {key: 1.0 if key == choice else 0.0 for key in ("edge_0", "new_1", "unsupported")}}},
        "usage": {"input_tokens": 123, "output_tokens": 8}}


def client_for(data):
    requests = []
    def transport(request, timeout):
        requests.append((request, timeout))
        return io.BytesIO(json.dumps(data).encode())
    return JevClient(JevConfig("test-secret"), transport), requests


def test_choice_adapter_uses_direct_endpoint_and_maps_opaque_ids():
    client, requests = client_for(payload())
    assert client.choose_graph_action(context()) == {"edge_id": "take-journal", "expand": False}
    request, timeout = requests[0]
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert request.get_header("Authorization") == "Bearer test-secret"
    body = json.loads(request.data)
    assert body["model"] == "jev-latest"
    assert body["questions"]["action"]["type"] == "choice"
    assert "test-secret" not in request.data.decode()
    assert "test-secret" not in repr(client.config)
    assert timeout == 15
    assert client.requests == 1 and client.input_tokens == 123


def test_expansion_identifies_exact_operation_and_unsupported_does_not_fall_back():
    client, _ = client_for(payload("new_1"))
    assert client.choose_graph_action(context()) == {"edge_id": "", "expand": True, "operation_id": "open:box"}
    client, _ = client_for(payload("unsupported"))
    assert client.choose_graph_action(context()) == {"edge_id": "", "expand": False}


def test_low_confidence_requests_existing_interpreter():
    client, _ = client_for(payload(confidence=0.4))
    assert client.choose_graph_action(context()) is None
    assert client.last_status.startswith("uncertain")


@pytest.mark.parametrize("defect", ["type", "choice", "missing", "nan", "boolean", "sum", "contradiction", "usage"])
def test_malformed_typed_answers_fail_closed(defect):
    data = payload()
    answer = data["answers"]["action"]
    if defect == "type":
        answer["type"] = "noul"
    elif defect == "choice":
        answer["choice"] = "not-offered"
    elif defect == "missing":
        answer["probabilities"].pop("unsupported")
    elif defect == "nan":
        answer["confidence"] = float("nan")
    elif defect == "boolean":
        answer["confidence"] = True
    elif defect == "sum":
        answer["probabilities"]["edge_0"] = 0.2
    elif defect == "contradiction":
        answer["choice"] = "unsupported"
    else:
        data["usage"]["input_tokens"] = -1
    client, _ = client_for(data)
    with pytest.raises(JevClientError):
        client.choose_graph_action(context())


@pytest.mark.parametrize("status", [401, 429, 503, 302])
def test_http_errors_are_bounded_and_do_not_expose_error_body(status):
    requests = []
    def transport(request, timeout):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, status, "test-secret", {}, io.BytesIO(b"test-secret"))
    client = JevClient(JevConfig("test-secret"), transport)
    with pytest.raises(JevClientError, match=f"HTTP {status}") as error:
        client.choose_graph_action(context())
    assert "test-secret" not in str(error.value)
    assert len(requests) == 1


def test_oversize_context_never_sends_request():
    client, requests = client_for(payload())
    state = context()
    state["action"] = "x" * 25000
    with pytest.raises(JevClientError, match="limit"):
        client.choose_graph_action(state)
    assert not requests


def test_director_uses_jev_then_falls_back_on_uncertainty(game_state):
    class LLM:
        calls = 0
        def complete_streaming(self, *args, **kwargs):
            self.calls += 1
            return '{"edge_id":"take-journal","expand":false}'
    llm = LLM()
    jev, _ = client_for(payload())
    director = LocalLLMDirector(llm, game_state.director, graph_selector=jev)
    assert director.choose_graph_action(context())["edge_id"] == "take-journal"
    assert llm.calls == 0
    uncertain, _ = client_for(payload(confidence=0.2))
    director.graph_selector = uncertain
    assert director.choose_graph_action(context())["edge_id"] == "take-journal"
    assert llm.calls == 1
    unsupported, _ = client_for(payload("unsupported"))
    director.graph_selector = unsupported
    assert director.choose_graph_action(context()) == {"edge_id": "", "expand": False}
    assert llm.calls == 1


def test_environment_selection_and_mock_mode_stay_offline(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")
    monkeypatch.setenv("WORLDSIM_DIRECTOR", "llm")
    monkeypatch.setenv("WORLDSIM_GRAPH_SELECTOR", "auto")
    assert director_from_env(1).graph_selector is not None
    monkeypatch.setenv("WORLDSIM_GRAPH_SELECTOR", "llm")
    assert director_from_env(1).graph_selector is None
    monkeypatch.setenv("WORLDSIM_DIRECTOR", "mock")
    monkeypatch.setenv("WORLDSIM_GRAPH_SELECTOR", "jev")
    assert isinstance(director_from_env(1), MockDirector)


def test_jev_selected_expansion_cannot_switch_operations(game_state):
    state = game_state
    state.world.scene_objects["0,0"] = ["journal", "box"]
    resolve = lambda command: state.engine.resolve_command(command, state.world, state.player, state.director, state.memory)
    resolve("situation")
    state.director.choose_graph_action = lambda context: {"edge_id": "", "expand": True, "operation_id": "open:box"}
    state.director.expand_action_graph = lambda context: {
        "label": "Take journal", "operation_id": "take:journal",
        "success_description": "Journal taken.", "failure_description": "Failed."}
    result = resolve("try open the box carefully")
    assert not result.advance_time
    assert "journal" not in state.player.inventory
