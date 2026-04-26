from __future__ import annotations

import json
from typing import Any

from worldsim.models import DirectorBeat, Location, Npc, Player, World


TEXT_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["narration"],
    "properties": {
        "narration": {"type": "string"},
    },
    "additionalProperties": False,
}

WORLD_DETAILS_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["weather", "opening_event", "locations", "npcs", "quest_hooks"],
    "properties": {
        "weather": {"type": "string"},
        "opening_event": {"type": "string"},
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "name", "summary"],
                "properties": {
                    "index": {"type": "integer"},
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "npcs": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "name", "role", "disposition", "location_name"],
                "properties": {
                    "index": {"type": "integer"},
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "disposition": {"type": "string"},
                    "location_name": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "quest_hooks": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6},
    },
    "additionalProperties": False,
}

DIRECTOR_BEAT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["title", "narration", "mechanical_request", "difficulty", "tags", "follow_up_hook"],
    "properties": {
        "title": {"type": "string"},
        "narration": {"type": "string"},
        "mechanical_request": {
            "type": ["string", "null"],
            "enum": ["exploration_check", "social_check", "combat_check", None],
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 20},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "follow_up_hook": {"type": ["string", "null"]},
        "scene_objects": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "inventory_add": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "inventory_remove": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "additionalProperties": False,
}


def director_context(
    world: World,
    player: Player | None = None,
    location: Location | None = None,
    npc: Npc | None = None,
    memory_context: list[str] | None = None,
    action: str | None = None,
    player_dialogue: str | None = None,
    dialogue_history: list[str] | None = None,
) -> dict[str, object]:
    position_key = f"{player.position.x},{player.position.y}" if player is not None else None
    return {
        "world": {
            "seed": world.seed,
            "tick": world.tick,
            "weather": world.weather,
            "stability": world.stability,
            "map_size": {"width": world.width, "height": world.height},
            "locations": [
                {
                    "index": index,
                    "name": location.name,
                    "biome": location.biome.value,
                    "danger": location.danger,
                    "summary": location.summary,
                    "position": {"x": location.position.x, "y": location.position.y},
                }
                for index, location in enumerate(world.locations)
            ],
            "npcs": [
                {
                    "index": index,
                    "name": npc.name,
                    "role": npc.role,
                    "disposition": npc.disposition,
                    "location_name": npc.location_name,
                }
                for index, npc in enumerate(world.npcs)
            ],
            "active_hooks": world.quest_hooks[:5],
            "recent_events": [
                {
                    "tick": event.tick,
                    "category": event.category,
                    "text": event.text,
                    "severity": event.severity,
                }
                for event in world.recent_events[:5]
            ],
        },
        "player": _player_payload(player) if player is not None else None,
        "location": _location_payload(location) if location is not None else None,
        "npc": _npc_payload(npc) if npc is not None else None,
        "active_dialogue_history": world.conversations.get(npc.name, [])[-8:] if npc is not None else [],
        "visible_scene_objects": _scene_objects_at(world, player) if player is not None else [],
        "state_ledger": {
            "current_position": position_key,
            "visible_scene_objects": _scene_objects_at(world, player) if player is not None else [],
            "object_states_here": _object_states_for_position(world, position_key),
            "player_inventory": list(player.inventory) if player is not None else [],
            "recent_state_facts": _compact_lines(world.state_facts[-12:], 180),
            "npc_conversation_history": _compact_lines(world.conversations.get(npc.name, [])[-10:], 180)
            if npc is not None
            else [],
            "npc_prior_replies": _compact_lines(_npc_prior_replies(world, npc), 160) if npc is not None else [],
        },
        "memory_context": _compact_lines(memory_context or [], 220),
        "action": action,
        "player_dialogue": player_dialogue,
        "dialogue_history": _compact_lines(dialogue_history or [], 220),
    }


def text_from_payload(payload: dict[str, object]) -> str:
    narration = payload.get("narration")
    if not isinstance(narration, str) or not narration.strip():
        raise ValueError("LLM text response must contain a non-empty narration.")
    return narration.strip()


def world_details_from_payload(payload: dict[str, object]) -> dict[str, object]:
    details: dict[str, object] = {
        "weather": _optional_string(payload, "weather"),
        "opening_event": _optional_string(payload, "opening_event"),
        "locations": [],
        "npcs": [],
        "quest_hooks": [],
    }
    for raw_location in payload.get("locations", []):
        if not isinstance(raw_location, dict) or not isinstance(raw_location.get("index"), int):
            continue
        name = raw_location.get("name")
        summary = raw_location.get("summary")
        if isinstance(name, str) and name.strip() and isinstance(summary, str) and summary.strip():
            details["locations"].append(
                {"index": raw_location["index"], "name": name.strip()[:40], "summary": summary.strip()[:160]}
            )
    for raw_npc in payload.get("npcs", []):
        if not isinstance(raw_npc, dict) or not isinstance(raw_npc.get("index"), int):
            continue
        name = raw_npc.get("name")
        role = raw_npc.get("role")
        disposition = raw_npc.get("disposition")
        location_name = raw_npc.get("location_name")
        if all(isinstance(value, str) and value.strip() for value in [name, role, disposition, location_name]):
            details["npcs"].append(
                {
                    "index": raw_npc["index"],
                    "name": name.strip()[:40],
                    "role": role.strip()[:40],
                    "disposition": disposition.strip()[:40],
                    "location_name": location_name.strip()[:40],
                }
            )
    hooks = [hook.strip() for hook in payload.get("quest_hooks", []) if isinstance(hook, str) and hook.strip()]
    details["quest_hooks"] = hooks[:6]
    return details


def director_beat_from_payload(payload: dict[str, object]) -> DirectorBeat:
    title = _required_string(payload, "title")
    narration = _required_string(payload, "narration")
    mechanical_request = payload.get("mechanical_request")
    if mechanical_request not in {"exploration_check", "social_check", "combat_check", None}:
        mechanical_request = None
    difficulty = payload.get("difficulty", 10)
    if not isinstance(difficulty, int):
        difficulty = 10
    tags = [tag for tag in payload.get("tags", []) if isinstance(tag, str)]
    follow_up_hook = payload.get("follow_up_hook")
    return DirectorBeat(
        title=title,
        narration=narration,
        mechanical_request=mechanical_request,
        difficulty=max(1, min(20, difficulty)),
        tags=tags[:8],
        follow_up_hook=follow_up_hook if isinstance(follow_up_hook, str) and follow_up_hook.strip() else None,
        scene_objects=_string_list(payload.get("scene_objects"), 8),
        inventory_add=_string_list(payload.get("inventory_add"), 4),
        inventory_remove=_string_list(payload.get("inventory_remove"), 4),
    )


def parse_json_object(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object.")
    return payload


def _player_payload(player: Player) -> dict[str, object]:
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


def _location_payload(location: Location) -> dict[str, object]:
    return {
        "name": location.name,
        "position": {"x": location.position.x, "y": location.position.y},
        "biome": location.biome.value,
        "danger": location.danger,
        "summary": location.summary,
    }


def _npc_payload(npc: Npc) -> dict[str, object]:
    return {
        "name": npc.name,
        "role": npc.role,
        "disposition": npc.disposition,
        "location_name": npc.location_name,
    }


def _scene_objects_at(world: World, player: Player) -> list[str]:
    return list(world.scene_objects.get(f"{player.position.x},{player.position.y}", []))


def _object_states_for_position(world: World, position_key: str | None) -> dict[str, dict[str, object]]:
    if position_key is None:
        return {}
    return {
        key: value
        for key, value in world.object_states.items()
        if value.get("position") == position_key or value.get("last_position") == position_key
    }


def _npc_prior_replies(world: World, npc: Npc) -> list[str]:
    prefix = f"{npc.name}:"
    replies: list[str] = []
    for line in world.conversations.get(npc.name, []):
        if line.startswith(prefix):
            replies.append(line.removeprefix(prefix).strip())
    return replies[-8:]


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip()[:60] for item in value if isinstance(item, str) and item.strip()][:limit]


def _compact_lines(lines: list[str], max_length: int) -> list[str]:
    compacted: list[str] = []
    for line in lines:
        normalized = " ".join(line.split())
        if len(normalized) > max_length:
            normalized = normalized[: max_length - 3].rstrip() + "..."
        compacted.append(normalized)
    return compacted


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LLM director beat must contain a non-empty {key}.")
    return value.strip()


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
