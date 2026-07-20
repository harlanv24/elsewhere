from __future__ import annotations

from copy import deepcopy
import math
import random
import secrets

from worldsim.director import Director
from worldsim.memory import CampaignMemory
from worldsim.models import (
    ActionIntent,
    Biome,
    CheckKind,
    CheckResult,
    CommandResult,
    Condition,
    DialogueState,
    DirectorBeat,
    EncounterState,
    EncounterStatus,
    Event,
    Location,
    Npc,
    Player,
    Position,
    Quest,
    QuestClock,
    TurnOutcome,
    TurnRecord,
    World,
)
from worldsim.progression import ProgressionService
from worldsim.scenes import SceneService
from worldsim.turn_effects import TurnEffectService
from worldsim.turn_resolution import StateReducer


class WorldEngine:
    def __init__(self, seed: int | None = None) -> None:
        self.seed = seed if seed is not None else secrets.randbelow(1_000_000_000)
        self.random = random.Random(self.seed)
        self.state_reducer = StateReducer()
        self.progression = ProgressionService(self)
        self.turn_effects = TurnEffectService(self)
        self.scene_service = SceneService(self)

    def create_world(self, director: Director | None = None, theme_prompt: str | None = None) -> World:
        width = 96
        height = 52
        theme = (theme_prompt or "character-driven adventure").strip()[:500]
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
            theme_prompt=theme,
        )
        self._add_event(world, "world", "The campaign begins around a problem waiting to be noticed.")
        world.quest_hooks = self._starting_hooks(locations)
        if director is not None:
            self._apply_world_details(world, director.generate_world_details(world))
        self._ensure_entity_ids(world)
        self._ensure_progression(world)
        self._refresh_alerts(world, None)
        return world

    def create_player(self, world: World, name: str, archetype: str, homeland: str) -> Player:
        start = world.locations[0]
        max_hp = {"warrior": 18, "rogue": 14, "mage": 12, "ranger": 16}.get(archetype, 14)
        boosts = dict(world.player_archetype_boosts.get(archetype, {}))
        player = Player(
            name=name,
            archetype=archetype,
            homeland=homeland,
            hp=max_hp,
            max_hp=max_hp,
            gold=12,
            xp=0,
            position=start.position,
            inventory=list(world.starting_inventory[:8]),
            boosts=boosts,
        )
        self._sync_scene(world, player)
        return player

    def resolve_command(
        self,
        command: str,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
    ) -> CommandResult:
        raw_command = command.strip()
        if raw_command.startswith("/"):
            raw_command = raw_command[1:].strip()
        text = raw_command.lower()
        if not text:
            return CommandResult("Type a command. Try `help` if you want the list.")
        self._ensure_entity_ids(world)
        self._ensure_progression(world)
        self._ensure_legacy_encounter(world)
        self._sync_scene(world, player)

        if text in {"quit", "exit"}:
            return CommandResult("The world will wait.", should_quit=True)

        campaign_result = self.progression.resolve_command(
            raw_command,
            world,
            player,
            director,
            memory,
        )
        if campaign_result is not None:
            return campaign_result

        if text in {"end conversation", "end dialogue", "goodbye"}:
            if world.dialogue_state is None or not world.dialogue_state.active:
                return CommandResult("You are not in an active conversation.")
            world.dialogue_state.active = False
            world.dialogue_state = None
            self.scene_service.refresh_actions(world)
            return CommandResult("You end the conversation.")

        scene_result = self.scene_service.resolve(
            raw_command,
            world,
            player,
            director,
            memory,
        )
        if scene_result is not None:
            return scene_result

        if text in {"flee", "retreat", "withdraw"}:
            if self._movement_lock_reason(world) is None:
                return CommandResult("There is nothing immediate holding you here.")
            self._resolve_active_encounter(world, EncounterStatus.ESCAPED, "The player withdrew from danger.")
            world.current_choices = []
            world.last_roll = None
            self._remember_state_fact(world, f"{player.name} withdrew from the immediate situation.", world.tick)
            return CommandResult("You break away from the immediate danger. The road is open again.")

        if text == "help":
            return CommandResult(
                "Commands: north south east west, look, explore, enter area <name>, leave area, push deeper, pull back, force exit, talk, say <message>, end conversation, attack, rest, wait, inventory, inspect <item>, use <item>, drop <item>, take <item>, campaign status, resolve finale, abandon campaign, help, quit. The DM may request exploration, social, or combat checks; the engine rolls them using class and item bonuses. While speaking with an NPC, bare prose is dialogue; quit, help, inventory, and end conversation remain global commands, and other commands can be prefixed with /."
            )

        if text in {"north", "south", "east", "west", "n", "s", "e", "w"} or text.startswith("move "):
            if self.scene_service.is_local(world):
                return CommandResult("Leave the local area before traveling across the overworld.")
            movement_lock = self._movement_lock_reason(world)
            if movement_lock is not None:
                return CommandResult(
                    f"You cannot travel while {movement_lock}. Resolve the situation, choose an option, or type `flee`."
                )
            direction = text
            if text.startswith("move "):
                direction = text.split(maxsplit=1)[1]
            direction = {"n": "north", "s": "south", "e": "east", "w": "west"}.get(direction, direction)
            result = self._move_player(direction, world, player)
            if not result.advance_time:
                return result
            self._advance_world(world, player, director, memory, "move")
            world.dialogue_state = None
            self._sync_scene(world, player)
            location = self.location_at(world, player.position)
            npc = self.npc_at(location, world)
            if location is not None:
                memory.remember_location(location, world.tick)
            memory.remember_world_state(world, player)
            if location is None:
                return CommandResult(result.message, advance_time=True)
            description = director.describe_location(
                world,
                player,
                location,
                npc,
                memory.relevant_context(world, player, location.name if location else None),
            )
            return CommandResult(f"{result.message} {description}", advance_time=True)

        location = self.location_at(world, player.position)
        npc = self.scene_service.active_npc(world, self.npc_at(location, world))
        memory_context = memory.relevant_context(world, player, location.name if location else None)

        if text in {"inventory", "inv", "items"}:
            return CommandResult(self._inventory_summary(player, world, location))

        if text.startswith("inspect "):
            target = raw_command.split(maxsplit=1)[1].strip()
            cleaned_target = self._clean_item_name(target)
            visible_items = self.scene_objects_at(world, player.position)
            if not (
                self._matches_known_object(cleaned_target, player.inventory)
                or self._matches_known_object(cleaned_target, visible_items)
                or self._object_state_for_target(world, player.position, cleaned_target) is not None
            ):
                return self._resolve_freeform_action(raw_command, world, player, director, memory, location, npc, memory_context)
            return CommandResult(self._inspect_target(target, world, player, location))

        if text.startswith("use "):
            target = raw_command.split(maxsplit=1)[1].strip()
            result = self._use_inventory_item(target, world, player, location, director, memory, memory_context)
            if result.advance_time:
                self._advance_world(world, player, director, memory, "freeform")
                memory.remember_world_state(world, player)
            return result

        if text.startswith("drop "):
            target = raw_command.split(maxsplit=1)[1].strip()
            result = self._drop_inventory_item(target, world, player, location, director, memory)
            if result.advance_time:
                self._advance_world(world, player, director, memory, "freeform")
                memory.remember_world_state(world, player)
            return result

        if text == "look":
            return CommandResult(director.describe_location(world, player, location, npc, memory_context))

        if text == "explore":
            beat = director.respond_to_action(world, player, "explore", location, npc, memory_context)
            self._apply_beat_context(world, beat, player, location)
            success = self._roll_check(world, player, beat.difficulty, beat.mechanical_request)
            place = location.name if location else "the wilds"
            follow_up = self._roll_follow_up("explore", success, beat, location, npc)
            world.current_choices = self._merge_choices(follow_up["choices"], beat.choices)
            if success:
                self._remember_scene_objects(world, player.position, beat.scene_objects)
                if (
                    beat.mechanical_request == "exploration_check"
                    and world.active_encounter is not None
                    and world.active_encounter.movement_locked
                ):
                    self._resolve_active_encounter(
                        world,
                        EncounterStatus.ESCAPED,
                        "A successful exploration action opened an alternate route.",
                    )
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
                message = f"{beat.narration}\n\n{world.last_roll} You recover {gain} gold and useful leverage.\n{follow_up['prompt']}"
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
                message = f"{beat.narration}\n\n{world.last_roll} The search goes badly and you take {damage} damage.\n{follow_up['prompt']}"
            self._apply_progression(world, player, beat, memory, location, success)
            self._advance_world(world, player, director, memory, "explore")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text == "talk":
            beat = director.respond_to_action(world, player, "talk", location, npc, memory_context)
            self._apply_beat_context(world, beat, player, location)
            success = (
                self._roll_check(world, player, beat.difficulty, beat.mechanical_request)
                if beat.mechanical_request is not None
                else None
            )
            if success is not False:
                self._remember_scene_objects(world, player.position, beat.scene_objects)
            if npc is None:
                message = beat.narration
            else:
                world.dialogue_state = DialogueState(
                    npc_id=npc.id or npc.name,
                    npc_name=npc.name,
                    started_tick=world.tick,
                )
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
                if world.last_roll and beat.mechanical_request is not None:
                    message = f"{message}\n\n{world.last_roll}"
                self._remember_dialogue(world, npc, f"{npc.name}: {beat.narration}")
            self._apply_progression(world, player, beat, memory, location, success)
            self._advance_world(world, player, director, memory, "talk")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text.startswith("say "):
            player_dialogue = raw_command[4:].strip()
            if not player_dialogue:
                return CommandResult("Say what?")
            if npc is None:
                return CommandResult("There is no one here to answer.")
            history = self._dialogue_history(world, npc)
            dialogue_response = director.respond_to_dialogue(
                world,
                player,
                player_dialogue,
                location,
                npc,
                memory_context,
                history,
            )
            beat = (
                dialogue_response
                if isinstance(dialogue_response, DirectorBeat)
                else DirectorBeat(title="Dialogue", narration=dialogue_response)
            )
            self._apply_beat_context(world, beat, player, location)
            success = (
                self._roll_check(world, player, beat.difficulty, beat.mechanical_request)
                if beat.mechanical_request is not None
                else None
            )
            if success is not False:
                self._remember_scene_objects(world, player.position, beat.scene_objects)
            reply = beat.narration
            world.dialogue_state = DialogueState(
                npc_id=npc.id or npc.name,
                npc_name=npc.name,
                started_tick=world.dialogue_state.started_tick if world.dialogue_state is not None else world.tick,
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
            self._apply_progression(world, player, beat, memory, location, success)
            self._advance_world(world, player, director, memory, "talk")
            memory.remember_world_state(world, player)
            return CommandResult(reply, advance_time=True)

        if text == "rest":
            beat = director.respond_to_action(world, player, "rest", location, npc, memory_context)
            self._apply_beat_context(world, beat, player, location)
            self._remember_scene_objects(world, player.position, beat.scene_objects)
            heal = self.random.randint(2, 5)
            player.hp = min(player.max_hp, player.hp + heal)
            self._apply_progression(world, player, beat, memory, location, None)
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
            if location is None:
                self._advance_world(world, player, director, memory, "attack")
                memory.remember_world_state(world, player)
                return CommandResult("There is no clear target here beyond shadows and nerves.", advance_time=True)

            if location.danger <= 0:
                self._advance_world(world, player, director, memory, "attack")
                memory.remember_location(location, world.tick)
                memory.remember_world_state(world, player)
                return CommandResult(f"{location.name} is tense but quiet. Nothing attacks back.", advance_time=True)

            self._apply_beat_context(world, beat, player, location)
            success = self._roll_attack(world, player, 10 + location.danger)
            follow_up = self._roll_follow_up("attack", success, beat, location, npc)
            world.current_choices = self._merge_choices(follow_up["choices"], beat.choices)
            if success:
                self._remember_scene_objects(world, player.position, beat.scene_objects)
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
                self._resolve_active_encounter(world, EncounterStatus.RESOLVED, "The immediate threat was defeated.")
                message = f"{beat.narration}\n\n{world.last_roll} You drive the threat back and claim {reward} gold in salvage.\n{follow_up['prompt']}"
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
                self._start_encounter(world, player, location, "Survive or overcome the hostile threat.")
                message = f"{beat.narration}\n\n{world.last_roll} The fight turns against you. You take {damage} damage.\n{follow_up['prompt']}"
            self._apply_progression(world, player, beat, memory, location, success)
            self._advance_world(world, player, director, memory, "attack")
            memory.remember_world_state(world, player)
            return CommandResult(message, advance_time=True)

        if text == "wait":
            self._advance_world(world, player, director, memory, "wait")
            memory.remember_world_state(world, player)
            return CommandResult("You keep still long enough to notice the world changing around you.", advance_time=True)

        return self._resolve_freeform_action(raw_command, world, player, director, memory, location, npc, memory_context)

    def location_at(self, world: World, position: Position) -> Location | None:
        for location in world.locations:
            if location.position == position:
                return location
        return None

    def _ensure_entity_ids(self, world: World) -> None:
        for index, location in enumerate(world.locations):
            if not location.id:
                location.id = f"location-{index + 1:03d}"
        locations_by_name = {location.name: location for location in world.locations}
        for index, npc in enumerate(world.npcs):
            if not npc.id:
                npc.id = f"npc-{index + 1:03d}"
            location = locations_by_name.get(npc.location_name)
            if location is not None:
                npc.location_id = location.id

    def _sync_scene(self, world: World, player: Player) -> None:
        location = self.location_at(world, player.position)
        self.scene_service.sync(world, player, location)

    def _ensure_legacy_encounter(self, world: World) -> None:
        encounter = world.active_encounter
        if encounter is not None and encounter.movement_locked:
            world.current_activity = "combat"
            world.movement_lock = "you are in a fight"
            return
        lock = world.movement_lock or ""
        if encounter is not None and not encounter.movement_locked and (
            world.current_activity == "combat"
            or any(token in lock.lower() for token in ("fight", "combat"))
        ):
            world.current_activity = None
            world.movement_lock = None
            return
        if encounter is None and (
            world.current_activity == "combat"
            or any(token in lock.lower() for token in ("fight", "combat"))
        ):
            world.active_encounter = EncounterState(
                id=f"legacy-encounter-{world.tick}",
                kind="combat",
                participants=[],
                objective="Resolve the legacy combat state.",
                phase="engaged",
                exits=["flee"],
            )
            world.current_activity = "combat"
            world.movement_lock = "you are in a fight"

    def _movement_lock_reason(self, world: World) -> str | None:
        encounter = world.active_encounter
        if encounter is not None and encounter.movement_locked:
            return f"encounter {encounter.id} is active"
        return None

    def movement_lock_reason(self, world: World) -> str | None:
        """Return the encounter-derived movement lock, if any."""

        return self._movement_lock_reason(world)

    def _start_encounter(
        self,
        world: World,
        player: Player,
        location: Location | None,
        objective: str,
    ) -> None:
        if world.active_encounter is None or not world.active_encounter.movement_locked:
            npc = self.scene_service.active_npc(world, self.npc_at(location, world))
            participants = [player.name]
            if npc is not None:
                participants.append(npc.id or npc.name)
            location_token = location.id if location is not None else f"{player.position.x}-{player.position.y}"
            world.active_encounter = EncounterState(
                id=f"encounter-{world.tick}-{location_token}",
                kind="combat",
                participants=participants,
                objective=objective,
                phase="engaged",
                obstacles=["hostile pressure"],
                exits=["flee", "alternate route", "negotiation"],
            )
        world.current_activity = "combat"
        world.movement_lock = "you are in a fight"

    def _resolve_active_encounter(
        self,
        world: World,
        status: EncounterStatus,
        resolution: str,
    ) -> None:
        encounter = world.active_encounter
        if encounter is not None:
            encounter.status = status
            encounter.phase = "resolved"
            encounter.resolution = resolution
            if status == EncounterStatus.RESOLVED and encounter.id not in world.resolved_encounter_ids:
                world.resolved_encounter_ids.append(encounter.id)
                del world.resolved_encounter_ids[:-80]
        world.current_activity = None
        world.movement_lock = None

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

    def available_areas(self, world: World, player: Player) -> list[str]:
        return self.scene_service.available_areas(world, player)

    def scene_description(self, world: World, player: Player) -> str:
        return self.scene_service.describe(world, self.location_at(world, player.position))

    def is_local_scene(self, world: World) -> bool:
        return self.scene_service.is_local(world)

    def active_npc(self, world: World, player: Player) -> Npc | None:
        location = self.location_at(world, player.position)
        return self.scene_service.active_npc(world, self.npc_at(location, world))

    def player_bonus(self, player: Player, check_kind: str | None = None) -> int:
        bonus = {"warrior": 4, "rogue": 3, "mage": 2, "ranger": 3}.get(player.archetype, 2)
        if check_kind:
            bonus += player.boosts.get(check_kind, 0)
            bonus += self._skill_bonus_for_check(player, check_kind)
        carried = {item.lower() for item in player.inventory}
        if check_kind == "exploration_check":
            if "torch" in carried:
                bonus += 1
            if player.archetype == "ranger":
                bonus += 1
        elif check_kind == "social_check":
            if player.archetype == "rogue":
                bonus += 1
        elif check_kind == "combat_check":
            if player.archetype == "warrior":
                bonus += 1
        return bonus

    def _skill_bonus_for_check(self, player: Player, check_kind: str) -> int:
        skill_groups = {
            "exploration_check": {
                "tracking",
                "stealth",
                "survival",
                "investigation",
                "perception",
                "spirit_lore",
                "ritual",
                "medicine",
                "sailing",
                "endurance",
            },
            "social_check": {
                "courtly_etiquette",
                "diplomacy",
                "deception",
                "insight",
                "command",
                "intimidation",
                "performance",
                "trade",
                "streetwise",
            },
            "combat_check": {
                "dueling",
                "archery",
                "melee",
                "tactics",
                "endurance",
                "command",
                "guard",
                "athletics",
            },
        }
        matching = [player.boosts.get(skill, 0) for skill in skill_groups.get(check_kind, set())]
        return max(matching, default=0)

    def _apply_beat_context(
        self,
        world: World,
        beat: DirectorBeat,
        player: Player | None = None,
        location: Location | None = None,
    ) -> None:
        world.current_choices = list(beat.choices[:4]) or self._default_choices(beat)
        if beat.mechanical_request == "combat_check" or "combat" in beat.tags:
            if player is not None:
                self._start_encounter(world, player, location, "Resolve the immediate hostile conflict.")
            else:
                world.current_activity = "combat"
                world.movement_lock = "you are in a fight"
        elif beat.mechanical_request in {"exploration_check", "social_check"}:
            world.current_activity = beat.mechanical_request.replace("_check", "")
            if world.active_encounter is None or not world.active_encounter.movement_locked:
                world.movement_lock = None

    def _default_choices(self, beat: DirectorBeat) -> list[str]:
        tags = set(beat.tags)
        if beat.mechanical_request == "combat_check" or "combat" in tags:
            return ["press the attack", "look for cover", "call for help"]
        if beat.mechanical_request == "social_check" or "social" in tags:
            return ["press for details", "offer help", "change the subject"]
        if beat.mechanical_request == "exploration_check" or "exploration" in tags:
            return ["search carefully", "follow the clue", "inspect the scene"]
        if "dialogue" in tags or "rumor" in tags:
            return ["press for details", "ask who else knows", "change the subject"]
        if "rest" in tags:
            return ["keep watch", "pack up", "push onward"]
        return ["press deeper", "change approach", "reassess"]

    def _merge_choices(self, primary: list[str], secondary: list[str], limit: int = 4) -> list[str]:
        merged: list[str] = []
        for choice in primary + secondary:
            cleaned = " ".join(choice.split())
            if not cleaned:
                continue
            if cleaned in merged:
                continue
            merged.append(cleaned)
            if len(merged) >= limit:
                break
        return merged

    def _roll_follow_up(
        self,
        action: str,
        success: bool,
        beat: DirectorBeat,
        location: Location | None,
        npc: Npc | None,
    ) -> dict[str, list[str] | str]:
        place = location.name if location is not None else "here"
        if action == "explore":
            if success:
                return {
                    "prompt": f"Next: follow the lead, search {place} again, or return to the road.",
                    "choices": ["follow the lead", "search again", "return to the road"],
                }
            return {
                "prompt": f"Next: regroup, try a different angle, or retreat from {place}.",
                "choices": ["regroup", "try a different angle", "retreat"],
            }
        if action == "attack":
            if success:
                return {
                    "prompt": "Next: press the advantage, search the fallen threat, or stand down.",
                    "choices": ["press the advantage", "search the fallen threat", "stand down"],
                }
            return {
                "prompt": "Next: hold your ground, retreat, or brace for another strike.",
                "choices": ["hold your ground", "retreat", "brace for another strike"],
            }
        if beat.mechanical_request == "social_check" or "social" in beat.tags:
            if success:
                return {
                    "prompt": "Next: press for details, offer help, or ask about the next lead.",
                    "choices": ["press for details", "offer help", "ask for the next lead"],
                }
            return {
                "prompt": "Next: change the subject, back off, or try a gentler approach.",
                "choices": ["change the subject", "back off", "try a gentler approach"],
            }
        if beat.mechanical_request == "exploration_check" or "exploration" in beat.tags:
            if success:
                return {
                    "prompt": f"Next: inspect the scene, follow it deeper, or leave {place} for now.",
                    "choices": ["inspect the scene", "follow it deeper", "leave for now"],
                }
            return {
                "prompt": "Next: look again, change tactics, or fall back and recover.",
                "choices": ["look again", "change tactics", "fall back"],
            }
        if location is not None:
            if success:
                return {
                    "prompt": f"Next: leave {place}, press deeper, or inspect the scene.",
                    "choices": ["leave area", "press deeper", "inspect the scene"],
                }
            return {
                "prompt": f"Next: force a way out, hold position, or study {place} for another opening.",
                "choices": ["force the exit", "hold position", "study the scene"],
            }
        return {
            "prompt": "Next: change approach, press deeper, or reassess.",
            "choices": ["change approach", "press deeper", "reassess"],
        }

    def _ensure_progression(self, world: World) -> None:
        self.progression.ensure(world)

    def _quest_from_hook(self, hook: str, index: int, world: World) -> Quest:
        return self.progression.quest_from_hook(hook, index, world)

    def _quest_title(self, hook: str, index: int) -> str:
        cleaned = " ".join(hook.strip().rstrip(".").split())
        if not cleaned:
            return f"Thread {index + 1}"
        words = cleaned.split()
        return " ".join(words[:5]).title()[:60]

    def _slug(self, text: str) -> str:
        chars: list[str] = []
        previous_dash = False
        for char in text.lower():
            if char.isalnum():
                chars.append(char)
                previous_dash = False
            elif not previous_dash:
                chars.append("-")
                previous_dash = True
        return "".join(chars).strip("-")[:60] or "quest"

    def _active_quest(self, world: World) -> Quest | None:
        return self.progression.active_quest(world)

    def _sync_active_quest_display(self, world: World) -> None:
        self.progression.sync_active_quest_display(world)

    def _apply_progression(
        self,
        world: World,
        player: Player,
        beat: DirectorBeat,
        memory: CampaignMemory,
        location: Location | None,
        success: bool | None,
    ) -> None:
        self.progression.apply_beat(
            world,
            player,
            beat,
            memory,
            location,
            success,
        )

    def _quest_stage_satisfied(
        self,
        quest: Quest,
        world: World,
        player: Player,
        location: Location | None,
    ) -> bool:
        stage = self.progression.current_stage(quest)
        return bool(stage and stage.conditions) and all(
            self.progression.condition_satisfied(condition, world, player, location)
            for condition in stage.conditions
        )

    def _condition_satisfied(
        self,
        condition: Condition,
        world: World,
        player: Player,
        location: Location | None,
    ) -> bool:
        return self.progression.condition_satisfied(
            condition,
            world,
            player,
            location,
        )

    def _advance_quest_stage(self, world: World, quest: Quest, memory: CampaignMemory) -> None:
        stage = self.progression.current_stage(quest)
        if stage is not None:
            self.progression._complete_stage(world, quest, stage, memory)

    def _apply_clock_effect(self, world: World, effect: dict[str, object]) -> None:
        self.progression.apply_clock_effect(world, effect)

    def _fire_clock_triggers(self, world: World, clock: QuestClock) -> None:
        self.progression.fire_clock_triggers(world, clock)

    def summary_counts(self, world: World) -> dict[str, int]:
        return {
            "locations": len(world.locations),
            "npcs": len(world.npcs),
            "events": len(world.recent_events),
            "hooks": len(world.quest_hooks),
        }

    def scene_objects_at(self, world: World, position: Position) -> list[str]:
        return list(world.scene_objects.get(self._position_key(position), []))

    def ensure_progression(self, world: World) -> None:
        self._ensure_entity_ids(world)
        self._ensure_progression(world)
        self._ensure_legacy_encounter(world)

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

        turn_id = f"turn-{world.tick:06d}-{len(world.turn_records) + 1:04d}"
        intent = director.interpret_freeform_action(
            world,
            player,
            action,
            location,
            npc,
            turn_id,
            memory_context,
        )
        intent.id = turn_id
        intent.raw_input = action
        intent.proposed_effects = self.turn_effects.prepare(intent, world)
        check = (
            self._resolve_check(world, player, intent.difficulty, intent.check_kind)
            if intent.check_kind is not None
            else None
        )
        staged_memory = deepcopy(memory)
        accepted, rejected = self.state_reducer.apply(
            world,
            player,
            intent.proposed_effects,
            check,
            lambda effect: self.turn_effects.validate(effect, intent, world, player),
            lambda effect: self.turn_effects.commit(effect, intent, world, player, staged_memory, location),
        )
        outcome_location = self.location_at(world, player.position)
        self.progression.evaluate(
            world,
            player,
            outcome_location,
            staged_memory,
        )
        memory.entries = staged_memory.entries
        outcome = TurnOutcome(
            success=check.success if check is not None else None,
            accepted_effects=accepted,
            rejected_effects=rejected,
            authoritative_summary=self.turn_effects.summarize(check, accepted, rejected),
        )
        choices, follow_up_prompt = self._turn_choices(intent, check, location, npc)
        world.current_choices = choices
        record = TurnRecord(
            id=turn_id,
            tick=world.tick,
            command=action,
            intent=intent,
            check=check,
            outcome=outcome,
            narration="",
            choices=choices,
        )
        outcome_npc = self.scene_service.active_npc(
            world,
            self.npc_at(outcome_location, world),
        )
        narration = director.narrate_turn_outcome(
            world,
            player,
            outcome_location,
            outcome_npc,
            record,
            memory_context,
        )
        record.narration = narration
        world.turn_records.append(record)
        del world.turn_records[:-100]

        place = location.name if location else "the wilds"
        fact = f"{player.name} near {place}: {outcome.authoritative_summary}"
        self._remember_state_fact(world, fact, world.tick)
        memory.remember(
            "action",
            f"{player.name}:{world.tick}:{action}",
            fact,
            world.tick,
            importance=6,
            tags=[player.name, place, "action"],
        )
        self._advance_world(world, player, director, memory, "freeform")
        memory.remember_world_state(world, player)
        suffix_parts = [check.summary] if check is not None else []
        if follow_up_prompt:
            suffix_parts.append(follow_up_prompt)
        suffix = f"\n\n{' '.join(suffix_parts)}" if suffix_parts else ""
        return CommandResult(f"{narration}{suffix}", advance_time=True)

    def _turn_choices(
        self,
        intent: ActionIntent,
        check: CheckResult | None,
        location: Location | None,
        npc: Npc | None,
    ) -> tuple[list[str], str]:
        beat = DirectorBeat(
            title=intent.title,
            narration="",
            mechanical_request=intent.check_kind.value if intent.check_kind is not None else None,
            tags=list(intent.tags),
            choices=list(intent.choices),
        )
        if check is None:
            return list(intent.choices[:4]) or self._default_choices(beat), ""
        follow_up = self._roll_follow_up(intent.raw_input, check.success, beat, location, npc)
        choices = self._merge_choices(follow_up["choices"], intent.choices)
        return choices, str(follow_up["prompt"])

    def replay_turn(self, record: TurnRecord, world: World, player: Player) -> None:
        """Apply a persisted outcome without rerolling or consulting a director."""

        self.state_reducer.apply_accepted(
            world,
            player,
            record.outcome.accepted_effects,
            lambda effect: (
                self.scene_service.replay_effect(effect, world, player)
                if self.scene_service.owns_effect(effect)
                else self.progression.replay_effect(effect, world, player)
                if self.progression.owns_effect(effect)
                else self.turn_effects.commit(
                    effect,
                    record.intent,
                    world,
                    player,
                    CampaignMemory(),
                    self.location_at(world, player.position),
                )
            ),
        )
        world.current_choices = list(record.choices)
        self.scene_service.refresh_actions(world)

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
            "inspect",
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
            "consume",
            "eat",
            "drink",
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
        visible_match = self._matches_known_object(item, visible_items)
        inventory_match = self._matches_known_object(item, inventory)
        state = self._object_state_for_target(world, position, item)
        known_object = visible_match or inventory_match or state is not None
        if not known_object:
            return None
        if verb in {"take", "get", "grab"} and inventory_match:
            return f"You already have {item}."
        if state is not None and state.get("status") in {"destroyed", "removed", "in_inventory"}:
            status = state.get("status")
            if status == "in_inventory":
                return f"You already have {item}."
            return f"The {item} is already {status}."
        if visible_match or inventory_match or state is not None:
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

    def remember_state_fact(self, world: World, fact: str, tick: int) -> None:
        self._remember_state_fact(world, fact, tick)

    def _dialogue_history(self, world: World, npc: Npc, limit: int = 8) -> list[str]:
        return world.conversations.get(npc.name, [])[-limit:]

    def _remember_dialogue(self, world: World, npc: Npc, line: str, limit: int = 16) -> None:
        history = world.conversations.setdefault(npc.name, [])
        history.append(line)
        del history[:-limit]

    def _inventory_summary(self, player: Player, world: World, location: Location | None) -> str:
        if not player.inventory:
            return "Inventory: empty."
        lines = ["Inventory:"]
        for item in player.inventory:
            description = world.inventory_descriptions.get(item, "No special notes yet.")
            lines.append(f"- {item.title()}: {description}")
        if location is not None:
            visible = self.scene_objects_at(world, player.position)
            if visible:
                lines.append("")
                lines.append("Visible scene objects:")
                lines.extend(f"- {item.title()}" for item in visible)
        return "\n".join(lines)

    def _inspect_target(self, target: str, world: World, player: Player, location: Location | None) -> str:
        cleaned = self._clean_item_name(target)
        if not cleaned:
            return "Inspect what?"
        if self._matches_known_object(cleaned, player.inventory):
            description = world.inventory_descriptions.get(cleaned, "No special notes yet.")
            return f"{cleaned.title()}: {description}"
        if location is not None:
            visible = self.scene_objects_at(world, player.position)
            if self._matches_known_object(cleaned, visible):
                state = self._object_state_for_target(world, player.position, cleaned)
                if state is not None:
                    status = state.get("status", "present")
                    owner = state.get("owner")
                    owner_text = f" Owner: {owner}." if isinstance(owner, str) and owner else ""
                    return f"{cleaned.title()} is here. Status: {status}.{owner_text}"
                return f"{cleaned.title()} is here, waiting to be taken or used."
        state = self._object_state_for_target(world, player.position, cleaned)
        if state is not None:
            status = state.get("status", "unknown")
            return f"{cleaned.title()} is recorded here with status {status}."
        return f"There is no clear sign of {cleaned}."

    def _use_inventory_item(
        self,
        target: str,
        world: World,
        player: Player,
        location: Location | None,
        director: Director,
        memory: CampaignMemory,
        memory_context: list[str],
    ) -> CommandResult:
        cleaned = self._clean_item_name(target)
        if not cleaned:
            return CommandResult("Use what?")
        if not self._matches_known_object(cleaned, player.inventory):
            return CommandResult(f"You do not have {cleaned}.")
        if cleaned in {"rations", "snack", "food"}:
            heal = self.random.randint(1, 3)
            player.hp = min(player.max_hp, player.hp + heal)
            player.inventory.remove(next(item for item in player.inventory if self._clean_item_name(item) == cleaned))
            memory.remember(
                "item",
                f"{player.name}:{cleaned}:{world.tick}",
                f"{player.name} used {cleaned} and recovered {heal} HP.",
                world.tick,
                importance=6,
                tags=[player.name, cleaned, "item"],
            )
            return CommandResult(f"You use {cleaned} and recover {heal} HP.", advance_time=True)
        if cleaned in {"torch", "light source"}:
            world.current_activity = "exploration"
            world.last_roll = "Item use: light is steady and the scene is easier to read."
            memory.remember(
                "item",
                f"{player.name}:{cleaned}:{world.tick}",
                f"{player.name} used {cleaned} to improve visibility.",
                world.tick,
                importance=6,
                tags=[player.name, cleaned, "item"],
            )
            return CommandResult(f"You ready the {cleaned}. The dark is easier to read now.", advance_time=True)
        description = world.inventory_descriptions.get(cleaned)
        if description:
            memory.remember(
                "item",
                f"{player.name}:{cleaned}:{world.tick}",
                f"{player.name} used {cleaned}.",
                world.tick,
                importance=5,
                tags=[player.name, cleaned, "item"],
            )
            beat = director.respond_to_freeform_action(world, player, f"use {cleaned}", location, None, memory_context)
            return CommandResult(f"{description} {beat.narration}", advance_time=True)
        return CommandResult(f"You use {cleaned}, but it does not do anything obvious.")

    def _drop_inventory_item(
        self,
        target: str,
        world: World,
        player: Player,
        location: Location | None,
        director: Director,
        memory: CampaignMemory,
    ) -> CommandResult:
        cleaned = self._clean_item_name(target)
        if not cleaned:
            return CommandResult("Drop what?")
        for index, item in enumerate(list(player.inventory)):
            if not self._matches_known_object(cleaned, [item]):
                continue
            player.inventory.pop(index)
            self._remember_scene_objects(world, player.position, [item])
            self._set_object_state(world, player.position, item, status="dropped", tick=world.tick, owner=player.name)
            memory.remember(
                "item",
                f"{player.name}:{cleaned}:{world.tick}:drop",
                f"{player.name} dropped {item}.",
                world.tick,
                importance=5,
                tags=[player.name, cleaned, "item"],
            )
            place = location.name if location else "the ground"
            return CommandResult(f"You drop {item} at {place}.", advance_time=True)
        return CommandResult(f"You do not have {cleaned}.")

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
        if world.campaign_status in self.progression.TERMINAL_STATUSES:
            self._refresh_alerts(world, player)
            self.scene_service.refresh_actions(world)
            return
        if self.random.random() < 0.35:
            world.weather = self.random.choice(
                ["Cold drizzle", "Harsh sunlight", "Crosswind", "Quiet fog", "Distant thunder"]
            )
        if cause != "move" and self.random.random() < 0.4:
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
        self.progression.evaluate(
            world,
            player,
            active_location,
            memory,
        )
        self._refresh_alerts(world, player)
        self.scene_service.refresh_actions(world)

    def advance_world(
        self,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
        cause: str,
    ) -> None:
        self._advance_world(world, player, director, memory, cause)

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
        campaign_title = details.get("campaign_title")
        if isinstance(campaign_title, str) and campaign_title.strip():
            world.campaign_title = campaign_title.strip()[:80]

        overarching_quest = details.get("overarching_quest")
        if isinstance(overarching_quest, str) and overarching_quest.strip():
            world.overarching_quest = overarching_quest.strip()[:240]

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
                location.summary = summary.strip()[:110]

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
            world.quest_hooks = [hook[:130] for hook in hooks[:6]]

        archetypes = [item.strip().lower() for item in details.get("player_archetypes", []) if isinstance(item, str) and item.strip()]
        if archetypes:
            world.player_archetype_options = archetypes[:6]

        blurbs = details.get("player_archetype_blurbs")
        if isinstance(blurbs, dict):
            world.player_archetype_blurbs = {
                key: value
                for key, value in blurbs.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        boosts = details.get("player_archetype_boosts")
        if isinstance(boosts, dict):
            world.player_archetype_boosts = {
                key: {
                    boost_key: boost_value
                    for boost_key, boost_value in value.items()
                    if isinstance(boost_key, str) and isinstance(boost_value, int)
                }
                for key, value in boosts.items()
                if isinstance(key, str) and isinstance(value, dict)
            }

        homelands = [item.strip() for item in details.get("homelands", []) if isinstance(item, str) and item.strip()]
        if homelands:
            world.homeland_options = homelands[:8]

        homeland_descriptions = details.get("homeland_descriptions")
        if isinstance(homeland_descriptions, dict):
            world.homeland_descriptions = {
                key: value
                for key, value in homeland_descriptions.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        inventory = [item.strip().lower() for item in details.get("starting_inventory", []) if isinstance(item, str) and item.strip()]
        if inventory:
            world.starting_inventory = inventory[:5]

        inventory_descriptions = details.get("inventory_descriptions")
        if isinstance(inventory_descriptions, dict):
            world.inventory_descriptions = {
                key: value
                for key, value in inventory_descriptions.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        skill_descriptions = details.get("skill_descriptions")
        if isinstance(skill_descriptions, dict):
            world.skill_descriptions = {
                key: value
                for key, value in skill_descriptions.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        opening_event = details.get("opening_event")
        if isinstance(opening_event, str) and opening_event.strip():
            self._add_event(world, "world", opening_event.strip()[:180])

    def _add_event(self, world: World, category: str, text: str, severity: str = "info") -> None:
        world.recent_events.insert(0, Event(tick=world.tick, category=category, text=text, severity=severity))
        del world.recent_events[6:]

    def _roll_check(self, world: World, player: Player, difficulty: int, check_kind: str | None = None) -> bool:
        try:
            kind = CheckKind(check_kind or CheckKind.GENERIC.value)
        except ValueError:
            kind = CheckKind.GENERIC
        return self._resolve_check(world, player, difficulty, kind).success

    def resolve_typed_check(
        self,
        world: World,
        player: Player,
        difficulty: int,
        check_kind: CheckKind,
    ) -> CheckResult:
        return self._resolve_check(world, player, difficulty, check_kind)

    def _resolve_check(
        self,
        world: World,
        player: Player,
        difficulty: int,
        check_kind: CheckKind,
    ) -> CheckResult:
        roll = self.random.randint(1, 20)
        bonus = self.player_bonus(player, check_kind.value)
        total = roll + bonus
        success = total >= difficulty
        result = CheckResult(
            kind=check_kind,
            difficulty=difficulty,
            raw_roll=roll,
            bonus=bonus,
            total=total,
            success=success,
        )
        world.last_roll = result.summary
        return result

    def _roll_attack(self, world: World, player: Player, difficulty: int) -> bool:
        return self._roll_check(world, player, difficulty, "combat_check")

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
        locations = locations[:12]
        for index, location in enumerate(locations):
            location.id = f"location-{index + 1:03d}"
        return locations

    def _generate_npcs(self, locations: list[Location]) -> list[Npc]:
        first_names = ["Mira", "Thane", "Ivo", "Sable", "Orrin", "Kael", "Brin", "Lysa"]
        roles = ["guide", "warden", "merchant", "scribe", "hunter", "priest"]
        moods = ["wary", "friendly", "guarded", "intense", "skeptical"]
        npcs: list[Npc] = []
        for index, location in enumerate(locations[:8]):
            npcs.append(
                Npc(
                    name=self.random.choice(first_names),
                    role=self.random.choice(roles),
                    disposition=self.random.choice(moods),
                    location_name=location.name,
                    id=f"npc-{index + 1:03d}",
                    location_id=location.id,
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
