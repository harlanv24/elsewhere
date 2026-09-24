from copy import deepcopy
import json

import pytest

from worldsim.action_graph import validate_graph
from worldsim.director import LocalLLMDirector
from worldsim.memory import CampaignStore, SAVE_SCHEMA_VERSION
from worldsim.models import (
    CheckKind, CheckResult, Condition, ConditionKind, EncounterState,
    Quest, QuestStage, QuestStatus,
)


def resolve(state, command):
    return state.engine.resolve_command(command, state.world, state.player, state.director, state.memory)


def current(state):
    return state.engine.action_graphs.current(state.world)


def plan(operation="observe"):
    return {"title": "Investigate the lead", "start": "start", "nodes": [
        {"id": "start", "description": "A lead awaits investigation.", "terminal": False},
        {"id": "found", "description": "Your investigation is resolved.", "terminal": True},
        {"id": "blocked", "description": "You need a different approach.", "terminal": False},
        {"id": "assessed", "description": "You have reassessed the available evidence.", "terminal": True},
    ], "edges": [
        {"id": "investigate", "source": "start", "label": "Investigate the lead",
         "operation_id": operation, "success": "found", "failure": "blocked"},
        {"id": "recover", "source": "blocked", "label": "Reassess the evidence",
         "operation_id": "reassess", "success": "assessed", "failure": "assessed"},
    ]}


def scripted_plan(state, payload=None):
    state.director.plan_action_graph = lambda context: deepcopy(payload or plan())


def force_roll(state, success):
    def roll(world, player, difficulty, kind):
        return CheckResult(kind, difficulty, 20 if success else 1, 0, 20 if success else 1, success)
    state.engine.resolve_typed_check = roll


@pytest.mark.parametrize("success", [True, False])
def test_roll_selects_branch_and_failure_has_an_executable_recovery(game_state, success):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    force_roll(state, success)
    result = resolve(state, "choose Investigate the lead")
    assert result.advance_time
    assert current(state)["current"] == ("found" if success else "blocked")
    record = state.world.turn_records[-1]
    assert record.action_graph_after == current(state)
    assert record.check.success is success
    if not success:
        resolve(state, "choose Reassess the evidence")
        assert current(state)["current"] == "assessed"
        tick = state.world.tick
        resolve(state, "choose Investigate the lead")
        assert state.world.tick == tick  # A finished graph cannot reroll.


def test_generated_plan_is_installed_before_local_scene_presentation(game_state):
    state = game_state
    scripted_plan(state)
    area = state.engine.available_areas(state.world, state.player)[0]
    result = resolve(state, f"enter area {area}")
    assert "A lead awaits investigation" in result.message
    assert "choose Investigate the lead" in result.message
    assert state.world.turn_records[-1].action_graph_after == current(state)
    graph = deepcopy(current(state))
    resolve(state, "leave area")
    resolve(state, f"enter area {area}")
    assert current(state) == graph


@pytest.mark.parametrize("defect", ["cycle", "dangling", "dead_end", "unreachable", "duplicate", "terminal_edge"])
def test_invalid_graph_topology_is_rejected(defect):
    graph = plan()
    if defect == "cycle":
        graph["edges"][0]["failure"] = "start"
    elif defect == "dangling":
        graph["edges"][0]["success"] = "missing"
    elif defect == "dead_end":
        graph["edges"].pop()
    elif defect == "unreachable":
        graph["nodes"].append({"id": "orphan", "description": "Unused", "terminal": True})
    elif defect == "duplicate":
        graph["nodes"].append(deepcopy(graph["nodes"][0]))
    else:
        graph["nodes"][0]["terminal"] = True
    with pytest.raises(ValueError):
        validate_graph(graph)


def test_bad_provider_plan_falls_back_to_playable_graph(game_state):
    state = game_state
    scripted_plan(state, plan("invent_a_dragon"))
    resolve(state, "situation")
    validate_graph(current(state))
    assert current(state)["title"] == "A local opportunity"
    assert "choose Set this situation aside" in state.world.current_choices


def test_freeform_mapping_does_not_allow_nonavailable_edges(game_state):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    state.director.choose_graph_action = lambda context: {"edge_id": "recover", "expand": False}
    before = deepcopy(state.world)
    result = resolve(state, "I think about an entirely different route")
    assert not result.advance_time
    assert current(state)["current"] == "start"
    assert state.world.tick == before.tick
    assert not state.world.turn_records


def test_freeform_maps_to_available_edge_and_preserves_original_input(game_state):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    state.director.choose_graph_action = lambda context: {"edge_id": "investigate", "expand": False}
    force_roll(state, True)
    resolve(state, "I look for a pattern in the evidence")
    record = state.world.turn_records[-1]
    assert record.command == record.intent.raw_input == "I look for a pattern in the evidence"
    assert current(state)["current"] == "found"


def test_expansion_commits_new_state_and_real_effect_then_survives_save_replay(game_state, tmp_path):
    state = game_state
    state.world.scene_objects["0,0"] = ["journal"]
    scripted_plan(state)
    resolve(state, "situation")
    baseline_world, baseline_player = deepcopy(state.world), deepcopy(state.player)
    state.director.choose_graph_action = lambda context: {"edge_id": "", "expand": True}
    state.director.expand_action_graph = lambda context: {
        "label": "Pocket the journal", "operation_id": "take:journal",
        "success_description": "You secured the journal.", "failure_description": "The journal is out of reach."}
    resolve(state, "I pocket the journal")
    assert "journal" in state.player.inventory
    assert current(state)["expansions"] == 1
    assert current(state)["current"] == "_branch1:success"
    record = deepcopy(state.world.turn_records[-1])
    assert record.intent.raw_input == "I pocket the journal"
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    world, player, _ = store.load()
    assert world.action_graphs == state.world.action_graphs
    assert world.turn_records[-1].action_graph_after == record.action_graph_after
    state.engine.replay_turn(world.turn_records[-1], baseline_world, baseline_player)
    assert baseline_world.action_graphs == world.action_graphs
    assert baseline_player.inventory == player.inventory


def test_invalid_expansion_cannot_invent_capabilities_or_mutate_state(game_state):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    state.director.choose_graph_action = lambda context: {"edge_id": "", "expand": True}
    state.director.expand_action_graph = lambda context: {
        "label": "Fly away", "operation_id": "spawn_dragon",
        "success_description": "A dragon appears.", "failure_description": "No dragon."}
    before = deepcopy(current(state))
    result = resolve(state, "I summon a dragon")
    assert not result.advance_time
    assert current(state) == before
    assert not state.world.turn_records


def test_stale_target_and_encounter_lock_remove_actions_but_preserve_exit(game_state):
    state = game_state
    state.world.scene_objects["0,0"] = ["chest"]
    scripted_plan(state, plan("open:chest"))
    resolve(state, "situation")
    state.world.scene_objects["0,0"] = []
    assert state.engine.action_graphs.choices(state.world, state.player) == ["choose Set this situation aside"]
    result = resolve(state, "choose Investigate the lead")
    assert not result.advance_time
    state.world.active_encounter = EncounterState("ambush", "combat", [], "Survive")
    resolve(state, "choose Set this situation aside")
    assert state.world.active_encounter.movement_locked
    assert current(state)["current"] == "_engine_exit"


def test_graph_and_effects_rollback_together_on_commit_failure(game_state, monkeypatch):
    state = game_state
    state.world.scene_objects["0,0"] = ["chest"]
    scripted_plan(state, plan("open:chest"))
    resolve(state, "situation")
    force_roll(state, True)
    before = deepcopy(current(state))
    def broken_commit(*args):
        state.player.gold = 999
        raise RuntimeError("simulated commit failure")
    monkeypatch.setattr(state.engine.turn_effects, "commit", broken_commit)
    gold = state.player.gold
    with pytest.raises(RuntimeError, match="simulated"):
        resolve(state, "choose Investigate the lead")
    assert state.player.gold == gold
    assert current(state) == before
    assert not state.world.turn_records


def test_narration_failure_keeps_committed_record_and_does_not_repeat_effects(game_state):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    force_roll(state, True)
    def broken_narration(*args):
        raise RuntimeError("provider unavailable")
    state.director.narrate_turn_outcome = broken_narration
    result = resolve(state, "choose Investigate the lead")
    assert result.advance_time
    assert current(state)["current"] == "found"
    assert len(state.world.turn_records) == 1
    resolve(state, "choose Investigate the lead")
    assert len(state.world.turn_records) == 1


def test_quest_fact_is_awarded_only_on_success_and_reaches_progression(game_state):
    state = game_state
    state.world.quests = [Quest("lead", "Lead", "Learn the truth", [
        QuestStage("lead:1", "Evidence", "Investigate the evidence", [Condition(ConditionKind.FACT_DISCOVERED, "truth")])],
        related_locations=["location-market"], required_for_finale=False)]
    state.world.active_quest_id = "lead"
    scripted_plan(state, plan("quest:lead:1:0"))
    resolve(state, "situation")
    force_roll(state, True)
    resolve(state, "choose Investigate the lead")
    assert "truth" in state.world.discovered_facts
    assert state.world.quests[0].status == QuestStatus.COMPLETE


def test_v5_save_migrates_without_creating_or_replaying_situations(game_state, tmp_path):
    state = game_state
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    payload = json.loads(store.path.read_text())
    payload["schema_version"] = 5
    payload["world"].pop("action_graphs")
    store.path.write_text(json.dumps(payload))
    world, player, memory = store.load()
    assert world.action_graphs == {}
    store.save(world, player, memory)
    assert json.loads(store.path.read_text())["schema_version"] == SAVE_SCHEMA_VERSION


def test_llm_graph_tasks_use_existing_schema_repair_and_mock_fallback(game_state):
    class BrokenClient:
        def complete_streaming(self, *args, **kwargs):
            return '{"invented": true}'
    director = LocalLLMDirector(BrokenClient(), game_state.director)
    assert director.plan_action_graph({"operations": []}) is None
    assert director.last_used_fallback


def test_offline_director_can_expand_an_exact_supported_action(game_state):
    state = game_state
    scripted_plan(state)
    state.world.scene_objects["0,0"] = ["journal"]
    resolve(state, "situation")
    resolve(state, "try take journal")
    assert "journal" in state.player.inventory
    assert current(state)["expansions"] == 1


def test_failed_check_cannot_award_effect_and_reentering_cannot_reset_it(game_state):
    state = game_state
    state.world.scene_objects["0,0"] = ["chest"]
    scripted_plan(state, plan("open:chest"))
    resolve(state, "situation")
    force_roll(state, False)
    resolve(state, "choose Investigate the lead")
    assert not state.world.object_states
    record = state.world.turn_records[-1]
    assert not record.outcome.accepted_effects
    assert len(record.outcome.rejected_effects) == 1
    before = deepcopy(current(state))
    resolve(state, "situation")
    resolve(state, "next situation")
    assert current(state) == before


def test_next_situation_requires_changed_capabilities(game_state):
    state = game_state
    state.engine.progression.evaluate(state.world, state.player, state.world.locations[0], state.memory)
    scripted_plan(state)
    resolve(state, "situation")
    resolve(state, "choose Set this situation aside")
    before = deepcopy(current(state))
    resolve(state, "next situation")
    assert current(state) == before
    state.world.scene_objects["0,0"] = ["journal"]
    resolve(state, "next situation")
    assert current(state)["current"] == "start"
    assert current(state)["id"] != before["id"]


def test_expansion_budget_blocks_repeated_new_attempts(game_state):
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    force_roll(state, False)
    for _ in range(4):
        resolve(state, "try study the situation")
    # The first maps to the existing edge; only the following three expand.
    resolve(state, "try study the situation")
    assert current(state)["expansions"] == 4
    tick = state.world.tick
    result = resolve(state, "try study the situation")
    assert not result.advance_time
    assert state.world.tick == tick
    assert "choose Set this situation aside" in state.engine.action_graphs.choices(state.world, state.player)


def test_tui_prioritizes_live_graph_choices_and_keeps_exit_visible(game_state, tmp_path):
    from worldsim.tui import Session, WorldSimApp
    from worldsim.command_input import normalize_command_input
    state = game_state
    scripted_plan(state)
    resolve(state, "situation")
    app = WorldSimApp(store=CampaignStore(tmp_path / "campaign.json"), engine=state.engine)
    app.session = Session(state.world, state.player, state.memory, "")
    labels = app._visible_choice_labels(state.world.locations[0])
    assert labels == ["choose Investigate the lead", "choose Set this situation aside"]
    assert normalize_command_input(labels[0], dialogue_active=True) == labels[0]
    assert normalize_command_input("try another approach", dialogue_active=True) == "try another approach"
