# Phase 2: Unified Turn Resolution

Phase 2 routes freeform commands through one authoritative sequence:

1. `Director.interpret_freeform_action` proposes an `ActionIntent`.
2. The engine validates the target and resolves an optional typed `CheckResult`.
3. `TurnEffectService` applies the engine's legality policy, while
   `StateReducer` filters effects by outcome, validates the whole batch, and
   commits it transactionally.
4. The engine derives a `TurnOutcome` containing accepted and rejected effects.
5. `Director.narrate_turn_outcome` receives the resolved record and updated
   state, so narration cannot precede the roll.
6. The completed `TurnRecord` is retained on `World`, persisted in schema
   version 2 saves, and can be replayed without rerolling.

## Authority boundary

Director effects are proposals. When an intent requests a check, director
mutations must be success-conditional. Encounter start, escape, and resolution
effects are engine-authored only. Object effects must target a present object
and match the attempted verb. Inventory additions require an explicit take
action against a visible object.

The reducer validates every applicable effect before beginning the commit. It
restores the world and player snapshots if any committer raises. The engine
stages campaign-memory changes alongside the batch and publishes them only
after a successful commit.

## Compatibility

Existing directors can continue returning `DirectorBeat`; the base
`Director.interpret_freeform_action` method adapts those beats into intents.
Existing explicit commands continue to use their current command branches, but
their boolean roll API now adapts the same typed `CheckResult` used by freeform
turns.

Schema version 1 saves migrate to version 2 by adding an empty turn history.
Unversioned saves still follow the version 0 → 1 → 2 migration chain.

## Deferred to later phases

- Persistent TUI area movement and dialogue lifecycle are Phase 3.
- Fully typed quest lifecycle, finales, and campaign endings are Phase 4.
- Relevance-budgeted director context and schema repair are Phase 5.
- Stable game-object records, route graphs, and layered ASCII rendering remain
  in their planned phases.
