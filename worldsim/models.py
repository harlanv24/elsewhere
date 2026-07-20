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


class EncounterStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ESCAPED = "escaped"


class ConditionKind(str, Enum):
    ITEM_ACQUIRED = "item_acquired"
    NPC_RECRUITED = "npc_recruited"
    FACT_DISCOVERED = "fact_discovered"
    TARGET_DEFEATED = "target_defeated"
    LOCATION_REACHED = "location_reached"
    OBJECT_ACTIVATED = "object_activated"
    CHOICE_COMMITTED = "choice_committed"
    CLOCK_THRESHOLD = "clock_threshold"


class ClockTriggerKind(str, Enum):
    ADD_FACT = "add_fact"
    FAIL_QUEST = "fail_quest"
    STABILITY_DELTA = "stability_delta"
    START_ENCOUNTER = "start_encounter"


class ActionKind(str, Enum):
    FREEFORM = "freeform"
    EXPLICIT = "explicit"


class CheckKind(str, Enum):
    GENERIC = "check"
    EXPLORATION = "exploration_check"
    SOCIAL = "social_check"
    COMBAT = "combat_check"


class EffectKind(str, Enum):
    SCENE_OBJECT_ADD = "scene_object_add"
    SCENE_OBJECT_REMOVE = "scene_object_remove"
    INVENTORY_ADD = "inventory_add"
    INVENTORY_REMOVE = "inventory_remove"
    OBJECT_STATUS = "object_status"
    QUEST_HOOK_ADD = "quest_hook_add"
    FACT_DISCOVERED = "fact_discovered"
    QUEST_PROGRESS = "quest_progress"
    CLOCK_DELTA = "clock_delta"
    ENCOUNTER_RESOLVE = "encounter_resolve"
    ENCOUNTER_ESCAPE = "encounter_escape"
    ENCOUNTER_START = "encounter_start"


class EffectCondition(str, Enum):
    ALWAYS = "always"
    SUCCESS = "success"
    FAILURE = "failure"


class EffectSource(str, Enum):
    DIRECTOR = "director"
    ENGINE = "engine"


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
    id: str = ""


@dataclass
class Npc:
    name: str
    role: str
    disposition: str
    location_name: str
    id: str = ""
    location_id: str | None = None


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
class Condition:
    """An engine-verifiable requirement for completing a quest stage."""

    kind: ConditionKind
    target_id: str
    expected: str | None = None
    minimum: int | None = None


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
    stage_conditions: list[list[Condition]] = field(default_factory=list)


@dataclass
class ClockTrigger:
    """A structured, exactly-once consequence fired when a clock fills."""

    id: str
    kind: ClockTriggerKind
    target_id: str | None = None
    amount: int = 0
    text: str = ""
    fired: bool = False


@dataclass
class QuestClock:
    id: str
    title: str
    value: int = 0
    max_value: int = 6
    description: str = ""
    status: str = "active"
    triggers: list[ClockTrigger] = field(default_factory=list)
    triggered: bool = False


@dataclass
class SceneState:
    """Persistent engine-owned state for the player's current scene."""

    id: str
    location_id: str | None = None
    area_name: str | None = None
    step: int = 0
    tension: int = 0
    theme: str | None = None
    hazard: str | None = None
    local_npc_id: str | None = None
    exit_open: bool = False
    available_actions: list[str] = field(default_factory=list)


@dataclass
class EncounterState:
    """Persistent encounter state; movement locks are derived from its status."""

    id: str
    kind: str
    participants: list[str]
    objective: str
    phase: str = "opening"
    obstacles: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)
    status: EncounterStatus = EncounterStatus.ACTIVE
    resolution: str | None = None

    @property
    def movement_locked(self) -> bool:
        return self.status == EncounterStatus.ACTIVE


@dataclass
class DialogueState:
    """Explicit, persistent conversation mode instead of history-based inference."""

    npc_id: str
    npc_name: str
    started_tick: int
    active: bool = True


@dataclass(frozen=True)
class StateEffect:
    """A proposed or engine-authored mutation evaluated by the state reducer."""

    kind: EffectKind
    target_id: str | None = None
    value: str | None = None
    amount: int = 0
    condition: EffectCondition = EffectCondition.SUCCESS
    flag: bool = False
    source: EffectSource = EffectSource.DIRECTOR


@dataclass(frozen=True)
class RejectedEffect:
    effect: StateEffect
    reason: str


@dataclass
class ActionIntent:
    """A non-authoritative interpretation of what the player is attempting."""

    id: str
    raw_input: str
    kind: ActionKind = ActionKind.FREEFORM
    title: str = "Improvised Action"
    stakes: str = ""
    check_kind: CheckKind | None = None
    difficulty: int = 10
    proposed_effects: list[StateEffect] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    choices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckResult:
    kind: CheckKind
    difficulty: int
    raw_roll: int
    bonus: int
    total: int
    success: bool

    @property
    def summary(self) -> str:
        sign = "+" if self.bonus >= 0 else ""
        verdict = "success" if self.success else "failure"
        return (
            f"Roll: {self.kind.value.replace('_', ' ')} vs DC {self.difficulty}: "
            f"raw d20 {self.raw_roll}, bonus {sign}{self.bonus}, total {self.total} -> {verdict}."
        )


@dataclass
class TurnOutcome:
    success: bool | None
    accepted_effects: list[StateEffect] = field(default_factory=list)
    rejected_effects: list[RejectedEffect] = field(default_factory=list)
    authoritative_summary: str = ""


@dataclass
class TurnRecord:
    """Persisted authoritative record of one resolved turn."""

    id: str
    tick: int
    command: str
    intent: ActionIntent
    check: CheckResult | None
    outcome: TurnOutcome
    narration: str
    choices: list[str] = field(default_factory=list)


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
    facts_discovered: list[str] = field(default_factory=list)


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
    active_scene: SceneState | None = None
    active_encounter: EncounterState | None = None
    dialogue_state: DialogueState | None = None
    discovered_facts: list[str] = field(default_factory=list)
    committed_choices: list[str] = field(default_factory=list)
    turn_records: list[TurnRecord] = field(default_factory=list)


@dataclass
class CommandResult:
    message: str
    advance_time: bool = False
    should_quit: bool = False
