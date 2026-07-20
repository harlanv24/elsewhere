# Phase 6: Navigation and ASCII Rendering

Phase 6 makes narrative geography authoritative and moves map composition out of
the Textual application. The LLM still names and describes locations, but it
does not create route topology or final ASCII.

## Location graph

`NavigationService` generates a connected graph after stable location IDs exist.
It builds a deterministic minimum spanning tree and a small deterministic set of
extra links. Each persisted `LocationRoute` contains:

- a stable route ID;
- origin and destination location IDs;
- a terrain-aware tile path;
- a route kind (`road`, `trail`, or `ferry`);
- a compact danger rating.

Path generation uses deterministic four-directional search. Plains are cheap,
forests and hills cost more, mountains are expensive, and water is allowed at a
high cost so disconnected land masses can still receive ferry routes.

All named locations are reachable through successive graph edges. Cardinal
commands remain one-tile wilderness movement. A named command such as
`travel to Observatory` is accepted only when:

- the action explicitly names that destination;
- the player starts at a named location;
- the destination is directly connected by a route;
- no local scene or active encounter prevents travel.

The transition remains an engine-owned `LOCATION_TRANSITION` effect. It commits
before outcome narration, so the narration pass sees the destination scene.
Director context exposes only routes leaving the current location.

## Rendering layers

`AsciiRenderer` is independent from Textual and the LLM. It composes
two-character ASCII cells in this order:

1. deterministic biome texture and terrain boundaries;
2. rivers and coast edges inferred from water topology;
3. road, trail, and ferry paths;
4. reusable landmark sprite footprints;
5. word-safe location labels;
6. quest, NPC, object, hazard, and player overlays.

Landmark footprints are reserved before route drawing. Visual routes stop at the
edge of each footprint, and labels only occupy unreserved cells. Gameplay
overlays intentionally have the highest priority because current state must
remain readable.

Local scenes use a separate composition with walls, a traversal path, landmark
detail, exit, player, NPC, object, hazard, and quest overlays. The CLI dashboard
and Textual UI consume the same compositions; Textual only maps semantic roles
to colors.

## Save compatibility

Save schema version 5 persists route IDs, endpoints, paths, kinds, and danger.
The version-4 migration adds an empty route collection. On resume, the engine
uses the saved seed, locations, and terrain to regenerate the exact graph before
the map is displayed.

## Verification gate

The Phase 6 tests verify that:

- the same seed produces the same graph and full ASCII snapshot;
- every named location is reachable;
- route paths touch their declared endpoints and remain in bounds;
- landmark footprints do not overlap one another or critical route cells;
- labels avoid landmarks and critical routes;
- named travel follows a direct edge and rejects disconnected destinations;
- post-transition narration receives the new location;
- local-scene rendering includes all gameplay overlays;
- routes survive a save/load round trip and regenerate from schema version 4.
