# Worldsim

A terminal-first adventure world sim with a deterministic game engine and a pluggable "dungeon master" director layer.

This prototype is built around one design rule:

- The engine owns rules, rolls, HP, movement, map topology, and state mutation.
- The director owns names, hooks, atmosphere, scene framing, and narrative possibilities.

That keeps the game coherent while still allowing an LLM to improvise.

## What Exists

- Colorized TUI built with `textual`
- Top tabs, bordered panels, and a command bar closer to the reference layout
- Procedural world map with biome coloring
- Named locations and NPCs
- Player creation with archetype selection
- Command loop for moving, exploring, talking, resting, fighting, and waiting
- Living world events that continue between turns
- Local campaign persistence in `data/campaign.json`
- Compact memory entries for locations, NPCs, hooks, battles, and discoveries
- A `MockDirector` that behaves like a local DM
- A `Director` interface where a real LLM backend can be plugged in later
- Optional local LLM director using JSON prompts and OpenAI-compatible chat completions
- Versioned campaign saves with migrations from schema versions 0 and 1
- Persisted, replayable turn records for freeform actions
- Deterministic pytest regression coverage for state safety and turn resolution

## Run

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

## Commands

- `north` / `south` / `east` / `west`
- `move north`
- `look`
- `explore`
- `talk`
- `say <message>`
- `attack`
- `rest`
- `wait`
- `help`
- `quit`
- `end conversation`

You can also type freeform actions, such as `take journal`, `read the inscription`,
or `open the rusted box`. The director proposes a typed intent and effects
without deciding the outcome. The engine rolls, validates and commits the
effects transactionally, then asks the director to narrate the known result.

During an active conversation, bare prose is dialogue. Global commands such as
`quit`, `help`, `inventory`, and `end conversation` remain commands without a
leading slash.

## Architecture

`worldsim/models.py`

- Game state, locations, events, NPCs, director responses, persistent scenes,
  encounters, dialogue state, quest conditions, and clock triggers

`worldsim/engine.py`

- World generation
- Command parsing
- Deterministic resolution of movement, combat, rest, and discovery
- Freeform orchestration through typed intent, check, outcome, and turn records

`worldsim/turn_resolution.py`

- Transactional effect validation, commit, rollback, and replay

`worldsim/turn_effects.py`

- Focused freeform-effect preparation, legality policy, and commit handlers

`worldsim/director.py`

- `Director` base class
- `MockDirector` for local play
- `LocalLLMDirector` for local OpenAI-compatible LLM servers
- A prompt contract showing how a real LLM should speak to the engine

`worldsim/schemas.py`

- JSON payloads sent to the director layer
- JSON response schemas for narration, action intents, and legacy action beats
- Response parsing into engine-owned models

`worldsim/llm_client.py`

- Minimal dependency-free client for OpenAI-compatible `/v1/chat/completions` endpoints

`worldsim/area.py`

- Area choice, hazard, theme, and scene helpers used by the TUI

`worldsim/memory.py`

- Compact long-term memory store
- Versioned local save/load with version-0/version-1 migrations and atomic replacement
- Retrieval of relevant memories for the director layer

`worldsim/tui.py`

- `textual` app shell, tabs, command bar, and live panel updates

`worldsim/worldsim.tcss`

- Layout and color styling for the terminal UI

`worldsim/game.py`

- Launcher for the `textual` app

## Wiring In A Real LLM

The director never directly mutates state. For freeform actions it returns
structured intent without an outcome, for example:

```json
{
  "title": "Force the Shrine Door",
  "stakes": "The warped door may give way or draw attention.",
  "check_kind": "exploration_check",
  "difficulty": 11,
  "tags": ["exploration"],
  "choices": ["try the latch", "look for another entrance"],
  "proposed_effects": [
    {
      "kind": "object_status",
      "target_id": "shrine door",
      "value": "open",
      "amount": 0,
      "condition": "success",
      "flag": false
    }
  ]
}
```

The engine then:

- validates the requested check and effects
- resolves the engine-owned roll
- commits the accepted effect batch or rolls it back
- evaluates progression and encounter consequences
- creates a persisted `TurnRecord`
- gives the resolved record and updated state to the narration pass

That is the handoff boundary between "LLM as DM" and "code as rules engine."

The architecture is currently in a phased migration. Phase 2 now supplies the
unified intent-to-check-to-reducer-to-outcome-to-narration pipeline for
freeform actions. Explicit commands remain compatible through the typed check
adapter; moving persistent area state into the pipeline remains Phase 3 work. See
[`docs/architecture-audit.md`](docs/architecture-audit.md) for the verified
baseline findings, ownership model, phase gates, and compatibility risks.

The demo uses an OpenAI-compatible chat completions endpoint. If `OPENAI_API_KEY`
or `WORLDSIM_LLM_API_KEY` is set and `WORLDSIM_LLM_BASE_URL` is not set, it uses
OpenAI's API by default:

```bash
export OPENAI_API_KEY=sk-...
python main.py
```

By default, that selects `gpt-5.2-chat-latest`, the current ChatGPT chat model
listed in the OpenAI model docs. You can override it:

```bash
export WORLDSIM_LLM_MODEL=gpt-5.2
python main.py
```

Your ChatGPT login or subscription does not automatically authenticate a local
Python app. For OpenAI-hosted models, create and set an API key. API usage is
billed separately from most ChatGPT plans.

To use a locally hosted OpenAI-compatible chat server instead:

```bash
export WORLDSIM_LLM_BASE_URL=http://localhost:8080/v1
export WORLDSIM_LLM_MODEL=Qwen2.5-7B-Coder
python main.py
```

On Windows `cmd.exe`, use `set` instead of `export`. To force the deterministic
mock instead, set `WORLDSIM_DIRECTOR=mock`. Streaming is enabled by default; set
`WORLDSIM_LLM_STREAM=0` to use one blocking response.

With `llama.cpp`, start `llama-server` with an OpenAI-compatible endpoint first. A typical shape is:

```bash
llama-server -m path/to/Qwen2.5-7B-Coder.gguf --host 127.0.0.1 --port 8080 -c 8192
```

Then probe the integration before opening the TUI:

```bash
python -m worldsim.llm_probe
```

The System tab shows the active director and reports the last LLM fallback error.
During LLM commands, the console streams just the narration portion of the JSON response and then replaces it with the parsed final beat.

When OpenAI streaming is active, the app requests token usage metadata and shows
session totals plus estimated cost in the top bar and System tab. The estimate is
based on reported prompt/completion tokens and a local pricing table. To override
pricing for another model or changed rates, set:

```bash
export WORLDSIM_LLM_INPUT_COST_PER_1M=1.75
export WORLDSIM_LLM_OUTPUT_COST_PER_1M=14
export WORLDSIM_LLM_CACHED_INPUT_COST_PER_1M=0.175
```

The app sends JSON with:

- `task`: the director operation, such as `generate_world_details`, `describe_location`, or `respond_to_action`
- `context`: compact world, player, location, NPC, action, memory, hook, and event data
- `response_schema`: the exact JSON object shape the model must return

If the local model is unavailable or returns invalid JSON, the app falls back to `MockDirector` for that beat.

## Memory Model

The game now keeps two different forms of persistence:

- Exact campaign state in `data/campaign.json`
- Current structured state mirror in `data/state.json`
- Compressed memory entries for important facts, so the director can pull a few relevant reminders instead of replaying the full log
- Per-session debug logs in `data/debug/session-*.jsonl`

That is the basis for a scalable LLM-backed campaign loop: retrieval first, full transcript never.

## Debug Logs

Each app run writes a new JSONL file under `data/debug`. These logs include:

- full LLM request payloads sent to `/v1/chat/completions`
- raw streamed SSE events and assembled text
- director task prompts and parsed JSON payloads
- fallback errors when parsing or model calls fail

The System tab shows the exact debug log path for the current session.

`data/state.json` is a compact mirror of the current campaign state intended for debugging and LLM context inspection. It includes current visible objects, object status records such as `in_inventory` or `destroyed`, recent state facts, inventory, and recent conversations.

## Save Schema

`data/campaign.json` now includes `schema_version: 2`. Existing saves without a
version are treated as version 0; schema version 1 saves are also migrated in
memory when loaded. The migrations backfill stable location/NPC IDs, persistent
scene state, a structured encounter for legacy combat locks, and an empty turn
history where one did not exist. The next save writes version 2. Saves from a
newer unsupported schema fail with an explicit error instead of being misread.

Both `campaign.json` and the debug `state.json` mirror are written through a
temporary file followed by atomic replacement.

## Testing and Debugging

Install runtime and test dependencies, then run:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
python -m compileall -q main.py worldsim tests
```

The deterministic suite covers the Phase 1 safety boundary plus Phase 2
pre-roll/post-roll ordering, transactional rollback, successful and failed door
effects, persisted turn records, replay without rerolling, explicit-command
compatibility, and save migration/round-tripping.

When debugging a live LLM turn, compare:

1. `data/debug/session-*.jsonl` for prompts, streamed responses, parsed payloads,
   and fallback errors;
2. `data/state.json` for the latest authoritative state mirror;
3. `data/campaign.json` for the versioned persistent campaign.
