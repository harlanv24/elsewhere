from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256

from worldsim.models import (
    Biome,
    Location,
    Player,
    Position,
    QuestStatus,
    SceneMode,
    World,
)


@dataclass(frozen=True)
class RenderCell:
    token: str
    role: str
    layer: int


@dataclass
class MapComposition:
    width: int
    height: int
    cells: dict[Position, RenderCell] = field(default_factory=dict)
    landmark_cells: set[Position] = field(default_factory=set)
    landmark_footprints: dict[str, set[Position]] = field(default_factory=dict)
    critical_route_cells: set[Position] = field(default_factory=set)
    label_cells: set[Position] = field(default_factory=set)

    def set_cell(
        self,
        position: Position,
        token: str,
        role: str,
        layer: int,
    ) -> None:
        if not self.contains(position):
            return
        current = self.cells.get(position)
        if current is not None and current.layer > layer:
            return
        self.cells[position] = RenderCell(
            token=token[:2].ljust(2),
            role=role,
            layer=layer,
        )

    def cell(self, position: Position) -> RenderCell:
        return self.cells.get(position, RenderCell("  ", "empty", 0))

    def plain_lines(
        self,
        start_x: int = 0,
        start_y: int = 0,
        width: int | None = None,
        height: int | None = None,
    ) -> list[str]:
        render_width = min(width or self.width, self.width - start_x)
        render_height = min(height or self.height, self.height - start_y)
        return [
            "".join(
                self.cell(Position(x, y)).token
                for x in range(start_x, start_x + render_width)
            )
            for y in range(start_y, start_y + render_height)
        ]

    def contains(self, position: Position) -> bool:
        return (
            0 <= position.x < self.width
            and 0 <= position.y < self.height
        )


class AsciiRenderer:
    """Pure deterministic compositor for overworld and local-scene maps."""

    TERRAIN_TOKENS = {
        Biome.WATER: ("~~", "~.", ".~", "=="),
        Biome.PLAIN: ("..", " .", " ,", ",."),
        Biome.FOREST: ("tt", "YY", "||", "t|"),
        Biome.HILL: ("^^", "n^", "^^", "~^"),
        Biome.MOUNTAIN: ("/\\", "A^", "MM", "/^"),
        Biome.SWAMP: (";;", "::", ",;", ";,"),
    }
    LANDMARK_SPRITES = (
        (
            ("/\\", "/\\"),
            ("||", "[]"),
        ),
        (
            ("^^", "^^"),
            ("| ", "[]"),
        ),
        (
            ("+-", "-+"),
            ("| ", "[]"),
        ),
    )

    def compose_overworld(
        self,
        world: World,
        player: Player,
    ) -> MapComposition:
        composition = MapComposition(world.width, world.height)
        for y in range(world.height):
            for x in range(world.width):
                position = Position(x, y)
                token, role = self._terrain_cell(world, position)
                composition.set_cell(position, token, role, 10)

        landmark_placements = self._landmark_placements(world)
        composition.landmark_footprints = {
            location_id: set(tokens)
            for location_id, tokens in landmark_placements.items()
        }
        landmark_tokens = {
            position: token
            for tokens in landmark_placements.values()
            for position, token in tokens.items()
        }
        composition.landmark_cells = set(landmark_tokens)
        self._compose_routes(world, composition)
        for position, token in landmark_tokens.items():
            composition.set_cell(position, token, "landmark", 40)
        self._compose_labels(world, composition)
        self._compose_overlays(world, player, composition)
        return composition

    def compose_local(
        self,
        world: World,
        player: Player,
        width: int = 28,
        height: int = 14,
    ) -> MapComposition:
        width = max(12, width)
        height = max(8, height)
        composition = MapComposition(width, height)
        variant = self._stable_int(
            world.seed,
            world.active_scene.id if world.active_scene is not None else "local",
        )
        for y in range(height):
            for x in range(width):
                position = Position(x, y)
                border = x in {0, width - 1} or y in {0, height - 1}
                if border:
                    composition.set_cell(position, "##", "local_wall", 10)
                else:
                    token = (". ", ", ", "  ")[
                        (x * 7 + y * 11 + variant) % 3
                    ]
                    composition.set_cell(position, token, "local_floor", 10)

        center_y = height // 2
        route_cells = {
            Position(x, center_y)
            for x in range(1, width - 1)
        }
        landmark_cells = {
            Position(width // 2, center_y - 1),
            Position(width // 2 + 1, center_y - 1),
        }
        composition.landmark_cells = landmark_cells
        composition.landmark_footprints = {"local": set(landmark_cells)}
        composition.critical_route_cells = route_cells - landmark_cells
        for position in composition.critical_route_cells:
            composition.set_cell(position, "==", "local_path", 20)
        for position in sorted(
            landmark_cells,
            key=lambda item: (item.y, item.x),
        ):
            composition.set_cell(position, "[]", "local_landmark", 40)

        composition.set_cell(Position(1, center_y), "<>", "exit", 70)
        composition.set_cell(
            Position(width // 2, center_y + 2),
            "@ ",
            "player",
            100,
        )

        scene = world.active_scene
        if scene is not None and scene.local_npc_id is not None:
            composition.set_cell(
                Position(width // 2 + 3, center_y + 1),
                "& ",
                "npc",
                90,
            )
        if scene is not None and scene.hazard:
            composition.set_cell(
                Position(width // 2 - 3, center_y - 1),
                "!!",
                "hazard",
                80,
            )
        objects = list(
            world.scene_objects.get(
                f"{player.position.x},{player.position.y}",
                [],
            )
        )
        for index, _ in enumerate(objects[:4]):
            composition.set_cell(
                Position(width // 2 - 2 + index, center_y + 3),
                "? ",
                "object",
                80,
            )
        if self._current_location_is_quest_relevant(world):
            composition.set_cell(
                Position(width // 2 + 2, center_y - 2),
                "!?",
                "quest",
                85,
            )
        return composition

    def compose_for_scene(
        self,
        world: World,
        player: Player,
        local_width: int = 28,
        local_height: int = 14,
    ) -> MapComposition:
        if (
            world.active_scene is not None
            and world.active_scene.mode == SceneMode.LOCAL
        ):
            return self.compose_local(
                world,
                player,
                local_width,
                local_height,
            )
        return self.compose_overworld(world, player)

    def _terrain_cell(
        self,
        world: World,
        position: Position,
    ) -> tuple[str, str]:
        biome = world.tiles[position.y][position.x]
        neighbors = self._neighbor_biomes(world, position)
        variant = (
            position.x * 17
            + position.y * 31
            + world.seed
        ) % 4
        if biome == Biome.WATER:
            water_neighbors = sum(item == Biome.WATER for item in neighbors)
            if any(item != Biome.WATER for item in neighbors):
                return "}~", "coast"
            if water_neighbors <= 2:
                return "==", "river"
            return self.TERRAIN_TOKENS[biome][variant], "water"
        if any(
            item not in {biome, Biome.WATER}
            for item in neighbors
        ):
            return self.TERRAIN_TOKENS[biome][variant], "terrain_boundary"
        return self.TERRAIN_TOKENS[biome][variant], biome.name.casefold()

    def _landmark_placements(
        self,
        world: World,
    ) -> dict[str, dict[Position, str]]:
        placements: dict[str, dict[Position, str]] = {}
        reserved: set[Position] = set()
        for location in sorted(world.locations, key=lambda item: item.id):
            sprite = self.LANDMARK_SPRITES[
                self._stable_int(world.seed, location.id)
                % len(self.LANDMARK_SPRITES)
            ]
            proposed = {
                Position(
                    location.position.x + column - 1,
                    location.position.y + row - 1,
                ): token
                for row, sprite_row in enumerate(sprite)
                for column, token in enumerate(sprite_row)
                if 0 <= location.position.x + column - 1 < world.width
                and 0 <= location.position.y + row - 1 < world.height
            }
            if not proposed or set(proposed) & reserved:
                proposed = {location.position: "[]"}
            accepted: dict[Position, str] = {}
            for position, token in proposed.items():
                if position in reserved:
                    continue
                accepted[position] = token
                reserved.add(position)
            placements[location.id] = accepted
        return placements

    def _compose_routes(
        self,
        world: World,
        composition: MapComposition,
    ) -> None:
        route_kinds: dict[Position, set[str]] = {}
        for route in world.routes:
            for position in route.path:
                if position in composition.landmark_cells:
                    continue
                route_kinds.setdefault(position, set()).add(route.kind)
        composition.critical_route_cells = set(route_kinds)
        route_cells = set(route_kinds)
        for position, kinds in route_kinds.items():
            if "ferry" in kinds:
                token = "=="
                role = "ferry"
            elif "trail" in kinds:
                token = "::"
                role = "trail"
            else:
                token = self._road_token(position, route_cells)
                role = "road"
            composition.set_cell(position, token, role, 30)

    def _road_token(
        self,
        position: Position,
        route_cells: set[Position],
    ) -> str:
        horizontal = (
            Position(position.x - 1, position.y) in route_cells
            or Position(position.x + 1, position.y) in route_cells
        )
        vertical = (
            Position(position.x, position.y - 1) in route_cells
            or Position(position.x, position.y + 1) in route_cells
        )
        if horizontal and vertical:
            return "++"
        if vertical:
            return "||"
        return "--"

    def _compose_labels(
        self,
        world: World,
        composition: MapComposition,
    ) -> None:
        reserved = (
            set(composition.landmark_cells)
            | set(composition.critical_route_cells)
        )
        for location in sorted(world.locations, key=lambda item: item.id):
            chunks = self._label_chunks(location.name)
            for positions in self._label_candidates(
                location,
                len(chunks),
            ):
                if (
                    all(composition.contains(position) for position in positions)
                    and not set(positions) & reserved
                    and not set(positions) & composition.label_cells
                ):
                    for position, token in zip(positions, chunks):
                        composition.set_cell(position, token, "label", 50)
                    composition.label_cells.update(positions)
                    break

    def _label_candidates(
        self,
        location: Location,
        length: int,
    ) -> list[list[Position]]:
        x = location.position.x
        y = location.position.y
        starts = [
            Position(x + 1, y),
            Position(x + 1, y + 1),
            Position(x + 1, y - 1),
            Position(x - length, y + 1),
            Position(x - length, y - 1),
        ]
        return [
            [
                Position(start.x + offset, start.y)
                for offset in range(length)
            ]
            for start in starts
        ]

    def _compose_overlays(
        self,
        world: World,
        player: Player,
        composition: MapComposition,
    ) -> None:
        active = next(
            (
                quest
                for quest in world.quests
                if quest.id == world.active_quest_id
                and quest.status == QuestStatus.ACTIVE
            ),
            None,
        )
        related_ids = (
            set(active.related_locations)
            if active is not None
            else set()
        )
        for location in world.locations:
            if location.id in related_ids:
                composition.set_cell(location.position, "!?", "quest", 70)
        locations_by_id = {
            location.id: location
            for location in world.locations
        }
        for npc in world.npcs:
            location = locations_by_id.get(npc.location_id or "")
            if location is not None:
                composition.set_cell(location.position, "& ", "npc", 80)
        for position_key, objects in world.scene_objects.items():
            if not objects:
                continue
            position = self._parse_position(position_key)
            if position is not None:
                composition.set_cell(position, "? ", "object", 82)
        if world.active_scene is not None and world.active_scene.hazard:
            location = locations_by_id.get(
                world.active_scene.location_id or ""
            )
            if location is not None:
                composition.set_cell(location.position, "!!", "hazard", 85)
        composition.set_cell(player.position, "@ ", "player", 100)

    def _neighbor_biomes(
        self,
        world: World,
        position: Position,
    ) -> list[Biome]:
        return [
            world.tiles[y][x]
            for x, y in (
                (position.x - 1, position.y),
                (position.x + 1, position.y),
                (position.x, position.y - 1),
                (position.x, position.y + 1),
            )
            if 0 <= x < world.width and 0 <= y < world.height
        ]

    def _label_chunks(self, name: str) -> list[str]:
        compact = " ".join(name.split())[:16]
        return [
            compact[index : index + 2].ljust(2)
            for index in range(0, len(compact), 2)
        ] or ["??"]

    def _parse_position(self, value: str) -> Position | None:
        try:
            x, y = value.split(",", maxsplit=1)
            return Position(int(x), int(y))
        except (TypeError, ValueError):
            return None

    def _current_location_is_quest_relevant(self, world: World) -> bool:
        scene = world.active_scene
        if scene is None or scene.location_id is None:
            return False
        active = next(
            (
                quest
                for quest in world.quests
                if quest.id == world.active_quest_id
            ),
            None,
        )
        return (
            active is not None
            and scene.location_id in active.related_locations
        )

    def _stable_int(self, seed: int, *parts: str) -> int:
        token = ":".join((str(seed), *parts)).encode("utf-8")
        return int.from_bytes(sha256(token).digest()[:8], "big")
