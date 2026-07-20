# Architecture Audit and Migration Plan

This audit describes the pre-Phase 1 baseline. Line references are to that
baseline; later migration work can move them. The repository was audited in
full before implementation began.

## Baseline command path

1. Textual input enters `WorldSimApp._submit_command_input` and
   `_command_from_input` (`worldsim/tui.py:493-517`).
2. `_handle_command_worker` calls the monolithic
   `WorldEngine.resolve_command` (`worldsim/tui.py:925-949`,
   `worldsim/engine.py:61-350`).
3. Each command branch calls the director and mutates `World`/`Player` in a
   different order. There is no shared intent, transaction, reducer, outcome,
   or turn record.
4. `LocalLLMDirector._request_json` sends `director_context`, parses either
   narration or a `DirectorBeat`, and falls back to `MockDirector`
   (`worldsim/director.py:463-495`).
5. `_finish_command` appends output, calls `CampaignStore.save`, and refreshes
   panels (`worldsim/tui.py:957-976`).
6. The area subsystem bypasses that path and mutates `Session` or
   `Player.position` directly (`worldsim/tui.py:1966-2213`).

## Findings

All twenty reported findings were confirmed:

1. Freeform beats have no destination or transition field, and freeform
   resolution never updates position (`models.py:99-115`,
   `engine.py:688-749`).
2. Explore, attack, and freeform narration is requested before dice are rolled
   (`engine.py:162-166`, `289-304`, `705-738`).
3. Freeform scene and inventory effects were applied before the check
   (`engine.py:706-710`, then `735-738`).
4. Dialogue returned only `str`, then entered progression through an empty beat
   (`director.py:104-115`, `engine.py:243-265`).
5. Generated quests shared three generic stages; summaries became progress and
   completion flags were trusted (`engine.py:550-563`, `601-632`).
6. `_advance_quest_stage` checked counters, not goal conditions
   (`engine.py:641-653`).
7. Every quest used the first two locations and NPCs
   (`engine.py:561-562`).
8. Combat existed only as `current_activity` and `movement_lock` strings
   (`models.py:143-146`, `engine.py:430-438`).
9. Exploration preserved a pre-existing combat string lock
   (`engine.py:435-438`), including after successful freeform checks.
10. A full clock only changed status and emitted an event
    (`engine.py:655-672`).
11. No campaign status or ending model existed; zero HP only caused a TUI
    message (`models.py:118-154`, `tui.py:971-974`).
12. Area, depth, tension, hazard, NPC, and exit state lived in `Session`
    (`tui.py:54-69`) and was reset on load (`622-635`).
13. Area movement and exit used direct mutations and a separate d10
    (`tui.py:1966-2065`).
14. Conversation history implicitly enabled dialogue forever, and all bare
    text became `say`, including reserved commands (`tui.py:510-532`).
15. Objects were mutable strings resolved with bidirectional substring
    matching (`engine.py:856-893`).
16. World details, objects, and display names were sliced mid-word in schemas
    and engine normalization (`schemas.py:305-399`, `545-548`;
    `engine.py:911-948`, `1148-1190`).
17. Normal director context included all locations, NPCs, quests, and clocks,
    plus duplicated inventory/object/dialogue data (`schemas.py:188-271`).
18. Ambient events committed prose only to event and memory logs
    (`engine.py:1109-1112`).
19. Named locations were isolated coordinates; travel used only cardinal grid
    movement (`models.py:35-42`, `engine.py:1083-1094`).
20. Rendering composed biome tokens and point markers only
    (`tui.py:1115-1172`, `render.py:86-105`).

## Compatibility risks

The baseline save was unversioned. NPC locations, conversations, quest
relations, and object keys depended on mutable names. Area state was already
lost on load. Adding nested dataclasses without migration would break direct
`Quest(**payload)` and `QuestClock(**payload)` construction. Required legacy
keys used direct indexing, and writes replaced the canonical file in place.

Phase 1 treats every unversioned save as schema version 0, normalizes it to
version 1, backfills deterministic entity IDs, bridges legacy combat strings
to an encounter record, and rejects unsupported future versions explicitly.

## Target ownership

- The interpreter/director proposes `ActionIntent` and typed effects.
- The engine validates legality and owns `CheckResult` and `TurnOutcome`.
- A state reducer is the only component that commits `StateEffect`.
- `TurnRecord` persists input, intent, check, accepted/rejected effects,
  authoritative outcome, narration, and choices.
- `SceneState`, `EncounterState`, and dialogue state are persistent engine
  state; UI code only dispatches and displays them.
- `QuestStage` conditions and clock triggers are evaluated by the engine.
- Stable IDs identify locations, NPCs, objects, scenes, encounters, quests, and
  clocks independently of display names.
- Campaign status owns finale, victory, defeat, abandonment, and epilogue.

## Migration phases and gates

1. **Safety baseline:** deterministic pytest fixtures; save version/migration;
   atomic saves; failed-effect regression; encounter-lock cleanup; reserved
   dialogue commands; structured dialogue progression; verified quest
   conditions; exactly-once clock consequences; scene/encounter round-trip.
2. **Unified turns:** intent/check/effect/outcome/record types; transactional
   reducer; post-roll narration; existing explicit-command adapters. Gate on
   failed/successful door scenarios and TurnRecord replay.
3. **Scenes and encounters:** move all remaining area state out of `Session`;
   route area movement and exits through normal turns; explicit dialogue
   lifecycle; encounter-derived locks/actions. Gate on alternate escape and
   exact mid-encounter save/load.
4. **Progression and endings:** fully typed stages and entity relations; quest
   prerequisites/lifecycle; trigger plans; finale/victory/defeat/epilogue.
   Gate on irrelevant-discovery rejection, completion validation, exactly-once
   triggers, and persisted endings.
5. **LLM contracts and context:** task-specific schemas; relevance selector;
   configurable budget; post-outcome narration; repair/retry and
   instrumentation. Gate on budget and unrelated-data exclusion tests.
6. **Navigation and rendering:** seeded location graph, reachability, routes,
   deterministic layered overworld/local renderers, sprites, footprints,
   labels, and collision rules. Gate on seed snapshots, reachability, and
   non-overlap tests.
