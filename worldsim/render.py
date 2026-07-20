from __future__ import annotations

import textwrap

from worldsim.ascii_render import AsciiRenderer
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory
from worldsim.models import Player, SceneMode, World


def render_dashboard(
    world: World,
    player: Player,
    engine: WorldEngine,
    memory: CampaignMemory,
    last_message: str,
) -> str:
    active_location = engine.location_at(world, player.position)
    active_npc = engine.npc_at(active_location, world)
    left_width = 62
    right_width = 38
    total_width = left_width + right_width + 3

    header = f"WORLDSIM ALPHA  |  Seed {world.seed}  |  Tick {world.tick}  |  Weather: {world.weather}"
    nav = "[1] WORLD  [2] ADVENTURE  [3] LOG  [4] MEMORY"

    map_lines = _render_map(world, player)
    location_lines = [
        f"Position: {player.position.x},{player.position.y}",
        f"Biome: {engine.biome_at(world, player.position).value}",
    ]
    if active_location is not None:
        location_lines.extend(
            [
                f"Location: {active_location.name}",
                f"Danger: {active_location.danger}/9",
                active_location.summary,
            ]
        )
    else:
        location_lines.append("Location: Untamed frontier")

    if active_npc is not None:
        location_lines.append(f"NPC: {active_npc.name}, {active_npc.disposition} {active_npc.role}")

    map_panel = _panel("WORLD MAP", map_lines + [""] + location_lines, left_width, 24)
    event_lines = [f"[{event.tick}] {event.text}" for event in world.recent_events]
    log_panel = _panel("SIMULATION LOG", event_lines, left_width, 10)

    if active_location is not None:
        route_names = _route_names(world, engine, active_location.id)
        selected_region_lines = [
            active_location.name,
            active_location.summary,
            f"Terrain: {active_location.biome.value}",
            f"Danger Rating: {active_location.danger}/9",
            "Routes: " + (", ".join(route_names) or "none"),
        ]
    else:
        selected_region_lines = [
            "Untamed frontier",
            "No settlement controls this ground.",
            f"Terrain: {engine.biome_at(world, player.position).value}",
        ]

    hooks_panel = _panel("QUESTS", _quest_lines(world), right_width, 12)
    alerts_panel = _panel("ALERTS", world.alerts or ["No immediate alerts."], right_width, 8)
    player_panel = _panel("PLAYER", _player_lines(player), right_width, 10)
    region_panel = _panel("SELECTED REGION", selected_region_lines, right_width, 12)
    summary_panel = _panel("WORLD SUMMARY", _summary_lines(world, engine), right_width, 10)
    memory_panel = _panel("MEMORY", _memory_lines(memory, world, player), right_width, 10)

    sections = [
        header[:total_width],
        nav[:total_width],
        "-" * total_width,
        *_merge_columns(map_panel, region_panel),
        *_merge_columns(log_panel, hooks_panel),
        *_merge_columns([""] * len(player_panel), player_panel),
        *_merge_columns([""] * len(alerts_panel), alerts_panel),
        *_merge_columns([""] * len(summary_panel), summary_panel),
        *_merge_columns([""] * len(memory_panel), memory_panel),
        *_panel("DIRECTOR", [last_message], total_width, 6),
        f"Commands: move/look/explore/talk/attack/rest/wait/help/quit".ljust(total_width),
    ]
    return "\n".join(sections)


def _render_map(world: World, player: Player) -> list[str]:
    renderer = AsciiRenderer()
    if (
        world.active_scene is not None
        and world.active_scene.mode == SceneMode.LOCAL
    ):
        return renderer.compose_local(world, player, 29, 14).plain_lines()
    composition = renderer.compose_overworld(world, player)
    view_width = min(29, world.width)
    view_height = min(14, world.height)
    start_x = max(
        0,
        min(
            player.position.x - view_width // 2,
            world.width - view_width,
        ),
    )
    start_y = max(
        0,
        min(
            player.position.y - view_height // 2,
            world.height - view_height,
        ),
    )
    return composition.plain_lines(
        start_x,
        start_y,
        view_width,
        view_height,
    )


def _player_lines(player: Player) -> list[str]:
    return [
        f"Name: {player.name}",
        f"Archetype: {player.archetype.title()}",
        f"Homeland: {player.homeland}",
        f"HP: {player.hp}/{player.max_hp}",
        f"Gold: {player.gold}",
        f"XP: {player.xp}",
        "Inventory: " + ", ".join(player.inventory),
    ]


def _summary_lines(world: World, engine: WorldEngine) -> list[str]:
    counts = engine.summary_counts(world)
    return [
        f"Campaign: {world.campaign_status.value}",
        f"Age: {world.tick} turns",
        f"Locations: {counts['locations']}",
        f"Routes: {counts['routes']}",
        f"NPCs: {counts['npcs']}",
        f"Events tracked: {counts['events']}",
        f"Hooks: {counts['hooks']}",
        f"Stability: {world.stability}%",
    ]


def _route_names(
    world: World,
    engine: WorldEngine,
    location_id: str,
) -> list[str]:
    locations = {
        location.id: location.name
        for location in world.locations
    }
    return [
        locations[destination_id]
        for destination_id in engine.navigation.neighbor_ids(
            world,
            location_id,
        )
        if destination_id in locations
    ]


def _quest_lines(world: World) -> list[str]:
    if not world.quests:
        return world.quest_hooks or ["No active hooks."]
    active = next((quest for quest in world.quests if quest.id == world.active_quest_id), world.quests[0])
    stage = active.stages[min(active.current_stage, len(active.stages) - 1)] if active.stages else active.goal
    lines = [
        f"Active: {active.title}",
        stage.description if hasattr(stage, "description") else str(stage),
        f"Status: {active.status.value}",
    ]
    active_clocks = [clock for clock in world.clocks if clock.status == "active"]
    if active_clocks:
        lines.append("")
        lines.append("Clocks:")
        lines.extend(f"{clock.title}: {clock.value}/{clock.max_value}" for clock in active_clocks[:3])
    return lines


def _memory_lines(memory: CampaignMemory, world: World, player: Player) -> list[str]:
    scope = memory.relevant_context(world, player, limit=2)
    latest = memory.latest_lines(limit=2)
    lines: list[str] = []
    seen: set[str] = set()
    for item in scope + latest:
        if item in seen:
            continue
        seen.add(item)
        lines.append(item)
    return lines or ["No persistent memories yet."]


def _panel(title: str, lines: list[str], width: int, height: int) -> list[str]:
    usable = width - 4
    rendered = [f"+- {title[: usable - 1].ljust(usable - 1)}-+"]
    body: list[str] = []
    for line in lines:
        wrapped = textwrap.wrap(line, usable) or [""]
        body.extend(wrapped)
    for row in body[: height - 2]:
        rendered.append(f"| {row.ljust(usable)} |")
    while len(rendered) < height - 1:
        rendered.append(f"| {' ' * usable} |")
    rendered.append(f"+-{'-' * usable}-+")
    return rendered


def _merge_columns(left: list[str], right: list[str]) -> list[str]:
    height = max(len(left), len(right))
    left_pad = left + [""] * (height - len(left))
    right_pad = right + [""] * (height - len(right))
    return [f"{left_pad[index]}   {right_pad[index]}" for index in range(height)]
