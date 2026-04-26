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

You can also type freeform actions, such as `take journal`, `read the inscription`, or `open the rusted box`. The director narrates the attempt, while the engine decides what inventory or world state changes are allowed.

## Architecture

`worldsim/models.py`

- Game state, locations, events, NPCs, and director responses

`worldsim/engine.py`

- World generation
- Command parsing
- Deterministic resolution of movement, combat, rest, and discovery

`worldsim/director.py`

- `Director` base class
- `MockDirector` for local play
- `LocalLLMDirector` for local OpenAI-compatible LLM servers
- A prompt contract showing how a real LLM should speak to the engine

`worldsim/schemas.py`

- JSON payloads sent to the director layer
- JSON response schemas for narration and action beats
- Response parsing into engine-owned models

`worldsim/llm_client.py`

- Minimal dependency-free client for local `/v1/chat/completions` endpoints

`worldsim/area.py`

- Area choice, hazard, theme, and scene helpers used by the TUI

`worldsim/memory.py`

- Compact long-term memory store
- Local save/load for campaign state
- Retrieval of relevant memories for the director layer

`worldsim/tui.py`

- `textual` app shell, tabs, command bar, and live panel updates

`worldsim/worldsim.tcss`

- Layout and color styling for the terminal UI

`worldsim/game.py`

- Launcher for the `textual` app

## Wiring In A Real LLM

The director should never directly mutate state. It should return structured intent, for example:

```json
{
  "title": "Ruined Shrine",
  "narration": "A broken shrine leans out of the mist.",
  "mechanical_request": "exploration_check",
  "difficulty": 11,
  "tags": ["mystery", "ancient"],
  "follow_up_hook": "Someone still leaves fresh candles here."
}
```

The engine then decides:

- whether a check is needed
- what dice to roll
- whether the player takes damage
- what loot or XP is granted
- how the world state changes

That is the handoff boundary between "LLM as DM" and "code as rules engine."

The demo now uses a locally hosted OpenAI-compatible chat server by default for testing:

```bash
set WORLDSIM_LLM_BASE_URL=http://localhost:8080/v1
set WORLDSIM_LLM_MODEL=Qwen2.5-7B-Coder
python main.py
```

On macOS/Linux, use `export` instead of `set`. To force the deterministic mock instead, set `WORLDSIM_DIRECTOR=mock`. Streaming is enabled by default; set `WORLDSIM_LLM_STREAM=0` to use one blocking response.

With `llama.cpp`, start `llama-server` with an OpenAI-compatible endpoint first. A typical shape is:

```bash
llama-server -m path/to/Qwen2.5-7B-Coder.gguf --host 127.0.0.1 --port 8080 -c 8192
```

Then probe the integration before opening the TUI:

```bash
python -m worldsim.llm_probe
```

The System tab shows the active director and reports the last LLM fallback error.
During LLM commands, the Director panel streams the raw JSON response as it arrives and then replaces it with the parsed narration once the response is complete.

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
