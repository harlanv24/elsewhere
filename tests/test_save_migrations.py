from __future__ import annotations

import json

import pytest

from worldsim.memory import CampaignStore, SAVE_SCHEMA_VERSION, UnsupportedSaveVersion
from worldsim.models import DialogueState, EncounterState, EncounterStatus, SceneState


def test_save_load_preserves_active_scene_encounter_dialogue_and_entity_ids(game_state, tmp_path) -> None:
    state = game_state
    state.world.active_scene = SceneState(
        id="scene:market-cellar",
        location_id="location-market",
        area_name="Market Cellar",
        step=2,
        tension=7,
        theme="tight stone passages",
        hazard="rising water",
        local_npc_id="npc-witness",
        exit_open=False,
        available_actions=["force the grate", "negotiate"],
    )
    state.world.active_encounter = EncounterState(
        id="encounter-cellar",
        kind="escape",
        participants=[state.player.name, "npc-witness"],
        objective="Open the flooded grate.",
        phase="rising_water",
        obstacles=["locked grate"],
        exits=["grate", "service tunnel"],
    )
    state.world.dialogue_state = DialogueState(
        npc_id="npc-witness",
        npc_name="Iris",
        started_tick=state.world.tick,
    )
    store = CampaignStore(tmp_path / "campaign.json")

    store.save(state.world, state.player, state.memory)
    loaded = store.load()

    assert loaded is not None
    world, player, _ = loaded
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema_version"] == SAVE_SCHEMA_VERSION
    assert world.active_scene == state.world.active_scene
    assert world.active_encounter == state.world.active_encounter
    assert world.dialogue_state == state.world.dialogue_state
    assert world.locations[0].id == "location-market"
    assert world.npcs[0].id == "npc-witness"
    assert world.npcs[0].location_id == "location-market"
    assert player.position == state.player.position
    assert not list(tmp_path.glob("*.tmp"))


def test_unversioned_save_migrates_legacy_combat_and_backfills_ids(game_state, tmp_path) -> None:
    state = game_state
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload.pop("schema_version")
    world = payload["world"]
    world.pop("active_scene")
    world.pop("active_encounter")
    world.pop("dialogue_state")
    world.pop("discovered_facts")
    world.pop("committed_choices")
    world["current_activity"] = "combat"
    world["movement_lock"] = "you are in a fight"
    for location in world["locations"]:
        location.pop("id")
    for npc in world["npcs"]:
        npc.pop("id")
        npc.pop("location_id")
    for quest in world["quests"]:
        quest.pop("stage_conditions")
    for clock in world["clocks"]:
        clock.pop("triggers")
        clock.pop("triggered")
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.load()

    assert loaded is not None
    migrated_world, _, _ = loaded
    assert migrated_world.locations[0].id == "location-001"
    assert migrated_world.npcs[0].id == "npc-001"
    assert migrated_world.npcs[0].location_id == "location-001"
    assert migrated_world.active_scene is not None
    assert migrated_world.active_encounter is not None
    assert migrated_world.active_encounter.status == EncounterStatus.ACTIVE


def test_future_save_version_is_rejected(game_state, tmp_path) -> None:
    state = game_state
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["schema_version"] = SAVE_SCHEMA_VERSION + 1
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedSaveVersion, match="newer than supported"):
        store.load()


def test_version_one_save_migrates_with_empty_turn_history(game_state, tmp_path) -> None:
    state = game_state
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    payload["world"].pop("turn_records")
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.load()

    assert loaded is not None
    migrated_world, _, _ = loaded
    assert migrated_world.turn_records == []
