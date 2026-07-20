# Phase 3: Persistent Scenes and Encounters

Phase 3 removes the local-area rules system from the Textual session. The UI
retains only `selected_area`, which is a presentation choice; the active area,
depth, tension, theme, hazard, local NPC ID, exit state, and available actions
belong to the persisted `SceneState`.

## Scene lifecycle

`SceneMode` distinguishes the overworld from a local scene. Entering an area
creates a deterministic local scene tied to its parent location and overworld
scene ID. Leaving returns to that parent scene without silently changing the
player's world coordinate. Cardinal travel is rejected until the local scene is
exited.

The following commands now pass through `WorldEngine.resolve_command`:

- `enter area <name>`
- `leave area`
- `push deeper`
- `pull back`
- `force exit`

They create explicit `ActionIntent` and `TurnRecord` values. Depth and tension
changes are typed state effects committed by `StateReducer`. A forced exit uses
the common d20 exploration check; the old TUI-owned d10 resolver no longer
exists.

## Encounters and actions

Movement locks and scene actions are derived from the active
`EncounterState`. An active encounter replaces `leave area` with `force exit`
and suppresses ordinary local movement. A successful forced exit commits both
the scene transition and an engine-owned encounter escape effect. Failure keeps
the exact scene and encounter active while increasing tension.

The active local NPC is a stable world NPC ID. `talk` starts persisted
`DialogueState`, `end conversation` clears it explicitly, and local movement or
exit ends the conversation.

## Named-location transitions

Freeform travel verbs can produce `location_transition` effects. The effect
must reference an exact stable location ID, match an explicit travel action,
occur outside a local scene, and pass encounter-lock validation. The engine
updates player position and scene state before outcome narration.

## Persistence and replay

Save schema version 3 adds scene mode, parent scene ID, and entry tick. Version
2 saves infer local mode when an area name exists. Scene entry, movement, exit,
and location transition effects replay without consulting the director or
consuming RNG state.

Phase 4 adds the typed quest lifecycle, prerequisites, clock consequences,
finale activation, victory, defeat, abandonment, and epilogues described in
[`phase-4-campaign-resolution.md`](phase-4-campaign-resolution.md).
