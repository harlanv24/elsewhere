from __future__ import annotations

from dataclasses import dataclass

import pytest

from worldsim.director import Director
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory
from worldsim.models import (
    Biome,
    DirectorBeat,
    Location,
    Npc,
    Player,
    Position,
    Quest,
    QuestClock,
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
