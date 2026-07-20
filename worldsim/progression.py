from __future__ import annotations

from typing import TYPE_CHECKING

from worldsim.memory import CampaignMemory
from worldsim.models import (
    ActionIntent,
    ActionKind,
    CampaignStatus,
    CheckKind,
    ClockStatus,
    ClockTrigger,
    ClockTriggerKind,
    CommandResult,
    Condition,
    ConditionKind,
    DirectorBeat,
    EffectCondition,
    EffectKind,
    EffectSource,
    EncounterState,
    EncounterStatus,
    Player,
    Quest,
    QuestClock,
    QuestStage,
    QuestStageStatus,
    QuestStatus,
    StateEffect,
    TurnOutcome,
    TurnRecord,
    World,
)

if TYPE_CHECKING:
    from worldsim.director import Director
    from worldsim.engine import WorldEngine
    from worldsim.models import Location


class ProgressionService:
    """Owns quest, clock, finale, and terminal campaign state."""

    TERMINAL_STATUSES = {
        CampaignStatus.VICTORY,
        CampaignStatus.DEFEAT,
        CampaignStatus.ABANDONED,
    }

    def __init__(self, engine: WorldEngine) -> None:
        self.engine = engine

    def owns_effect(self, effect: StateEffect) -> bool:
        return effect.kind in {
            EffectKind.CAMPAIGN_VICTORY,
            EffectKind.CAMPAIGN_DEFEAT,
        }

    def replay_effect(self, effect: StateEffect, world: World, player: Player) -> None:
        self._commit_campaign_effect(effect, world, CampaignMemory())

    def ensure(self, world: World) -> None:
        try:
            world.campaign_status = CampaignStatus(world.campaign_status)
        except ValueError:
            world.campaign_status = CampaignStatus.ACTIVE

        generated = not world.quests
        if generated:
            hooks = world.quest_hooks or [world.overarching_quest]
            world.quests = [
                self.quest_from_hook(hook, index, world)
                for index, hook in enumerate(hooks[:6])
            ]
            for index, quest in enumerate(world.quests):
                if index:
                    quest.prerequisite_quest_ids = [world.quests[index - 1].id]
                    quest.status = QuestStatus.LOCKED

        for quest in world.quests:
            self._normalize_quest(quest, world)
        if world.quests and not world.main_quest_id:
            world.main_quest_id = world.quests[-1].id if generated else (
                world.active_quest_id or world.quests[-1].id
            )
        if world.main_quest_id and not world.finale_requirements:
            world.finale_requirements = [
                Condition(ConditionKind.QUEST_COMPLETED, world.main_quest_id)
            ]

        self._unlock_quests(world, emit_events=False)
        active = next(
            (quest for quest in world.quests if quest.status == QuestStatus.ACTIVE),
            None,
        )
        if active is not None and not any(
            quest.id == world.active_quest_id
            and quest.status == QuestStatus.ACTIVE
            for quest in world.quests
        ):
            world.active_quest_id = active.id
        if world.quests and not self._has_active_quest(world):
            self._activate_next_quest(world, emit_event=False)

        if not world.clocks:
            world.clocks = [
                QuestClock(
                    id="central_threat",
                    title="Central Threat",
                    value=1,
                    max_value=6,
                    description=world.overarching_quest,
                    triggers=[
                        ClockTrigger(
                            id="central-threat-consequence",
                            kind=ClockTriggerKind.STABILITY_DELTA,
                            amount=-10,
                            text="The central threat destabilizes the campaign.",
                        ),
                        ClockTrigger(
                            id="central-threat-fact",
                            kind=ClockTriggerKind.ADD_FACT,
                            target_id="central_threat_reached_breaking_point",
                            text="The central threat reached its breaking point.",
                        ),
                        ClockTrigger(
                            id="central-threat-defeat",
                            kind=ClockTriggerKind.CAMPAIGN_DEFEAT,
                            text="The central threat reaches its endgame before it can be stopped.",
                        ),
                    ],
                )
            ]
        for clock in world.clocks:
            if not isinstance(clock, QuestClock):
                continue
            clock.__post_init__()
            if not clock.triggers:
                clock.triggers.append(
                    ClockTrigger(
                        id=f"{clock.id}-breaking-point",
                        kind=ClockTriggerKind.ADD_FACT,
                        target_id=f"clock:{clock.id}:breaking_point",
                        text=f"{clock.title} reached its breaking point.",
                    )
                )
        self.sync_active_quest_display(world)

    def quest_from_hook(self, hook: str, index: int, world: World) -> Quest:
        title = self._quest_title(hook, index)
        quest_id = self._slug(f"{index + 1}-{title}")
        location = (
            world.locations[(index * 3 + 1) % len(world.locations)]
            if world.locations
            else None
        )
        npc = None
        if location is not None:
            npc = next(
                (item for item in world.npcs if item.location_id == location.id),
                None,
            )
        if npc is None and world.npcs:
            npc = world.npcs[index % len(world.npcs)]

        location_id = location.id if location is not None else ""
        npc_id = npc.id if npc is not None else ""
        stages = [
            QuestStage(
                id=f"{quest_id}:stage:1",
                title="Reach the lead",
                description=(
                    f"Reach {location.name} and establish what is happening."
                    if location is not None
                    else "Reach the place connected to the lead."
                ),
                conditions=[
                    Condition(ConditionKind.LOCATION_REACHED, location_id)
                ]
                if location_id
                else [Condition(ConditionKind.FACT_DISCOVERED, f"quest:{quest_id}:lead")],
                status=QuestStageStatus.ACTIVE if index == 0 else QuestStageStatus.LOCKED,
            ),
            QuestStage(
                id=f"{quest_id}:stage:2",
                title="Win a witness",
                description=(
                    f"Recruit {npc.name} to the cause."
                    if npc is not None
                    else "Secure testimony that exposes the real threat."
                ),
                conditions=[
                    Condition(ConditionKind.NPC_RECRUITED, npc_id, expected="allied")
                ]
                if npc_id
                else [Condition(ConditionKind.FACT_DISCOVERED, f"quest:{quest_id}:truth")],
            ),
            QuestStage(
                id=f"{quest_id}:stage:3",
                title="Commit the resolution",
                description="Make and carry out the decision that resolves this threat.",
                conditions=[
                    Condition(ConditionKind.CHOICE_COMMITTED, f"quest:{quest_id}:resolution")
                ],
            ),
        ]
        return Quest(
            id=quest_id,
            title=title,
            goal=hook,
            stages=stages,
            status=QuestStatus.ACTIVE if index == 0 else QuestStatus.LOCKED,
            related_locations=[location_id] if location_id else [],
            related_npcs=[npc_id] if npc_id else [],
        )

    def active_quest(self, world: World) -> Quest | None:
        self.ensure(world)
        return next(
            (
                quest
                for quest in world.quests
                if quest.id == world.active_quest_id
                and quest.status == QuestStatus.ACTIVE
            ),
            None,
        )

    def sync_active_quest_display(self, world: World) -> None:
        quest = next(
            (
                item
                for item in world.quests
                if item.id == world.active_quest_id
                and item.status == QuestStatus.ACTIVE
            ),
            None,
        )
        if quest is None:
            world.active_quest = (
                f"Finale: {world.finale_title}"
                if world.campaign_status == CampaignStatus.FINALE
                else None
            )
            return
        stage = self.current_stage(quest)
        world.active_quest = (
            f"{quest.title}: {stage.description}" if stage is not None else quest.goal
        )

    def current_stage(self, quest: Quest) -> QuestStage | None:
        if not quest.stages:
            return None
        index = min(max(quest.current_stage, 0), len(quest.stages) - 1)
        stage = quest.stages[index]
        return stage if isinstance(stage, QuestStage) else None

    def apply_beat(
        self,
        world: World,
        player: Player,
        beat: DirectorBeat,
        memory: CampaignMemory,
        location: Location | None,
        success: bool | None,
    ) -> None:
        self.ensure(world)
        if success is False:
            for effect in beat.clock_effects:
                delta = effect.get("delta", 0)
                if isinstance(delta, int) and delta > 0:
                    self.apply_clock_effect(world, effect, player, memory)
            return

        for fact in beat.facts_discovered:
            self.add_fact(world, fact)
        for change in beat.npc_disposition_changes:
            npc_id = change.get("npc_id")
            disposition = change.get("disposition")
            if not isinstance(npc_id, str) or not isinstance(disposition, str):
                continue
            if self.condition_target_is_active(
                world,
                ConditionKind.NPC_RECRUITED,
                npc_id,
                disposition,
            ):
                npc = next((item for item in world.npcs if item.id == npc_id), None)
                if npc is not None:
                    npc.disposition = disposition
        for choice_id in beat.choices_committed:
            if self.condition_target_is_active(
                world,
                ConditionKind.CHOICE_COMMITTED,
                choice_id,
            ):
                self.commit_choice(world, choice_id)

        quest = self.active_quest(world)
        progress_attempted = bool(
            beat.complete_current_stage
            or beat.facts_discovered
            or beat.npc_disposition_changes
            or beat.choices_committed
        )
        if quest is not None and beat.progress_summary and progress_attempted:
            summary = " ".join(beat.progress_summary.split())
            if summary:
                quest.discoveries.append(summary)
                del quest.discoveries[:-8]
                self.engine._remember_state_fact(
                    world,
                    f"Quest evidence for {quest.title}: {summary}",
                    world.tick,
                )
                memory.remember(
                    "quest",
                    quest.id,
                    f"{quest.title}: {summary}",
                    world.tick,
                    importance=9,
                    tags=["quest", quest.title, location.name if location else "world"],
                )
        for effect in beat.clock_effects:
            self.apply_clock_effect(world, effect, player, memory)
        self.evaluate(world, player, location, memory)

    def evaluate(
        self,
        world: World,
        player: Player,
        location: Location | None,
        memory: CampaignMemory,
    ) -> None:
        self.ensure(world)
        if world.campaign_status in self.TERMINAL_STATUSES:
            return
        if player.hp <= 0:
            self.finish_campaign(
                world,
                CampaignStatus.DEFEAT,
                "The protagonist fell before the campaign could be resolved.",
                memory,
            )
            return
        if world.stability <= 0:
            self.finish_campaign(
                world,
                CampaignStatus.DEFEAT,
                "The world lost all remaining stability.",
                memory,
            )
            return

        changed = True
        passes = 0
        while changed and passes < max(1, len(world.quests) * 4):
            passes += 1
            changed = self._unlock_quests(world)
            if not self._has_active_quest(world):
                changed = self._activate_next_quest(world) or changed
            quest = self.active_quest(world)
            if quest is None:
                break
            stage = self.current_stage(quest)
            if stage is None or not stage.conditions:
                break
            if not all(
                self.condition_satisfied(condition, world, player, location)
                for condition in stage.conditions
            ):
                break
            self._complete_stage(world, quest, stage, memory)
            changed = True

        required = [quest for quest in world.quests if quest.required_for_finale]
        if (
            world.campaign_status == CampaignStatus.ACTIVE
            and required
            and all(quest.status == QuestStatus.COMPLETE for quest in required)
        ):
            self.start_finale(
                world,
                f"All required quest lines are resolved. {world.finale_title} begins.",
                memory,
            )
        self.sync_active_quest_display(world)

    def condition_satisfied(
        self,
        condition: Condition,
        world: World,
        player: Player,
        location: Location | None,
    ) -> bool:
        target = condition.target_id.casefold()
        if condition.kind == ConditionKind.ITEM_ACQUIRED:
            return any(item.casefold() == target for item in player.inventory)
        if condition.kind == ConditionKind.NPC_RECRUITED:
            npc = next((item for item in world.npcs if item.id.casefold() == target), None)
            expected = (condition.expected or "allied").casefold()
            return npc is not None and npc.disposition.casefold() == expected
        if condition.kind == ConditionKind.FACT_DISCOVERED:
            return any(fact.casefold() == target for fact in world.discovered_facts)
        if condition.kind == ConditionKind.TARGET_DEFEATED:
            return any(item.casefold() == target for item in world.resolved_encounter_ids)
        if condition.kind == ConditionKind.LOCATION_REACHED:
            return location is not None and location.id.casefold() == target
        if condition.kind == ConditionKind.OBJECT_ACTIVATED:
            expected = (condition.expected or "activated").casefold()
            return any(
                (
                    key.casefold() == target
                    or str(record.get("id", "")).casefold() == target
                )
                and str(record.get("status", "")).casefold() == expected
                for key, record in world.object_states.items()
            )
        if condition.kind == ConditionKind.CHOICE_COMMITTED:
            return any(choice.casefold() == target for choice in world.committed_choices)
        if condition.kind == ConditionKind.CLOCK_THRESHOLD:
            clock = next(
                (item for item in world.clocks if item.id.casefold() == target),
                None,
            )
            threshold = (
                condition.minimum
                if condition.minimum is not None
                else clock.max_value
                if clock is not None
                else 0
            )
            return clock is not None and clock.value >= threshold
        if condition.kind == ConditionKind.QUEST_COMPLETED:
            quest = next(
                (item for item in world.quests if item.id.casefold() == target),
                None,
            )
            return quest is not None and quest.status == QuestStatus.COMPLETE
        return False

    def apply_clock_effect(
        self,
        world: World,
        effect: dict[str, object],
        player: Player | None = None,
        memory: CampaignMemory | None = None,
    ) -> None:
        clock_id = effect.get("clock_id")
        delta = effect.get("delta")
        if not isinstance(clock_id, str) or not isinstance(delta, int) or delta == 0:
            return
        clock = next(
            (
                item
                for item in world.clocks
                if item.id == clock_id and item.status == ClockStatus.ACTIVE
            ),
            None,
        )
        if clock is None:
            return
        before = clock.value
        clock.value = max(0, min(clock.max_value, clock.value + delta))
        if clock.value == before:
            return
        reason = effect.get("reason")
        reason_text = (
            reason
            if isinstance(reason, str) and reason.strip()
            else "pressure changed"
        )
        self.engine._remember_state_fact(
            world,
            f"Clock {clock.title} changed from {before} to {clock.value}: {reason_text}",
            world.tick,
        )
        if clock.value >= clock.max_value:
            clock.status = ClockStatus.COMPLETE
            self.engine._add_event(
                world,
                "clock",
                f"{clock.title} reaches its breaking point: {reason_text}",
                severity="warning",
            )
            self.fire_clock_triggers(world, clock, player, memory)

    def fire_clock_triggers(
        self,
        world: World,
        clock: QuestClock,
        player: Player | None = None,
        memory: CampaignMemory | None = None,
    ) -> None:
        if clock.triggered:
            return
        for trigger in clock.triggers:
            if trigger.fired:
                continue
            self._fire_clock_trigger(world, clock, trigger, player, memory)
            trigger.fired = True
        clock.triggered = True
        self._unlock_quests(world)
        self.sync_active_quest_display(world)

    def resolve_command(
        self,
        command: str,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
    ) -> CommandResult | None:
        text = " ".join(command.casefold().split())
        if text in {"campaign", "campaign status", "status"}:
            return CommandResult(self.campaign_summary(world))

        if world.campaign_status in self.TERMINAL_STATUSES:
            if text in {"inventory", "inv", "items"}:
                inventory = ", ".join(player.inventory) or "nothing"
                return CommandResult(f"You carry: {inventory}.")
            if text == "help":
                return CommandResult(
                    "The campaign has ended. Available commands: campaign status, inventory, quit."
                )
            return CommandResult(
                f"The campaign has ended with {world.campaign_status.value}. "
                "Use `campaign status`, `inventory`, or `quit`."
            )

        if text in {"abandon campaign", "abandon"}:
            self.finish_campaign(
                world,
                CampaignStatus.ABANDONED,
                "The protagonist chose to leave the campaign unresolved.",
                memory,
            )
            return CommandResult(world.epilogue or "The campaign is abandoned.")

        if text in {"resolve finale", "face finale", "conclude campaign"}:
            return self._resolve_finale(world, player, director, memory, command)
        return None

    def campaign_summary(self, world: World) -> str:
        lines = [
            f"Campaign status: {world.campaign_status.value}.",
            f"Finale: {world.finale_title}.",
        ]
        if world.ending_reason:
            lines.append(f"Outcome: {world.ending_reason}")
        if world.epilogue:
            lines.append(world.epilogue)
        return " ".join(lines)

    def start_finale(
        self,
        world: World,
        reason: str,
        memory: CampaignMemory | None = None,
    ) -> None:
        if world.campaign_status != CampaignStatus.ACTIVE:
            return
        world.campaign_status = CampaignStatus.FINALE
        world.ending_reason = reason
        world.active_quest_id = None
        world.active_quest = f"Finale: {world.finale_title}"
        world.current_choices = ["resolve finale", "campaign status", "inventory"]
        self.engine._add_event(world, "campaign", reason, severity="warning")
        self.engine._remember_state_fact(world, reason, world.tick)
        if memory is not None:
            memory.remember(
                "campaign",
                "finale",
                reason,
                world.tick,
                importance=10,
                tags=["campaign", "finale"],
            )

    def finish_campaign(
        self,
        world: World,
        status: CampaignStatus,
        reason: str,
        memory: CampaignMemory | None = None,
    ) -> None:
        if world.campaign_status in self.TERMINAL_STATUSES:
            return
        world.campaign_status = status
        world.ending_reason = reason
        if status == CampaignStatus.VICTORY:
            world.epilogue = (
                f"{world.campaign_title} ends in victory. {reason} "
                "The surviving people carry the result forward."
            )
        elif status == CampaignStatus.DEFEAT:
            world.epilogue = (
                f"{world.campaign_title} ends in defeat. {reason} "
                "The consequences become the final record of the campaign."
            )
        else:
            world.epilogue = (
                f"{world.campaign_title} is abandoned. {reason} "
                "Its unresolved threads remain in the world."
            )
        world.dialogue_state = None
        if world.active_encounter is not None:
            world.active_encounter.phase = "campaign_ended"
            world.active_encounter.status = EncounterStatus.RESOLVED
            world.active_encounter.resolution = reason
            if world.active_encounter.id not in world.resolved_encounter_ids:
                world.resolved_encounter_ids.append(world.active_encounter.id)
                del world.resolved_encounter_ids[:-80]
        world.current_activity = None
        world.movement_lock = None
        world.current_choices = ["campaign status", "inventory", "quit"]
        self.engine._add_event(
            world,
            "campaign",
            world.epilogue,
            severity="success" if status == CampaignStatus.VICTORY else "warning",
        )
        self.engine._remember_state_fact(world, world.epilogue, world.tick)
        if memory is not None:
            memory.remember(
                "campaign",
                "ending",
                world.epilogue,
                world.tick,
                importance=10,
                tags=["campaign", status.value],
            )

    def validate_effect(
        self,
        effect: StateEffect,
        world: World,
        player: Player,
    ) -> str | None:
        if effect.kind in {
            EffectKind.CAMPAIGN_VICTORY,
            EffectKind.CAMPAIGN_DEFEAT,
        }:
            if effect.source != EffectSource.ENGINE:
                return "campaign outcomes are engine-owned"
            if world.campaign_status != CampaignStatus.FINALE:
                return "campaign outcomes require an active finale"
        return None

    def commit_effect(
        self,
        effect: StateEffect,
        world: World,
        player: Player,
        memory: CampaignMemory,
    ) -> None:
        if effect.kind in {
            EffectKind.CAMPAIGN_VICTORY,
            EffectKind.CAMPAIGN_DEFEAT,
        }:
            self._commit_campaign_effect(effect, world, memory)

    def _resolve_finale(
        self,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
        command: str,
    ) -> CommandResult:
        if world.campaign_status != CampaignStatus.FINALE:
            return CommandResult("The campaign has not reached its finale.")
        location = self.engine.location_at(world, player.position)
        unmet = [
            condition
            for condition in world.finale_requirements
            if not self.condition_satisfied(condition, world, player, location)
        ]
        if unmet:
            labels = ", ".join(condition.target_id for condition in unmet)
            return CommandResult(f"The finale is not ready. Unmet requirements: {labels}.")

        difficulty = max(8, min(18, 12 + max(0, 50 - world.stability) // 10))
        check = self.engine.resolve_typed_check(
            world,
            player,
            difficulty,
            CheckKind.COMBAT,
        )
        effects = [
            StateEffect(
                kind=EffectKind.CAMPAIGN_VICTORY,
                value="The final confrontation was won.",
                condition=EffectCondition.SUCCESS,
                source=EffectSource.ENGINE,
            ),
            StateEffect(
                kind=EffectKind.CAMPAIGN_DEFEAT,
                value="The final confrontation was lost.",
                condition=EffectCondition.FAILURE,
                source=EffectSource.ENGINE,
            ),
        ]
        turn_id = f"turn-{world.tick:06d}-{len(world.turn_records) + 1:04d}"
        intent = ActionIntent(
            id=turn_id,
            raw_input=command,
            kind=ActionKind.EXPLICIT,
            title=world.finale_title,
            stakes="The campaign's final outcome is decided.",
            check_kind=check.kind,
            difficulty=check.difficulty,
            proposed_effects=effects,
            tags=["campaign", "finale"],
        )
        accepted, rejected = self.engine.state_reducer.apply(
            world,
            player,
            effects,
            check,
            lambda effect: self.validate_effect(effect, world, player),
            lambda effect: self.commit_effect(effect, world, player, memory),
        )
        outcome = TurnOutcome(
            success=check.success,
            accepted_effects=accepted,
            rejected_effects=rejected,
            authoritative_summary=self.engine.turn_effects.summarize(
                check,
                accepted,
                rejected,
            ),
        )
        narration = world.epilogue or self.campaign_summary(world)
        choices = list(world.current_choices)
        world.turn_records.append(
            TurnRecord(
                id=turn_id,
                tick=world.tick,
                command=command,
                intent=intent,
                check=check,
                outcome=outcome,
                narration=narration,
                choices=choices,
            )
        )
        del world.turn_records[:-100]
        self.engine.advance_world(world, player, director, memory, "finale")
        memory.remember_world_state(world, player)
        return CommandResult(
            f"{narration}\n\n{check.summary}",
            advance_time=True,
        )

    def _commit_campaign_effect(
        self,
        effect: StateEffect,
        world: World,
        memory: CampaignMemory,
    ) -> None:
        status = (
            CampaignStatus.VICTORY
            if effect.kind == EffectKind.CAMPAIGN_VICTORY
            else CampaignStatus.DEFEAT
        )
        self.finish_campaign(
            world,
            status,
            effect.value or f"The finale ended in {status.value}.",
            memory,
        )

    def _complete_stage(
        self,
        world: World,
        quest: Quest,
        stage: QuestStage,
        memory: CampaignMemory,
    ) -> None:
        stage.status = QuestStageStatus.COMPLETE
        self.engine._add_event(
            world,
            "quest",
            f"{quest.title} completed stage: {stage.title}.",
        )
        quest.current_stage += 1
        quest.progress = 0
        if quest.current_stage >= len(quest.stages):
            quest.current_stage = max(0, len(quest.stages) - 1)
            quest.status = QuestStatus.COMPLETE
            self.engine._add_event(world, "quest", f"Quest completed: {quest.title}.")
            memory.remember(
                "quest",
                quest.id,
                f"{quest.title} is complete.",
                world.tick,
                importance=10,
                tags=["quest", "complete"],
            )
            if world.active_quest_id == quest.id:
                world.active_quest_id = None
            return
        next_stage = self.current_stage(quest)
        if next_stage is not None:
            next_stage.status = QuestStageStatus.ACTIVE
            self.engine._add_event(
                world,
                "quest",
                f"{quest.title} advanced: {next_stage.description}",
            )
            memory.remember(
                "quest",
                quest.id,
                f"{quest.title} advanced to: {next_stage.description}",
                world.tick,
                importance=9,
                tags=["quest"],
            )

    def _unlock_quests(self, world: World, emit_events: bool = True) -> bool:
        changed = False
        completed = {
            quest.id for quest in world.quests if quest.status == QuestStatus.COMPLETE
        }
        for quest in world.quests:
            if quest.status != QuestStatus.LOCKED:
                continue
            if all(item in completed for item in quest.prerequisite_quest_ids):
                quest.status = QuestStatus.AVAILABLE
                changed = True
                if emit_events:
                    self.engine._add_event(
                        world,
                        "quest",
                        f"Quest available: {quest.title}.",
                    )
        return changed

    def _activate_next_quest(
        self,
        world: World,
        emit_event: bool = True,
    ) -> bool:
        candidate = next(
            (quest for quest in world.quests if quest.status == QuestStatus.AVAILABLE),
            None,
        )
        if candidate is None:
            candidate = next(
                (quest for quest in world.quests if quest.status == QuestStatus.ACTIVE),
                None,
            )
        if candidate is None:
            return False
        candidate.status = QuestStatus.ACTIVE
        stage = self.current_stage(candidate)
        if stage is not None:
            stage.status = QuestStageStatus.ACTIVE
        world.active_quest_id = candidate.id
        if emit_event:
            self.engine._add_event(world, "quest", f"Quest activated: {candidate.title}.")
        return True

    def _has_active_quest(self, world: World) -> bool:
        return any(quest.status == QuestStatus.ACTIVE for quest in world.quests)

    def _normalize_quest(self, quest: Quest, world: World) -> None:
        quest.__post_init__()
        locations_by_name = {location.name.casefold(): location.id for location in world.locations}
        npcs_by_name = {npc.name.casefold(): npc.id for npc in world.npcs}
        quest.related_locations = [
            locations_by_name.get(item.casefold(), item)
            for item in quest.related_locations
        ]
        quest.related_npcs = [
            npcs_by_name.get(item.casefold(), item)
            for item in quest.related_npcs
        ]
        for index, stage in enumerate(quest.stages):
            if not isinstance(stage, QuestStage):
                continue
            for condition in stage.conditions:
                if condition.kind == ConditionKind.LOCATION_REACHED:
                    condition.target_id = locations_by_name.get(
                        condition.target_id.casefold(),
                        condition.target_id,
                    )
                elif condition.kind == ConditionKind.NPC_RECRUITED:
                    condition.target_id = npcs_by_name.get(
                        condition.target_id.casefold(),
                        condition.target_id,
                    )
            if not stage.conditions:
                stage.conditions = [
                    self._default_stage_condition(quest, index)
                ]
        quest.stage_conditions = [
            list(stage.conditions)
            for stage in quest.stages
            if isinstance(stage, QuestStage)
        ]

    def _default_stage_condition(self, quest: Quest, index: int) -> Condition:
        if index == 0 and quest.related_locations:
            return Condition(
                ConditionKind.LOCATION_REACHED,
                quest.related_locations[0],
            )
        if index == 1 and quest.related_npcs:
            return Condition(
                ConditionKind.NPC_RECRUITED,
                quest.related_npcs[0],
                expected="allied",
            )
        return Condition(
            ConditionKind.FACT_DISCOVERED,
            f"quest:{quest.id}:stage:{index + 1}",
        )

    def condition_target_is_active(
        self,
        world: World,
        kind: ConditionKind,
        target_id: str,
        expected: str | None = None,
    ) -> bool:
        quest = self.active_quest(world)
        stage = self.current_stage(quest) if quest is not None else None
        if stage is None:
            return False
        return any(
            condition.kind == kind
            and condition.target_id == target_id
            and (
                expected is None
                or condition.expected is None
                or condition.expected.casefold() == expected.casefold()
            )
            for condition in stage.conditions
        )

    def add_fact(self, world: World, fact: str) -> None:
        normalized = " ".join(fact.split())
        if normalized and normalized not in world.discovered_facts:
            world.discovered_facts.append(normalized)
            del world.discovered_facts[:-80]
            self.engine._remember_state_fact(
                world,
                f"Fact discovered: {normalized}",
                world.tick,
            )

    def commit_choice(self, world: World, choice_id: str) -> None:
        if choice_id not in world.committed_choices:
            world.committed_choices.append(choice_id)
            del world.committed_choices[:-80]
            self.engine._remember_state_fact(
                world,
                f"Choice committed: {choice_id}",
                world.tick,
            )

    def _fire_clock_trigger(
        self,
        world: World,
        clock: QuestClock,
        trigger: ClockTrigger,
        player: Player | None,
        memory: CampaignMemory | None,
    ) -> None:
        if trigger.kind == ClockTriggerKind.ADD_FACT:
            self.add_fact(
                world,
                trigger.target_id or trigger.text or f"clock:{clock.id}:complete",
            )
        elif trigger.kind == ClockTriggerKind.FAIL_QUEST:
            quest = next(
                (item for item in world.quests if item.id == trigger.target_id),
                None,
            )
            if quest is not None and quest.status not in {
                QuestStatus.COMPLETE,
                QuestStatus.FAILED,
            }:
                quest.status = QuestStatus.FAILED
                quest.failure_reason = trigger.text or f"{clock.title} expired."
                stage = self.current_stage(quest)
                if stage is not None:
                    stage.status = QuestStageStatus.FAILED
                self.engine._add_event(
                    world,
                    "quest",
                    f"Quest failed: {quest.title}.",
                    severity="warning",
                )
        elif trigger.kind == ClockTriggerKind.ACTIVATE_QUEST:
            quest = next(
                (item for item in world.quests if item.id == trigger.target_id),
                None,
            )
            if quest is not None and quest.status in {
                QuestStatus.LOCKED,
                QuestStatus.AVAILABLE,
            }:
                active = self.active_quest(world)
                if active is not None:
                    active.status = QuestStatus.AVAILABLE
                quest.status = QuestStatus.ACTIVE
                world.active_quest_id = quest.id
        elif trigger.kind == ClockTriggerKind.STABILITY_DELTA:
            world.stability = max(0, min(100, world.stability + trigger.amount))
        elif trigger.kind == ClockTriggerKind.START_ENCOUNTER:
            if player is not None:
                location = self.engine.location_at(world, player.position)
                self.engine._start_encounter(
                    world,
                    player,
                    location,
                    trigger.text or f"Survive the consequences of {clock.title}.",
                )
                if trigger.target_id and world.active_encounter is not None:
                    world.active_encounter.id = trigger.target_id
            else:
                world.active_encounter = EncounterState(
                    id=trigger.target_id or f"clock-encounter-{clock.id}",
                    kind="clock_trigger",
                    participants=[],
                    objective=trigger.text or f"Survive the consequences of {clock.title}.",
                    phase="opening",
                    exits=["flee"],
                )
        elif trigger.kind == ClockTriggerKind.SCENE_TENSION:
            if world.active_scene is not None:
                world.active_scene.tension = max(
                    0,
                    world.active_scene.tension + trigger.amount,
                )
        elif trigger.kind == ClockTriggerKind.START_FINALE:
            self.start_finale(
                world,
                trigger.text or f"{clock.title} forces the finale to begin.",
                memory,
            )
        elif trigger.kind == ClockTriggerKind.CAMPAIGN_VICTORY:
            self.finish_campaign(
                world,
                CampaignStatus.VICTORY,
                trigger.text or f"{clock.title} resolves in the protagonist's favor.",
                memory,
            )
        elif trigger.kind == ClockTriggerKind.CAMPAIGN_DEFEAT:
            self.finish_campaign(
                world,
                CampaignStatus.DEFEAT,
                trigger.text or f"{clock.title} resolves against the protagonist.",
                memory,
            )
        if trigger.text:
            self.engine._remember_state_fact(world, trigger.text, world.tick)

    def _quest_title(self, hook: str, index: int) -> str:
        cleaned = " ".join(hook.strip().rstrip(".").split())
        if not cleaned:
            return f"Thread {index + 1}"
        return " ".join(cleaned.split()[:5]).title()

    def _slug(self, text: str) -> str:
        chars: list[str] = []
        previous_dash = False
        for char in text.casefold():
            if char.isalnum():
                chars.append(char)
                previous_dash = False
            elif not previous_dash:
                chars.append("-")
                previous_dash = True
        return "".join(chars).strip("-")[:60] or "quest"
