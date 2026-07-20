from __future__ import annotations

from dataclasses import dataclass

import pytest

from worldsim.director import Director
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory
from worldsim.models import (
    ActionIntent,
    Biome,
    DirectorBeat,
    Location,
    Npc,
    Player,
    Position,
    Quest,
    QuestClock,
    TurnRecord,
    World,
)


class ScriptedDirector(Director):
    def __init__(self) -> None:
        self.action_beats: dict[str, DirectorBeat] = {}
        self.freeform_beat = DirectorBeat(title="Action", narration="The attempt changes nothing.")
        self.dialogue_beat: DirectorBeat | str = DirectorBeat(
            title="Dialogue",
            narration="The witness answers cautiously.",
        )
        self.interpretation_observations: list[dict[str, object]] = []
        self.narration_observations: list[dict[str, object]] = []
        self.outcome_narration: str | None = None

    def introduce_world(self, world, player, memory_context=None) -> str:
        return "The campaign begins."

    def describe_location(self, world, player, location, npc, memory_context=None) -> str:
        return f"You are at {location.name if location else 'the frontier'}."

    def respond_to_action(self, world, player, action, location, npc, memory_context=None) -> DirectorBeat:
        return self.action_beats.get(
            action,
            DirectorBeat(title=action.title(), narration=f"You attempt to {action}."),
        )

    def ambient_world_event(self, world) -> str:
        return "A harmless wind crosses the road."

    def respond_to_freeform_action(
        self,
        world,
        player,
        action,
        location,
        npc,
        memory_context=None,
    ) -> DirectorBeat:
        return self.freeform_beat

    def interpret_freeform_action(
        self,
        world,
        player,
        action,
        location,
        npc,
        intent_id,
        memory_context=None,
    ) -> ActionIntent:
        self.interpretation_observations.append(
            {
                "scene_objects": list(world.scene_objects.get(f"{player.position.x},{player.position.y}", [])),
                "object_states": dict(world.object_states),
                "last_roll": world.last_roll,
            }
        )
        return super().interpret_freeform_action(
            world,
            player,
            action,
            location,
            npc,
            intent_id,
            memory_context,
        )

    def narrate_turn_outcome(
        self,
        world,
        player,
        location,
        npc,
        record: TurnRecord,
        memory_context=None,
    ) -> str:
        self.narration_observations.append(
            {
                "scene_objects": list(world.scene_objects.get(f"{player.position.x},{player.position.y}", [])),
                "object_states": dict(world.object_states),
                "check": record.check,
                "outcome": record.outcome,
            }
        )
        if self.outcome_narration is not None:
            return self.outcome_narration
        return super().narrate_turn_outcome(
            world,
            player,
            location,
            npc,
            record,
            memory_context,
        )

    def respond_to_dialogue(
        self,
        world,
        player,
        player_dialogue,
        location,
        npc,
        memory_context=None,
        dialogue_history=None,
    ) -> DirectorBeat | str:
        return self.dialogue_beat


@dataclass
class GameState:
    engine: WorldEngine
    world: World
    player: Player
    memory: CampaignMemory
    director: ScriptedDirector


@pytest.fixture
def game_state() -> GameState:
    engine = WorldEngine(seed=1)
    locations = [
        Location(
            id="location-market",
            name="Market",
            position=Position(0, 0),
            biome=Biome.PLAIN,
            danger=2,
            summary="A crowded starting point.",
        ),
        Location(
            id="location-observatory",
            name="Observatory",
            position=Position(2, 0),
            biome=Biome.HILL,
            danger=3,
            summary="A distant tower.",
        ),
    ]
    npc = Npc(
        id="npc-witness",
        name="Iris",
        role="witness",
        disposition="guarded",
        location_name="Market",
        location_id="location-market",
    )
    quest = Quest(
        id="main-thread",
        title="The Main Thread",
        goal="Learn what happened.",
        stages=["Find evidence.", "Resolve the threat."],
        progress_required=2,
        related_locations=["location-market"],
        related_npcs=["npc-witness"],
    )
    clock = QuestClock(
        id="pressure",
        title="Pressure",
        value=0,
        max_value=4,
        description="Trouble gathers.",
    )
    world = World(
        seed=1,
        tick=1,
        width=3,
        height=2,
        tiles=[
            [Biome.PLAIN, Biome.PLAIN, Biome.HILL],
            [Biome.PLAIN, Biome.PLAIN, Biome.PLAIN],
        ],
        locations=locations,
        npcs=[npc],
        quests=[quest],
        clocks=[clock],
        active_quest_id=quest.id,
    )
    player = Player(
        name="Rowan",
        archetype="ranger",
        homeland="Market",
        hp=16,
        max_hp=16,
        gold=0,
        xp=0,
        position=Position(0, 0),
        inventory=["torch", "rations"],
    )
    engine.ensure_progression(world)
    engine._sync_scene(world, player)
    return GameState(
        engine=engine,
        world=world,
        player=player,
        memory=CampaignMemory(),
        director=ScriptedDirector(),
    )
