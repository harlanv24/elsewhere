from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

from worldsim.memory import CampaignStore
from worldsim.models import (
    ActionIntent,
    ActionKind,
    EffectKind,
    EncounterState,
    EncounterStatus,
    SceneMode,
    StateEffect,
)
from worldsim.tui import Session


def resolve(state, command: str):
    return state.engine.resolve_command(
        command,
        state.world,
        state.player,
        state.director,
        state.memory,
    )


def first_area(state) -> str:
    return state.engine.available_areas(state.world, state.player)[0]


def enter_first_area(state):
    return resolve(state, f"enter area {first_area(state)}")


def start_ambush(state) -> None:
    state.world.active_encounter = EncounterState(
        id="encounter-market-ambush",
        kind="combat",
        participants=[state.player.name, "npc-witness"],
        objective="Escape the market ambush.",
        phase="engaged",
        exits=["service passage"],
    )
    state.engine.ensure_progression(state.world)
    state.engine.scene_service.refresh_actions(state.world)


def test_session_no_longer_owns_gameplay_area_state() -> None:
    field_names = {item.name for item in fields(Session)}

    assert field_names.isdisjoint(
        {
            "entered_area",
            "entered_area_step",
            "entered_area_tension",
            "entered_area_theme",
            "entered_area_hazard",
            "entered_area_npc",
            "area_exit_open",
        }
    )


def test_enter_and_move_are_persisted_engine_turns(game_state, tmp_path) -> None:
    state = game_state
    tick_before = state.world.tick
    area_name = first_area(state)

    entered = resolve(state, f"enter area {area_name}")
    moved = resolve(state, "push deeper")

    scene = state.world.active_scene
    assert entered.advance_time is True
    assert moved.advance_time is True
    assert scene is not None
    assert scene.mode == SceneMode.LOCAL
    assert scene.area_name == area_name
    assert scene.parent_scene_id == "scene:location-market"
    assert scene.step == 1
    assert scene.tension == 3
    assert scene.hazard
    assert scene.local_npc_id == "npc-witness"
    assert state.world.tick == tick_before + 2
    assert [record.intent.kind for record in state.world.turn_records[-2:]] == [
        ActionKind.EXPLICIT,
        ActionKind.EXPLICIT,
    ]

    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    loaded = store.load()

    assert loaded is not None
    loaded_world, loaded_player, _ = loaded
    assert loaded_world.active_scene == scene
    assert loaded_player.position == state.player.position
    assert loaded_world.active_scene.available_actions == scene.available_actions


def test_overworld_movement_is_blocked_until_local_scene_exit(game_state) -> None:
    state = game_state
    position_before = state.player.position
    enter_first_area(state)

    result = resolve(state, "east")

    assert result.advance_time is False
    assert "Leave the local area" in result.message
    assert state.player.position == position_before
    assert state.world.active_scene.mode == SceneMode.LOCAL


def test_failed_force_exit_uses_common_d20_and_preserves_encounter(game_state) -> None:
    state = game_state
    enter_first_area(state)
    start_ambush(state)
    state.engine.random.randint = lambda low, high: 1
    tension_before = state.world.active_scene.tension

    result = resolve(state, "force exit")

    record = state.world.turn_records[-1]
    assert result.advance_time is True
    assert record.check is not None
    assert record.check.raw_roll == 1
    assert record.check.kind.value == "exploration_check"
    assert record.check.success is False
    assert state.world.active_scene.mode == SceneMode.LOCAL
    assert state.world.active_scene.tension == tension_before + 1
    assert state.world.active_encounter.status == EncounterStatus.ACTIVE
    assert state.engine.movement_lock_reason(state.world)
    assert "force exit" in state.world.active_scene.available_actions


def test_successful_alternate_exit_clears_encounter_without_moving_overworld_position(game_state, tmp_path) -> None:
    state = game_state
    position_before = state.player.position
    enter_first_area(state)
    start_ambush(state)
    state.engine.random.randint = lambda low, high: 20

    result = resolve(state, "force exit")

    assert result.advance_time is True
    assert state.world.active_scene.mode == SceneMode.OVERWORLD
    assert state.world.active_scene.area_name is None
    assert state.world.active_encounter.status == EncounterStatus.ESCAPED
    assert state.world.active_encounter.resolution
    assert state.engine.movement_lock_reason(state.world) is None
    assert state.world.movement_lock is None
    assert state.world.current_activity is None
    assert state.player.position == position_before

    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    loaded = store.load()

    assert loaded is not None
    loaded_world, loaded_player, _ = loaded
    assert loaded_world.active_scene.mode == SceneMode.OVERWORLD
    assert loaded_world.active_encounter.status == EncounterStatus.ESCAPED
    assert loaded_player.position == position_before


def test_local_dialogue_uses_scene_npc_and_has_explicit_end(game_state) -> None:
    state = game_state
    enter_first_area(state)

    resolve(state, "talk")

    assert state.world.active_scene.local_npc_id == "npc-witness"
    assert state.world.dialogue_state is not None
    assert state.world.dialogue_state.npc_id == "npc-witness"
    assert state.world.dialogue_state.active is True

    result = resolve(state, "end conversation")

    assert "end the conversation" in result.message
    assert state.world.dialogue_state is None


def test_scene_actions_are_derived_from_encounter_and_depth(game_state) -> None:
    state = game_state
    enter_first_area(state)
    assert "leave area" in state.world.active_scene.available_actions
    assert "push deeper" in state.world.active_scene.available_actions

    start_ambush(state)

    assert "force exit" in state.world.active_scene.available_actions
    assert "push deeper" not in state.world.active_scene.available_actions
    assert "press the advantage" in state.world.active_scene.available_actions


def test_freeform_named_travel_commits_location_before_narration(game_state) -> None:
    state = game_state
    destination = state.world.locations[1]

    result = resolve(state, f"walk to the {destination.name}")

    record = state.world.turn_records[-1]
    observation = state.director.narration_observations[-1]
    assert result.advance_time is True
    assert state.player.position == destination.position
    assert state.world.active_scene.mode == SceneMode.OVERWORLD
    assert state.world.active_scene.location_id == destination.id
    assert observation["player_position"] == destination.position
    assert observation["location_id"] == destination.id
    assert any(
        effect.kind == EffectKind.LOCATION_TRANSITION
        and effect.target_id == destination.id
        for effect in record.outcome.accepted_effects
    )


def test_unknown_or_non_travel_location_transition_is_rejected(game_state) -> None:
    state = game_state
    position_before = state.player.position

    def invalid_intent(*args, **kwargs):
        return ActionIntent(
            id="invalid-travel",
            raw_input="sing about the observatory",
            title="Invalid Travel",
            stakes="A song should not move the player.",
            proposed_effects=[
                StateEffect(
                    kind=EffectKind.LOCATION_TRANSITION,
                    target_id="location-observatory",
                )
            ],
        )

    state.director.interpret_freeform_action = invalid_intent

    resolve(state, "sing about the observatory")

    record = state.world.turn_records[-1]
    assert state.player.position == position_before
    assert record.outcome.accepted_effects == []
    assert any(
        "does not match a travel action" in rejected.reason
        for rejected in record.outcome.rejected_effects
    )


def test_named_travel_is_rejected_during_an_active_encounter(game_state) -> None:
    state = game_state
    destination = state.world.locations[1]
    position_before = state.player.position
    start_ambush(state)

    resolve(state, f"walk to the {destination.name}")

    record = state.world.turn_records[-1]
    assert state.player.position == position_before
    assert any(
        "active encounter prevents travel" in rejected.reason
        for rejected in record.outcome.rejected_effects
    )


def test_director_cannot_submit_scene_lifecycle_effects(game_state) -> None:
    state = game_state
    scene_before = deepcopy(state.world.active_scene)

    def unsafe_intent(*args, **kwargs):
        return ActionIntent(
            id="unsafe-scene",
            raw_input="inspect the street",
            title="Unsafe Scene Mutation",
            stakes="The director must not own scene lifecycle.",
            proposed_effects=[StateEffect(kind=EffectKind.SCENE_STEP, amount=5)],
        )

    state.director.interpret_freeform_action = unsafe_intent

    resolve(state, "inspect the street")

    record = state.world.turn_records[-1]
    assert state.world.active_scene == scene_before
    assert record.outcome.accepted_effects == []
    assert any(
        "scene lifecycle effects are engine-owned" in rejected.reason
        for rejected in record.outcome.rejected_effects
    )


def test_scene_turn_records_replay_without_rerolling(game_state) -> None:
    state = game_state
    baseline_world = deepcopy(state.world)
    baseline_player = deepcopy(state.player)

    enter_first_area(state)
    resolve(state, "push deeper")
    records = deepcopy(state.world.turn_records[-2:])

    replay_engine = state.engine.__class__(seed=999)
    random_state = replay_engine.random.getstate()
    for record in records:
        replay_engine.replay_turn(record, baseline_world, baseline_player)

    assert baseline_world.active_scene == state.world.active_scene
    assert baseline_player.position == state.player.position
    assert replay_engine.random.getstate() == random_state
