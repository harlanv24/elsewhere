from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from worldsim.schemas import (
    ACTION_INTENT_SCHEMA,
    DIRECTOR_BEAT_SCHEMA,
    TEXT_RESPONSE_SCHEMA,
    WORLD_DETAILS_SCHEMA,
)


class SchemaValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class TaskContract:
    task: str
    instruction: str
    schema: dict[str, object]


def _schema(
    base: dict[str, object],
    title: str,
    description: str,
) -> dict[str, object]:
    schema = deepcopy(base)
    schema["title"] = title
    schema["description"] = description
    properties = schema.get("properties")
    if isinstance(properties, dict):
        narration = properties.get("narration")
        if isinstance(narration, dict):
            narration.setdefault("minLength", 1)
    return schema


TASK_CONTRACTS: dict[str, TaskContract] = {
    "introduce_world": TaskContract(
        "introduce_world",
        "Introduce the current campaign in two to five concise sentences. "
        "Ground the opening in the supplied location, active quest, and "
        "named entities.",
        _schema(
            TEXT_RESPONSE_SCHEMA,
            "WorldIntroduction",
            "Opening narration for an already authoritative campaign state.",
        ),
    ),
    "describe_location": TaskContract(
        "describe_location",
        "Describe only the current location and visible state. Mention "
        "actionable details without implying uncommitted movement or mutations.",
        _schema(
            TEXT_RESPONSE_SCHEMA,
            "LocationDescription",
            "Grounded description of the current authoritative scene.",
        ),
    ),
    "ambient_world_event": TaskContract(
        "ambient_world_event",
        "Write a minor flavor-only ambient event. It must not imply destroyed "
        "objects, moved characters, faction victories, or other authoritative "
        "changes.",
        _schema(
            TEXT_RESPONSE_SCHEMA,
            "AmbientFlavorEvent",
            "Flavor-only world event with no mechanical state change.",
        ),
    ),
    "narrate_turn_outcome": TaskContract(
        "narrate_turn_outcome",
        "Narrate the resolved turn in two to five concise sentences. The check "
        "and accepted effects are authoritative; never include rejected effects "
        "or invent another mutation.",
        _schema(
            TEXT_RESPONSE_SCHEMA,
            "ResolvedOutcomeNarration",
            "Post-resolution narration of an authoritative turn record.",
        ),
    ),
    "respond_to_action": TaskContract(
        "respond_to_action",
        "Frame the explicit action with concrete stakes and valid next choices. "
        "Proposed progression details must use exact IDs from the active stage.",
        _schema(
            DIRECTOR_BEAT_SCHEMA,
            "ExplicitActionBeat",
            "Structured creative beat for an explicit engine command.",
        ),
    ),
    "respond_to_freeform_action": TaskContract(
        "respond_to_freeform_action",
        "Frame a legacy freeform action without deciding its outcome. Use exact "
        "visible entities and current-stage IDs.",
        _schema(
            DIRECTOR_BEAT_SCHEMA,
            "LegacyFreeformBeat",
            "Compatibility beat for freeform action interpretation.",
        ),
    ),
    "respond_to_dialogue": TaskContract(
        "respond_to_dialogue",
        "Reply in character using the single supplied dialogue history. Use "
        "exact NPC, fact, and choice IDs for structured progression proposals.",
        _schema(
            DIRECTOR_BEAT_SCHEMA,
            "DialogueResolutionBeat",
            "Structured dialogue reply and proposed social or quest effects.",
        ),
    ),
    "interpret_freeform_action": TaskContract(
        "interpret_freeform_action",
        "Interpret neutral intent and stakes before resolution. Propose only "
        "typed effects grounded in the state ledger, exact IDs, and explicit "
        "player action.",
        _schema(
            ACTION_INTENT_SCHEMA,
            "FreeformActionIntent",
            "Pre-roll intent and non-authoritative proposed effects.",
        ),
    ),
    "generate_world_details": TaskContract(
        "generate_world_details",
        "Create concise, theme-specific world details for the supplied indexed "
        "scaffold. Preserve indexes and stable IDs; do not output final ASCII.",
        _schema(
            WORLD_DETAILS_SCHEMA,
            "GeneratedWorldDetails",
            "Creative world content mapped onto the deterministic scaffold.",
        ),
    ),
}


def contract_for(task: str) -> TaskContract:
    try:
        return TASK_CONTRACTS[task]
    except KeyError as exc:
        raise ValueError(f"No director contract is registered for task {task!r}.") from exc


def task_system_prompt(task: str, engine_contract: str) -> str:
    contract = contract_for(task)
    return (
        f"{engine_contract}\n\n"
        f"Current task: {task}.\n"
        f"{contract.instruction}\n"
        "Return exactly one JSON object matching the supplied response_schema. "
        "Use null for absent optional values. Do not wrap JSON in Markdown."
    )


def repair_system_prompt(task: str, engine_contract: str) -> str:
    contract = contract_for(task)
    return (
        f"{engine_contract}\n\n"
        f"Repair a malformed response for task {task}. "
        f"{contract.instruction} "
        "Return only one corrected JSON object matching response_schema. "
        "Do not explain the repair or add Markdown."
    )


def validate_payload(
    payload: object,
    schema: dict[str, object],
) -> dict[str, object]:
    errors: list[str] = []
    _validate(payload, schema, "$", errors)
    if errors:
        raise SchemaValidationError(errors)
    if not isinstance(payload, dict):
        raise SchemaValidationError(["$: expected object"])
    return payload


def _validate(
    value: object,
    schema: dict[str, object],
    path: str,
    errors: list[str],
) -> None:
    allowed_types = schema.get("type")
    if allowed_types is not None:
        names = (
            [allowed_types]
            if isinstance(allowed_types, str)
            else list(allowed_types)
            if isinstance(allowed_types, list)
            else []
        )
        if names and not any(_matches_type(value, name) for name in names):
            expected = " or ".join(str(name) for name in names)
            errors.append(f"{path}: expected {expected}")
            return

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: value is not in the allowed enum")
        return

    if isinstance(value, dict):
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}: required property is missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in property_map:
                    errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child in value.items():
            child_schema = property_map.get(key)
            if isinstance(child_schema, dict):
                _validate(child, child_schema, f"{path}.{key}", errors)

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: requires at least {minimum} items")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: allows at most {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]", errors)

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: requires at least {minimum} characters")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: allows at most {maximum} characters")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: must be at least {minimum}")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: must be at most {maximum}")


def _matches_type(value: object, name: object) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return True
