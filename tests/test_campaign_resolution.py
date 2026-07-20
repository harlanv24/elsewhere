from __future__ import annotations

import json

from worldsim.engine import WorldEngine
from worldsim.memory import CampaignStore
from worldsim.models import (
    ActionIntent,
    CampaignStatus,
    ClockTrigger,
    ClockTriggerKind,
    Condition,
    ConditionKind,
    DialogueState,
    DirectorBeat,
    EffectKind,
    EncounterState,
    EncounterStatus,
    Quest,
    QuestClock,
    QuestStage,
    QuestStageStatus,
    QuestStatus,
    StateEffect,
)


def resolve(state, command: str):
    return state.engine.resolve_command(
        command,
        state.world,
        state.player,
        state.director,
        state.memory,
    )


def set_single_stage_quest(state, condition: Condition) -> Quest:
    quest = Quest(
        id="main-resolution",
        title="Resolve the Main Threat",
        goal="Meet the campaign's explicit requirement.",
        stages=[
            QuestStage(
                id="main-resolution:stage:1",
                title="Meet the requirement",
                description="Meet the campaign's explicit requirement.",
                conditions=[condition],
                status=QuestStageStatus.ACTIVE,
            )
        ],
        status=QuestStatus.ACTIVE,
        related_locations=["location-market"],
        related_npcs=["npc-witness"],
    )
    state.world.quests = [quest]
    state.world.active_quest_id = quest.id
    state.world.main_quest_id = quest.id
    state.world.finale_requirements = [
        Condition(ConditionKind.QUEST_COMPLETED, quest.id)
    ]
    state.world.campaign_status = CampaignStatus.ACTIVE
    state.world.epilogue = None
    state.world.ending_reason = None
    state.engine.ensure_progression(state.world)
    return quest


def test_generated_quests_use_typed_stages_stable_ids_and_prerequisites() -> None:
    engine = WorldEngine(seed=42)
    world = engine.create_world()

    location_ids = {location.id for location in world.locations}
    npc_ids = {npc.id for npc in world.npcs}
    assert world.active_quest_id == world.quests[0].id
    assert world.main_quest_id == world.quests[-1].id
    assert all(
        isinstance(stage, QuestStage)
        for quest in world.quests
        for stage in quest.stages
    )
    assert all(
        set(quest.related_locations) <= location_ids
        and set(quest.related_npcs) <= npc_ids
        for quest in world.quests
    )
    assert any(
        location_id not in {world.locations[0].id, world.locations[1].id}
        for quest in world.quests
        for location_id in quest.related_locations
    )
    assert world.quests[0].status == QuestStatus.ACTIVE
    for index, quest in enumerate(world.quests[1:], start=1):
        assert quest.status == QuestStatus.LOCKED
        assert quest.prerequisite_quest_ids == [world.quests[index - 1].id]


def test_irrelevant_discoveries_do_not_advance_a_condition_stage(game_state) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.FACT_DISCOVERED, "archive_truth"),
    )
    state.director.freeform_beat = DirectorBeat(
        title="Wrong Clue",
        narration="An unrelated detail emerges.",
        facts_discovered=["weather_note", "old_rumor"],
        progress_summary="Several irrelevant details were found.",
        complete_current_stage=True,
    )

    resolve(state, "search the shelves")

    assert quest.status == QuestStatus.ACTIVE
    assert quest.current_stage == 0
    assert "archive_truth" not in state.world.discovered_facts


def test_quest_completes_only_after_its_explicit_condition(game_state) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.ITEM_ACQUIRED, "moon relic"),
    )
    state.director.freeform_beat = DirectorBeat(
        title="Premature Claim",
        narration="Words alone change nothing.",
        complete_current_stage=True,
        quest_progress_delta=2,
    )

    resolve(state, "claim the quest is complete")
    assert quest.status == QuestStatus.ACTIVE

    state.player.inventory.append("moon relic")
    resolve(state, "present the moon relic")

    assert quest.status == QuestStatus.COMPLETE
    assert state.world.campaign_status == CampaignStatus.FINALE


def test_dialogue_recruits_exact_npc_and_advances_stage(game_state) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(
            ConditionKind.NPC_RECRUITED,
            "npc-witness",
            expected="allied",
        ),
    )
    state.world.dialogue_state = DialogueState(
        npc_id="npc-witness",
        npc_name="Iris",
        started_tick=state.world.tick,
    )
    state.director.dialogue_beat = DirectorBeat(
        title="An Alliance",
        narration="Iris agrees to help.",
        npc_disposition_changes=[
            {"npc_id": "npc-witness", "disposition": "allied"}
        ],
        progress_summary="Iris committed to the cause.",
    )

    resolve(state, "say stand with me")

    assert state.world.npcs[0].disposition == "allied"
    assert quest.status == QuestStatus.COMPLETE
    assert quest.discoveries == ["Iris committed to the cause."]


def test_defeated_target_remains_verifiable_after_encounter_resolution(game_state) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.TARGET_DEFEATED, "encounter-main"),
    )
    state.world.active_encounter = EncounterState(
        id="encounter-main",
        kind="combat",
        participants=[state.player.name],
        objective="Defeat the main threat.",
    )

    state.engine._resolve_active_encounter(
        state.world,
        EncounterStatus.RESOLVED,
        "The main threat was defeated.",
    )
    state.engine.progression.evaluate(
        state.world,
        state.player,
        state.engine.location_at(state.world, state.player.position),
        state.memory,
    )

    assert state.world.resolved_encounter_ids == ["encounter-main"]
    assert quest.status == QuestStatus.COMPLETE


def test_choice_effect_must_match_the_active_stage(game_state) -> None:
    state = game_state
    choice_id = "quest:main-resolution:resolution"
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.CHOICE_COMMITTED, choice_id),
    )

    def wrong_choice(*args, **kwargs):
        return ActionIntent(
            id="wrong-choice",
            raw_input="choose the unrelated route",
            proposed_effects=[
                StateEffect(
                    kind=EffectKind.CHOICE_COMMIT,
                    target_id="quest:other:resolution",
                )
            ],
        )

    state.director.interpret_freeform_action = wrong_choice
    resolve(state, "choose the unrelated route")
    assert quest.status == QuestStatus.ACTIVE
    assert state.world.committed_choices == []

    def right_choice(*args, **kwargs):
        return ActionIntent(
            id="right-choice",
            raw_input="choose the final route",
            proposed_effects=[
                StateEffect(kind=EffectKind.CHOICE_COMMIT, target_id=choice_id)
            ],
        )

    state.director.interpret_freeform_action = right_choice
    resolve(state, "choose the final route")

    assert state.world.committed_choices == [choice_id]
    assert quest.status == QuestStatus.COMPLETE


def test_completed_prerequisite_activates_the_next_quest(game_state) -> None:
    state = game_state
    first = Quest(
        id="first",
        title="First",
        goal="Find the first truth.",
        stages=[
            QuestStage(
                id="first:stage:1",
                title="First truth",
                description="Find the first truth.",
                conditions=[Condition(ConditionKind.FACT_DISCOVERED, "first_truth")],
                status=QuestStageStatus.ACTIVE,
            )
        ],
        status=QuestStatus.ACTIVE,
    )
    second = Quest(
        id="second",
        title="Second",
        goal="Find the second truth.",
        stages=[
            QuestStage(
                id="second:stage:1",
                title="Second truth",
                description="Find the second truth.",
                conditions=[Condition(ConditionKind.FACT_DISCOVERED, "second_truth")],
            )
        ],
        status=QuestStatus.LOCKED,
        prerequisite_quest_ids=["first"],
    )
    state.world.quests = [first, second]
    state.world.active_quest_id = first.id
    state.world.main_quest_id = second.id
    state.world.finale_requirements = [
        Condition(ConditionKind.QUEST_COMPLETED, second.id)
    ]
    state.world.discovered_facts.append("first_truth")

    state.engine.progression.evaluate(
        state.world,
        state.player,
        state.engine.location_at(state.world, state.player.position),
        state.memory,
    )

    assert first.status == QuestStatus.COMPLETE
    assert second.status == QuestStatus.ACTIVE
    assert state.world.active_quest_id == second.id
    assert state.world.campaign_status == CampaignStatus.ACTIVE


def test_clock_runs_structured_scene_quest_and_encounter_triggers_once(game_state) -> None:
    state = game_state
    target = Quest(
        id="clock-quest",
        title="Clock Quest",
        goal="Respond to the clock.",
        stages=[
            QuestStage(
                id="clock-quest:stage:1",
                title="Respond",
                description="Respond to the clock.",
                conditions=[Condition(ConditionKind.FACT_DISCOVERED, "clock_answer")],
            )
        ],
        status=QuestStatus.LOCKED,
        prerequisite_quest_ids=["never"],
        required_for_finale=False,
    )
    state.world.quests.append(target)
    state.world.active_scene.tension = 2
    state.world.clocks = [
        QuestClock(
            id="deadline",
            title="Deadline",
            value=1,
            max_value=2,
            triggers=[
                ClockTrigger(
                    id="activate",
                    kind=ClockTriggerKind.ACTIVATE_QUEST,
                    target_id=target.id,
                ),
                ClockTrigger(
                    id="tense",
                    kind=ClockTriggerKind.SCENE_TENSION,
                    amount=3,
                ),
                ClockTrigger(
                    id="ambush",
                    kind=ClockTriggerKind.START_ENCOUNTER,
                    target_id="encounter-deadline",
                    text="The deadline becomes an ambush.",
                ),
            ],
        )
    ]

    state.engine.progression.apply_clock_effect(
        state.world,
        {"clock_id": "deadline", "delta": 1, "reason": "Time expires."},
        state.player,
        state.memory,
    )
    state.engine.progression.fire_clock_triggers(
        state.world,
        state.world.clocks[0],
        state.player,
        state.memory,
    )

    assert target.status == QuestStatus.ACTIVE
    assert state.world.active_quest_id == target.id
    assert state.world.active_scene.tension == 5
    assert state.world.active_encounter.id == "encounter-deadline"
    assert all(trigger.fired for trigger in state.world.clocks[0].triggers)
    assert state.world.clocks[0].triggered is True


def test_clock_can_end_campaign_exactly_once(game_state) -> None:
    state = game_state
    state.world.clocks = [
        QuestClock(
            id="doom",
            title="Doom",
            value=0,
            max_value=1,
            triggers=[
                ClockTrigger(
                    id="doom-ending",
                    kind=ClockTriggerKind.CAMPAIGN_DEFEAT,
                    text="Doom overtakes the campaign.",
                )
            ],
        )
    ]

    state.engine.progression.apply_clock_effect(
        state.world,
        {"clock_id": "doom", "delta": 1, "reason": "The last hour passes."},
        state.player,
        state.memory,
    )
    epilogue = state.world.epilogue
    campaign_events = [
        event for event in state.world.recent_events if event.category == "campaign"
    ]
    state.engine.progression.fire_clock_triggers(
        state.world,
        state.world.clocks[0],
        state.player,
        state.memory,
    )

    assert state.world.campaign_status == CampaignStatus.DEFEAT
    assert state.world.epilogue == epilogue
    assert [
        event for event in state.world.recent_events if event.category == "campaign"
    ] == campaign_events


def test_completing_required_quests_starts_finale_without_auto_victory(game_state) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.FACT_DISCOVERED, "final_truth"),
    )
    state.world.discovered_facts.append("final_truth")

    state.engine.progression.evaluate(
        state.world,
        state.player,
        state.engine.location_at(state.world, state.player.position),
        state.memory,
    )

    assert quest.status == QuestStatus.COMPLETE
    assert state.world.campaign_status == CampaignStatus.FINALE
    assert state.world.epilogue is None
    assert "resolve finale" in state.world.current_choices


def test_finale_victory_is_persisted_and_replayable(game_state, tmp_path) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.FACT_DISCOVERED, "final_truth"),
    )
    quest.status = QuestStatus.COMPLETE
    state.engine.progression.start_finale(
        state.world,
        "The last confrontation begins.",
        state.memory,
    )
    state.player.boosts["combat_check"] = 30

    result = resolve(state, "resolve finale")

    record = state.world.turn_records[-1]
    assert result.advance_time is True
    assert state.world.campaign_status == CampaignStatus.VICTORY
    assert any(
        effect.kind == EffectKind.CAMPAIGN_VICTORY
        for effect in record.outcome.accepted_effects
    )
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    loaded = store.load()
    assert loaded is not None
    loaded_world, _, _ = loaded
    assert loaded_world.campaign_status == CampaignStatus.VICTORY
    assert loaded_world.epilogue == state.world.epilogue

    replay_engine = WorldEngine(seed=999)
    replay_world = loaded_world
    replay_world.campaign_status = CampaignStatus.FINALE
    replay_world.epilogue = None
    replay_engine.replay_turn(record, replay_world, state.player)
    assert replay_world.campaign_status == CampaignStatus.VICTORY


def test_failed_finale_roll_reaches_persisted_defeat(game_state, tmp_path) -> None:
    state = game_state
    quest = set_single_stage_quest(
        state,
        Condition(ConditionKind.FACT_DISCOVERED, "final_truth"),
    )
    quest.status = QuestStatus.COMPLETE
    state.engine.progression.start_finale(
        state.world,
        "The last confrontation begins.",
        state.memory,
    )
    state.player.boosts["combat_check"] = -30

    resolve(state, "resolve finale")

    assert state.world.campaign_status == CampaignStatus.DEFEAT
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    loaded = store.load()
    assert loaded is not None
    assert loaded[0].campaign_status == CampaignStatus.DEFEAT
    assert loaded[0].ending_reason == "The final confrontation was lost."


def test_terminal_campaign_blocks_gameplay_but_keeps_meta_commands(game_state) -> None:
    state = game_state
    state.world.active_encounter = EncounterState(
        id="encounter-final",
        kind="combat",
        participants=[state.player.name],
        objective="Survive the final threat.",
    )
    state.engine.progression.finish_campaign(
        state.world,
        CampaignStatus.VICTORY,
        "The threat is ended.",
        state.memory,
    )
    position = state.player.position
    tick = state.world.tick

    blocked = resolve(state, "north")
    inventory = resolve(state, "inventory")
    status = resolve(state, "campaign status")
    quit_result = resolve(state, "quit")

    assert "campaign has ended" in blocked.message.lower()
    assert state.player.position == position
    assert state.world.tick == tick
    assert state.engine.movement_lock_reason(state.world) is None
    assert "torch" in inventory.message
    assert "victory" in status.message.lower()
    assert quit_result.should_quit is True


def test_schema_three_save_migrates_typed_campaign_progression(game_state, tmp_path) -> None:
    state = game_state
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(state.world, state.player, state.memory)
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["schema_version"] = 3
    world = payload["world"]
    for field in (
        "campaign_status",
        "main_quest_id",
        "finale_requirements",
        "finale_title",
        "epilogue",
        "ending_reason",
        "resolved_encounter_ids",
    ):
        world.pop(field)
    for quest in world["quests"]:
        conditions = quest["stage_conditions"]
        quest["stages"] = [stage["description"] for stage in quest["stages"]]
        quest["stage_conditions"] = conditions
        quest.pop("prerequisite_quest_ids")
        quest.pop("required_for_finale")
        quest.pop("failure_reason")
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.load()

    assert loaded is not None
    migrated_world, _, _ = loaded
    assert migrated_world.campaign_status == CampaignStatus.ACTIVE
    assert migrated_world.main_quest_id
    assert migrated_world.finale_requirements[0].kind == ConditionKind.QUEST_COMPLETED
    assert all(
        isinstance(stage, QuestStage)
        for quest in migrated_world.quests
        for stage in quest.stages
    )
    assert migrated_world.quests[0].stages[0].conditions
