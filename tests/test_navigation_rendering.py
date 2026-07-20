from __future__ import annotations

from hashlib import sha256
import json

from worldsim.ascii_render import AsciiRenderer
from worldsim.context import ContextSelector
from worldsim.director import MockDirector
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory, CampaignStore
from worldsim.models import (
    DirectorBeat,
    EffectKind,
    SceneMode,
    SceneState,
)


def resolve(state, command: str):
    return state.engine.resolve_command(
        command,
        state.world,
        state.player,
        state.director,
        state.memory,
    )


def route_payload(world) -> list[tuple[object, ...]]:
    return [
        (
            route.id,
            route.origin_id,
            route.destination_id,
            route.kind,
            route.danger,
            [(position.x, position.y) for position in route.path],
        )
        for route in world.routes
    ]


def test_same_seed_produces_same_graph_and_ascii_snapshot() -> None:
    first_engine = WorldEngine(seed=42)
    first_world = first_engine.create_world()
    first_player = first_engine.create_player(
        first_world,
        "Rowan",
        "ranger",
        first_world.locations[0].name,
    )
    second_engine = WorldEngine(seed=42)
    second_world = second_engine.create_world()
    second_player = second_engine.create_player(
        second_world,
        "Rowan",
        "ranger",
        second_world.locations[0].name,
    )
    renderer = AsciiRenderer()

    first_ascii = "\n".join(
        renderer.compose_overworld(
            first_world,
            first_player,
        ).plain_lines()
    )
    second_ascii = "\n".join(
        renderer.compose_overworld(
            second_world,
            second_player,
        ).plain_lines()
    )

    assert route_payload(first_world) == route_payload(second_world)
    assert first_ascii == second_ascii
    assert (
        sha256(first_ascii.encode("ascii")).hexdigest()
        == "b1094ddcc914812960ba451efb13079ac6044d2cf2600b44de3101ba8d5d81a4"
    )


def test_all_named_locations_are_reachable_and_paths_touch_endpoints() -> None:
    engine = WorldEngine(seed=73)
    world = engine.create_world()
    location_ids = {location.id for location in world.locations}
    positions = {
        location.id: location.position
        for location in world.locations
    }

    reachable = engine.navigation.reachable_location_ids(
        world,
        world.locations[0].id,
    )

    assert reachable == location_ids
    assert len(world.routes) >= len(world.locations) - 1
    for route in world.routes:
        assert route.path[0] == positions[route.origin_id]
        assert route.path[-1] == positions[route.destination_id]
        assert all(
            0 <= position.x < world.width
            and 0 <= position.y < world.height
            for position in route.path
        )


def test_landmarks_labels_and_critical_routes_do_not_overlap() -> None:
    engine = WorldEngine(seed=42)
    world = engine.create_world()
    player = engine.create_player(
        world,
        "Rowan",
        "ranger",
        world.locations[0].name,
    )

    composition = AsciiRenderer().compose_overworld(world, player)
    footprints = list(composition.landmark_footprints.values())

    assert len(footprints) == len(world.locations)
    assert all(footprint for footprint in footprints)
    assert sum(map(len, footprints)) == len(
        set().union(*footprints)
    )
    assert not composition.landmark_cells & composition.critical_route_cells
    assert not composition.label_cells & composition.critical_route_cells
    assert not composition.label_cells & composition.landmark_cells


def test_named_travel_uses_graph_and_narrates_the_destination(
    game_state,
) -> None:
    state = game_state
    destination = state.world.locations[1]
    state.engine.ensure_navigation(state.world)
    state.director.freeform_beat = DirectorBeat(
        title="Travel",
        narration="The route is proposed but not yet authoritative.",
    )
    state.director.outcome_narration = (
        "You follow the mapped road and arrive at the Observatory."
    )

    result = resolve(state, "travel to Observatory")

    record = state.world.turn_records[-1]
    assert state.player.position == destination.position
    assert state.world.active_scene is not None
    assert state.world.active_scene.location_id == destination.id
    assert any(
        effect.kind == EffectKind.LOCATION_TRANSITION
        for effect in record.outcome.accepted_effects
    )
    assert state.director.narration_observations[-1]["location_id"] == (
        destination.id
    )
    assert "arrive at the Observatory" in result.message


def test_named_travel_rejects_a_location_without_a_direct_route() -> None:
    engine = WorldEngine(seed=42)
    world = engine.create_world()
    player = engine.create_player(
        world,
        "Rowan",
        "ranger",
        world.locations[0].name,
    )
    origin = world.locations[0]
    neighbor_ids = set(
        engine.navigation.neighbor_ids(world, origin.id)
    )
    destination = next(
        location
        for location in world.locations
        if location.id != origin.id
        and location.id not in neighbor_ids
    )

    engine.resolve_command(
        f"travel to {destination.name}",
        world,
        player,
        MockDirector(seed=42),
        CampaignMemory(),
    )

    record = world.turn_records[-1]
    assert player.position == origin.position
    assert any(
        "not directly connected" in rejected.reason
        for rejected in record.outcome.rejected_effects
    )


def test_context_exposes_only_routes_from_the_current_location(
    game_state,
) -> None:
    state = game_state
    state.engine.ensure_navigation(state.world)

    selection = ContextSelector().select(
        "interpret_freeform_action",
        state.world,
        player=state.player,
        location=state.world.locations[0],
        npc=state.world.npcs[0],
        action="travel to Observatory",
    )

    assert selection.context["available_routes"] == [
        {
            "route_id": (
                "route:location-market:location-observatory"
            ),
            "destination_id": "location-observatory",
            "destination_name": "Observatory",
            "kind": "road",
            "danger": 2,
        }
    ]


def test_local_scene_renderer_has_independent_gameplay_overlays(
    game_state,
) -> None:
    state = game_state
    state.world.active_scene = SceneState(
        id="scene:market:local:cellar",
        mode=SceneMode.LOCAL,
        location_id="location-market",
        parent_scene_id="scene:location-market",
        area_name="Market Cellar",
        local_npc_id="npc-witness",
        hazard="rising water",
    )
    state.world.scene_objects["0,0"] = ["iron door"]

    composition = AsciiRenderer().compose_local(
        state.world,
        state.player,
        24,
        12,
    )
    roles = {cell.role for cell in composition.cells.values()}
    rendered = "\n".join(composition.plain_lines())

    assert composition.width == 24
    assert composition.height == 12
    assert {
        "player",
        "npc",
        "object",
        "hazard",
        "quest",
        "exit",
        "local_landmark",
        "local_path",
    } <= roles
    assert not composition.landmark_cells & composition.critical_route_cells
    rendered.encode("ascii")


def test_routes_round_trip_and_version_four_regenerates_graph(
    tmp_path,
) -> None:
    engine = WorldEngine(seed=91)
    world = engine.create_world()
    player = engine.create_player(
        world,
        "Rowan",
        "ranger",
        world.locations[0].name,
    )
    store = CampaignStore(tmp_path / "campaign.json")
    store.save(world, player, CampaignMemory())

    loaded = store.load()
    assert loaded is not None
    loaded_world, _, _ = loaded
    assert route_payload(loaded_world) == route_payload(world)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["schema_version"] = 4
    payload["world"].pop("routes")
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = store.load()
    assert migrated is not None
    migrated_world, _, _ = migrated
    assert migrated_world.routes == []

    engine.ensure_navigation(migrated_world)
    assert engine.navigation.reachable_location_ids(
        migrated_world,
        migrated_world.locations[0].id,
    ) == {location.id for location in migrated_world.locations}
