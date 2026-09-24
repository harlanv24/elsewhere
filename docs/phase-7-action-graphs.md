# Phase 7: Playable situation graphs

Local situations now have a bounded action graph behind their presentation.
The director plans narrative states and connects engine capabilities; the engine
validates the plan, rolls checks, commits effects, and records the resulting state.
Jev is optional and can now select actions through a dependency-free HTTP adapter.
Without a key, the existing director handles the same decision boundary.

## Play it

Enter any local area using `enter area <name>`. A situation is prepared before
its opening text is shown. Existing local movement, dialogue, and exits remain
available. Alternatively, type `situation` to prepare a situation at the current
overworld position, or inspect the one already prepared.

- `choose <displayed action>` executes an exact available transition without an
  interpretation call. TUI buttons prioritize these choices and retain the exit.
- Freeform input reaching the freeform resolver can map to an existing edge.
  `try <approach>` explicitly routes input to the graph, including during dialogue.
- `choose Set this situation aside` ends this situation without moving the player,
  winning the quest, or escaping an active encounter.
- `next situation` prepares another graph after the old one ends and the set of
  engine capabilities changes (for example, a quest stage advances or an item
  becomes available). It cannot reset an active graph or farm an identical one.

Without an LLM, the engine supplies a deterministic graph. Exact operation text
can also add a supported branch offline: for a visible journal, `try take journal`
can extend a graph that did not already offer that operation. Semantic paraphrase
matching and creative graph planning use the existing configured LLM director.

## Ownership and data

`worldsim/action_graph.py` owns graph validation and execution. A graph contains
an ID, scene ID, start/current node, nodes, edges, an expansion count, and the
operation IDs offered when it was created. Each edge names an operation and its
success and failure destinations. Nodes can reconnect, but cycles are rejected.

Operations are built in code from current state. They contain a canonical action,
optional check/DC, and at most one supported effect. The first version supports
observation/reassessment, taking visible objects, opening visible objects, and
applicable quest-stage fact, NPC recruitment, and choice operations. Quest
operations are restricted to related locations and the active stage; recruitment
requires the target NPC to be present. Encounter locks suppress these mechanical
operations. Targets and availability are rebuilt before execution, so stale
choices cannot apply effects to missing objects or completed quest requirements.

Raw player input stays in `ActionIntent` and `TurnRecord`. An engine-owned
canonical action is used for the existing effect validator; model text cannot
replace the validator's action or provide an arbitrary effect batch. The normal
progression evaluator observes committed quest effects.

`Director.plan_action_graph`, `choose_graph_action`, and `expand_action_graph`
are optional hooks. `LocalLLMDirector` uses task-specific schemas, bounded repair,
and deterministic fallback through its existing client. Graph requests respect
the context budget by failing back when oversized, rather than dropping exact
capability IDs or prerequisite evidence. Initial plans have at most 12 nodes and
20 edges; runtime graphs have at most 24 nodes, 48 edges, and four expansions.

## Avoiding stuck loops

Validation rejects dangling references, duplicate IDs/labels, unreachable states,
terminal states with actions, nonterminal dead ends, and cycles. Checked actions
must have different success and failure destinations. Unchecked actions have
one destination. The engine adds an unconditional situation-exit edge at every
nonterminal node, so a change in world state cannot strand the player when other
edges become unavailable.

The fallback graph moves a failed attempt to a setback state with reassessment
and withdrawal. Reassessment costs a turn and ends this attempt; it does not
award the effect that the failed roll withheld. A generated graph may offer a
more substantive alternate route through other supported operations.

Freeform expansion is a proposal containing one operation ID and outcome
descriptions. The engine adds success/failure nodes and a recovery route, then
validates the whole candidate graph before installing anything. The roll chooses
which new state becomes current. Unsupported and ambiguous actions do not roll,
advance time, or silently fall through to an unrelated mutation.

This is a structural guarantee for each situation, not proof that every campaign
quest remains winnable. A terminal situation can leave an objective unresolved.
Existing explicit commands and dialogue still use their established paths;
campaign-wide reachability analysis and richer capability prerequisites remain
future work. Narrative descriptions are AI-authored and can still be inaccurate;
mechanical effects come only from the engine catalog.

## Persistence and replay

Save schema 6 adds `World.action_graphs`, keyed by scene ID. Schema 5 saves migrate
with an empty collection; earlier migrations still run first. Returning to a
scene restores its graph rather than rerolling a fresh opening. Saves and the
debug state mirror include graphs. Invalid saved graph topology is rejected.

Graph turns and scene-entry records include a full validated
`action_graph_after` snapshot. Replay applies accepted effects and restores that
snapshot without consulting a model or rerolling. Graph transitions and their
mechanical effects roll back together if commit/progression fails. A turn record
is installed before outcome narration; narration failure uses the authoritative
summary and does not repeat the transition.

## Optional Jev selection

`Director.choose_graph_action` is the provider boundary. `worldsim/jev_client.py`
implements it using the direct [TypeSafe API](https://docs.typesafe.ai/api).
The adapter offers opaque IDs for existing edges and specific operations that
could support new branches, plus an explicit unsupported option. A selected
expansion constrains the generative planner to that exact operation.

Set `TYPESAFE_API_KEY` outside source control. `WORLDSIM_GRAPH_SELECTOR=auto`
(the default) selects Jev when that key is present; `llm` disables Jev and `jev`
requires it. Explicit `WORLDSIM_DIRECTOR=mock` always stays offline. Graph planning,
expansion, and narration continue using the configured generative director.

Optional settings:

```text
WORLDSIM_JEV_MODEL=jev-latest
WORLDSIM_JEV_TIMEOUT=15
WORLDSIM_JEV_MIN_CONFIDENCE=0.8
```

The confidence threshold is provisional, not calibrated to this game. Low
confidence, HTTP failures, timeouts, and malformed answers fall back to the
existing interpreter before any mutation. A confident unsupported answer does
not trigger another model to guess. Exact menu choices bypass model selection.
Requests have no automatic retries and do not follow redirects. Error messages
omit HTTP bodies and authentication headers. The System tab reports Jev status,
request counts, and token usage separately from the existing LLM cost estimate;
Jev usage is session-only and is not included in saved cost totals.

Run `python -m worldsim.jev_probe` for one synthetic request without campaign
data. The probe exits nonzero when the expected selection is not accepted,
including a valid but uncertain response. It prints only decision and usage
metadata, never the key.

A single live probe on 2026-09-23 authenticated and returned model
`jev-1.13.0` with 559 input tokens and 35 output tokens. Confidence was 0.18,
so selection deferred to the fallback boundary. This verifies access and response
parsing, not action-selection accuracy or suitability of the default threshold.

## Verification

`tests/test_action_graphs.py` covers successful and failed branches, recovery,
invalid topology, stale targets, encounter locks, semantic matching, unsupported
and supported expansion, expansion limits, effect rollback, narration failure,
quest progression, save migration, replay, re-entry, and TUI choice priority.
The regression suite runs offline with deterministic fixtures and mocked director
responses. `tests/test_jev_client.py` also checks confidence handling, malformed
distributions, bounded HTTP failures, credential redaction, provider selection,
and that an expansion cannot substitute a different operation.
