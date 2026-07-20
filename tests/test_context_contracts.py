from __future__ import annotations

import json

import pytest

from worldsim.context import ContextBudget, ContextSelector
from worldsim.contracts import (
    SchemaValidationError,
    contract_for,
    task_system_prompt,
    validate_payload,
)
from worldsim.director import LLM_ENGINE_CONTRACT, LocalLLMDirector
from worldsim.models import (
    Biome,
    Location,
    Npc,
    Position,
    Quest,
    QuestClock,
)


def test_context_selects_relevant_world_data_and_excludes_unrelated_data(
    game_state,
) -> None:
    world = game_state.world
    world.locations.append(
        Location(
            id="location-unused",
            name="Unused Vault",
            position=Position(1, 1),
            biome=Biome.MOUNTAIN,
            danger=9,
            summary="This location is unrelated to the active turn.",
        )
    )
    world.npcs.append(
        Npc(
            id="npc-unused",
            name="Unrelated Curator",
            role="archivist",
            disposition="aloof",
            location_name="Unused Vault",
            location_id="location-unused",
        )
    )
    world.quests.append(
        Quest(
            id="side-thread-unused",
            title="The Unrelated Thread",
            goal="Stay outside this request.",
            stages=["Do something elsewhere."],
            related_locations=["location-unused"],
            related_npcs=["npc-unused"],
        )
    )
    world.clocks.append(
        QuestClock(
            id="unrelated-clock",
            title="Unrelated Clock",
            description="Pressure in a disconnected storyline.",
        )
    )

    selection = ContextSelector().select(
        "interpret_freeform_action",
        world,
        player=game_state.player,
        location=world.locations[0],
        npc=world.npcs[0],
        action="Travel toward the Observatory.",
    )

    serialized = json.dumps(selection.context)
    assert selection.context["active_quest"]["id"] == "main-thread"
    assert {
        location["id"]
        for location in selection.context["relevant_locations"]
    } == {"location-market", "location-observatory"}
    assert "Unused Vault" not in serialized
    assert "Unrelated Curator" not in serialized
    assert "The Unrelated Thread" not in serialized
    assert "Unrelated Clock" not in serialized


def test_context_is_compacted_under_the_configured_budget(game_state) -> None:
    world = game_state.world
    world.theme_prompt = " ".join(["expansive"] * 600)
    world.state_facts.extend(
        f"fact-{index} " + " ".join(["detail"] * 100)
        for index in range(30)
    )
    memory = [
        f"memory-{index} " + " ".join(["history"] * 100)
        for index in range(20)
    ]
    selector = ContextSelector(
        ContextBudget(max_estimated_tokens=600, characters_per_token=4)
    )

    selection = selector.select(
        "interpret_freeform_action",
        world,
        player=game_state.player,
        location=world.locations[0],
        npc=world.npcs[0],
        memory_context=memory,
        action="Search the market with the torch.",
    )

    assert selection.metrics.within_budget
    assert selection.metrics.estimated_tokens <= 600
    assert selection.metrics.dropped_items > 0
    assert selection.metrics.truncated_strings > 0
    assert selection.context["action"] == "Search the market with the torch."


def test_dialogue_context_has_one_canonical_deduplicated_history(
    game_state,
) -> None:
    world = game_state.world
    npc = world.npcs[0]
    world.conversations[npc.name] = [
        "Rowan: What happened?",
        "Iris: The bell rang.",
    ]

    selection = ContextSelector().select(
        "respond_to_dialogue",
        world,
        player=game_state.player,
        location=world.locations[0],
        npc=npc,
        player_dialogue="What happened?",
        dialogue_history=[
            "Rowan: What happened?",
            "Iris: The bell rang.",
            "Iris: The bell rang.",
        ],
    )

    serialized = json.dumps(selection.context)
    assert selection.context["dialogue_history"] == [
        "Rowan: What happened?",
        "Iris: The bell rang.",
    ]
    assert serialized.count('"dialogue_history"') == 1
    assert "active_dialogue_history" not in serialized
    assert "npc_conversation_history" not in serialized
    assert "npc_prior_replies" not in serialized


def test_task_contracts_have_distinct_schemas_and_prompts() -> None:
    introduction = contract_for("introduce_world")
    location = contract_for("describe_location")

    assert introduction.schema["title"] == "WorldIntroduction"
    assert location.schema["title"] == "LocationDescription"
    assert introduction.schema is not location.schema
    assert task_system_prompt(
        introduction.task,
        LLM_ENGINE_CONTRACT,
    ) != task_system_prompt(location.task, LLM_ENGINE_CONTRACT)


def test_schema_validation_rejects_wrong_types_and_extra_properties() -> None:
    schema = contract_for("introduce_world").schema

    with pytest.raises(SchemaValidationError) as error:
        validate_payload(
            {"narration": 42, "unsupported": True},
            schema,
        )

    assert "$.narration: expected string" in error.value.errors
    assert (
        "$.unsupported: additional property is not allowed"
        in error.value.errors
    )


class RecordingClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    def complete_streaming(
        self,
        system,
        user,
        on_delta=None,
        response_schema=None,
    ) -> str:
        self.requests.append(
            {
                "system": system,
                "user": json.loads(user),
                "on_delta": on_delta,
                "schema": response_schema,
            }
        )
        return json.dumps(self.responses.pop(0))


def test_local_llm_director_repairs_one_invalid_response(game_state) -> None:
    client = RecordingClient(
        [
            {"narration": 42},
            {"narration": "The campaign opens beneath a restless sky."},
        ]
    )
    director = LocalLLMDirector(
        client,
        game_state.director,
        repair_attempts=1,
    )

    result = director.introduce_world(
        game_state.world,
        game_state.player,
    )

    assert result == "The campaign opens beneath a restless sky."
    assert len(client.requests) == 2
    assert director.last_repair_count == 1
    assert director.last_used_fallback is False
    assert "Repair a malformed response" in client.requests[1]["system"]
    assert client.requests[1]["on_delta"] is None
    assert director.last_context_metrics is not None
    assert director.last_context_metrics.within_budget


def test_local_llm_director_bounds_repairs_then_uses_fallback(
    game_state,
) -> None:
    client = RecordingClient(
        [
            {"narration": 42},
            {"narration": False},
        ]
    )
    director = LocalLLMDirector(
        client,
        game_state.director,
        repair_attempts=1,
    )

    result = director.introduce_world(
        game_state.world,
        game_state.player,
    )

    assert result == "The campaign begins."
    assert len(client.requests) == 2
    assert director.last_repair_count == 1
    assert director.last_used_fallback is True
    assert director.last_error == "$.narration: expected string"


def test_local_llm_director_falls_back_when_minimum_context_cannot_fit(
    game_state,
) -> None:
    client = RecordingClient([])
    selector = ContextSelector(
        ContextBudget(max_estimated_tokens=1, characters_per_token=4)
    )
    director = LocalLLMDirector(
        client,
        game_state.director,
        context_selector=selector,
    )

    result = director.introduce_world(
        game_state.world,
        game_state.player,
    )

    assert result == "The campaign begins."
    assert client.requests == []
    assert director.last_used_fallback is True
    assert "minimum task context exceeds" in director.last_error
