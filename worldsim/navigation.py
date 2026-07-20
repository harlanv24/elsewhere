from __future__ import annotations

from collections import deque
from hashlib import sha256
import heapq

from worldsim.models import Biome, Location, LocationRoute, Position, World


class NavigationService:
    """Owns the deterministic named-location graph and route paths."""

    def ensure_graph(self, world: World) -> list[LocationRoute]:
        location_ids = {location.id for location in world.locations}
        if self._valid_graph(world, location_ids):
            return world.routes
        world.routes = self._generate_routes(world)
        return world.routes

    def reachable_location_ids(
        self,
        world: World,
        start_id: str,
    ) -> set[str]:
        self.ensure_graph(world)
        visited = {start_id}
        queue = deque([start_id])
        while queue:
            current = queue.popleft()
            for route in world.routes:
                neighbor = route.other(current)
                if neighbor is None or neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        return visited

    def shortest_location_path(
        self,
        world: World,
        origin_id: str,
        destination_id: str,
    ) -> list[str] | None:
        self.ensure_graph(world)
        if origin_id == destination_id:
            return [origin_id]
        parents: dict[str, str | None] = {origin_id: None}
        queue = deque([origin_id])
        while queue:
            current = queue.popleft()
            for neighbor in self.neighbor_ids(world, current):
                if neighbor in parents:
                    continue
                parents[neighbor] = current
                if neighbor == destination_id:
                    return self._reconstruct_location_path(
                        parents,
                        destination_id,
                    )
                queue.append(neighbor)
        return None

    def neighbor_ids(self, world: World, location_id: str) -> list[str]:
        self.ensure_graph(world)
        neighbors = [
            neighbor
            for route in world.routes
            for neighbor in [route.other(location_id)]
            if neighbor is not None
        ]
        return sorted(set(neighbors))

    def route_between(
        self,
        world: World,
        first_id: str,
        second_id: str,
    ) -> LocationRoute | None:
        self.ensure_graph(world)
        return next(
            (
                route
                for route in world.routes
                if {route.origin_id, route.destination_id}
                == {first_id, second_id}
            ),
            None,
        )

    def route_cells(self, world: World) -> set[Position]:
        self.ensure_graph(world)
        return {
            position
            for route in world.routes
            for position in route.path
        }

    def _valid_graph(
        self,
        world: World,
        location_ids: set[str],
    ) -> bool:
        if len(location_ids) <= 1:
            return not world.routes
        if not world.routes:
            return False
        for route in world.routes:
            if (
                route.origin_id not in location_ids
                or route.destination_id not in location_ids
                or not route.path
                or any(
                    not 0 <= position.x < world.width
                    or not 0 <= position.y < world.height
                    for position in route.path
                )
            ):
                return False
        start_id = min(location_ids)
        return self._reachable_from_routes(world.routes, start_id) == location_ids

    def _generate_routes(self, world: World) -> list[LocationRoute]:
        locations = sorted(world.locations, key=lambda item: item.id)
        if len(locations) < 2:
            return []
        edges = self._minimum_spanning_edges(locations)
        edge_keys = {
            frozenset((first.id, second.id))
            for first, second in edges
        }
        for location in locations:
            candidates = [
                other
                for other in locations
                if other.id != location.id
                and frozenset((location.id, other.id)) not in edge_keys
            ]
            if not candidates:
                continue
            nearest = min(
                candidates,
                key=lambda item: (
                    self._distance(location, item),
                    item.id,
                ),
            )
            key = frozenset((location.id, nearest.id))
            if self._stable_int(world.seed, *sorted(key)) % 3 != 0:
                continue
            edges.append((location, nearest))
            edge_keys.add(key)

        routes = [
            self._route_for_pair(world, first, second)
            for first, second in edges
        ]
        return sorted(routes, key=lambda route: route.id)

    def _minimum_spanning_edges(
        self,
        locations: list[Location],
    ) -> list[tuple[Location, Location]]:
        by_id = {location.id: location for location in locations}
        visited = {locations[0].id}
        edges: list[tuple[Location, Location]] = []
        while len(visited) < len(locations):
            candidates = [
                (
                    self._distance(by_id[origin_id], destination),
                    origin_id,
                    destination.id,
                )
                for origin_id in sorted(visited)
                for destination in locations
                if destination.id not in visited
            ]
            _, origin_id, destination_id = min(candidates)
            edges.append((by_id[origin_id], by_id[destination_id]))
            visited.add(destination_id)
        return edges

    def _route_for_pair(
        self,
        world: World,
        first: Location,
        second: Location,
    ) -> LocationRoute:
        origin, destination = sorted(
            (first, second),
            key=lambda item: item.id,
        )
        path = self._terrain_path(
            world,
            origin.position,
            destination.position,
        )
        water_tiles = sum(
            world.tiles[position.y][position.x] == Biome.WATER
            for position in path
        )
        elevated_tiles = sum(
            world.tiles[position.y][position.x]
            in {Biome.HILL, Biome.MOUNTAIN}
            for position in path
        )
        if water_tiles:
            kind = "ferry"
        elif elevated_tiles > len(path) // 3:
            kind = "trail"
        else:
            kind = "road"
        return LocationRoute(
            id=f"route:{origin.id}:{destination.id}",
            origin_id=origin.id,
            destination_id=destination.id,
            path=path,
            kind=kind,
            danger=max(1, min(9, (origin.danger + destination.danger) // 2)),
        )

    def _terrain_path(
        self,
        world: World,
        start: Position,
        goal: Position,
    ) -> list[Position]:
        frontier: list[tuple[int, int, int, int]] = [
            (self._manhattan(start, goal), 0, start.y, start.x)
        ]
        costs = {start: 0}
        parents: dict[Position, Position | None] = {start: None}
        while frontier:
            _, cost, y, x = heapq.heappop(frontier)
            current = Position(x, y)
            if cost != costs.get(current):
                continue
            if current == goal:
                return self._reconstruct_position_path(parents, goal)
            for neighbor in self._neighbors(world, current):
                next_cost = cost + self._terrain_cost(
                    world.tiles[neighbor.y][neighbor.x]
                )
                if next_cost >= costs.get(neighbor, 1_000_000_000):
                    continue
                costs[neighbor] = next_cost
                parents[neighbor] = current
                priority = next_cost + self._manhattan(neighbor, goal)
                heapq.heappush(
                    frontier,
                    (priority, next_cost, neighbor.y, neighbor.x),
                )
        return [start, goal]

    def _neighbors(
        self,
        world: World,
        position: Position,
    ) -> list[Position]:
        candidates = [
            Position(position.x, position.y - 1),
            Position(position.x + 1, position.y),
            Position(position.x, position.y + 1),
            Position(position.x - 1, position.y),
        ]
        return [
            candidate
            for candidate in candidates
            if 0 <= candidate.x < world.width
            and 0 <= candidate.y < world.height
        ]

    def _terrain_cost(self, biome: Biome) -> int:
        return {
            Biome.PLAIN: 1,
            Biome.FOREST: 2,
            Biome.HILL: 3,
            Biome.SWAMP: 4,
            Biome.MOUNTAIN: 5,
            Biome.WATER: 12,
        }[biome]

    def _reachable_from_routes(
        self,
        routes: list[LocationRoute],
        start_id: str,
    ) -> set[str]:
        visited = {start_id}
        queue = deque([start_id])
        while queue:
            current = queue.popleft()
            for route in routes:
                neighbor = route.other(current)
                if neighbor is None or neighbor in visited:
                    continue
                visited.add(neighbor)
                queue.append(neighbor)
        return visited

    def _reconstruct_location_path(
        self,
        parents: dict[str, str | None],
        destination_id: str,
    ) -> list[str]:
        path = [destination_id]
        current = destination_id
        while parents[current] is not None:
            current = parents[current] or ""
            path.append(current)
        path.reverse()
        return path

    def _reconstruct_position_path(
        self,
        parents: dict[Position, Position | None],
        goal: Position,
    ) -> list[Position]:
        path = [goal]
        current = goal
        while parents[current] is not None:
            current = parents[current] or goal
            path.append(current)
        path.reverse()
        return path

    def _distance(self, first: Location, second: Location) -> int:
        return self._manhattan(first.position, second.position)

    def _manhattan(self, first: Position, second: Position) -> int:
        return abs(first.x - second.x) + abs(first.y - second.y)

    def _stable_int(self, seed: int, *parts: str) -> int:
        token = ":".join((str(seed), *parts)).encode("utf-8")
        return int.from_bytes(sha256(token).digest()[:8], "big")
