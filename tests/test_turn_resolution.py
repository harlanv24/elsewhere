from __future__ import annotations

from copy import deepcopy
import json

from worldsim.director import LocalLLMDirector
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignStore
from worldsim.models import (
    ActionIntent,
    CheckKind,
    DirectorBeat,
    EffectCondition,
    EffectKind,
    StateEffect,
)
from worldsim.turn_resolution import StateReducer


def resolve(state, command: str):
    return state.engine.resolve_command(
        command,
        state.world,
        state.player,
        state.director,
        state.memory,
    )


def test_failed_door_turn_records_rejected_effects_and_outcome_narration(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]
    state.director.freeform_beat = DirectorBeat(
        title="Break the Door",
        narration="The door gives way before you.",
        mechanical_request="exploration_check",
        difficulty=20,
        scene_objects=["broken hinge"],
    )

    result = resolve(state, "break iron door")

    record = state.world.turn_records[-1]
    assert record.check is not None and record.check.success is False
    assert record.outcome.accepted_effects == []
    assert {item.effect.kind for item in record.outcome.rejected_effects} == {
        EffectKind.SCENE_OBJECT_ADD,
        EffectKind.OBJECT_STATUS,
    }
    assert state.director.interpretation_observations[-1]["last_roll"] is None
    assert state.director.narration_observations[-1]["check"] == record.check
    assert "iron door" in state.world.scene_objects["0,0"]
    assert "door gives way" not in result.message


def test_successful_door_turn_commits_before_outcome_narration(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]
    state.director.freeform_beat = DirectorBeat(
        title="Break the Door",
        narration="Whether the door breaks is not known yet.",
        mechanical_request="exploration_check",
        difficulty=1,
        scene_objects=["broken hinge"],
        choices=["enter the passage"],
    )
    state.director.outcome_narration = "The iron door splits and the passage beyond opens."

    result = resolve(state, "break iron door")

    record = state.world.turn_records[-1]
    observation = state.director.narration_observations[-1]
    assert record.check is not None and record.check.success is True
    assert "iron door" not in observation["scene_objects"]
    assert "broken hinge" in observation["scene_objects"]
    assert any(
        item.get("name") == "iron door" and item.get("status") == "destroyed"
        for item in observation["object_states"].values()
    )
    assert "passage beyond opens" in result.message
    assert record.narration == state.director.outcome_narration
    assert state.world.current_choices[0] == "inspect the scene"


def test_turn_record_replay_applies_exact_accepted_effects_without_rerolling(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]
    baseline_world = deepcopy(state.world)
    baseline_player = deepcopy(state.player)
    state.director.freeform_beat = DirectorBeat(
        title="Break the Door",
        narration="The attempt has uncertain stakes.",
        mechanical_request="exploration_check",
        difficulty=1,
        scene_objects=["broken hinge"],
    )

    resolve(state, "break iron door")
    record = deepcopy(state.world.turn_records[-1])

    replay_engine = WorldEngine(seed=999)
    random_state = replay_engine.random.getstate()
    replay_engine.replay_turn(record, baseline_world, baseline_player)

    assert baseline_world.scene_objects["0,0"] == state.world.scene_objects["0,0"]
    assert baseline_world.object_states == state.world.object_states
    assert baseline_player.inventory == state.player.inventory
    assert replay_engine.random.getstate() == random_state


def test_reducer_rolls_back_the_whole_batch_when_a_commit_raises(game_state) -> None:
    state = game_state
    reducer = StateReducer()
    effects = [
        StateEffect(kind=EffectKind.FACT_DISCOVERED, target_id="first"),
        StateEffect(kind=EffectKind.FACT_DISCOVERED, target_id="second"),
    ]

    def commit(effect: StateEffect) -> None:
        state.world.discovered_facts.append(effect.target_id or "")
        if effect.target_id == "second":
            state.player.inventory.append("should roll back")
            raise RuntimeError("synthetic reducer failure")

    try:
        reducer.apply(
            state.world,
            state.player,
            effects,
            None,
            lambda effect: None,
            commit,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("Reducer did not propagate the commit failure.")

    assert state.world.discovered_facts == []
    assert state.player.inventory == ["torch", "rations"]


def test_turn_record_round_trips_through_save_schema_v2(game_state, tmp_path) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]
    state.director.freeform_beat = DirectorBeat(
        title="Break the Door",
        narration="The attempt is unresolved.",
        mechanical_request="exploration_check",
        difficulty=1,
    )
    resolve(state, "break iron door")
    store = CampaignStore(tmp_path / "campaign.json")

    store.save(state.world, state.player, state.memory)
    loaded = store.load()

    assert loaded is not None
    loaded_world, _, _ = loaded
    assert loaded_world.turn_records == state.world.turn_records


def test_explicit_explore_uses_typed_check_adapter_without_regression(game_state) -> None:
    state = game_state
    state.director.action_beats["explore"] = DirectorBeat(
        title="Explore",
        narration="You search the market.",
        mechanical_request="exploration_check",
        difficulty=1,
    )

    result = resolve(state, "explore")

    assert result.advance_time is True
    assert state.world.last_roll is not None
    assert "exploration check" in state.world.last_roll


def test_director_cannot_make_object_effect_unconditional_on_a_failed_check(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]

    def unsafe_intent(*args, **kwargs):
        return ActionIntent(
            id=kwargs.get("intent_id", "unsafe"),
            raw_input="break iron door",
            title="Unsafe Proposal",
            stakes="The door may break.",
            check_kind=CheckKind.EXPLORATION,
            difficulty=20,
            proposed_effects=[
                StateEffect(
                    kind=EffectKind.OBJECT_STATUS,
                    target_id="iron door",
                    value="destroyed",
                    condition=EffectCondition.ALWAYS,
                )
            ],
        )

    state.director.interpret_freeform_action = unsafe_intent

    resolve(state, "break iron door")

    record = state.world.turn_records[-1]
    assert record.check is not None and record.check.success is False
    assert "iron door" in state.world.scene_objects["0,0"]
    assert all(effect.kind != EffectKind.OBJECT_STATUS for effect in record.outcome.accepted_effects)
    assert any(
        "must depend on check success" in rejected.reason
        for rejected in record.outcome.rejected_effects
    )


def test_local_llm_director_uses_separate_intent_and_outcome_requests(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]

    class FakeClient:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []
            self.responses = [
                {
                    "title": "Break the Door",
                    "stakes": "The iron door may give way.",
                    "check_kind": "exploration_check",
                    "difficulty": 1,
                    "tags": ["exploration"],
                    "choices": ["enter"],
                    "proposed_effects": [
                        {
                            "kind": "object_status",
                            "target_id": "iron door",
                            "value": "destroyed",
                            "amount": 0,
                            "condition": "success",
                            "flag": False,
                        }
                    ],
                },
                {"narration": "The iron door breaks, revealing the way forward."},
            ]

        def complete_streaming(self, system, user, on_delta=None, response_schema=None):
            del system, on_delta, response_schema
            self.requests.append(json.loads(user))
            return json.dumps(self.responses.pop(0))

    client = FakeClient()
    director = LocalLLMDirector(client, state.director)
    state.engine._advance_world = lambda *args: None

    result = state.engine.resolve_command(
        "break iron door",
        state.world,
        state.player,
        director,
        state.memory,
    )

    assert [request["task"] for request in client.requests] == [
        "interpret_freeform_action",
        "narrate_turn_outcome",
    ]
    narration_context = client.requests[1]["context"]
    assert narration_context["resolved_turn"]["check"]["success"] is True
    assert any(
        item.get("name") == "iron door" and item.get("status") == "destroyed"
        for item in narration_context["state_ledger"]["object_states_here"].values()
    )
    assert result.message.startswith("The iron door breaks")
