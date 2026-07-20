from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from worldsim.models import (
    ActionIntent,
    ActionKind,
    Biome,
    CheckKind,
    CheckResult,
    ClockTrigger,
    ClockTriggerKind,
    Condition,
    ConditionKind,
    DialogueState,
    EffectCondition,
    EffectKind,
    EffectSource,
    EncounterState,
    EncounterStatus,
    Event,
    Location,
    Npc,
    Player,
    Position,
    Quest,
    QuestClock,
    RejectedEffect,
    SceneState,
    StateEffect,
    TurnOutcome,
    TurnRecord,
    World,
)
from worldsim.schemas import turn_record_to_payload
from worldsim.usage import UsageTotals


SAVE_SCHEMA_VERSION = 2


class UnsupportedSaveVersion(ValueError):
    pass


@dataclass
class MemoryEntry:
    key: str
    kind: str
    summary: str
    importance: int
    last_tick: int
    mentions: int = 1
    tags: list[str] = field(default_factory=list)


class CampaignMemory:
    def __init__(self, entries: dict[str, MemoryEntry] | None = None) -> None:
        self.entries = entries or {}

    def remember(
        self,
        kind: str,
        key: str,
        summary: str,
        tick: int,
        importance: int = 5,
        tags: list[str] | None = None,
    ) -> None:
        token = f"{kind}:{key}"
        new_tags = sorted(set(tags or []))
        existing = self.entries.get(token)
        if existing is None:
            self.entries[token] = MemoryEntry(
                key=key,
                kind=kind,
                summary=summary,
                importance=importance,
                last_tick=tick,
                tags=new_tags,
            )
        else:
            if importance >= existing.importance or tick >= existing.last_tick:
                existing.summary = summary
            existing.importance = max(existing.importance, importance)
            existing.last_tick = max(existing.last_tick, tick)
            existing.mentions += 1
            existing.tags = sorted(set(existing.tags + new_tags))
        self._trim()

    def remember_location(self, location: Location, tick: int) -> None:
        self.remember(
            "location",
            location.name,
            f"{location.name}: {location.summary}. Threat level {location.danger}/9.",
            tick,
            importance=7,
            tags=[location.name, location.biome.value.lower(), "location"],
        )

    def remember_npc(self, npc: Npc, tick: int) -> None:
        self.remember(
            "npc",
            npc.name,
            f"{npc.name} is a {npc.disposition} {npc.role} tied to {npc.location_name}.",
            tick,
            importance=6,
            tags=[npc.name, npc.location_name, "npc"],
        )

    def remember_hook(self, hook: str, tick: int) -> None:
        self.remember("hook", hook, hook, tick, importance=8, tags=["hook"])

    def remember_world_state(self, world: World, player: Player) -> None:
        location = next((item for item in world.locations if item.position == player.position), None)
        if location is not None:
            self.remember_location(location, world.tick)
        self.remember(
            "player",
            player.name,
            f"{player.name} the {player.archetype} is at {location.name if location else 'the frontier'} with {player.hp}/{player.max_hp} HP and {player.gold} gold.",
            world.tick,
            importance=9,
            tags=[player.name, player.archetype, "player"],
        )
        self.remember(
            "campaign",
            "overarching_quest",
            f"{world.campaign_title}: {world.overarching_quest}. Current objective: {world.active_quest or 'none'}.",
            world.tick,
            importance=9,
            tags=["campaign", "quest", world.campaign_title],
        )

    def relevant_context(self, world: World, player: Player, scope: str | None = None, limit: int = 5) -> list[str]:
        scored: list[tuple[int, MemoryEntry]] = []
        for entry in self.entries.values():
            score = entry.importance + min(entry.mentions, 3)
            if world.tick - entry.last_tick <= 8:
                score += 2
            if scope and scope in entry.tags:
                score += 4
            if player.name in entry.tags or player.archetype in entry.tags:
                score += 2
            scored.append((score, entry))
        scored.sort(key=lambda item: (item[0], item[1].last_tick), reverse=True)
        return [self._compact_summary(entry.summary) for _, entry in scored[:limit]]

    def latest_lines(self, limit: int = 4) -> list[str]:
        recent = sorted(self.entries.values(), key=lambda entry: (entry.last_tick, entry.importance), reverse=True)
        return [self._compact_summary(entry.summary) for entry in recent[:limit]]

    def to_dict(self) -> dict[str, object]:
        return {"entries": [asdict(entry) for entry in self.entries.values()]}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> CampaignMemory:
        entries: dict[str, MemoryEntry] = {}
        for raw in payload.get("entries", []):
            entry = MemoryEntry(**raw)
            entries[f"{entry.kind}:{entry.key}"] = entry
        return cls(entries)

    def _trim(self, limit: int = 64) -> None:
        ranked = sorted(
            self.entries.items(),
            key=lambda item: (item[1].importance, item[1].mentions, item[1].last_tick),
            reverse=True,
        )
        self.entries = dict(ranked[:limit])

    def _compact_summary(self, summary: str, max_length: int = 240) -> str:
        normalized = " ".join(summary.split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 3].rstrip() + "..."


class CampaignStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state_path = path.parent / "state.json"

    def load(self) -> tuple[World, Player, CampaignMemory] | None:
        if not self.path.exists():
            return None
        raw_payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw_payload, dict):
            raise ValueError("Campaign save root must be a JSON object.")
        payload = self._migrate_payload(raw_payload)
        world = self._deserialize_world(payload["world"])
        player = self._deserialize_player(payload["player"])
        memory = CampaignMemory.from_dict(payload.get("memory", {}))
        return world, player, memory

    def save(self, world: World, player: Player, memory: CampaignMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SAVE_SCHEMA_VERSION,
            "world": self._serialize_world(world),
            "player": self._serialize_player(player),
            "memory": memory.to_dict(),
        }
        self._write_json_atomic(self.path, payload)
        state = self._serialize_state(world, player, memory)
        state["schema_version"] = SAVE_SCHEMA_VERSION
        self._write_json_atomic(self.state_path, state)

    def _serialize_world(self, world: World) -> dict[str, object]:
        return {
            "seed": world.seed,
            "tick": world.tick,
            "width": world.width,
            "height": world.height,
            "tiles": [[tile.value for tile in row] for row in world.tiles],
            "locations": [
                {
                    "id": location.id,
                    "name": location.name,
                    "position": {"x": location.position.x, "y": location.position.y},
                    "biome": location.biome.value,
                    "danger": location.danger,
                    "summary": location.summary,
                }
                for location in world.locations
            ],
            "npcs": [asdict(npc) for npc in world.npcs],
            "recent_events": [asdict(event) for event in world.recent_events],
            "quest_hooks": list(world.quest_hooks),
            "alerts": list(world.alerts),
            "conversations": {key: list(lines) for key, lines in world.conversations.items()},
            "scene_objects": {key: list(items) for key, items in world.scene_objects.items()},
            "object_states": world.object_states,
            "state_facts": list(world.state_facts),
            "weather": world.weather,
            "stability": world.stability,
            "theme_prompt": world.theme_prompt,
            "campaign_title": world.campaign_title,
            "overarching_quest": world.overarching_quest,
            "active_quest": world.active_quest,
            "active_quest_id": world.active_quest_id,
            "quests": [self._serialize_quest(quest) for quest in world.quests],
            "clocks": [self._serialize_clock(clock) for clock in world.clocks],
            "usage_totals": world.usage_totals.to_dict(),
            "current_choices": list(world.current_choices),
            "current_activity": world.current_activity,
            "movement_lock": world.movement_lock,
            "last_roll": world.last_roll,
            "player_archetype_options": list(world.player_archetype_options),
            "player_archetype_blurbs": dict(world.player_archetype_blurbs),
            "player_archetype_boosts": dict(world.player_archetype_boosts),
            "homeland_options": list(world.homeland_options),
            "starting_inventory": list(world.starting_inventory),
            "inventory_descriptions": dict(world.inventory_descriptions),
            "skill_descriptions": dict(world.skill_descriptions),
            "homeland_descriptions": dict(world.homeland_descriptions),
            "active_scene": self._serialize_scene(world.active_scene),
            "active_encounter": self._serialize_encounter(world.active_encounter),
            "dialogue_state": asdict(world.dialogue_state) if world.dialogue_state is not None else None,
            "discovered_facts": list(world.discovered_facts),
            "committed_choices": list(world.committed_choices),
            "turn_records": [turn_record_to_payload(record) for record in world.turn_records],
        }

    def _deserialize_world(self, payload: dict[str, object]) -> World:
        tiles = [[Biome(tile) for tile in row] for row in payload["tiles"]]
        locations = [
            Location(
                name=location["name"],
                position=Position(location["position"]["x"], location["position"]["y"]),
                biome=Biome(location["biome"]),
                danger=location["danger"],
                summary=location["summary"],
                id=str(location.get("id", "")),
            )
            for location in payload["locations"]
        ]
        npcs = [Npc(**npc) for npc in payload["npcs"]]
        recent_events = [Event(**event) for event in payload["recent_events"]]
        return World(
            seed=payload["seed"],
            tick=payload["tick"],
            width=payload["width"],
            height=payload["height"],
            tiles=tiles,
            locations=locations,
            npcs=npcs,
            recent_events=recent_events,
            quest_hooks=list(payload["quest_hooks"]),
            alerts=list(payload["alerts"]),
            conversations={
                key: list(lines)
                for key, lines in payload.get("conversations", {}).items()
                if isinstance(lines, list)
            },
            scene_objects={
                key: list(items)
                for key, items in payload.get("scene_objects", {}).items()
                if isinstance(items, list)
            },
            object_states={
                key: value
                for key, value in payload.get("object_states", {}).items()
                if isinstance(value, dict)
            },
            state_facts=list(payload.get("state_facts", [])),
            weather=payload["weather"],
            stability=payload["stability"],
            theme_prompt=payload.get("theme_prompt", "character-driven adventure"),
            campaign_title=payload.get("campaign_title", "Untitled Frontier"),
            overarching_quest=payload.get("overarching_quest", "Uncover the central threat shaping the frontier."),
            active_quest=payload.get("active_quest"),
            active_quest_id=payload.get("active_quest_id"),
            quests=[self._deserialize_quest(quest) for quest in payload.get("quests", []) if isinstance(quest, dict)],
            clocks=[self._deserialize_clock(clock) for clock in payload.get("clocks", []) if isinstance(clock, dict)],
            usage_totals=UsageTotals.from_dict(payload.get("usage_totals")),
            current_choices=list(payload.get("current_choices", [])),
            current_activity=payload.get("current_activity"),
            movement_lock=payload.get("movement_lock"),
            last_roll=payload.get("last_roll"),
            player_archetype_options=list(payload.get("player_archetype_options", ["warrior", "rogue", "mage", "ranger"])),
            player_archetype_blurbs=dict(payload.get("player_archetype_blurbs", {})),
            player_archetype_boosts=dict(payload.get("player_archetype_boosts", {})),
            homeland_options=list(payload.get("homeland_options", [])),
            starting_inventory=list(payload.get("starting_inventory", ["notebook", "light source", "snack"])),
            inventory_descriptions=dict(payload.get("inventory_descriptions", {})),
            skill_descriptions=dict(payload.get("skill_descriptions", {})),
            homeland_descriptions=dict(payload.get("homeland_descriptions", {})),
            active_scene=self._deserialize_scene(payload.get("active_scene")),
            active_encounter=self._deserialize_encounter(payload.get("active_encounter")),
            dialogue_state=self._deserialize_dialogue(payload.get("dialogue_state")),
            discovered_facts=list(payload.get("discovered_facts", [])),
            committed_choices=list(payload.get("committed_choices", [])),
            turn_records=[
                record
                for item in payload.get("turn_records", [])
                if isinstance(item, dict)
                for record in [self._deserialize_turn_record(item)]
                if record is not None
            ],
        )

    def _serialize_state(self, world: World, player: Player, memory: CampaignMemory) -> dict[str, object]:
        position_key = f"{player.position.x},{player.position.y}"
        return {
            "tick": world.tick,
            "theme_prompt": world.theme_prompt,
            "campaign_title": world.campaign_title,
            "overarching_quest": world.overarching_quest,
            "active_quest": world.active_quest,
            "active_quest_id": world.active_quest_id,
            "quests": [self._serialize_quest(quest) for quest in world.quests],
            "clocks": [self._serialize_clock(clock) for clock in world.clocks],
            "usage_totals": world.usage_totals.to_dict(),
            "current_choices": list(world.current_choices),
            "current_activity": world.current_activity,
            "movement_lock": world.movement_lock,
            "last_roll": world.last_roll,
            "player_archetype_options": list(world.player_archetype_options),
            "player_archetype_blurbs": dict(world.player_archetype_blurbs),
            "player_archetype_boosts": dict(world.player_archetype_boosts),
            "homeland_options": list(world.homeland_options),
            "starting_inventory": list(world.starting_inventory),
            "inventory_descriptions": dict(world.inventory_descriptions),
            "skill_descriptions": dict(world.skill_descriptions),
            "homeland_descriptions": dict(world.homeland_descriptions),
            "player": self._serialize_player(player),
            "current_position": position_key,
            "visible_scene_objects": list(world.scene_objects.get(position_key, [])),
            "object_states": world.object_states,
            "conversations": {key: lines[-12:] for key, lines in world.conversations.items()},
            "recent_state_facts": world.state_facts[-24:],
            "memory": memory.latest_lines(limit=12),
            "active_scene": self._serialize_scene(world.active_scene),
            "active_encounter": self._serialize_encounter(world.active_encounter),
            "dialogue_state": asdict(world.dialogue_state) if world.dialogue_state is not None else None,
            "turn_records": [turn_record_to_payload(record) for record in world.turn_records[-12:]],
        }

    def _migrate_payload(self, payload: dict[str, object]) -> dict[str, object]:
        version = payload.get("schema_version", 0)
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("Campaign save has an invalid schema_version.")
        if version > SAVE_SCHEMA_VERSION:
            raise UnsupportedSaveVersion(
                f"Campaign save version {version} is newer than supported version {SAVE_SCHEMA_VERSION}."
            )
        if version == 0:
            payload = self._migrate_v0_to_v1(payload)
            version = 1
        if version == 1:
            payload = self._migrate_v1_to_v2(payload)
            version = 2
        if version != SAVE_SCHEMA_VERSION:
            raise UnsupportedSaveVersion(f"No migration path from campaign save version {version}.")
        return payload

    def _migrate_v0_to_v1(self, payload: dict[str, object]) -> dict[str, object]:
        world = payload.get("world")
        player = payload.get("player")
        if not isinstance(world, dict) or not isinstance(player, dict):
            raise ValueError("Legacy campaign save must contain world and player objects.")

        locations = world.get("locations", [])
        location_ids: dict[str, str] = {}
        if isinstance(locations, list):
            for index, raw in enumerate(locations):
                if not isinstance(raw, dict):
                    continue
                raw.setdefault("id", f"location-{index + 1:03d}")
                name = raw.get("name")
                if isinstance(name, str):
                    location_ids[name] = str(raw["id"])

        npcs = world.get("npcs", [])
        if isinstance(npcs, list):
            for index, raw in enumerate(npcs):
                if not isinstance(raw, dict):
                    continue
                raw.setdefault("id", f"npc-{index + 1:03d}")
                location_name = raw.get("location_name")
                raw.setdefault("location_id", location_ids.get(location_name) if isinstance(location_name, str) else None)

        quests = world.get("quests", [])
        if isinstance(quests, list):
            for raw in quests:
                if isinstance(raw, dict):
                    raw.setdefault("stage_conditions", [])

        clocks = world.get("clocks", [])
        if isinstance(clocks, list):
            for raw in clocks:
                if isinstance(raw, dict):
                    raw.setdefault("triggers", [])
                    raw.setdefault("triggered", raw.get("status") == "complete")

        position = player.get("position", {})
        matching_location_id = None
        if isinstance(position, dict) and isinstance(locations, list):
            for raw in locations:
                if isinstance(raw, dict) and raw.get("position") == position:
                    matching_location_id = raw.get("id")
                    break
        scene_id = (
            f"scene:{matching_location_id}"
            if matching_location_id
            else f"scene:{position.get('x', 0)},{position.get('y', 0)}"
            if isinstance(position, dict)
            else "scene:unknown"
        )
        world.setdefault(
            "active_scene",
            {
                "id": scene_id,
                "location_id": matching_location_id,
                "available_actions": [],
            },
        )

        movement_lock = world.get("movement_lock")
        current_activity = world.get("current_activity")
        if world.get("active_encounter") is None and (
            current_activity == "combat"
            or isinstance(movement_lock, str)
            and any(token in movement_lock.lower() for token in ("fight", "combat"))
        ):
            world["active_encounter"] = {
                "id": f"legacy-encounter-{world.get('tick', 0)}",
                "kind": "combat",
                "participants": [],
                "objective": "Resolve the legacy combat state.",
                "phase": "engaged",
                "obstacles": [],
                "exits": ["flee"],
                "status": EncounterStatus.ACTIVE.value,
                "resolution": None,
            }
        else:
            world.setdefault("active_encounter", None)

        world.setdefault("dialogue_state", None)
        world.setdefault("discovered_facts", [])
        world.setdefault("committed_choices", [])
        payload["schema_version"] = 1
        return payload

    def _migrate_v1_to_v2(self, payload: dict[str, object]) -> dict[str, object]:
        world = payload.get("world")
        if not isinstance(world, dict):
            raise ValueError("Version 1 campaign save must contain a world object.")
        world.setdefault("turn_records", [])
        payload["schema_version"] = 2
        return payload

    def _serialize_quest(self, quest: Quest) -> dict[str, object]:
        payload = asdict(quest)
        payload["stage_conditions"] = [
            [
                {
                    "kind": condition.kind.value,
                    "target_id": condition.target_id,
                    "expected": condition.expected,
                    "minimum": condition.minimum,
                }
                for condition in stage
            ]
            for stage in quest.stage_conditions
        ]
        return payload

    def _deserialize_quest(self, payload: dict[str, object]) -> Quest:
        raw = dict(payload)
        stages: list[list[Condition]] = []
        for raw_stage in raw.pop("stage_conditions", []):
            conditions: list[Condition] = []
            if isinstance(raw_stage, list):
                for item in raw_stage:
                    if not isinstance(item, dict):
                        continue
                    try:
                        kind = ConditionKind(str(item.get("kind")))
                    except ValueError:
                        continue
                    target_id = item.get("target_id")
                    if not isinstance(target_id, str) or not target_id:
                        continue
                    expected = item.get("expected")
                    minimum = item.get("minimum")
                    conditions.append(
                        Condition(
                            kind=kind,
                            target_id=target_id,
                            expected=expected if isinstance(expected, str) else None,
                            minimum=minimum if isinstance(minimum, int) and not isinstance(minimum, bool) else None,
                        )
                    )
            stages.append(conditions)
        return Quest(**raw, stage_conditions=stages)

    def _serialize_clock(self, clock: QuestClock) -> dict[str, object]:
        payload = asdict(clock)
        payload["triggers"] = [
            {
                "id": trigger.id,
                "kind": trigger.kind.value,
                "target_id": trigger.target_id,
                "amount": trigger.amount,
                "text": trigger.text,
                "fired": trigger.fired,
            }
            for trigger in clock.triggers
        ]
        return payload

    def _deserialize_clock(self, payload: dict[str, object]) -> QuestClock:
        raw = dict(payload)
        triggers: list[ClockTrigger] = []
        for item in raw.pop("triggers", []):
            if not isinstance(item, dict):
                continue
            try:
                kind = ClockTriggerKind(str(item.get("kind")))
            except ValueError:
                continue
            trigger_id = item.get("id")
            if not isinstance(trigger_id, str) or not trigger_id:
                continue
            triggers.append(
                ClockTrigger(
                    id=trigger_id,
                    kind=kind,
                    target_id=item.get("target_id") if isinstance(item.get("target_id"), str) else None,
                    amount=item.get("amount") if isinstance(item.get("amount"), int) else 0,
                    text=item.get("text") if isinstance(item.get("text"), str) else "",
                    fired=bool(item.get("fired", False)),
                )
            )
        return QuestClock(**raw, triggers=triggers)

    def _deserialize_turn_record(self, payload: dict[str, object]) -> TurnRecord | None:
        raw_intent = payload.get("intent")
        raw_outcome = payload.get("outcome")
        if not isinstance(raw_intent, dict) or not isinstance(raw_outcome, dict):
            return None
        try:
            action_kind = ActionKind(str(raw_intent.get("kind", ActionKind.FREEFORM.value)))
        except ValueError:
            action_kind = ActionKind.FREEFORM
        try:
            raw_check_kind = raw_intent.get("check_kind")
            intent_check_kind = CheckKind(str(raw_check_kind)) if raw_check_kind is not None else None
        except ValueError:
            intent_check_kind = None
        intent = ActionIntent(
            id=str(raw_intent.get("id", payload.get("id", "turn:unknown"))),
            raw_input=str(raw_intent.get("raw_input", payload.get("command", ""))),
            kind=action_kind,
            title=str(raw_intent.get("title", "Improvised Action")),
            stakes=str(raw_intent.get("stakes", "")),
            check_kind=intent_check_kind,
            difficulty=int(raw_intent.get("difficulty", 10)),
            proposed_effects=[
                effect
                for item in raw_intent.get("proposed_effects", [])
                if isinstance(item, dict)
                for effect in [self._deserialize_state_effect(item)]
                if effect is not None
            ],
            tags=[item for item in raw_intent.get("tags", []) if isinstance(item, str)],
            choices=[item for item in raw_intent.get("choices", []) if isinstance(item, str)],
        )

        check = None
        raw_check = payload.get("check")
        if isinstance(raw_check, dict):
            try:
                check = CheckResult(
                    kind=CheckKind(str(raw_check.get("kind"))),
                    difficulty=int(raw_check.get("difficulty", 10)),
                    raw_roll=int(raw_check.get("raw_roll", 1)),
                    bonus=int(raw_check.get("bonus", 0)),
                    total=int(raw_check.get("total", 1)),
                    success=bool(raw_check.get("success", False)),
                )
            except ValueError:
                check = None

        accepted = [
            effect
            for item in raw_outcome.get("accepted_effects", [])
            if isinstance(item, dict)
            for effect in [self._deserialize_state_effect(item)]
            if effect is not None
        ]
        rejected: list[RejectedEffect] = []
        for item in raw_outcome.get("rejected_effects", []):
            if not isinstance(item, dict) or not isinstance(item.get("effect"), dict):
                continue
            effect = self._deserialize_state_effect(item["effect"])
            if effect is not None:
                rejected.append(RejectedEffect(effect=effect, reason=str(item.get("reason", "rejected"))))
        success = raw_outcome.get("success")
        outcome = TurnOutcome(
            success=success if isinstance(success, bool) else None,
            accepted_effects=accepted,
            rejected_effects=rejected,
            authoritative_summary=str(raw_outcome.get("authoritative_summary", "")),
        )
        return TurnRecord(
            id=str(payload.get("id", intent.id)),
            tick=int(payload.get("tick", 0)),
            command=str(payload.get("command", intent.raw_input)),
            intent=intent,
            check=check,
            outcome=outcome,
            narration=str(payload.get("narration", "")),
            choices=[item for item in payload.get("choices", []) if isinstance(item, str)],
        )

    def _deserialize_state_effect(self, payload: dict[str, object]) -> StateEffect | None:
        try:
            kind = EffectKind(str(payload.get("kind")))
            condition = EffectCondition(str(payload.get("condition", EffectCondition.SUCCESS.value)))
            source = EffectSource(str(payload.get("source", EffectSource.DIRECTOR.value)))
        except ValueError:
            return None
        target_id = payload.get("target_id")
        value = payload.get("value")
        return StateEffect(
            kind=kind,
            target_id=target_id if isinstance(target_id, str) else None,
            value=value if isinstance(value, str) else None,
            amount=int(payload.get("amount", 0)),
            condition=condition,
            flag=bool(payload.get("flag", False)),
            source=source,
        )

    def _serialize_scene(self, scene: SceneState | None) -> dict[str, object] | None:
        return asdict(scene) if scene is not None else None

    def _deserialize_scene(self, payload: object) -> SceneState | None:
        if not isinstance(payload, dict):
            return None
        return SceneState(
            id=str(payload.get("id", "scene:unknown")),
            location_id=payload.get("location_id") if isinstance(payload.get("location_id"), str) else None,
            area_name=payload.get("area_name") if isinstance(payload.get("area_name"), str) else None,
            step=int(payload.get("step", 0)),
            tension=int(payload.get("tension", 0)),
            theme=payload.get("theme") if isinstance(payload.get("theme"), str) else None,
            hazard=payload.get("hazard") if isinstance(payload.get("hazard"), str) else None,
            local_npc_id=payload.get("local_npc_id") if isinstance(payload.get("local_npc_id"), str) else None,
            exit_open=bool(payload.get("exit_open", False)),
            available_actions=[
                action for action in payload.get("available_actions", []) if isinstance(action, str)
            ],
        )

    def _serialize_encounter(self, encounter: EncounterState | None) -> dict[str, object] | None:
        if encounter is None:
            return None
        payload = asdict(encounter)
        payload["status"] = encounter.status.value
        return payload

    def _deserialize_encounter(self, payload: object) -> EncounterState | None:
        if not isinstance(payload, dict):
            return None
        try:
            status = EncounterStatus(str(payload.get("status", EncounterStatus.ACTIVE.value)))
        except ValueError:
            status = EncounterStatus.ACTIVE
        return EncounterState(
            id=str(payload.get("id", "encounter:unknown")),
            kind=str(payload.get("kind", "conflict")),
            participants=[item for item in payload.get("participants", []) if isinstance(item, str)],
            objective=str(payload.get("objective", "Resolve the encounter.")),
            phase=str(payload.get("phase", "opening")),
            obstacles=[item for item in payload.get("obstacles", []) if isinstance(item, str)],
            exits=[item for item in payload.get("exits", []) if isinstance(item, str)],
            status=status,
            resolution=payload.get("resolution") if isinstance(payload.get("resolution"), str) else None,
        )

    def _deserialize_dialogue(self, payload: object) -> DialogueState | None:
        if not isinstance(payload, dict):
            return None
        npc_id = payload.get("npc_id")
        npc_name = payload.get("npc_name")
        if not isinstance(npc_id, str) or not isinstance(npc_name, str):
            return None
        return DialogueState(
            npc_id=npc_id,
            npc_name=npc_name,
            started_tick=int(payload.get("started_tick", 0)),
            active=bool(payload.get("active", True)),
        )

    def _write_json_atomic(self, path: Path, payload: dict[str, object]) -> None:
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            temp_path.replace(path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _serialize_player(self, player: Player) -> dict[str, object]:
        return {
            "name": player.name,
            "archetype": player.archetype,
            "homeland": player.homeland,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "gold": player.gold,
            "xp": player.xp,
            "position": {"x": player.position.x, "y": player.position.y},
            "inventory": list(player.inventory),
            "boosts": dict(player.boosts),
        }

    def _deserialize_player(self, payload: dict[str, object]) -> Player:
        return Player(
            name=payload["name"],
            archetype=payload["archetype"],
            homeland=payload["homeland"],
            hp=payload["hp"],
            max_hp=payload["max_hp"],
            gold=payload["gold"],
            xp=payload["xp"],
            position=Position(payload["position"]["x"], payload["position"]["y"]),
            inventory=list(payload["inventory"]),
            boosts=dict(payload.get("boosts", {})),
        )
