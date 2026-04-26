from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from worldsim.models import Biome, Event, Location, Npc, Player, Position, World


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
        payload = json.loads(self.path.read_text())
        world = self._deserialize_world(payload["world"])
        player = self._deserialize_player(payload["player"])
        memory = CampaignMemory.from_dict(payload.get("memory", {}))
        return world, player, memory

    def save(self, world: World, player: Player, memory: CampaignMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "world": self._serialize_world(world),
            "player": self._serialize_player(player),
            "memory": memory.to_dict(),
        }
        self.path.write_text(json.dumps(payload, indent=2))
        self.state_path.write_text(json.dumps(self._serialize_state(world, player, memory), indent=2))

    def _serialize_world(self, world: World) -> dict[str, object]:
        return {
            "seed": world.seed,
            "tick": world.tick,
            "width": world.width,
            "height": world.height,
            "tiles": [[tile.value for tile in row] for row in world.tiles],
            "locations": [
                {
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
        )

    def _serialize_state(self, world: World, player: Player, memory: CampaignMemory) -> dict[str, object]:
        position_key = f"{player.position.x},{player.position.y}"
        return {
            "tick": world.tick,
            "player": self._serialize_player(player),
            "current_position": position_key,
            "visible_scene_objects": list(world.scene_objects.get(position_key, [])),
            "object_states": world.object_states,
            "conversations": {key: lines[-12:] for key, lines in world.conversations.items()},
            "recent_state_facts": world.state_facts[-24:],
            "memory": memory.latest_lines(limit=12),
        }

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
        )
