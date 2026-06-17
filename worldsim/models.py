from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from worldsim.usage import UsageTotals


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
    boosts: dict[str, int] = field(default_factory=dict)


@dataclass
class Event:
    tick: int
    category: str
    text: str
    severity: str = "info"


@dataclass
class Quest:
    id: str
    title: str
    goal: str
    stages: list[str]
    current_stage: int = 0
    progress: int = 0
    progress_required: int = 2
    status: str = "active"
    related_locations: list[str] = field(default_factory=list)
    related_npcs: list[str] = field(default_factory=list)
    discoveries: list[str] = field(default_factory=list)


@dataclass
class QuestClock:
    id: str
    title: str
    value: int = 0
    max_value: int = 6
    description: str = ""
    status: str = "active"


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
    choices: list[str] = field(default_factory=list)
    progress_summary: str | None = None
    quest_progress_delta: int = 0
    complete_current_stage: bool = False
    clock_effects: list[dict[str, object]] = field(default_factory=list)


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
    theme_prompt: str = "character-driven adventure"
    campaign_title: str = "Untitled Frontier"
    overarching_quest: str = "Uncover the central threat shaping the frontier."
    active_quest: str | None = None
    active_quest_id: str | None = None
    quests: list[Quest] = field(default_factory=list)
    clocks: list[QuestClock] = field(default_factory=list)
    usage_totals: UsageTotals = field(default_factory=UsageTotals)
    current_choices: list[str] = field(default_factory=list)
    current_activity: str | None = None
    movement_lock: str | None = None
    last_roll: str | None = None
    player_archetype_options: list[str] = field(default_factory=lambda: ["warrior", "rogue", "mage", "ranger"])
    player_archetype_blurbs: dict[str, str] = field(default_factory=dict)
    player_archetype_boosts: dict[str, dict[str, int]] = field(default_factory=dict)
    homeland_options: list[str] = field(default_factory=list)
    starting_inventory: list[str] = field(default_factory=lambda: ["notebook", "light source", "snack"])
    inventory_descriptions: dict[str, str] = field(default_factory=dict)
    skill_descriptions: dict[str, str] = field(default_factory=dict)
    homeland_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass
class CommandResult:
    message: str
    advance_time: bool = False
    should_quit: bool = False
