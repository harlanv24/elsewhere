from __future__ import annotations

from typing import TYPE_CHECKING

from worldsim.memory import CampaignMemory
from worldsim.models import (
    ActionIntent,
    CheckKind,
    CheckResult,
    ConditionKind,
    DirectorBeat,
    EffectCondition,
    EffectKind,
    EffectSource,
    EncounterStatus,
    Location,
    Player,
    RejectedEffect,
    SceneMode,
    StateEffect,
    World,
)

if TYPE_CHECKING:
    from worldsim.engine import WorldEngine


class TurnEffectService:
    """Builds, validates, commits, and summarizes authoritative turn effects."""

    def __init__(self, engine: WorldEngine) -> None:
        self.engine = engine

    def prepare(self, intent: ActionIntent, world: World) -> list[StateEffect]:
        effects = list(intent.proposed_effects)
        if not any(effect.kind == EffectKind.LOCATION_TRANSITION for effect in effects):
            destination = self._requested_location_transition(intent.raw_input, world)
            if destination is not None:
                effects.append(
                    StateEffect(
                        kind=EffectKind.LOCATION_TRANSITION,
                        target_id=destination.id,
                        source=EffectSource.ENGINE,
                    )
                )
        targeted = self.engine._targeted_object_action(intent.raw_input)
        if targeted is not None:
            verb, target = targeted
            if verb in {"take", "get", "grab"}:
                effects.append(
                    StateEffect(
                        kind=EffectKind.INVENTORY_ADD,
                        target_id=target,
                        source=EffectSource.ENGINE,
                    )
                )
            elif verb in {"burn", "destroy", "break", "tear", "shatter", "discard"}:
                effects.append(
                    StateEffect(
                        kind=EffectKind.OBJECT_STATUS,
                        target_id=target,
                        value="destroyed",
                        source=EffectSource.ENGINE,
                    )
                )
        if intent.check_kind == CheckKind.COMBAT:
            effects.extend(
                [
                    StateEffect(
                        kind=EffectKind.ENCOUNTER_RESOLVE,
                        value="The hostile encounter was overcome.",
                        condition=EffectCondition.SUCCESS,
                        source=EffectSource.ENGINE,
                    ),
                    StateEffect(
                        kind=EffectKind.ENCOUNTER_START,
                        value="Survive or overcome the hostile threat.",
                        condition=EffectCondition.FAILURE,
                        source=EffectSource.ENGINE,
                    ),
                ]
            )
        elif (
            intent.check_kind == CheckKind.EXPLORATION
            and world.active_encounter is not None
            and world.active_encounter.movement_locked
        ):
            effects.append(
                StateEffect(
                    kind=EffectKind.ENCOUNTER_ESCAPE,
                    value="A successful exploration action found a route out.",
                    condition=EffectCondition.SUCCESS,
                    source=EffectSource.ENGINE,
                )
            )
        unique: list[StateEffect] = []
        for effect in effects:
            if effect not in unique:
                unique.append(effect)
        priority = {
            EffectKind.FACT_DISCOVERED: 0,
            EffectKind.NPC_DISPOSITION: 0,
            EffectKind.CHOICE_COMMIT: 0,
            EffectKind.QUEST_PROGRESS: 1,
        }
        return sorted(unique, key=lambda effect: priority.get(effect.kind, 0))

    def validate(
        self,
        effect: StateEffect,
        intent: ActionIntent,
        world: World,
        player: Player,
    ) -> str | None:
        target = self.engine._clean_item_name(effect.target_id or "")
        visible = self.engine.scene_objects_at(world, player.position)
        if (
            effect.source == EffectSource.DIRECTOR
            and intent.check_kind is not None
            and effect.condition != EffectCondition.SUCCESS
        ):
            return "director-proposed mutations must depend on check success"
        if (
            effect.kind
            in {
                EffectKind.ENCOUNTER_RESOLVE,
                EffectKind.ENCOUNTER_ESCAPE,
                EffectKind.ENCOUNTER_START,
            }
            and effect.source != EffectSource.ENGINE
        ):
            return "encounter lifecycle effects are engine-owned"
        if effect.kind in {
            EffectKind.SCENE_ENTER,
            EffectKind.SCENE_EXIT,
            EffectKind.SCENE_STEP,
            EffectKind.SCENE_TENSION,
        }:
            return "scene lifecycle effects are engine-owned"
        if effect.kind in {
            EffectKind.CAMPAIGN_VICTORY,
            EffectKind.CAMPAIGN_DEFEAT,
        }:
            return self.engine.progression.validate_effect(effect, world, player)
        if (
            any(item.kind == EffectKind.LOCATION_TRANSITION for item in intent.proposed_effects)
            and effect.kind
            in {
                EffectKind.SCENE_OBJECT_ADD,
                EffectKind.SCENE_OBJECT_REMOVE,
                EffectKind.INVENTORY_ADD,
                EffectKind.INVENTORY_REMOVE,
                EffectKind.OBJECT_STATUS,
            }
        ):
            return "travel turns cannot also mutate scene objects or inventory"
        if effect.kind == EffectKind.SCENE_OBJECT_ADD:
            if not target:
                return "scene object name is empty"
            state = self.engine._object_state_for_target(world, player.position, target)
            if state is not None and state.get("status") in {"destroyed", "removed", "in_inventory"}:
                return f"{target} is already {state.get('status')}"
        elif effect.kind == EffectKind.SCENE_OBJECT_REMOVE:
            if effect.source != EffectSource.ENGINE:
                return "scene object removal is engine-owned"
            if not target or not self.engine._matches_known_object(target, visible):
                return f"{target or 'object'} is not visible"
        elif effect.kind == EffectKind.INVENTORY_ADD:
            requested = self.engine._requested_take_item(intent.raw_input)
            if requested is None or not self.engine._matches_known_object(target, [requested]):
                return "inventory additions require an explicit take action"
            if not self.engine._matches_known_object(target, visible):
                return f"{target or 'item'} is not visible"
        elif effect.kind == EffectKind.INVENTORY_REMOVE:
            if not target or not self.engine._matches_known_object(target, player.inventory):
                return f"{target or 'item'} is not carried"
            targeted = self.engine._targeted_object_action(intent.raw_input)
            if (
                targeted is None
                or targeted[0] not in {"use", "discard", "consume", "eat", "drink"}
                or not self.engine._matches_known_object(target, [targeted[1]])
            ):
                return "inventory removal does not match an explicit use or discard action"
        elif effect.kind == EffectKind.OBJECT_STATUS:
            if not target:
                return "object target is empty"
            targeted = self.engine._targeted_object_action(intent.raw_input)
            if targeted is None or not self.engine._matches_known_object(target, [targeted[1]]):
                return "object status does not match the action target"
            allowed_statuses = {
                "open": {"open"},
                "close": {"closed"},
                "use": {"activated", "disabled"},
                "burn": {"destroyed", "removed"},
                "destroy": {"destroyed", "removed"},
                "break": {"destroyed", "removed"},
                "tear": {"destroyed", "removed"},
                "shatter": {"destroyed", "removed"},
                "discard": {"destroyed", "removed"},
            }
            if effect.value not in allowed_statuses.get(targeted[0], set()):
                return "object status is not legal for the attempted action"
            if not self.engine._matches_known_object(target, visible) and self.engine._object_state_for_target(
                world, player.position, target
            ) is None:
                return f"{target} is not present"
        elif effect.kind == EffectKind.QUEST_HOOK_ADD:
            if not effect.value:
                return "quest hook is empty"
        elif effect.kind == EffectKind.FACT_DISCOVERED:
            if not effect.target_id and not effect.value:
                return "fact is empty"
        elif effect.kind == EffectKind.NPC_DISPOSITION:
            npc = next((item for item in world.npcs if item.id == effect.target_id), None)
            if npc is None or not effect.value:
                return "NPC disposition requires an exact NPC ID and value"
            if not self.engine.progression.condition_target_is_active(
                world,
                ConditionKind.NPC_RECRUITED,
                npc.id,
                effect.value,
            ):
                return "NPC disposition does not satisfy the active quest stage"
        elif effect.kind == EffectKind.CHOICE_COMMIT:
            if not effect.target_id or not self.engine.progression.condition_target_is_active(
                world,
                ConditionKind.CHOICE_COMMITTED,
                effect.target_id,
            ):
                return "choice does not match the active quest stage"
        elif effect.kind == EffectKind.QUEST_PROGRESS:
            quest = self.engine._active_quest(world)
            if quest is None or (effect.target_id and effect.target_id != quest.id):
                return "active quest does not match the proposed progress"
        elif effect.kind == EffectKind.CLOCK_DELTA:
            clock = next(
                (item for item in world.clocks if item.id == effect.target_id and item.status == "active"),
                None,
            )
            if clock is None or effect.amount == 0:
                return "clock is unavailable or delta is zero"
        elif effect.kind in {EffectKind.ENCOUNTER_RESOLVE, EffectKind.ENCOUNTER_ESCAPE}:
            if world.active_encounter is None or not world.active_encounter.movement_locked:
                return "there is no active encounter to resolve"
        elif effect.kind == EffectKind.LOCATION_TRANSITION:
            destination = next(
                (location for location in world.locations if location.id == effect.target_id),
                None,
            )
            if destination is None:
                return "destination location ID is unknown"
            if not self._is_travel_action(intent.raw_input):
                return "location transition does not match a travel action"
            if world.active_scene is not None and world.active_scene.mode == SceneMode.LOCAL:
                return "leave the local scene before traveling"
            if world.active_encounter is not None and world.active_encounter.movement_locked:
                return "an active encounter prevents travel"
            current = self.engine.location_at(world, player.position)
            if current is not None and current.id == destination.id:
                return "the player is already at that location"
        return None

    def commit(
        self,
        effect: StateEffect,
        intent: ActionIntent,
        world: World,
        player: Player,
        memory: CampaignMemory,
        location: Location | None,
    ) -> None:
        target = self.engine._clean_item_name(effect.target_id or "")
        if effect.kind == EffectKind.SCENE_OBJECT_ADD:
            self.engine._remember_scene_objects(world, player.position, [target])
        elif effect.kind == EffectKind.SCENE_OBJECT_REMOVE:
            self.engine._remove_scene_object(world, player.position, target)
        elif effect.kind == EffectKind.INVENTORY_ADD:
            if target not in player.inventory:
                player.inventory.append(target)
            self.engine._set_object_state(
                world,
                player.position,
                target,
                status="in_inventory",
                tick=world.tick,
                owner=player.name,
            )
            self.engine._remove_scene_object(world, player.position, target)
        elif effect.kind == EffectKind.INVENTORY_REMOVE:
            match = next(
                item for item in player.inventory if self.engine._matches_known_object(target, [item])
            )
            player.inventory.remove(match)
        elif effect.kind == EffectKind.OBJECT_STATUS:
            self.engine._set_object_state(
                world,
                player.position,
                target,
                effect.value or "changed",
                world.tick,
            )
            if effect.value in {"destroyed", "removed"}:
                self.engine._remove_scene_object(world, player.position, target)
        elif effect.kind == EffectKind.QUEST_HOOK_ADD:
            hook = effect.value or ""
            if hook and hook not in world.quest_hooks:
                world.quest_hooks.insert(0, hook)
                memory.remember_hook(hook, world.tick)
        elif effect.kind == EffectKind.FACT_DISCOVERED:
            fact = effect.target_id or effect.value or ""
            self.engine.progression.add_fact(world, fact)
        elif effect.kind == EffectKind.NPC_DISPOSITION:
            npc = next(item for item in world.npcs if item.id == effect.target_id)
            npc.disposition = effect.value or npc.disposition
            self.engine._remember_state_fact(
                world,
                f"NPC disposition changed: {npc.id} is now {npc.disposition}.",
                world.tick,
            )
        elif effect.kind == EffectKind.CHOICE_COMMIT:
            self.engine.progression.commit_choice(world, effect.target_id or "")
        elif effect.kind == EffectKind.QUEST_PROGRESS:
            beat = DirectorBeat(
                title=intent.title,
                narration="",
                progress_summary=effect.value,
                quest_progress_delta=effect.amount,
                complete_current_stage=effect.flag,
                facts_discovered=[],
            )
            self.engine._apply_progression(world, player, beat, memory, location, True)
        elif effect.kind == EffectKind.CLOCK_DELTA:
            self.engine.progression.apply_clock_effect(
                world,
                {
                    "clock_id": effect.target_id,
                    "delta": effect.amount,
                    "reason": effect.value or "pressure changed",
                },
                player,
                memory,
            )
        elif effect.kind == EffectKind.ENCOUNTER_RESOLVE:
            self.engine._resolve_active_encounter(
                world,
                EncounterStatus.RESOLVED,
                effect.value or "Encounter resolved.",
            )
        elif effect.kind == EffectKind.ENCOUNTER_ESCAPE:
            self.engine._resolve_active_encounter(
                world,
                EncounterStatus.ESCAPED,
                effect.value or "Encounter escaped.",
            )
        elif effect.kind == EffectKind.ENCOUNTER_START:
            self.engine._start_encounter(world, player, location, effect.value or "Survive the threat.")
        elif effect.kind == EffectKind.LOCATION_TRANSITION:
            destination = next(
                location for location in world.locations if location.id == effect.target_id
            )
            player.position = destination.position
            world.dialogue_state = None
            self.engine.scene_service.sync(world, player, destination)
        elif effect.kind in {
            EffectKind.CAMPAIGN_VICTORY,
            EffectKind.CAMPAIGN_DEFEAT,
        }:
            self.engine.progression.commit_effect(effect, world, player, memory)

    def summarize(
        self,
        check: CheckResult | None,
        accepted: list[StateEffect],
        rejected: list[RejectedEffect],
    ) -> str:
        if check is not None and not check.success:
            base = "The check failed."
        elif check is not None:
            base = "The check succeeded."
        else:
            base = "The action required no check."
        if accepted:
            changes = ", ".join(effect.kind.value.replace("_", " ") for effect in accepted[:6])
            base += f" Committed effects: {changes}."
        else:
            base += " No authoritative state changed."
        if rejected:
            base += f" Rejected {len(rejected)} proposed effect(s)."
        return base

    def _requested_location_transition(self, action: str, world: World) -> Location | None:
        if not self._is_travel_action(action):
            return None
        normalized = " ".join(action.casefold().split())
        matches = [
            location
            for location in world.locations
            if " ".join(location.name.casefold().split()) in normalized
        ]
        return max(matches, key=lambda location: len(location.name), default=None)

    def _is_travel_action(self, action: str) -> bool:
        normalized = " ".join(action.casefold().split())
        verbs = (
            "travel to ",
            "go to ",
            "walk to ",
            "head to ",
            "journey to ",
            "return to ",
            "move to ",
        )
        return any(normalized.startswith(verb) for verb in verbs)
