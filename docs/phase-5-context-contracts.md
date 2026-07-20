# Phase 5: LLM Context and Response Contracts

Phase 5 replaces the director's single broad prompt and full-world payload with
task-specific contracts. Creative generation still belongs to the director;
authoritative checks and state changes still belong to the engine.

## Task contracts

Each director operation has a dedicated instruction and a separately titled
JSON schema:

- introductions, location descriptions, ambient events, and resolved outcomes
  return narration-only objects;
- explicit actions and dialogue return structured director beats;
- freeform interpretation returns an `ActionIntent` proposal;
- world generation returns content mapped to the deterministic scaffold.

Responses are parsed as JSON and recursively checked for required fields, types,
enums, array bounds, numeric bounds, string bounds, and unsupported properties.
An invalid response gets a bounded repair request containing its validation
errors and the same schema. The deterministic director is used if repair fails.

`WORLDSIM_LLM_REPAIR_ATTEMPTS` defaults to `1` and is clamped from `0` to `2`.

## Relevance selection

The context selector builds a projection for the current task. A normal turn can
include the current player, location, NPC, scene, encounter, active quest stage,
applicable clocks, directly related entities, recent outcomes, and the relevant
state ledger. It does not send every location, NPC, quest, or clock.

Dialogue has one canonical, deduplicated `dialogue_history`. The previous
parallel current-history, conversation-history, and prior-reply representations
are gone. Outcome narration receives the resolved turn and the post-resolution
state, so it describes accepted engine effects instead of predicting them.

## Context budget

The selector estimates tokens from compact serialized JSON and trims data in a
stable order: older optional list entries first, then long prose at word
boundaries, then optional sections. Exact IDs, entity names, action text, and
other contract keys are protected from prose truncation.

Configuration:

```text
WORLDSIM_CONTEXT_TOKEN_BUDGET=1800
WORLDSIM_CONTEXT_CHARS_PER_TOKEN=4
```

The token budget has a minimum of 256 when read from the environment. A context
that cannot fit its minimum task contract raises an error and takes the normal
director fallback path rather than silently exceeding the configured ceiling.

Each selection records estimated tokens, budget, character count, dropped items,
and truncated strings. The most recent estimate appears in the director status
line, and full metrics are written as `director_context_budget` debug events.

## Verification gate

The Phase 5 tests verify that:

- unrelated world entities and storylines are excluded;
- oversized memory and world prose stay under a configured budget;
- dialogue history has one canonical representation;
- task schemas and prompts remain distinct;
- wrong types and unsupported properties fail validation;
- repair succeeds once when possible and falls back after the configured bound.
