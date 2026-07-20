from __future__ import annotations

from copy import deepcopy

from worldsim.command_input import normalize_command_input
from worldsim.models import (
    ClockTrigger,
    ClockTriggerKind,
    Condition,
    ConditionKind,
    DialogueState,
    DirectorBeat,
    EncounterState,
    EncounterStatus,
    Quest,
    QuestClock,
)


def resolve(state, command: str):
    return state.engine.resolve_command(
        command,
        state.world,
        state.player,
        state.director,
        state.memory,
    )


def test_world_and_player_creation_initialize_stable_scene_state() -> None:
    from worldsim.engine import WorldEngine

    engine = WorldEngine(seed=42)
    world = engine.create_world()
    player = engine.create_player(world, "Rowan", "ranger", "Northreach")

    assert world.locations
    assert all(location.id for location in world.locations)
    assert all(npc.id and npc.location_id for npc in world.npcs)
    assert world.active_scene is not None
    assert world.active_scene.location_id == world.locations[0].id
    assert player.position == world.locations[0].position


def test_failed_action_does_not_mutate_inventory_or_scene_state(game_state) -> None:
    state = game_state
    state.world.scene_objects["0,0"] = ["iron door"]
    state.director.freeform_beat = DirectorBeat(
        title="Break the Door",
        narration="The door gives way.",
        mechanical_request="exploration_check",
        difficulty=20,
        scene_objects=["iron door", "broken hinge"],
        inventory_add=["iron key"],
        tags=["exploration"],
    )
    inventory_before = list(state.player.inventory)
    scene_before = deepcopy(state.world.scene_objects)
    objects_before = deepcopy(state.world.object_states)

    result = resolve(state, "break iron door")

    assert "failure" in (state.world.last_roll or "")
    assert "Rejected" in state.world.state_facts[-1]
    assert state.player.inventory == inventory_before
    assert state.world.scene_objects == scene_before
    assert state.world.object_states == objects_before
    assert "fails" in result.message
    assert "door gives way" not in result.message


def test_successful_exploration_escape_clears_encounter_and_legacy_lock(game_state) -> None:
    state = game_state
    state.world.active_encounter = EncounterState(
        id="encounter-ambush",
        kind="combat",
        participants=[state.player.name],
        objective="Escape the ambush.",
        phase="engaged",
        exits=["drainage route"],
    )
    state.world.current_activity = "combat"
    state.world.movement_lock = "you are in a fight"
    state.director.freeform_beat = DirectorBeat(
        title="Drainage Route",
        narration="You find another way out.",
        mechanical_request="exploration_check",
        difficulty=1,
        tags=["exploration", "escape"],
    )

    resolve(state, "escape through the drainage route")

    assert state.world.active_encounter.status == EncounterStatus.ESCAPED
    assert state.world.active_encounter.resolution
    assert state.world.movement_lock is None
    assert state.world.current_activity is None


def test_global_commands_are_not_rewritten_during_dialogue() -> None:
    for command in ("quit", "help", "inventory", "end conversation"):
        assert normalize_command_input(command, dialogue_active=True) == command
    assert normalize_command_input("tell me more", dialogue_active=True) == "say tell me more"
    assert normalize_command_input("/look", dialogue_active=True) == "look"


def test_quit_command_reaches_engine_during_active_dialogue(game_state) -> None:
    state = game_state
    state.world.dialogue_state = DialogueState(
        npc_id="npc-witness",
        npc_name="Iris",
        started_tick=state.world.tick,
    )
    command = normalize_command_input("quit", dialogue_active=True)

    result = resolve(state, command)

    assert command == "quit"
    assert result.should_quit is True
    assert state.world.dialogue_state is not None


def test_structured_dialogue_fact_advances_the_correct_quest(game_state) -> None:
    state = game_state
    state.world.quests[0].stage_conditions = [
        [Condition(ConditionKind.FACT_DISCOVERED, "archive_truth")],
        [],
    ]
    state.director.dialogue_beat = DirectorBeat(
        title="Testimony",
        narration="Iris reveals the archive truth.",
        facts_discovered=["archive_truth"],
        progress_summary="Iris revealed the archive truth.",
        complete_current_stage=True,
        tags=["dialogue"],
    )

    resolve(state, "say what did you see?")

    quest = state.world.quests[0]
    assert "archive_truth" in state.world.discovered_facts
    assert quest.current_stage == 1
    assert quest.status == "active"
    assert quest.discoveries == ["Iris revealed the archive truth."]


def test_narrated_movement_cannot_change_authoritative_location(game_state) -> None:
    state = game_state
    position_before = state.player.position
    scene_before = state.world.active_scene.id
    state.director.freeform_beat = DirectorBeat(
        title="False Arrival",
        narration="You cross the city and arrive inside the Observatory.",
    )

    resolve(state, "walk to the observatory")

    assert state.player.position == position_before
    assert state.engine.location_at(state.world, state.player.position).id == "location-market"
    assert state.world.active_scene.id == scene_before


def test_clock_maximum_fires_structured_consequence_exactly_once(game_state) -> None:
    state = game_state
    state.world.stability = 68
    state.world.clocks = [
        QuestClock(
            id="pressure",
            title="Pressure",
            value=1,
            max_value=2,
            triggers=[
                ClockTrigger(
                    id="pressure-stability",
                    kind=ClockTriggerKind.STABILITY_DELTA,
                    amount=-7,
                    text="Pressure fractures local trust.",
                )
            ],
        )
    ]
    state.director.freeform_beat = DirectorBeat(
        title="Delay",
        narration="Time runs short.",
        clock_effects=[{"clock_id": "pressure", "delta": 1, "reason": "The deadline passes."}],
    )

    resolve(state, "wait for the deadline")
    stability_after_first = state.world.stability
    resolve(state, "wait for the deadline")

    clock = state.world.clocks[0]
    assert clock.status == "complete"
    assert clock.triggered is True
    assert clock.triggers[0].fired is True
    assert stability_after_first == 61
    assert state.world.stability == stability_after_first
    assert sum(event.category == "clock" for event in state.world.recent_events) == 1


def test_quest_completion_proposal_is_rejected_until_condition_is_true(game_state) -> None:
    state = game_state
    state.world.quests = [
        Quest(
            id="relic-quest",
            title="Recover the Relic",
            goal="Acquire the moon relic.",
            stages=["Acquire the moon relic."],
            progress_required=1,
            stage_conditions=[[Condition(ConditionKind.ITEM_ACQUIRED, "moon relic")]],
        )
    ]
    state.world.active_quest_id = "relic-quest"
    state.director.freeform_beat = DirectorBeat(
        title="Claim",
        narration="The quest is surely complete.",
        quest_progress_delta=2,
        complete_current_stage=True,
        progress_summary="The relic was supposedly claimed.",
    )

    resolve(state, "claim victory without the relic")
    assert state.world.quests[0].status == "active"

    state.player.inventory.append("moon relic")
    resolve(state, "present the moon relic")
    assert state.world.quests[0].status == "complete"
