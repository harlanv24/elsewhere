from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Biome(str, Enum):
    WATER = "Water"
    PLAIN = "Plain"
    FOREST = "Forest"
    HILL = "Hill"
    MOUNTAIN = "Mountain"
    SWAMP = "Swamp"

    @property
    def glyph(self) -> str:
        return {
            Biome.WATER: "~",
            Biome.PLAIN: ".",
            Biome.FOREST: '"',
            Biome.HILL: "^",
            Biome.MOUNTAIN: "A",
            Biome.SWAMP: ",",
        }[self]


@dataclass(frozen=True)
class Position:
    x: int
    y: int


@dataclass
class Location:
    name: str
    position: Position
    biome: Biome
    danger: int
    summary: str


@dataclass
class Npc:
    name: str
    role: str
    disposition: str
    location_name: str


@dataclass
class Player:
    name: str
    archetype: str
    homeland: str
    hp: int
    max_hp: int
    gold: int
    xp: int
    position: Position
    inventory: list[str] = field(default_factory=lambda: ["bedroll", "torch", "rations"])


@dataclass
class Event:
    tick: int
    category: str
    text: str
    severity: str = "info"


@dataclass
class DirectorBeat:
    title: str
    narration: str
    mechanical_request: str | None = None
    difficulty: int = 10
    tags: list[str] = field(default_factory=list)
    follow_up_hook: str | None = None
    scene_objects: list[str] = field(default_factory=list)
    inventory_add: list[str] = field(default_factory=list)
    inventory_remove: list[str] = field(default_factory=list)


@dataclass
class World:
    seed: int
    tick: int
    width: int
    height: int
    tiles: list[list[Biome]]
    locations: list[Location]
    npcs: list[Npc]
    recent_events: list[Event] = field(default_factory=list)
    quest_hooks: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    conversations: dict[str, list[str]] = field(default_factory=dict)
    scene_objects: dict[str, list[str]] = field(default_factory=dict)
    object_states: dict[str, dict[str, object]] = field(default_factory=dict)
    state_facts: list[str] = field(default_factory=list)
    weather: str = "Clear"
    stability: int = 70


@dataclass
class CommandResult:
    message: str
    advance_time: bool = False
    should_quit: bool = False
