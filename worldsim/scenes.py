from __future__ import annotations

from typing import TYPE_CHECKING

from worldsim import area
from worldsim.memory import CampaignMemory
from worldsim.models import (
    ActionIntent,
    ActionKind,
    CheckKind,
    CommandResult,
    EffectCondition,
    EffectKind,
    EffectSource,
    Location,
    Npc,
    Player,
    SceneMode,
    SceneState,
    StateEffect,
    TurnOutcome,
    TurnRecord,
    World,
)

if TYPE_CHECKING:
    from worldsim.director import Director
    from worldsim.engine import WorldEngine


class SceneService:
    """Owns persistent local-scene transitions, actions, and presentation state."""

    def __init__(self, engine: WorldEngine) -> None:
        self.engine = engine

    def owns_effect(self, effect: StateEffect) -> bool:
        return effect.kind in {
            EffectKind.SCENE_ENTER,
            EffectKind.SCENE_EXIT,
            EffectKind.SCENE_STEP,
            EffectKind.SCENE_TENSION,
        }

    def replay_effect(self, effect: StateEffect, world: World, player: Player) -> None:
        self._commit(
            effect,
            world,
            player,
            self.engine.location_at(world, player.position),
        )

    def sync(self, world: World, player: Player, location: Location | None) -> None:
        location_id = location.id if location is not None else None
        scene = world.active_scene
        if (
            scene is not None
            and scene.mode == SceneMode.LOCAL
            and scene.location_id == location_id
        ):
            self.refresh_actions(world)
            return
        scene_id = self._overworld_scene_id(location_id, player)
        if scene is None or scene.id != scene_id or scene.mode != SceneMode.OVERWORLD:
            world.active_scene = SceneState(
                id=scene_id,
                mode=SceneMode.OVERWORLD,
                location_id=location_id,
            )
        else:
            scene.location_id = location_id
        self.refresh_actions(world)

    def available_areas(
        self,
        world: World,
        player: Player,
        location: Location | None = None,
    ) -> list[str]:
        current_location = location if location is not None else self.engine.location_at(world, player.position)
        biome = self.engine.biome_at(world, player.position)
        return area.area_choices(current_location, biome)

    def is_local(self, world: World) -> bool:
        return world.active_scene is not None and world.active_scene.mode == SceneMode.LOCAL

    def active_npc(self, world: World, fallback: Npc | None = None) -> Npc | None:
        scene = world.active_scene
        if scene is not None and scene.mode == SceneMode.LOCAL and scene.local_npc_id:
            return next((npc for npc in world.npcs if npc.id == scene.local_npc_id), fallback)
        return fallback

    def describe(self, world: World, location: Location | None) -> str:
        scene = world.active_scene
        if scene is None or scene.mode != SceneMode.LOCAL:
            return "You are not inside a local scene."
        npc = self.active_npc(world)
        return area.scene_text(
            location,
            scene.area_name,
            scene.step,
            scene.theme,
            scene.hazard,
            npc.name if npc is not None else None,
        )

    def refresh_actions(self, world: World) -> list[str]:
        scene = world.active_scene
        if scene is None:
            return []
        if scene.mode == SceneMode.OVERWORLD:
            scene.available_actions = []
            return []
        encounter_locked = (
            world.active_encounter is not None
            and world.active_encounter.movement_locked
        )
        choices = ["force exit" if encounter_locked and not scene.exit_open else "leave area"]
        if not encounter_locked:
            if scene.step < 2:
                choices.append("push deeper")
            if scene.step > 0:
                choices.append("pull back")
        choices.append("inspect the scene")
        if scene.local_npc_id is not None:
            choices.append("talk")
        if encounter_locked:
            choices.append("press the advantage")
        elif scene.local_npc_id is None:
            choices.append("reassess")
        scene.available_actions = choices[:6]
        return list(scene.available_actions)

    def resolve(
        self,
        command: str,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
    ) -> CommandResult | None:
        text = " ".join(command.lower().split())
        scene = world.active_scene
        local = scene is not None and scene.mode == SceneMode.LOCAL
        if local and text == "look":
            return CommandResult(self.describe(world, self.engine.location_at(world, player.position)))

        area_name = None
        if text.startswith("enter area "):
            area_name = command.strip()[11:].strip()
        elif text == "enter area":
            return CommandResult("Enter which area?")
        elif text not in {
            "leave area",
            "force exit",
            "push deeper",
            "pull back",
            "flee",
            "retreat",
            "withdraw",
        }:
            return None

        location = self.engine.location_at(world, player.position)
        if area_name is not None:
            if local:
                return CommandResult("Leave the current local scene before entering another.")
            if self.engine.movement_lock_reason(world) is not None:
                return CommandResult("You cannot enter a new area while an encounter is unresolved.")
            available = self.available_areas(world, player, location)
            canonical = next((item for item in available if item.casefold() == area_name.casefold()), None)
            if canonical is None:
                return CommandResult("That area is not reachable from the current position.")
            return self._resolve_effect_turn(
                command,
                "Enter Local Scene",
                f"Transition from the overworld into {canonical}.",
                [
                    StateEffect(
                        kind=EffectKind.SCENE_ENTER,
                        target_id=canonical,
                        source=EffectSource.ENGINE,
                    )
                ],
                None,
                world,
                player,
                director,
                memory,
                location,
            )

        if not local:
            if text in {"flee", "retreat", "withdraw"}:
                return None
            return CommandResult("You are not inside a local area.")
        assert scene is not None

        if text in {"leave area", "flee", "retreat", "withdraw"}:
            if self.engine.movement_lock_reason(world) is not None and not scene.exit_open:
                text = "force exit"
            else:
                return self._resolve_effect_turn(
                    command,
                    "Leave Local Scene",
                    f"Return from {scene.area_name or 'the area'} to its overworld location.",
                    [
                        StateEffect(
                            kind=EffectKind.SCENE_EXIT,
                            source=EffectSource.ENGINE,
                        )
                    ],
                    None,
                    world,
                    player,
                    director,
                    memory,
                    location,
                )

        if text == "force exit":
            if self.engine.movement_lock_reason(world) is None:
                return self._resolve_effect_turn(
                    command,
                    "Leave Local Scene",
                    f"Return from {scene.area_name or 'the area'} to its overworld location.",
                    [
                        StateEffect(
                            kind=EffectKind.SCENE_EXIT,
                            source=EffectSource.ENGINE,
                        )
                    ],
                    None,
                    world,
                    player,
                    director,
                    memory,
                    location,
                )
            difficulty = min(20, 8 + scene.tension + scene.step)
            check = self.engine.resolve_typed_check(
                world,
                player,
                difficulty,
                CheckKind.EXPLORATION,
            )
            return self._resolve_effect_turn(
                command,
                "Force the Exit",
                f"Escape {scene.area_name or 'the area'} despite {scene.hazard or 'the danger'}.",
                [
                    StateEffect(
                        kind=EffectKind.SCENE_EXIT,
                        condition=EffectCondition.SUCCESS,
                        source=EffectSource.ENGINE,
                    ),
                    StateEffect(
                        kind=EffectKind.ENCOUNTER_ESCAPE,
                        value="The player escaped through the local scene exit.",
                        condition=EffectCondition.SUCCESS,
                        source=EffectSource.ENGINE,
                    ),
                    StateEffect(
                        kind=EffectKind.SCENE_TENSION,
                        amount=1,
                        condition=EffectCondition.FAILURE,
                        source=EffectSource.ENGINE,
                    ),
                ],
                check,
                world,
                player,
                director,
                memory,
                location,
            )

        if self.engine.movement_lock_reason(world) is not None:
            return CommandResult("Resolve the encounter or force the exit before moving within the area.")
        if text == "push deeper":
            if scene.step >= 2:
                return CommandResult("You are already at the deepest point of this area.")
            effects = [
                StateEffect(
                    kind=EffectKind.SCENE_STEP,
                    amount=1,
                    source=EffectSource.ENGINE,
                ),
                StateEffect(
                    kind=EffectKind.SCENE_TENSION,
                    amount=1,
                    source=EffectSource.ENGINE,
                ),
            ]
            title = "Push Deeper"
            stakes = f"Move farther into {scene.area_name or 'the area'}."
        else:
            if scene.step <= 0:
                return CommandResult("You are already at the edge of this area.")
            effects = [
                StateEffect(
                    kind=EffectKind.SCENE_STEP,
                    amount=-1,
                    source=EffectSource.ENGINE,
                )
            ]
            title = "Pull Back"
            stakes = f"Move toward the edge of {scene.area_name or 'the area'}."
        return self._resolve_effect_turn(
            command,
            title,
            stakes,
            effects,
            None,
            world,
            player,
            director,
            memory,
            location,
        )

    def _resolve_effect_turn(
        self,
        command: str,
        title: str,
        stakes: str,
        effects: list[StateEffect],
        check,
        world: World,
        player: Player,
        director: Director,
        memory: CampaignMemory,
        location: Location | None,
    ) -> CommandResult:
        turn_id = f"turn-{world.tick:06d}-{len(world.turn_records) + 1:04d}"
        intent = ActionIntent(
            id=turn_id,
            raw_input=command,
            kind=ActionKind.EXPLICIT,
            title=title,
            stakes=stakes,
            check_kind=check.kind if check is not None else None,
            difficulty=check.difficulty if check is not None else 10,
            proposed_effects=effects,
            tags=["scene"],
        )
        if check is None:
            world.last_roll = None
        accepted, rejected = self.engine.state_reducer.apply(
            world,
            player,
            effects,
            check,
            lambda effect: self._validate(effect, world, player, location),
            lambda effect: self._commit(effect, world, player, location),
        )
        self.refresh_actions(world)
        outcome = TurnOutcome(
            success=check.success if check is not None else None,
            accepted_effects=accepted,
            rejected_effects=rejected,
            authoritative_summary=self.engine.turn_effects.summarize(check, accepted, rejected),
        )
        narration = self._narration(title, check, world, location)
        choices = self.refresh_actions(world)
        world.current_choices = list(choices)
        record = TurnRecord(
            id=turn_id,
            tick=world.tick,
            command=command,
            intent=intent,
            check=check,
            outcome=outcome,
            narration=narration,
            choices=choices,
        )
        world.turn_records.append(record)
        del world.turn_records[:-100]
        self.engine.remember_state_fact(
            world,
            f"{player.name}: {outcome.authoritative_summary}",
            world.tick,
        )
        memory.remember(
            "scene",
            turn_id,
            f"{title}: {outcome.authoritative_summary}",
            world.tick,
            importance=6,
            tags=[
                "scene",
                world.active_scene.area_name
                if world.active_scene is not None and world.active_scene.area_name
                else "overworld",
            ],
        )
        self.engine.advance_world(world, player, director, memory, "scene")
        memory.remember_world_state(world, player)
        suffix = f"\n\n{check.summary}" if check is not None else ""
        return CommandResult(f"{narration}{suffix}", advance_time=True)

    def _validate(
        self,
        effect: StateEffect,
        world: World,
        player: Player,
        location: Location | None,
    ) -> str | None:
        scene = world.active_scene
        if effect.source != EffectSource.ENGINE:
            return "scene lifecycle effects are engine-owned"
        if effect.kind == EffectKind.SCENE_ENTER:
            if scene is not None and scene.mode == SceneMode.LOCAL:
                return "a local scene is already active"
            if effect.target_id not in self.available_areas(world, player, location):
                return "the destination area is unavailable"
        elif effect.kind == EffectKind.SCENE_EXIT:
            if scene is None or scene.mode != SceneMode.LOCAL:
                return "there is no local scene to exit"
        elif effect.kind == EffectKind.SCENE_STEP:
            if scene is None or scene.mode != SceneMode.LOCAL:
                return "there is no local scene to move through"
            if not 0 <= scene.step + effect.amount <= 2:
                return "the requested local step is outside the scene"
        elif effect.kind == EffectKind.SCENE_TENSION:
            if scene is None or scene.mode != SceneMode.LOCAL:
                return "there is no local scene whose tension can change"
        elif effect.kind == EffectKind.ENCOUNTER_ESCAPE:
            return self.engine.turn_effects.validate(
                effect,
                ActionIntent(id="scene", raw_input="force exit"),
                world,
                player,
            )
        else:
            return "unsupported scene effect"
        return None

    def _commit(
        self,
        effect: StateEffect,
        world: World,
        player: Player,
        location: Location | None,
    ) -> None:
        scene = world.active_scene
        if effect.kind == EffectKind.SCENE_ENTER:
            assert effect.target_id is not None
            biome = self.engine.biome_at(world, player.position)
            npc = self.engine.npc_at(location, world)
            parent_id = self._overworld_scene_id(location.id if location else None, player)
            world.active_scene = SceneState(
                id=f"{parent_id}:local:{self._slug(effect.target_id)}",
                mode=SceneMode.LOCAL,
                location_id=location.id if location is not None else None,
                parent_scene_id=parent_id,
                area_name=effect.target_id,
                entered_tick=world.tick,
                step=0,
                tension=area.initial_tension(location),
                theme=area.area_theme(biome),
                hazard=area.area_hazard(
                    biome,
                    area.stable_area_hash(world.seed, effect.target_id, effect.target_id),
                ),
                local_npc_id=npc.id if npc is not None else None,
                exit_open=False,
            )
            world.dialogue_state = None
        elif effect.kind == EffectKind.SCENE_EXIT:
            location_id = scene.location_id if scene is not None else (location.id if location else None)
            world.active_scene = SceneState(
                id=self._overworld_scene_id(location_id, player),
                mode=SceneMode.OVERWORLD,
                location_id=location_id,
            )
            world.dialogue_state = None
        elif effect.kind == EffectKind.SCENE_STEP:
            assert scene is not None
            scene.step = max(0, min(2, scene.step + effect.amount))
            world.dialogue_state = None
        elif effect.kind == EffectKind.SCENE_TENSION:
            assert scene is not None
            scene.tension = max(0, min(9, scene.tension + effect.amount))
        elif effect.kind == EffectKind.ENCOUNTER_ESCAPE:
            self.engine.turn_effects.commit(
                effect,
                ActionIntent(id="scene", raw_input="force exit"),
                world,
                player,
                CampaignMemory(),
                location,
            )

    def _narration(self, title: str, check, world: World, location: Location | None) -> str:
        scene = world.active_scene
        if title == "Enter Local Scene":
            return self.describe(world, location)
        if title == "Leave Local Scene":
            return f"You leave the local scene and return to {location.name if location else 'the overworld'}."
        if title == "Push Deeper":
            return f"You push deeper into {scene.area_name if scene else 'the area'}."
        if title == "Pull Back":
            return f"You pull back toward the edge of {scene.area_name if scene else 'the area'}."
        if check is not None and check.success:
            return f"You find a viable way out and escape to {location.name if location else 'the overworld'}."
        hazard = scene.hazard if scene is not None else "The danger"
        return f"The exit remains out of reach. {hazard or 'The area'} tightens around you."

    def _overworld_scene_id(self, location_id: str | None, player: Player) -> str:
        return (
            f"scene:{location_id}"
            if location_id
            else f"scene:{player.position.x},{player.position.y}"
        )

    def _slug(self, text: str) -> str:
        return "-".join("".join(char.lower() if char.isalnum() else " " for char in text).split()) or "area"
