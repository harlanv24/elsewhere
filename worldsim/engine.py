from __future__ import annotations

import math
import random

from worldsim.director import Director
from worldsim.memory import CampaignMemory
from worldsim.models import Biome, CommandResult, DirectorBeat, Event, Location, Npc, Player, Position, World


class WorldEngine:
    def __init__(self, seed: int = 696969) -> None:
        self.seed = seed
        self.random = random.Random(seed)

    def create_world(self, director: Director | None = None) -> World:
        width = 96
        height = 52
        tiles = self._generate_tiles(width, height)
        locations = self._generate_locations(tiles, width, height)
        npcs = self._generate_npcs(locations)
        world = World(
            seed=self.seed,
            tick=1,
            width=width,
            height=height,
            tiles=tiles,
            locations=locations,
            npcs=npcs,
            weather="Wind from the west",
            stability=68,
        )
        self._add_event(world, "world", "The frontier wakes under a restless sky.")
        world.quest_hooks = self._starting_hooks(locations)
        if director is not None:
            self._apply_world_details(world, director.generate_world_details(world))
        self._refresh_alerts(world, None)
        return world

    def create_player(self, world: World, name: str, archetype: str, homeland: str) -> Player:
        start = world.locations[0]
        max_hp = {"warrior": 18, "rogue": 14, "mage": 12, "ranger": 16}.get(archetype, 14)
        return Player(
            name=name,
            archetype=archetype,
            homeland=homeland,
            hp=max_hp,
            max_hp=max_hp,
            gold=12,
            xp=0,
            position=start.position,
        )

    def resolve_command(
        self,
        command: str,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
    ) -> CommandResult:
        text = command.strip().lower()
        if text.startswith("/"):
            text = text[1:].strip()
        if not text:
            return CommandResult("Type a command. Try `help` if you want the list.")

        if text in {"quit", "exit"}:
            return CommandResult("The world will wait.", should_quit=True)

        if text == "help":
            return CommandResult(
                "Commands: north south east west, look, explore, talk, say <message>, attack, rest, wait, help, quit. You can also type actions like `take journal`."
            )

        if text in {"north", "south", "east", "west", "n", "s", "e", "w"} or text.startswith("move "):
            direction = text
            if text.startswith("move "):
                direction = text.split(maxsplit=1)[1]
            direction = {"n": "north", "s": "south", "e": "east", "w": "west"}.get(direction, direction)
            result = self._move_player(direction, world, player)
            if not result.advance_time:
                return result
            self._advance_world(world, player, director, memory, "move")
            location = self.location_at(world, player.position)
            npc = self.npc_at(location, world)
            if location is not None:
                memory.remember_location(location, world.tick)
            memory.remember_world_state(world, player)
            description = director.describe_location(
                world,
                player,
                location,
                npc,
                memory.relevant_context(world, player, location.name if location else None),
            )
            return CommandResult(f"{result.message} {description}", advance_time=True)

        location = self.location_at(world, player.position)
        npc = self.npc_at(location, world)
        memory_context = memory.relevant_context(world, player, location.name if location else None)

        if text == "look":
            return CommandResult(director.describe_location(world, player, location, npc, memory_context))

        if text == "explore":
            beat = director.respond_to_action(world, player, "explore", location, npc, memory_context)
            self._remember_scene_objects(world, player.position, beat.scene_objects)
            success = self._roll_check(player, beat.difficulty)
            place = location.name if location else "the wilds"
            if success:
                gain = self.random.randint(2, 6)
                player.gold += gain
                player.xp += 3
                if location is not None:
                    location.danger = max(0, location.danger - 1)
                    memory.remember_location(location, world.tick)
                if beat.follow_up_hook:
                    world.quest_hooks.insert(0, beat.follow_up_hook)
                    memory.remember_hook(beat.follow_up_hook, world.tick)
                memory.remember(
                    "discovery",
                    f"{place}:{world.tick}",
                    f"Exploration in {place} paid off with coin and leverage.",
                    world.tick,
                    importance=7,
                    tags=[place, "discovery"],
                )
                message = f"{beat.narration} You recover {gain} gold and useful leverage."
            else:
                damage = self.random.randint(1, 4)
                player.hp = max(0, player.hp - damage)
                if location is not None:
                    location.danger = min(9, location.danger + 1)
                    memory.remember_location(location, world.tick)
                memory.remember(
                    "danger",
                    f"{place}:{world.tick}",
                    f"Exploration near {place} ended badly and left {player.name} wounded.",
                    world.tick,
                    importance=8,
                    tags=[place, "danger"],
                )
                message = f"{beat.narration} The search goes badly and you take {damage} damage."
            self._advance_world(world, player, director, memory, "explore")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text == "talk":
            beat = director.respond_to_action(world, player, "talk", location, npc, memory_context)
            self._remember_scene_objects(world, player.position, beat.scene_objects)
            if npc is None:
                message = beat.narration
            else:
                player.xp += 1
                if beat.follow_up_hook:
                    world.quest_hooks.insert(0, beat.follow_up_hook)
                    memory.remember_hook(beat.follow_up_hook, world.tick)
                memory.remember_npc(npc, world.tick)
                memory.remember(
                    "rumor",
                    f"{npc.name}:{world.tick}",
                    beat.narration,
                    world.tick,
                    importance=7,
                    tags=[npc.name, npc.location_name, "rumor"],
                )
                message = beat.narration
                self._remember_dialogue(world, npc, f"{npc.name}: {beat.narration}")
            self._advance_world(world, player, director, memory, "talk")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text.startswith("say "):
            player_dialogue = command.strip()[4:].strip()
            if not player_dialogue:
                return CommandResult("Say what?")
            if npc is None:
                return CommandResult("There is no one here to answer.")
            history = self._dialogue_history(world, npc)
            reply = director.respond_to_dialogue(
                world,
                player,
                player_dialogue,
                location,
                npc,
                memory_context,
                history,
            )
            self._remember_dialogue(world, npc, f"{player.name}: {player_dialogue}")
            self._remember_dialogue(world, npc, f"{npc.name}: {reply}")
            self._remember_state_fact(world, f"{player.name} told {npc.name}: {player_dialogue}", world.tick)
            self._remember_state_fact(world, f"{npc.name} replied to {player.name}: {reply}", world.tick)
            memory.remember_npc(npc, world.tick)
            memory.remember(
                "dialogue",
                f"{npc.name}:{world.tick}",
                f"{player.name} spoke with {npc.name}: {player_dialogue} / {reply}",
                world.tick,
                importance=7,
                tags=[npc.name, npc.location_name, "dialogue"],
            )
            self._advance_world(world, player, director, memory, "talk")
            memory.remember_world_state(world, player)
            return CommandResult(reply, advance_time=True)

        if text == "rest":
            beat = director.respond_to_action(world, player, "rest", location, npc, memory_context)
            self._remember_scene_objects(world, player.position, beat.scene_objects)
            heal = self.random.randint(2, 5)
            player.hp = min(player.max_hp, player.hp + heal)
            self._advance_world(world, player, director, memory, "rest")
            memory.remember(
                "rest",
                f"{player.name}:{world.tick}",
                f"{player.name} made camp and recovered strength.",
                world.tick,
                importance=5,
                tags=[player.name, "rest"],
            )
            memory.remember_world_state(world, player)
            return CommandResult(f"{beat.narration} You recover {heal} HP.", advance_time=True)

        if text == "attack":
            beat = director.respond_to_action(world, player, "attack", location, npc, memory_context)
            self._remember_scene_objects(world, player.position, beat.scene_objects)
            if location is None:
                self._advance_world(world, player, director, memory, "attack")
                memory.remember_world_state(world, player)
                return CommandResult("There is no clear target here beyond shadows and nerves.", advance_time=True)

            if location.danger <= 0:
                self._advance_world(world, player, director, memory, "attack")
                memory.remember_location(location, world.tick)
                memory.remember_world_state(world, player)
                return CommandResult(f"{location.name} is tense but quiet. Nothing attacks back.", advance_time=True)

            success = self._roll_attack(player, 10 + location.danger)
            if success:
                reward = self.random.randint(3, 8)
                player.gold += reward
                player.xp += 5
                location.danger = max(0, location.danger - 2)
                memory.remember(
                    "battle",
                    f"{location.name}:victory",
                    f"{player.name} drove back a threat in {location.name}. Local danger fell to {location.danger}/9.",
                    world.tick,
                    importance=9,
                    tags=[location.name, "combat", "victory"],
                )
                memory.remember_location(location, world.tick)
                message = f"{beat.narration} You drive the threat back and claim {reward} gold in salvage."
            else:
                damage = self.random.randint(2, 6)
                player.hp = max(0, player.hp - damage)
                location.danger = min(9, location.danger + 1)
                memory.remember(
                    "battle",
                    f"{location.name}:setback",
                    f"{player.name} was bloodied in {location.name}; danger climbed to {location.danger}/9.",
                    world.tick,
                    importance=9,
                    tags=[location.name, "combat", "danger"],
                )
                memory.remember_location(location, world.tick)
                message = f"{beat.narration} The fight turns against you. You take {damage} damage."
            self._advance_world(world, player, director, memory, "attack")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text == "wait":
            self._advance_world(world, player, director, memory, "wait")
            memory.remember_world_state(world, player)
            return CommandResult("You keep still long enough to notice the world changing around you.", advance_time=True)

        return self._resolve_freeform_action(command.strip(), world, player, director, memory, location, npc, memory_context)

    def location_at(self, world: World, position: Position) -> Location | None:
        for location in world.locations:
            if location.position == position:
                return location
        return None

    def npc_at(self, location: Location | None, world: World) -> Npc | None:
        if location is None:
            return None
        for npc in world.npcs:
            if npc.location_name == location.name:
                return npc
        return None

    def biome_at(self, world: World, position: Position) -> Biome:
        return world.tiles[position.y][position.x]

    def passable(self, world: World, position: Position) -> bool:
        return world.tiles[position.y][position.x] != Biome.WATER

    def player_bonus(self, player: Player) -> int:
        return {"warrior": 4, "rogue": 3, "mage": 2, "ranger": 3}.get(player.archetype, 2)

    def summary_counts(self, world: World) -> dict[str, int]:
        return {
            "locations": len(world.locations),
            "npcs": len(world.npcs),
            "events": len(world.recent_events),
            "hooks": len(world.quest_hooks),
        }

    def scene_objects_at(self, world: World, position: Position) -> list[str]:
        return list(world.scene_objects.get(self._position_key(position), []))

    def _resolve_freeform_action(
        self,
        action: str,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str],
    ) -> CommandResult:
        if not action:
            return CommandResult("Type a command. Try `help` if you want the list.")
        visible_items = self.scene_objects_at(world, player.position)
        unavailable = self._unavailable_target_message(action, world, player.position, visible_items, player.inventory)
        if unavailable is not None:
            return CommandResult(unavailable)
        beat = director.respond_to_freeform_action(world, player, action, location, npc, memory_context)
        self._remember_scene_objects(world, player.position, beat.scene_objects)
        visible_items = self.scene_objects_at(world, player.position)
        added = self._apply_inventory_changes(player, world, action, beat, visible_items)
        removed_scene_items = self._apply_scene_object_action(world, player.position, action, visible_items)
        place = location.name if location else "the wilds"
        outcome_parts = [f"attempted `{action}`"]
        if beat.tags:
            outcome_parts.append(f"tags: {', '.join(beat.tags[:4])}")
        if added:
            outcome_parts.append(f"took: {', '.join(added)}")
        if removed_scene_items:
            outcome_parts.append(f"changed/removed: {', '.join(removed_scene_items)}")
        if beat.follow_up_hook:
            outcome_parts.append(f"hook: {beat.follow_up_hook}")
        self._remember_state_fact(world, f"{player.name} near {place}: {'; '.join(outcome_parts)}.", world.tick)
        memory.remember(
            "action",
            f"{player.name}:{world.tick}:{action}",
            f"{player.name} near {place}: {'; '.join(outcome_parts)}.",
            world.tick,
            importance=6,
            tags=[player.name, place, "action"],
        )
        self._advance_world(world, player, director, memory, "freeform")
        memory.remember_world_state(world, player)
        suffix_parts = []
        if added:
            suffix_parts.append(f"Added to inventory: {', '.join(added)}.")
        if removed_scene_items:
            suffix_parts.append(f"Scene changed: {', '.join(removed_scene_items)}.")
        suffix = f" {' '.join(suffix_parts)}" if suffix_parts else ""
        return CommandResult(f"{beat.narration}{suffix}", advance_time=True)

    def _apply_inventory_changes(
        self,
        player: Player,
        world: World,
        action: str,
        beat: DirectorBeat,
        visible_items: list[str],
    ) -> list[str]:
        requested_item = self._requested_take_item(action)
        proposed_adds = list(beat.inventory_add)
        if requested_item and self._matches_known_object(requested_item, visible_items):
            proposed_adds.insert(0, requested_item)
        added: list[str] = []
        for item in proposed_adds:
            normalized = self._clean_item_name(item)
            if not normalized or normalized in player.inventory:
                continue
            if not requested_item or not self._matches_known_object(normalized, visible_items + [requested_item]):
                continue
            player.inventory.append(normalized)
            added.append(normalized)
            self._set_object_state(
                world,
                player.position,
                normalized,
                status="in_inventory",
                tick=world.tick,
                owner=player.name,
            )
            self._remove_scene_object(world, player.position, normalized)
        for item in beat.inventory_remove:
            normalized = self._clean_item_name(item)
            if normalized in player.inventory:
                player.inventory.remove(normalized)
        return added

    def _requested_take_item(self, action: str) -> str | None:
        words = action.strip().split(maxsplit=1)
        if len(words) != 2 or words[0].lower() not in {"take", "get", "grab", "pick"}:
            return None
        item = words[1]
        if item.lower().startswith("up "):
            item = item[3:]
        return self._clean_item_name(item)

    def _targeted_object_action(self, action: str) -> tuple[str, str] | None:
        words = action.strip().split(maxsplit=1)
        if len(words) != 2:
            return None
        verb = words[0].lower()
        if verb == "pick" and words[1].lower().startswith("up "):
            return "take", self._clean_item_name(words[1][3:])
        if verb not in {
            "take",
            "get",
            "grab",
            "burn",
            "destroy",
            "break",
            "tear",
            "shatter",
            "discard",
            "read",
            "open",
            "close",
            "move",
            "push",
            "pull",
            "use",
        }:
            return None
        return verb, self._clean_item_name(words[1])

    def _unavailable_target_message(
        self,
        action: str,
        world: World,
        position: Position,
        visible_items: list[str],
        inventory: list[str],
    ) -> str | None:
        target = self._targeted_object_action(action)
        if target is None:
            return None
        verb, item = target
        if not item:
            return None
        if verb in {"take", "get", "grab"} and self._matches_known_object(item, inventory):
            return f"You already have {item}."
        state = self._object_state_for_target(world, position, item)
        if state is not None and state.get("status") in {"destroyed", "removed", "in_inventory"}:
            status = state.get("status")
            if status == "in_inventory":
                return f"You already have {item}."
            return f"The {item} is already {status}."
        if self._matches_known_object(item, visible_items) or self._matches_known_object(item, inventory):
            return None
        return f"There is no {item} here to {verb}."

    def _object_state_for_target(self, world: World, position: Position, item: str) -> dict[str, object] | None:
        target = self._clean_item_name(item)
        position_key = self._position_key(position)
        for record in world.object_states.values():
            name = str(record.get("name", "")).lower()
            if record.get("last_position") != position_key and record.get("position") != position_key:
                continue
            if target == name or target in name or name in target:
                return record
        return None

    def _matches_known_object(self, item: str, objects: list[str]) -> bool:
        item_key = item.lower()
        return any(item_key == obj.lower() or item_key in obj.lower() or obj.lower() in item_key for obj in objects)

    def _remember_scene_objects(self, world: World, position: Position, objects: list[str]) -> None:
        cleaned = [self._clean_item_name(item) for item in objects]
        cleaned = [item for item in cleaned if item]
        if not cleaned:
            return
        key = self._position_key(position)
        existing = world.scene_objects.setdefault(key, [])
        for item in cleaned:
            if item not in existing:
                existing.append(item)
        del existing[12:]

    def _remove_scene_object(self, world: World, position: Position, item: str) -> None:
        key = self._position_key(position)
        objects = world.scene_objects.get(key)
        if not objects:
            return
        item_key = item.lower()
        world.scene_objects[key] = [
            obj
            for obj in objects
            if obj.lower() != item_key and item_key not in obj.lower() and obj.lower() not in item_key
        ]

    def _apply_scene_object_action(self, world: World, position: Position, action: str, visible_items: list[str]) -> list[str]:
        words = action.strip().split(maxsplit=1)
        if len(words) != 2 or words[0].lower() not in {"burn", "destroy", "break", "tear", "shatter", "discard"}:
            return []
        target = self._clean_item_name(words[1])
        if not target or not self._matches_known_object(target, visible_items):
            return []
        removed = [item for item in visible_items if self._matches_known_object(target, [item])]
        for item in removed:
            self._set_object_state(world, position, item, status="destroyed", tick=world.tick)
        self._remove_scene_object(world, position, target)
        return removed

    def _position_key(self, position: Position) -> str:
        return f"{position.x},{position.y}"

    def _clean_item_name(self, item: str) -> str:
        return item.strip().lower().removeprefix("the ").removeprefix("a ").removeprefix("an ")[:60]

    def _object_state_key(self, position: Position, item: str) -> str:
        return f"{self._position_key(position)}:{self._clean_item_name(item)}"

    def _set_object_state(
        self,
        world: World,
        position: Position,
        item: str,
        status: str,
        tick: int,
        owner: str | None = None,
    ) -> None:
        cleaned = self._clean_item_name(item)
        if not cleaned:
            return
        key = self._object_state_key(position, cleaned)
        record = world.object_states.setdefault(
            key,
            {
                "name": cleaned,
                "position": self._position_key(position),
                "created_tick": tick,
            },
        )
        record["status"] = status
        record["last_tick"] = tick
        record["last_position"] = self._position_key(position)
        if owner is not None:
            record["owner"] = owner
        self._remember_state_fact(world, f"{cleaned} at {self._position_key(position)} is now {status}.", tick)

    def _remember_state_fact(self, world: World, fact: str, tick: int, limit: int = 80) -> None:
        normalized = " ".join(fact.split())
        if len(normalized) > 240:
            normalized = normalized[:237].rstrip() + "..."
        line = f"[tick {tick}] {normalized}"
        world.state_facts.append(line)
        del world.state_facts[:-limit]

    def _dialogue_history(self, world: World, npc: Npc, limit: int = 8) -> list[str]:
        return world.conversations.get(npc.name, [])[-limit:]

    def _remember_dialogue(self, world: World, npc: Npc, line: str, limit: int = 16) -> None:
        history = world.conversations.setdefault(npc.name, [])
        history.append(line)
        del history[:-limit]

    def _move_player(self, direction: str, world: World, player: Player) -> CommandResult:
        offsets = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}
        if direction not in offsets:
            return CommandResult("Use north, south, east, or west.")
        dx, dy = offsets[direction]
        candidate = Position(player.position.x + dx, player.position.y + dy)
        if not (0 <= candidate.x < world.width and 0 <= candidate.y < world.height):
            return CommandResult("The frontier does not continue that way.")
        if not self.passable(world, candidate):
            return CommandResult("Water blocks the way.")
        player.position = candidate
        return CommandResult(f"You travel {direction}.", advance_time=True)

    def _advance_world(
        self,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
        cause: str,
    ) -> None:
        world.tick += 1
        if self.random.random() < 0.35:
            world.weather = self.random.choice(
                ["Cold drizzle", "Harsh sunlight", "Crosswind", "Quiet fog", "Distant thunder"]
            )
        if self.random.random() < 0.4:
            ambient = director.ambient_world_event(world)
            self._add_event(world, "world", ambient)
            memory.remember("world", f"ambient:{world.tick}", ambient, world.tick, importance=4, tags=["world"])
        active_location = self.location_at(world, player.position)
        if cause in {"wait", "rest"} and active_location is not None and self.random.random() < 0.3:
            active_location.danger = min(9, active_location.danger + 1)
            self._add_event(world, "danger", f"Tension rises around {active_location.name}.")
            memory.remember(
                "danger",
                active_location.name,
                f"Tension keeps building around {active_location.name}. Current danger is {active_location.danger}/9.",
                world.tick,
                importance=8,
                tags=[active_location.name, "danger"],
            )
        if cause == "attack":
            world.stability = min(100, world.stability + 1)
        elif cause == "explore":
            world.stability = max(35, world.stability - self.random.randint(0, 1))
        self._refresh_alerts(world, player)

    def _refresh_alerts(self, world: World, player: Player | None) -> None:
        alerts: list[str] = []
        if player is not None and player.hp <= max(4, player.max_hp // 3):
            alerts.append("Player HP is low")
        if player is not None:
            location = self.location_at(world, player.position)
            if location is not None and location.danger >= 6:
                alerts.append(f"{location.name} is becoming hostile")
        if world.stability < 50:
            alerts.append("World stability is slipping")
        world.alerts = alerts[:3]

    def _apply_world_details(self, world: World, details: dict[str, object] | None) -> None:
        if not details:
            return
        weather = details.get("weather")
        if isinstance(weather, str) and weather.strip():
            world.weather = weather.strip()[:80]

        old_names = [location.name for location in world.locations]
        renamed_locations: dict[str, str] = {}
        for update in details.get("locations", []):
            if not isinstance(update, dict):
                continue
            index = update.get("index")
            if not isinstance(index, int) or not 0 <= index < len(world.locations):
                continue
            location = world.locations[index]
            old_name = location.name
            name = update.get("name")
            summary = update.get("summary")
            if isinstance(name, str) and name.strip():
                location.name = name.strip()[:40]
                renamed_locations[old_name] = location.name
            if isinstance(summary, str) and summary.strip():
                location.summary = summary.strip()[:160]

        for npc in world.npcs:
            if npc.location_name in renamed_locations:
                npc.location_name = renamed_locations[npc.location_name]

        for update in details.get("npcs", []):
            if not isinstance(update, dict):
                continue
            index = update.get("index")
            if not isinstance(index, int) or not 0 <= index < len(world.npcs):
                continue
            npc = world.npcs[index]
            for field_name in ("name", "role", "disposition", "location_name"):
                value = update.get(field_name)
                if isinstance(value, str) and value.strip():
                    setattr(npc, field_name, value.strip()[:40])

        valid_location_names = {location.name for location in world.locations}
        for npc in world.npcs:
            if npc.location_name not in valid_location_names and old_names:
                fallback_name = renamed_locations.get(old_names[min(world.npcs.index(npc), len(old_names) - 1)])
                npc.location_name = fallback_name or world.locations[0].name

        hooks = [hook.strip() for hook in details.get("quest_hooks", []) if isinstance(hook, str) and hook.strip()]
        if hooks:
            world.quest_hooks = hooks[:6]

        opening_event = details.get("opening_event")
        if isinstance(opening_event, str) and opening_event.strip():
            self._add_event(world, "world", opening_event.strip()[:180])

    def _add_event(self, world: World, category: str, text: str, severity: str = "info") -> None:
        world.recent_events.insert(0, Event(tick=world.tick, category=category, text=text, severity=severity))
        del world.recent_events[6:]

    def _roll_check(self, player: Player, difficulty: int) -> bool:
        return self.random.randint(1, 20) + self.player_bonus(player) >= difficulty

    def _roll_attack(self, player: Player, difficulty: int) -> bool:
        return self.random.randint(1, 20) + self.player_bonus(player) >= difficulty

    def _generate_tiles(self, width: int, height: int) -> list[list[Biome]]:
        tiles: list[list[Biome]] = []
        for y in range(height):
            row: list[Biome] = []
            for x in range(width):
                nx = (x / max(1, width - 1)) * 2.0 - 1.0
                ny = (y / max(1, height - 1)) * 2.0 - 1.0
                radial = max(abs(nx), abs(ny))
                continent = (
                    0.68
                    - radial * 0.75
                    + self._layered_noise(x, y, 0) * 0.45
                    + self._layered_noise(x, y, 101) * 0.18
                )
                elevation = continent + self._layered_noise(x, y, 211) * 0.28
                moisture = 0.5 + self._layered_noise(x, y, 389) * 0.55 - elevation * 0.08

                if elevation < 0.18:
                    biome = Biome.WATER
                elif elevation > 0.72:
                    biome = Biome.MOUNTAIN
                elif elevation > 0.58:
                    biome = Biome.HILL
                elif moisture > 0.62 and elevation < 0.44:
                    biome = Biome.SWAMP
                elif moisture > 0.18:
                    biome = Biome.FOREST
                else:
                    biome = Biome.PLAIN
                row.append(biome)
            tiles.append(row)
        return tiles

    def _generate_locations(self, tiles: list[list[Biome]], width: int, height: int) -> list[Location]:
        locations: list[Location] = []
        attempts = 0
        while len(locations) < 12 and attempts < 500:
            attempts += 1
            x = self.random.randint(3, width - 4)
            y = self.random.randint(3, height - 4)
            biome = tiles[y][x]
            if biome == Biome.WATER:
                continue
            position = Position(x, y)
            if any(abs(position.x - item.position.x) + abs(position.y - item.position.y) < 10 for item in locations):
                continue
            locations.append(
                Location(
                    name=self._generate_name(),
                    position=position,
                    biome=biome,
                    danger=self.random.randint(1, 6),
                    summary=self.random.choice(
                        [
                            "half-buried ruins and stubborn settlers",
                            "a watchful market with too many secrets",
                            "old stones older than the local claims",
                            "hunters who trust the woods more than the law",
                            "anxious trade and unfinished repairs",
                        ]
                    ),
                )
            )
        locations.sort(key=lambda item: (item.position.y, item.position.x))
        return locations[:12]

    def _generate_npcs(self, locations: list[Location]) -> list[Npc]:
        first_names = ["Mira", "Thane", "Ivo", "Sable", "Orrin", "Kael", "Brin", "Lysa"]
        roles = ["guide", "warden", "merchant", "scribe", "hunter", "priest"]
        moods = ["wary", "friendly", "guarded", "intense", "skeptical"]
        npcs: list[Npc] = []
        for location in locations[:8]:
            npcs.append(
                Npc(
                    name=self.random.choice(first_names),
                    role=self.random.choice(roles),
                    disposition=self.random.choice(moods),
                    location_name=location.name,
                )
            )
        return npcs

    def _starting_hooks(self, locations: list[Location]) -> list[str]:
        if len(locations) < 4:
            return ["The frontier is young enough that every road feels unfinished."]
        primary = locations[1:4]
        return [
            f"A sealed vault is rumored beneath {primary[0].name}.",
            f"Caravans vanish between {primary[1].name} and the coast.",
            f"Someone is recruiting quietly in {primary[2].name}.",
        ]

    def _layered_noise(self, x: int, y: int, salt: int) -> float:
        return (
            self._value_noise(x, y, 24, salt) * 0.55
            + self._value_noise(x, y, 12, salt + 17) * 0.3
            + self._value_noise(x, y, 6, salt + 41) * 0.15
        )

    def _value_noise(self, x: int, y: int, scale: int, salt: int) -> float:
        gx = x / scale
        gy = y / scale
        x0 = math.floor(gx)
        y0 = math.floor(gy)
        x1 = x0 + 1
        y1 = y0 + 1
        sx = self._smoothstep(gx - x0)
        sy = self._smoothstep(gy - y0)

        n00 = self._hash_noise(x0, y0, salt)
        n10 = self._hash_noise(x1, y0, salt)
        n01 = self._hash_noise(x0, y1, salt)
        n11 = self._hash_noise(x1, y1, salt)

        ix0 = self._lerp(n00, n10, sx)
        ix1 = self._lerp(n01, n11, sx)
        return self._lerp(ix0, ix1, sy)

    def _hash_noise(self, x: int, y: int, salt: int) -> float:
        value = x * 374761393 + y * 668265263 + (self.seed + salt) * 1442695041
        value = (value ^ (value >> 13)) * 1274126177
        value ^= value >> 16
        return ((value & 0xFFFFFFFF) / 0xFFFFFFFF) * 2.0 - 1.0

    def _smoothstep(self, value: float) -> float:
        return value * value * (3.0 - 2.0 * value)

    def _lerp(self, start: float, end: float, t: float) -> float:
        return start + (end - start) * t

    def _generate_name(self) -> str:
        starts = ["Ash", "Raven", "Stone", "Green", "Iron", "Dusk", "Whisper", "Black"]
        ends = ["vale", "watch", "hollow", "ford", "brook", "mere", "gate", "rest"]
        return f"{self.random.choice(starts)}{self.random.choice(ends)}"
