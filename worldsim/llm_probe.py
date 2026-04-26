from __future__ import annotations

import os
from pathlib import Path

from worldsim.debug import DebugLogger
from worldsim.director import LocalLLMDirector, MockDirector
from worldsim.engine import WorldEngine
from worldsim.llm_client import LLMClient
from worldsim.memory import CampaignMemory


def main() -> None:
    os.environ.setdefault("WORLDSIM_DIRECTOR", "llm")
    engine = WorldEngine()
    debug_logger = DebugLogger.create(Path(__file__).resolve().parent.parent / "data")
    director = LocalLLMDirector(LLMClient.from_env(debug_logger), MockDirector(engine.seed), debug_logger)
    print(f"Debug log: {debug_logger.path.resolve()}")
    print(director.status_line)
    print("Requesting world details...")
    world = engine.create_world(director)
    print(director.status_line)
    if director.last_payload is not None:
        print(f"World detail keys: {', '.join(director.last_payload.keys())}")
        locations = director.last_payload.get("locations")
        hooks = director.last_payload.get("quest_hooks")
        if isinstance(locations, list) and locations:
            print(f"First generated location payload: {locations[0]}")
        if isinstance(hooks, list):
            print(f"Generated hooks payload count: {len(hooks)}")
            if hooks:
                print(f"First generated hook payload: {hooks[0]}")
    print(f"Starting weather: {world.weather}")
    print(f"Starting hooks: {' | '.join(world.quest_hooks[:3])}")
    print(f"Starting locations: {', '.join(location.name for location in world.locations[:4])}")
    player = engine.create_player(world, "Rowan", "ranger", "Northreach")
    memory = CampaignMemory()
    memory.remember_world_state(world, player)
    location = engine.location_at(world, player.position)

    print("Requesting scene description...")
    stream_chunks: list[str] = []
    director.on_stream_delta = stream_chunks.append
    description = director.describe_location(
        world,
        player,
        location,
        engine.npc_at(location, world),
        memory.relevant_context(world, player, location.name if location else None),
    )
    director.on_stream_delta = None
    print(description)
    print(f"Scene stream chunks: {len(stream_chunks)}")
    print(director.status_line)

    if director.last_used_fallback:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
