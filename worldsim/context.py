from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os

from worldsim.models import (
    ClockStatus,
    Location,
    Npc,
    Player,
    Quest,
    QuestStage,
    StateEffect,
    TurnRecord,
    World,
)


@dataclass(frozen=True)
class ContextBudget:
    """Approximate input-token ceiling for one director context."""

    max_estimated_tokens: int = 1800
    characters_per_token: int = 4

    @classmethod
    def from_env(cls) -> ContextBudget:
        raw_budget = os.getenv(
            "WORLDSIM_CONTEXT_TOKEN_BUDGET",
            str(cls.max_estimated_tokens),
        )
        raw_ratio = os.getenv(
            "WORLDSIM_CONTEXT_CHARS_PER_TOKEN",
            str(cls.characters_per_token),
        )
        try:
            budget = int(raw_budget)
        except ValueError:
            budget = cls.max_estimated_tokens
        try:
            ratio = int(raw_ratio)
        except ValueError:
            ratio = cls.characters_per_token
        return cls(
            max_estimated_tokens=max(256, budget),
            characters_per_token=max(1, ratio),
        )


@dataclass(frozen=True)
class ContextMetrics:
    task: str
    estimated_tokens: int
    budget_tokens: int
    character_count: int
    dropped_items: int
    truncated_strings: int

    @property
    def within_budget(self) -> bool:
        return self.estimated_tokens <= self.budget_tokens


@dataclass(frozen=True)
class ContextSelection:
    context: dict[str, object]
    metrics: ContextMetrics


class ContextSelector:
    """Builds relevance-based task context and enforces a deterministic budget."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget.from_env()

    def select(
        self,
        task: str,
        world: World,
        player: Player | None = None,
        location: Location | None = None,
        npc: Npc | None = None,
        memory_context: list[str] | None = None,
        action: str | None = None,
        player_dialogue: str | None = None,
        dialogue_history: list[str] | None = None,
        turn_record: TurnRecord | None = None,
    ) -> ContextSelection:
        if task == "generate_world_details":
            context = self._world_generation_context(world)
        else:
            context = self._turn_context(
                task,
                world,
                player,
                location,
                npc,
                memory_context or [],
                action,
                player_dialogue,
                dialogue_history,
                turn_record,
            )
        fitted, dropped, truncated = self._fit(context)
        serialized = self._serialize(fitted)
        metrics = ContextMetrics(
            task=task,
            estimated_tokens=self.estimate_tokens(fitted),
            budget_tokens=self.budget.max_estimated_tokens,
            character_count=len(serialized),
            dropped_items=dropped,
            truncated_strings=truncated,
        )
        return ContextSelection(fitted, metrics)

    def estimate_tokens(self, payload: object) -> int:
        return math.ceil(
            len(self._serialize(payload)) / self.budget.characters_per_token
        )

    def _turn_context(
        self,
        task: str,
        world: World,
        player: Player | None,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str],
        action: str | None,
        player_dialogue: str | None,
        dialogue_history: list[str] | None,
        turn_record: TurnRecord | None,
    ) -> dict[str, object]:
        active_quest = next(
            (quest for quest in world.quests if quest.id == world.active_quest_id),
            None,
        )
        position_key = (
            f"{player.position.x},{player.position.y}"
            if player is not None
            else None
        )
        relevant_locations = self._relevant_locations(
            world,
            location,
            active_quest,
            action,
        )
        relevant_npcs = self._relevant_npcs(world, npc, active_quest)
        canonical_dialogue = self._dialogue_history(
            world,
            npc,
            dialogue_history,
        )
        context: dict[str, object] = {
            "task": task,
            "campaign": {
                "title": world.campaign_title,
                "theme": world.theme_prompt,
                "status": world.campaign_status.value,
                "tick": world.tick,
                "weather": world.weather,
                "stability": world.stability,
                "overarching_quest": world.overarching_quest,
                "finale_title": world.finale_title,
            },
            "player": self._player_payload(player),
            "location": self._location_payload(location),
            "npc": self._npc_payload(npc),
            "scene": self._scene_payload(world),
            "encounter": self._encounter_payload(world),
            "active_quest": self._quest_payload(active_quest),
            "applicable_clocks": self._applicable_clocks(world, active_quest),
            "available_routes": self._available_routes(world, location),
            "relevant_locations": [
                self._location_payload(item) for item in relevant_locations
            ],
            "relevant_npcs": [
                self._npc_payload(item) for item in relevant_npcs
            ],
            "recent_events": [
                {
                    "tick": event.tick,
                    "category": event.category,
                    "text": event.text,
                    "severity": event.severity,
                }
                for event in world.recent_events[:4]
            ],
            "recent_outcomes": [
                self._recent_outcome(record)
                for record in world.turn_records[-3:]
            ],
            "memory_context": self._compact_unique(memory_context, 5, 180),
            "state_ledger": {
                "current_position": position_key,
                "visible_scene_objects": list(
                    world.scene_objects.get(position_key, [])
                )
                if position_key is not None
                else [],
                "object_states_here": self._object_states_here(
                    world,
                    position_key,
                ),
                "referenced_inventory": self._referenced_inventory(
                    world,
                    player,
                    action or player_dialogue,
                ),
                "recent_state_facts": self._compact_unique(
                    world.state_facts[-10:],
                    6,
                    160,
                ),
                "discovered_facts": self._relevant_facts(
                    world,
                    active_quest,
                ),
                "committed_choices": list(world.committed_choices[-8:]),
                "resolved_encounter_ids": list(
                    world.resolved_encounter_ids[-8:]
                ),
            },
        }
        if action is not None:
            context["action"] = action
        if player_dialogue is not None:
            context["player_dialogue"] = player_dialogue
        if canonical_dialogue:
            context["dialogue_history"] = canonical_dialogue
        if turn_record is not None:
            context["resolved_turn"] = self._resolved_turn(turn_record)
        return self._task_projection(task, context)

    def _task_projection(
        self,
        task: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        common = {
            "task",
            "campaign",
            "player",
            "location",
            "npc",
            "scene",
            "encounter",
            "active_quest",
            "applicable_clocks",
            "available_routes",
            "memory_context",
        }
        additions = {
            "introduce_world": {
                "relevant_locations",
                "relevant_npcs",
                "recent_events",
            },
            "describe_location": {
                "state_ledger",
                "recent_events",
                "recent_outcomes",
            },
            "respond_to_action": {
                "action",
                "state_ledger",
                "recent_outcomes",
                "relevant_locations",
            },
            "respond_to_freeform_action": {
                "action",
                "state_ledger",
                "recent_outcomes",
                "relevant_locations",
            },
            "interpret_freeform_action": {
                "action",
                "state_ledger",
                "recent_outcomes",
                "relevant_locations",
            },
            "narrate_turn_outcome": {
                "action",
                "state_ledger",
                "resolved_turn",
            },
            "respond_to_dialogue": {
                "player_dialogue",
                "dialogue_history",
                "state_ledger",
            },
            "ambient_world_event": {
                "recent_events",
                "recent_outcomes",
            },
        }
        keep = common | additions.get(task, set())
        return {
            key: value
            for key, value in context.items()
            if key in keep and value not in (None, [], {})
        }

    def _world_generation_context(self, world: World) -> dict[str, object]:
        return {
            "task": "generate_world_details",
            "theme_prompt": world.theme_prompt,
            "world_scaffold": {
                "seed": world.seed,
                "tick": world.tick,
                "stability": world.stability,
                "map_size": {
                    "width": world.width,
                    "height": world.height,
                },
                "locations": [
                    {
                        "index": index,
                        "id": location.id,
                        "biome": location.biome.value,
                        "danger": location.danger,
                        "position": {
                            "x": location.position.x,
                            "y": location.position.y,
                        },
                    }
                    for index, location in enumerate(world.locations)
                ],
                "npc_slots": [
                    {
                        "index": index,
                        "id": npc.id,
                        "location_id": npc.location_id,
                    }
                    for index, npc in enumerate(world.npcs)
                ],
            },
            "style": {
                "genre": world.theme_prompt,
                "tone": "infer from the theme; keep it playable and concise",
                "guidance": [
                    "Infer stakes, dialogue, social rules, pacing, and naming.",
                    "Treat terrain as an abstract scaffold when the genre needs it.",
                    "Avoid generic filler and unsupported state changes.",
                ],
            },
        }

    def _relevant_locations(
        self,
        world: World,
        current: Location | None,
        quest: Quest | None,
        action: str | None,
    ) -> list[Location]:
        selected: list[Location] = []

        def add(item: Location | None) -> None:
            if (
                item is not None
                and all(existing.id != item.id for existing in selected)
            ):
                selected.append(item)

        add(current)
        normalized_action = (action or "").casefold()
        for location in world.locations:
            if location.name.casefold() in normalized_action:
                add(location)
        if quest is not None:
            related = set(quest.related_locations)
            for location in world.locations:
                if location.id in related:
                    add(location)
        return selected[:4]

    def _relevant_npcs(
        self,
        world: World,
        current: Npc | None,
        quest: Quest | None,
    ) -> list[Npc]:
        selected: list[Npc] = []
        if current is not None:
            selected.append(current)
        if quest is not None:
            related = set(quest.related_npcs)
            for npc in world.npcs:
                if npc.id in related and all(item.id != npc.id for item in selected):
                    selected.append(npc)
        return selected[:4]

    def _applicable_clocks(
        self,
        world: World,
        quest: Quest | None,
    ) -> list[dict[str, object]]:
        quest_id = quest.id if quest is not None else None
        selected = []
        for clock in world.clocks:
            if clock.status != ClockStatus.ACTIVE:
                continue
            relevant = (
                clock.id == "central_threat"
                or quest_id is None
                or any(trigger.target_id == quest_id for trigger in clock.triggers)
            )
            if not relevant:
                continue
            selected.append(
                {
                    "id": clock.id,
                    "title": clock.title,
                    "value": clock.value,
                    "max_value": clock.max_value,
                    "description": clock.description,
                }
            )
        return selected[:3]

    def _available_routes(
        self,
        world: World,
        location: Location | None,
    ) -> list[dict[str, object]]:
        if location is None:
            return []
        locations_by_id = {
            item.id: item
            for item in world.locations
        }
        available: list[dict[str, object]] = []
        for route in world.routes:
            destination_id = route.other(location.id)
            destination = locations_by_id.get(destination_id or "")
            if destination is None:
                continue
            available.append(
                {
                    "route_id": route.id,
                    "destination_id": destination.id,
                    "destination_name": destination.name,
                    "kind": route.kind,
                    "danger": route.danger,
                }
            )
        return sorted(
            available,
            key=lambda item: str(item["destination_id"]),
        )[:6]

    def _player_payload(
        self,
        player: Player | None,
    ) -> dict[str, object] | None:
        if player is None:
            return None
        return {
            "name": player.name,
            "archetype": player.archetype,
            "homeland": player.homeland,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "gold": player.gold,
            "xp": player.xp,
            "position": {
                "x": player.position.x,
                "y": player.position.y,
            },
            "boosts": dict(player.boosts),
        }

    def _location_payload(
        self,
        location: Location | None,
    ) -> dict[str, object] | None:
        if location is None:
            return None
        return {
            "id": location.id,
            "name": location.name,
            "biome": location.biome.value,
            "danger": location.danger,
            "summary": location.summary,
            "position": {
                "x": location.position.x,
                "y": location.position.y,
            },
        }

    def _npc_payload(self, npc: Npc | None) -> dict[str, object] | None:
        if npc is None:
            return None
        return {
            "id": npc.id,
            "name": npc.name,
            "role": npc.role,
            "disposition": npc.disposition,
            "location_id": npc.location_id,
        }

    def _scene_payload(self, world: World) -> dict[str, object] | None:
        scene = world.active_scene
        if scene is None:
            return None
        return {
            "id": scene.id,
            "mode": scene.mode.value,
            "location_id": scene.location_id,
            "area_name": scene.area_name,
            "step": scene.step,
            "tension": scene.tension,
            "theme": scene.theme,
            "hazard": scene.hazard,
            "local_npc_id": scene.local_npc_id,
            "exit_open": scene.exit_open,
            "available_actions": list(scene.available_actions[:6]),
        }

    def _encounter_payload(self, world: World) -> dict[str, object] | None:
        encounter = world.active_encounter
        if encounter is None:
            return None
        return {
            "id": encounter.id,
            "kind": encounter.kind,
            "participants": list(encounter.participants[:8]),
            "objective": encounter.objective,
            "phase": encounter.phase,
            "obstacles": list(encounter.obstacles[:6]),
            "exits": list(encounter.exits[:6]),
            "status": encounter.status.value,
            "resolution": encounter.resolution,
        }

    def _quest_payload(self, quest: Quest | None) -> dict[str, object] | None:
        if quest is None:
            return None
        stage = (
            quest.stages[min(quest.current_stage, len(quest.stages) - 1)]
            if quest.stages
            else None
        )
        return {
            "id": quest.id,
            "title": quest.title,
            "goal": quest.goal,
            "status": quest.status.value,
            "current_stage_index": quest.current_stage,
            "current_stage": {
                "id": stage.id,
                "title": stage.title,
                "description": stage.description,
                "status": stage.status.value,
                "conditions": [
                    {
                        "kind": condition.kind.value,
                        "target_id": condition.target_id,
                        "expected": condition.expected,
                        "minimum": condition.minimum,
                    }
                    for condition in stage.conditions
                ],
            }
            if isinstance(stage, QuestStage)
            else None,
            "related_locations": list(quest.related_locations),
            "related_npcs": list(quest.related_npcs),
            "discoveries": self._compact_unique(
                quest.discoveries[-4:],
                4,
                160,
            ),
        }

    def _referenced_inventory(
        self,
        world: World | None,
        player: Player | None,
        reference: str | None,
    ) -> list[dict[str, object]]:
        if player is None:
            return []
        normalized = (reference or "").casefold()
        selected = [
            item
            for item in player.inventory
            if item.casefold() in normalized
        ]
        if not selected and reference is None:
            selected = list(player.inventory[:4])
        descriptions = world.inventory_descriptions if world is not None else {}
        return [
            {
                "name": item,
                "description": descriptions.get(item, ""),
            }
            for item in selected[:6]
        ]

    def _relevant_facts(
        self,
        world: World,
        quest: Quest | None,
    ) -> list[str]:
        targets: set[str] = set()
        if quest is not None and quest.stages:
            stage = quest.stages[
                min(quest.current_stage, len(quest.stages) - 1)
            ]
            if isinstance(stage, QuestStage):
                targets = {condition.target_id for condition in stage.conditions}
        selected = [
            fact
            for fact in world.discovered_facts
            if fact in targets
        ]
        for fact in world.discovered_facts[-6:]:
            if fact not in selected:
                selected.append(fact)
        return selected[-8:]

    def _object_states_here(
        self,
        world: World,
        position_key: str | None,
    ) -> dict[str, dict[str, object]]:
        if position_key is None:
            return {}
        prefix = f"{position_key}:"
        return {
            key: value
            for key, value in world.object_states.items()
            if (
                key.startswith(prefix)
                or value.get("position") == position_key
                or value.get("last_position") == position_key
            )
        }

    def _dialogue_history(
        self,
        world: World,
        npc: Npc | None,
        supplied: list[str] | None,
    ) -> list[str]:
        source = (
            supplied
            if supplied is not None
            else world.conversations.get(npc.name, [])
            if npc is not None
            else []
        )
        return self._compact_unique(source[-8:], 8, 180)

    def _recent_outcome(self, record: TurnRecord) -> dict[str, object]:
        return {
            "tick": record.tick,
            "command": record.command,
            "success": record.outcome.success,
            "summary": record.outcome.authoritative_summary,
            "accepted_effects": [
                self._effect_payload(effect)
                for effect in record.outcome.accepted_effects[:6]
            ],
        }

    def _resolved_turn(self, record: TurnRecord) -> dict[str, object]:
        return {
            "id": record.id,
            "tick": record.tick,
            "command": record.command,
            "intent": {
                "title": record.intent.title,
                "stakes": record.intent.stakes,
                "check_kind": record.intent.check_kind.value
                if record.intent.check_kind is not None
                else None,
            },
            "check": {
                "kind": record.check.kind.value,
                "difficulty": record.check.difficulty,
                "raw_roll": record.check.raw_roll,
                "bonus": record.check.bonus,
                "total": record.check.total,
                "success": record.check.success,
            }
            if record.check is not None
            else None,
            "outcome": {
                "success": record.outcome.success,
                "authoritative_summary": record.outcome.authoritative_summary,
                "accepted_effects": [
                    self._effect_payload(effect)
                    for effect in record.outcome.accepted_effects
                ],
                "rejected_effects": [
                    {
                        "effect": self._effect_payload(item.effect),
                        "reason": item.reason,
                    }
                    for item in record.outcome.rejected_effects
                ],
            },
            "choices": list(record.choices[:4]),
        }

    def _effect_payload(self, effect: StateEffect) -> dict[str, object]:
        return {
            "kind": effect.kind.value,
            "target_id": effect.target_id,
            "value": effect.value,
            "amount": effect.amount,
        }

    def _fit(
        self,
        context: dict[str, object],
    ) -> tuple[dict[str, object], int, int]:
        fitted = json.loads(self._serialize(context))
        dropped = 0
        truncated = 0
        list_paths = [
            ("memory_context",),
            ("recent_outcomes",),
            ("recent_events",),
            ("relevant_npcs",),
            ("relevant_locations",),
            ("applicable_clocks",),
            ("available_routes",),
            ("dialogue_history",),
            ("state_ledger", "recent_state_facts"),
            ("state_ledger", "discovered_facts"),
            ("state_ledger", "committed_choices"),
            ("state_ledger", "resolved_encounter_ids"),
            ("world_scaffold", "npc_slots"),
            ("world_scaffold", "locations"),
        ]
        minimums = {
            ("dialogue_history",): 3,
            ("world_scaffold", "locations"): 4,
            ("world_scaffold", "npc_slots"): 2,
        }
        while self.estimate_tokens(fitted) > self.budget.max_estimated_tokens:
            changed = False
            for path in list_paths:
                items = self._path_value(fitted, path)
                minimum = minimums.get(path, 0)
                if isinstance(items, list) and len(items) > minimum:
                    items.pop()
                    dropped += 1
                    changed = True
                    if self.estimate_tokens(fitted) <= self.budget.max_estimated_tokens:
                        break
            if not changed:
                break

        while self.estimate_tokens(fitted) > self.budget.max_estimated_tokens:
            path, value = self._longest_string(fitted)
            if path is None or value is None or len(value) <= 24:
                break
            replacement = self._word_safe(
                value,
                max(16, int(len(value) * 0.72)),
            )
            self._set_path(fitted, path, replacement)
            truncated += 1

        removable = [
            "memory_context",
            "recent_outcomes",
            "recent_events",
            "relevant_npcs",
            "relevant_locations",
            "applicable_clocks",
        ]
        for key in removable:
            if self.estimate_tokens(fitted) <= self.budget.max_estimated_tokens:
                break
            if key in fitted:
                del fitted[key]
                dropped += 1

        emergency_paths = [
            ("state_ledger", "recent_state_facts"),
            ("state_ledger", "discovered_facts"),
            ("state_ledger", "committed_choices"),
            ("state_ledger", "resolved_encounter_ids"),
            ("state_ledger", "visible_scene_objects"),
            ("player", "boosts"),
            ("campaign", "overarching_quest"),
            ("campaign", "finale_title"),
            ("campaign", "weather"),
            ("campaign", "theme"),
            ("scene", "available_actions"),
            ("scene", "theme"),
            ("scene", "hazard"),
            ("encounter", "obstacles"),
            ("encounter", "exits"),
            ("style", "guidance"),
            ("style", "tone"),
        ]
        for path in emergency_paths:
            if self.estimate_tokens(fitted) <= self.budget.max_estimated_tokens:
                break
            if self._delete_path(fitted, path):
                dropped += 1

        optional_roots = [
            "scene",
            "encounter",
            "npc",
            "active_quest",
            "state_ledger",
            "player",
            "location",
            "campaign",
            "style",
        ]
        for key in optional_roots:
            if self.estimate_tokens(fitted) <= self.budget.max_estimated_tokens:
                break
            if key in fitted:
                del fitted[key]
                dropped += 1

        if self.estimate_tokens(fitted) > self.budget.max_estimated_tokens:
            raise ValueError(
                "The minimum task context exceeds "
                f"the {self.budget.max_estimated_tokens}-token budget."
            )
        return fitted, dropped, truncated

    def _path_value(
        self,
        payload: dict[str, object],
        path: tuple[str, ...],
    ) -> object:
        current: object = payload
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    def _set_path(
        self,
        payload: dict[str, object],
        path: tuple[object, ...],
        value: str,
    ) -> None:
        current: object = payload
        for key in path[:-1]:
            current = current[key] if isinstance(current, dict | list) else None
        if isinstance(current, dict):
            current[path[-1]] = value
        elif isinstance(current, list) and isinstance(path[-1], int):
            current[path[-1]] = value

    def _delete_path(
        self,
        payload: dict[str, object],
        path: tuple[str, ...],
    ) -> bool:
        current: object = payload
        for key in path[:-1]:
            if not isinstance(current, dict):
                return False
            current = current.get(key)
        if not isinstance(current, dict) or path[-1] not in current:
            return False
        del current[path[-1]]
        return True

    def _longest_string(
        self,
        payload: object,
        path: tuple[object, ...] = (),
    ) -> tuple[tuple[object, ...] | None, str | None]:
        best_path: tuple[object, ...] | None = None
        best_value: str | None = None
        if isinstance(payload, str):
            if self._is_protected_string(path):
                return None, None
            return path, payload
        if isinstance(payload, dict):
            items = payload.items()
        elif isinstance(payload, list):
            items = enumerate(payload)
        else:
            return None, None
        for key, value in items:
            candidate_path, candidate_value = self._longest_string(
                value,
                (*path, key),
            )
            if candidate_value is not None and (
                best_value is None or len(candidate_value) > len(best_value)
            ):
                best_path = candidate_path
                best_value = candidate_value
        return best_path, best_value

    def _is_protected_string(self, path: tuple[object, ...]) -> bool:
        protected_fields = {
            "action",
            "check_kind",
            "command",
            "condition",
            "destination_id",
            "destination_name",
            "id",
            "kind",
            "location_id",
            "name",
            "npc_id",
            "player_dialogue",
            "route_id",
            "status",
            "target_id",
            "task",
        }
        protected_lists = {
            "committed_choices",
            "discovered_facts",
            "related_locations",
            "related_npcs",
            "resolved_encounter_ids",
            "visible_scene_objects",
        }
        return (
            bool(path)
            and isinstance(path[-1], str)
            and path[-1] in protected_fields
        ) or (
            len(path) > 1
            and isinstance(path[-1], int)
            and path[-2] in protected_lists
        )

    def _compact_unique(
        self,
        lines: list[str],
        limit: int,
        max_length: int,
    ) -> list[str]:
        compacted: list[str] = []
        for line in lines:
            normalized = " ".join(line.split())
            if not normalized or normalized in compacted:
                continue
            compacted.append(self._word_safe(normalized, max_length))
        return compacted[-limit:]

    def _word_safe(self, value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        prefix = normalized[: max(1, limit - 3)]
        if " " in prefix:
            prefix = prefix.rsplit(" ", 1)[0]
        return f"{prefix.rstrip()}..."

    def _serialize(self, payload: object) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
