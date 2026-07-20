from __future__ import annotations

import json

from worldsim.models import (
    ActionIntent,
    CheckKind,
    DirectorBeat,
    EffectCondition,
    EffectKind,
    EffectSource,
    StateEffect,
    TurnRecord,
)


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
    "required": [
        "campaign_title",
        "overarching_quest",
        "weather",
        "opening_event",
        "locations",
        "npcs",
        "quest_hooks",
        "player_archetypes",
        "homelands",
        "starting_inventory",
        "skill_descriptions",
    ],
    "properties": {
        "campaign_title": {"type": "string"},
        "overarching_quest": {"type": "string"},
        "weather": {"type": "string"},
        "opening_event": {"type": "string"},
        "player_archetypes": {
            "type": "array",
            "minItems": 5,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["name", "description", "skill_bonuses"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "skill_bonuses": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "required": ["skill", "bonus"],
                            "properties": {
                                "skill": {"type": "string"},
                                "bonus": {"type": "integer", "minimum": -2, "maximum": 4},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "boosts": {
                        "type": "object",
                        "properties": {
                            "exploration_check": {"type": "integer", "minimum": -2, "maximum": 4},
                            "social_check": {"type": "integer", "minimum": -2, "maximum": 4},
                            "combat_check": {"type": "integer", "minimum": -2, "maximum": 4},
                        },
                    },
                },
                "additionalProperties": False,
            },
        },
        "homelands": {
            "type": "array",
            "minItems": 5,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "starting_inventory": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "required": ["name", "description"],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "skill_descriptions": {
            "type": "array",
            "minItems": 8,
            "maxItems": 30,
            "items": {
                "type": "object",
                "required": ["skill", "description"],
                "properties": {
                    "skill": {"type": "string"},
                    "description": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
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
        "choices": {"type": "array", "items": {"type": "string"}, "minItems": 0, "maxItems": 4},
        "progress_summary": {"type": ["string", "null"]},
        "quest_progress_delta": {"type": "integer", "minimum": 0, "maximum": 2},
        "complete_current_stage": {"type": "boolean"},
        "facts_discovered": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "npc_disposition_changes": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["npc_id", "disposition"],
                "properties": {
                    "npc_id": {"type": "string"},
                    "disposition": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "choices_committed": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "clock_effects": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["clock_id", "delta", "reason"],
                "properties": {
                    "clock_id": {"type": "string"},
                    "delta": {"type": "integer", "minimum": -2, "maximum": 2},
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


ACTION_INTENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": [
        "title",
        "stakes",
        "check_kind",
        "difficulty",
        "tags",
        "choices",
        "proposed_effects",
    ],
    "properties": {
        "title": {"type": "string"},
        "stakes": {"type": "string"},
        "check_kind": {
            "type": ["string", "null"],
            "enum": [kind.value for kind in CheckKind if kind != CheckKind.GENERIC] + [None],
        },
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 20},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "proposed_effects": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "required": ["kind", "target_id", "value", "amount", "condition", "flag"],
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            kind.value
                            for kind in EffectKind
                            if kind
                            not in {
                                EffectKind.SCENE_OBJECT_REMOVE,
                                EffectKind.ENCOUNTER_RESOLVE,
                                EffectKind.ENCOUNTER_ESCAPE,
                                EffectKind.ENCOUNTER_START,
                                EffectKind.SCENE_ENTER,
                                EffectKind.SCENE_EXIT,
                                EffectKind.SCENE_STEP,
                                EffectKind.SCENE_TENSION,
                                EffectKind.CAMPAIGN_VICTORY,
                                EffectKind.CAMPAIGN_DEFEAT,
                            }
                        ],
                    },
                    "target_id": {"type": ["string", "null"]},
                    "value": {"type": ["string", "null"]},
                    "amount": {"type": "integer", "minimum": -10, "maximum": 10},
                    "condition": {
                        "type": "string",
                        "enum": [condition.value for condition in EffectCondition],
                    },
                    "flag": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def text_from_payload(payload: dict[str, object]) -> str:
    narration = payload.get("narration")
    if not isinstance(narration, str) or not narration.strip():
        raise ValueError("LLM text response must contain a non-empty narration.")
    return narration.strip()


def world_details_from_payload(payload: dict[str, object]) -> dict[str, object]:
    details: dict[str, object] = {
        "campaign_title": _optional_string(payload, "campaign_title"),
        "overarching_quest": _optional_string(payload, "overarching_quest"),
        "weather": _optional_string(payload, "weather"),
        "opening_event": _optional_string(payload, "opening_event"),
        "locations": [],
        "npcs": [],
        "quest_hooks": [],
        "player_archetypes": [],
        "player_archetype_blurbs": {},
        "player_archetype_boosts": {},
        "homelands": [],
        "homeland_descriptions": {},
        "starting_inventory": [],
        "inventory_descriptions": {},
        "skill_descriptions": {},
    }
    for raw_location in payload.get("locations", []):
        if not isinstance(raw_location, dict) or not isinstance(raw_location.get("index"), int):
            continue
        name = raw_location.get("name")
        summary = raw_location.get("summary")
        if isinstance(name, str) and name.strip() and isinstance(summary, str) and summary.strip():
            details["locations"].append(
                {"index": raw_location["index"], "name": name.strip()[:40], "summary": summary.strip()[:110]}
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
    details["quest_hooks"] = [hook[:130] for hook in hooks[:6]]
    archetypes: list[str] = []
    blurbs: dict[str, str] = {}
    boosts_by_archetype: dict[str, dict[str, int]] = {}
    for raw_archetype in payload.get("player_archetypes", []):
        if isinstance(raw_archetype, str):
            name = raw_archetype.strip().lower()
            description = ""
            boosts = {}
        elif isinstance(raw_archetype, dict):
            raw_name = raw_archetype.get("name")
            name = raw_name.strip().lower() if isinstance(raw_name, str) else ""
            raw_description = raw_archetype.get("description")
            description = raw_description.strip() if isinstance(raw_description, str) else ""
            boosts = _skill_bonuses(raw_archetype.get("skill_bonuses"))
            if not boosts:
                boosts = _check_boosts(raw_archetype.get("boosts"))
        else:
            continue
        if not name:
            continue
        archetypes.append(name)
        if description:
            blurbs[name] = description[:180]
        if boosts:
            boosts_by_archetype[name] = boosts
    homelands: list[str] = []
    homeland_descriptions: dict[str, str] = {}
    for raw_homeland in payload.get("homelands", []):
        if isinstance(raw_homeland, str):
            name = raw_homeland.strip()
            description = ""
        elif isinstance(raw_homeland, dict):
            raw_name = raw_homeland.get("name")
            raw_description = raw_homeland.get("description")
            name = raw_name.strip() if isinstance(raw_name, str) else ""
            description = raw_description.strip() if isinstance(raw_description, str) else ""
        else:
            continue
        if not name:
            continue
        homelands.append(name[:50])
        if description:
            homeland_descriptions[name[:50]] = description[:150]
    details["player_archetypes"] = archetypes[:6]
    details["player_archetype_blurbs"] = {key: blurbs[key] for key in archetypes[:6] if key in blurbs}
    details["player_archetype_boosts"] = {key: boosts_by_archetype[key] for key in archetypes[:6] if key in boosts_by_archetype}
    details["homelands"] = homelands[:8]
    details["homeland_descriptions"] = {key: homeland_descriptions[key] for key in homelands[:8] if key in homeland_descriptions}
    inventory: list[str] = []
    inventory_descriptions: dict[str, str] = {}
    for raw_item in payload.get("starting_inventory", []):
        if not isinstance(raw_item, dict):
            continue
        name = raw_item.get("name")
        description = raw_item.get("description")
        if not isinstance(name, str) or not name.strip():
            continue
        item = name.strip().lower()[:60]
        inventory.append(item)
        if isinstance(description, str) and description.strip():
            inventory_descriptions[item] = description.strip()[:140]
    details["starting_inventory"] = inventory[:5]
    details["inventory_descriptions"] = inventory_descriptions
    skill_descriptions: dict[str, str] = {}
    for raw_skill in payload.get("skill_descriptions", []):
        if not isinstance(raw_skill, dict):
            continue
        skill = raw_skill.get("skill")
        description = raw_skill.get("description")
        if not isinstance(skill, str) or not skill.strip() or not isinstance(description, str) or not description.strip():
            continue
        key = skill.strip().lower().replace(" ", "_").replace("-", "_")
        skill_descriptions[key] = description.strip()[:140]
    details["skill_descriptions"] = skill_descriptions
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
        choices=_string_list(payload.get("choices"), 4),
        progress_summary=_optional_string(payload, "progress_summary"),
        quest_progress_delta=_bounded_int(payload.get("quest_progress_delta"), 0, 2, default=0),
        complete_current_stage=bool(payload.get("complete_current_stage", False)),
        clock_effects=_clock_effects(payload.get("clock_effects")),
        facts_discovered=_string_list(payload.get("facts_discovered"), 6),
        npc_disposition_changes=_npc_disposition_changes(
            payload.get("npc_disposition_changes")
        ),
        choices_committed=_string_list(payload.get("choices_committed"), 4),
    )


def action_intent_from_payload(payload: dict[str, object], intent_id: str, raw_input: str) -> ActionIntent:
    check_kind_value = payload.get("check_kind")
    try:
        check_kind = CheckKind(check_kind_value) if isinstance(check_kind_value, str) else None
    except ValueError:
        check_kind = None
    difficulty = payload.get("difficulty", 10)
    if not isinstance(difficulty, int):
        difficulty = 10
    effects: list[StateEffect] = []
    for raw_effect in payload.get("proposed_effects", []):
        if not isinstance(raw_effect, dict):
            continue
        try:
            kind = EffectKind(str(raw_effect.get("kind")))
            condition = EffectCondition(str(raw_effect.get("condition", EffectCondition.SUCCESS.value)))
        except ValueError:
            continue
        target_id = raw_effect.get("target_id")
        value = raw_effect.get("value")
        amount = raw_effect.get("amount", 0)
        effects.append(
            StateEffect(
                kind=kind,
                target_id=target_id.strip() if isinstance(target_id, str) and target_id.strip() else None,
                value=value.strip() if isinstance(value, str) and value.strip() else None,
                amount=max(-10, min(10, amount)) if isinstance(amount, int) else 0,
                condition=condition,
                flag=bool(raw_effect.get("flag", False)),
                source=EffectSource.DIRECTOR,
            )
        )
    return ActionIntent(
        id=intent_id,
        raw_input=raw_input,
        title=_required_string(payload, "title"),
        stakes=_required_string(payload, "stakes"),
        check_kind=check_kind,
        difficulty=max(1, min(20, difficulty)),
        proposed_effects=effects[:16],
        tags=_string_list(payload.get("tags"), 8),
        choices=_string_list(payload.get("choices"), 4),
    )


def turn_record_to_payload(record: TurnRecord) -> dict[str, object]:
    check = None
    if record.check is not None:
        check = {
            "kind": record.check.kind.value,
            "difficulty": record.check.difficulty,
            "raw_roll": record.check.raw_roll,
            "bonus": record.check.bonus,
            "total": record.check.total,
            "success": record.check.success,
            "summary": record.check.summary,
        }
    return {
        "id": record.id,
        "tick": record.tick,
        "command": record.command,
        "intent": {
            "id": record.intent.id,
            "raw_input": record.intent.raw_input,
            "kind": record.intent.kind.value,
            "title": record.intent.title,
            "stakes": record.intent.stakes,
            "check_kind": record.intent.check_kind.value if record.intent.check_kind is not None else None,
            "difficulty": record.intent.difficulty,
            "proposed_effects": [_effect_payload(effect) for effect in record.intent.proposed_effects],
            "tags": list(record.intent.tags),
            "choices": list(record.intent.choices),
        },
        "check": check,
        "outcome": {
            "success": record.outcome.success,
            "authoritative_summary": record.outcome.authoritative_summary,
            "accepted_effects": [_effect_payload(effect) for effect in record.outcome.accepted_effects],
            "rejected_effects": [
                {"effect": _effect_payload(item.effect), "reason": item.reason}
                for item in record.outcome.rejected_effects
            ],
        },
        "narration": record.narration,
        "choices": list(record.choices),
    }


def _effect_payload(effect: StateEffect) -> dict[str, object]:
    return {
        "kind": effect.kind.value,
        "target_id": effect.target_id,
        "value": effect.value,
        "amount": effect.amount,
        "condition": effect.condition.value,
        "flag": effect.flag,
        "source": effect.source.value,
    }


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


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip()[:60] for item in value if isinstance(item, str) and item.strip()][:limit]


def _check_boosts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    boosts: dict[str, int] = {}
    for key in ("exploration_check", "social_check", "combat_check"):
        raw = value.get(key)
        if isinstance(raw, int):
            boosts[key] = max(-2, min(4, raw))
    return boosts


def _skill_bonuses(value: object) -> dict[str, int]:
    if not isinstance(value, list):
        return {}
    boosts: dict[str, int] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        skill = raw.get("skill")
        bonus = raw.get("bonus")
        if not isinstance(skill, str) or not skill.strip() or not isinstance(bonus, int):
            continue
        key = skill.strip().lower().replace(" ", "_").replace("-", "_")
        boosts[key] = max(-2, min(4, bonus))
    return boosts


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    if not isinstance(value, int):
        return default
    return max(minimum, min(maximum, value))


def _clock_effects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    effects: list[dict[str, object]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        clock_id = raw.get("clock_id")
        delta = raw.get("delta")
        reason = raw.get("reason")
        if not isinstance(clock_id, str) or not clock_id.strip() or not isinstance(delta, int):
            continue
        effects.append(
            {
                "clock_id": clock_id.strip()[:60],
                "delta": max(-2, min(2, delta)),
                "reason": reason.strip()[:160] if isinstance(reason, str) and reason.strip() else "Scene pressure changed.",
            }
        )
    return effects[:3]


def _npc_disposition_changes(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    changes: list[dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        npc_id = raw.get("npc_id")
        disposition = raw.get("disposition")
        if not isinstance(npc_id, str) or not npc_id.strip():
            continue
        if not isinstance(disposition, str) or not disposition.strip():
            continue
        changes.append(
            {
                "npc_id": npc_id.strip()[:60],
                "disposition": disposition.strip()[:60],
            }
        )
    return changes[:4]


def _compact_lines(lines: list[str], max_length: int) -> list[str]:
    compacted: list[str] = []
    for line in lines:
        normalized = " ".join(line.split())
        if len(normalized) > max_length:
            normalized = normalized[: max_length - 3].rstrip() + "..."
        compacted.append(normalized)
    return compacted


def _inventory_category(item: str) -> str:
    token = item.lower()
    if any(keyword in token for keyword in {"key", "map", "ledger", "note", "sigil", "badge", "token", "relic"}):
        return "quest"
    if any(keyword in token for keyword in {"torch", "lamp", "light"}):
        return "light"
    if any(keyword in token for keyword in {"rations", "snack", "food", "water", "drink"}):
        return "consumable"
    if any(keyword in token for keyword in {"rope", "hook", "kit", "lockpick", "tool"}):
        return "tool"
    return "utility"


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
