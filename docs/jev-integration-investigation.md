# Jev integration investigation

Investigated 2026-09-21 against `fcfdcbc`. Proposal only; no runtime integration
or authenticated Jev calls have been made.

Update 2026-09-23: the original proposal below is retained as investigation
history. An optional Jev action selector and a first situation-graph implementation
now exist; see [Phase 7](phase-7-action-graphs.md) for current behavior and the live
probe result.

## Recommendation

Use Jev to choose bounded game actions from engine-authored candidates, then
compile those decisions into existing `ActionIntent` and `StateEffect` models.
Keep the engine responsible for checks, legality, mutations, progression, and
replay. Keep the generative director for world building, dialogue, and narration.

The useful guarantee is a predictable decision vocabulary. It does not guarantee
that the selected action is semantically correct, legal, or successful. The
engine must continue enforcing those distinctions.

## Verified provider capabilities

- Jev accepts state and typed questions, returning Choice, Score, and Noul
  answers. It does not generate prose or arbitrary nested game-state patches.
  [Introduction](https://docs.typesafe.ai/introduction)
- The direct endpoint is `POST https://api.typesafe.ai/v1/systemone`, with bearer
  authentication and a body containing `model`, `state`, and `questions`.
  Choice supports up to 255 options; Score supports 2–10 rubric levels and may
  return a fractional value. Question IDs are response keys, not model context:
  instructions must explicitly identify the judgment and relevant state.
  [API reference](https://docs.typesafe.ai/api)
- Questions in a batch see the same state independently. One question cannot
  consume another question's answer in that batch.
  [Primitives](https://docs.typesafe.ai/primitives)
- Choice and Score include distribution-derived confidence; Noul is a 0–1
  probability, not a boolean. Thresholds need calibration on our own examples.
  [Confidence](https://docs.typesafe.ai/confidence)
- The official Python package is `typesafe-sdk`, with `TypeSafeClient`, `Choice`,
  `Score`, and `Noul`. It reads `TYPESAFE_API_KEY`; the documented default model
  is `jev-latest`. [Quick start](https://docs.typesafe.ai/introduction/quickstart)

## Existing integration points

| Code | Current behavior | Proposed change |
| --- | --- | --- |
| `worldsim/director.py:LocalLLMDirector.interpret_freeform_action` | Generates and validates an intent JSON object, repairing or falling back | Delegate supported decisions to a Jev interpreter |
| `worldsim/context.py` | Selects budgeted, relevant state | Build a decision projection with exact candidate references and sufficient evidence |
| `worldsim/engine.py:WorldEngine._resolve_freeform_action` | Intent → prepare effects → roll → reducer → progression → narration | Retain resolution sequence |
| `worldsim/turn_effects.py:TurnEffectService` | Derives effects from raw-input verbs and validates targets and legality | Introduce a normalized action contract before extending language coverage |
| `worldsim/turn_resolution.py:StateReducer` | Rejects invalid effects; atomically commits accepted effects with rollback | Retain; validate conflicts between selected effects as well |
| `worldsim/director.py:LocalLLMDirector.narrate_turn_outcome` | Narrates the committed result | Retain, with deterministic narration fallback |

The current reducer can reject part of a proposal and commit the remaining
accepted effects. Atomic commit does not mean every proposed effect is accepted.
For dependent effects, compile and validate them as a coherent action bundle.

Object targets currently use names and position-based records rather than a
uniform stable object ID. Use request-local opaque candidate IDs mapped back to
exact current objects; do not assume all entities already have stable IDs.

An important constraint: inventory and object-status validators reparse
`intent.raw_input`. Recognizing “pocket the journal” in Jev alone would not make
that action legal. Initially support existing canonical verbs. Later, add typed
normalized verb/target fields with persistence and replay coverage; preserve the
original player input instead of rewriting it to bypass validation.

## First vertical slice

1. Introduce a separate `JevClient` adapter and a decision interpreter interface.
   Keep provider-specific response objects outside engine models. Use an opt-in
   configuration so the existing director continues to work without a Jev key.
2. Start with visible-object take/open/close actions. Build a bounded catalog of
   operation-and-target candidates with engine-authored effect templates, plus
   an explicit `unsupported` option. Exclude absent, destroyed, or unavailable
   objects and unsupported operations.
3. Ask one Choice question to select the attempted action from this catalog.
   A combined candidate prevents independently selected verb and target answers
   from forming an invalid pair. Keep candidate counts within provider limits;
   use deterministic relevance filtering and explicit fallback when overloaded.
4. Convert the selected candidate into an `ActionIntent`. Derive checks, DCs,
   conditions, and effect values from game policy. Jev confidence must never
   replace a d20 roll or become a probability of in-game success.
5. Pass the proposal through existing preparation, validation, and commit logic.
   Check that preparation does not duplicate the compiler's effects. Narrate
   only the accepted outcome using the existing generative director.
6. On unsupported or uncertain decisions, use the existing interpretation path
   before any mutation. On HTTP or response-validation failures, use one bounded
   fallback. Never retry a committed turn because narration failed.

Example: “open the rusted box” → Choice `candidate_03` → local lookup of the
open-box action template → engine check → `OBJECT_STATUS(open)` on success →
persisted outcome → narration. The model never supplies an arbitrary status,
new target, or authoritative success value.

This removes intent JSON generation and repair for covered actions. It adds
candidate construction and a small response adapter. It does not remove creative
generation contracts, the reducer, progression, or save/replay orchestration.
Net latency and simplicity gains need measurement, especially on fallback turns.

## Evaluation before enabling by default

Use synthetic fixtures, then opt-in live evaluation against labeled actions.
Cover negation, ambiguous targets, absent/destroyed objects, compound actions,
unsupported verbs, movement locks, and requests to invent items or ignore rules.
Verify missing answers, invalid option IDs, non-finite probabilities, timeouts,
authentication failures, and rate limits take the bounded fallback path.

Compare action/target accuracy, confidence versus observed errors, fallback rate,
end-to-end turn latency, token usage, and accepted/rejected effects against the
current director. Log model version and question-contract version without keys.
Run replay tests without network access and check that failed checks cannot
commit success-only effects. Calibrate thresholds from these results rather
than assuming a provider confidence value proves correctness.

## Access needed

- A direct TypeSafe API key placed locally in `TYPESAFE_API_KEY`, or confirmation
  that the account is through a gateway requiring a different adapter. Do not
  paste the key into chat or commit it to the repository.
- Confirmation that API access is enabled for the account; any account-specific
  endpoint/model restrictions or rate limits shown in the dashboard.
- Before live benchmarking, agree on a small request/spend cap. Synthetic state
  is sufficient for the first probe; existing campaign data is unnecessary.

The existing LLM configuration can continue supplying narration. No account
password, session cookie, or production campaign upload is needed.
