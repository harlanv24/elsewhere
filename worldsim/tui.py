from __future__ import annotations

import re
import random
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.timer import Timer
from textual.widgets import Button, Input, RichLog, Static
from textual.widgets._content_switcher import ContentSwitcher
from textual.widgets._tabbed_content import TabPane, TabbedContent

from worldsim.ascii_render import AsciiRenderer
from worldsim.command_input import normalize_command_input
from worldsim.debug import DebugLogger
from worldsim.director import director_from_env
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory, CampaignStore
from worldsim.models import Location, Npc, Player, Position, SceneMode, World
from worldsim.usage import TokenUsageTracker, UsageTotals, format_tokens

if TYPE_CHECKING:
    from textual.events import Click


THINKING_VERBS = [
    "thinking",
    "weaving",
    "reckoning",
    "scrying",
    "drafting",
    "pondering",
    "plotting",
    "listening",
    "consulting",
    "composing",
    "divining",
    "tracing",
    "weighing",
    "shaping",
    "attuning",
    "remembering",
]


@dataclass
class Session:
    world: World
    player: Player
    memory: CampaignMemory
    last_message: str
    transcript: list[str] = field(default_factory=list)
    last_command: str | None = None
    selected_area: str | None = None


def _partial_json_string_field(text: str, field: str) -> str | None:
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        key, key_end = _complete_json_string(text, index)
        if key != field or key_end is None:
            index += 1
            continue
        cursor = key_end + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != ":":
            index = key_end + 1
            continue
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] != '"':
            return None
        return _partial_json_string_value(text, cursor)
    return None


def _complete_json_string(text: str, start: int) -> tuple[str | None, int | None]:
    value = _partial_json_string_value(text, start)
    if value is None:
        return None, None
    cursor = start + 1
    escaped = False
    while cursor < len(text):
        char = text[cursor]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return value, cursor
        cursor += 1
    return None, None


def _partial_json_string_value(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] != '"':
        return None
    cursor = start + 1
    chars: list[str] = []
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            return "".join(chars)
        if char != "\\":
            chars.append(char)
            cursor += 1
            continue
        if cursor + 1 >= len(text):
            break
        escaped = text[cursor + 1]
        if escaped in {'"', "\\", "/"}:
            chars.append(escaped)
            cursor += 2
        elif escaped == "b":
            chars.append("\b")
            cursor += 2
        elif escaped == "f":
            chars.append("\f")
            cursor += 2
        elif escaped == "n":
            chars.append("\n")
            cursor += 2
        elif escaped == "r":
            chars.append("\r")
            cursor += 2
        elif escaped == "t":
            chars.append("\t")
            cursor += 2
        elif escaped == "u":
            hex_value = text[cursor + 2 : cursor + 6]
            if len(hex_value) < 4:
                break
            try:
                chars.append(chr(int(hex_value, 16)))
            except ValueError:
                chars.append("\\u" + hex_value)
            cursor += 6
        else:
            chars.append(escaped)
            cursor += 2
    return "".join(chars)


class MapPanel(Static):
    can_focus = True
    BINDINGS = [
        Binding("up", "move_north", show=False),
        Binding("down", "move_south", show=False),
        Binding("left", "move_west", show=False),
        Binding("right", "move_east", show=False),
        Binding("shift+up", "pan_up", show=False),
        Binding("shift+down", "pan_down", show=False),
        Binding("shift+left", "pan_left", show=False),
        Binding("shift+right", "pan_right", show=False),
        Binding("c", "center_map", show=False),
        Binding("f", "toggle_follow", show=False),
    ]

    def on_click(self, event: Click) -> None:
        del event
        self.focus()

    def action_move_north(self) -> None:
        self.app.action_move_north()

    def action_move_south(self) -> None:
        self.app.action_move_south()

    def action_move_east(self) -> None:
        self.app.action_move_east()

    def action_move_west(self) -> None:
        self.app.action_move_west()

    def action_pan_up(self) -> None:
        self.app.action_pan_up()

    def action_pan_down(self) -> None:
        self.app.action_pan_down()

    def action_pan_left(self) -> None:
        self.app.action_pan_left()

    def action_pan_right(self) -> None:
        self.app.action_pan_right()

    def action_center_map(self) -> None:
        self.app.action_center_map()

    def action_toggle_follow(self) -> None:
        self.app.action_toggle_follow()


class ChoiceCard(Static):
    can_focus = True

    def __init__(self, choice_index: int, label: str = "", **kwargs) -> None:
        super().__init__(label, expand=True, shrink=True, markup=False, **kwargs)
        self.choice_index = choice_index

    def on_click(self, event: Click) -> None:
        del event
        self.app.action_select_choice(self.choice_index)


class WorldSimApp(App[None]):
    CSS_PATH = "worldsim.tcss"
    BINDINGS = [
        Binding("ctrl+n", "show_setup", "New Campaign", show=False),
        Binding("m", "show_map_tab", "Map", show=False, priority=True),
        Binding("w", "show_world_tab", "World", show=False, priority=True),
        Binding("escape", "focus_map", "Map Focus", show=False, priority=True),
        Binding("slash", "focus_command", "Command", show=False, priority=True),
    ]

    def __init__(
        self,
        store: CampaignStore,
        engine: WorldEngine | None = None,
        debug_logger: DebugLogger | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.engine = engine or WorldEngine()
        self.ascii_renderer = AsciiRenderer()
        self.debug_logger = debug_logger
        self.director = director_from_env(self.engine.seed, debug_logger)
        self.session: Session | None = None
        self.loaded_session = self.store.load()
        self.camera_x = 0
        self.camera_y = 0
        self.follow_player = True
        self.area_choices: list[str] = []
        self.command_in_progress = False
        self.stream_buffer = ""
        self.loading_timer: Timer | None = None
        self.loading_step = 0
        self.loading_theme = ""
        self.loading_context = ""
        self.loading_verb = ""
        self.loading_random = random.Random()
        self.synced_usage = UsageTotals()
        self.pending_world: World | None = None
        self.selected_archetype: str | None = None
        self.selected_homeland: str | None = None
        self.selected_inventory_index = 0
        self.selected_skill_index = 0

    def compose(self) -> ComposeResult:
        with Container(id="root"):
            with ContentSwitcher(
                id="switcher",
                initial="landing-screen",
            ):
                yield from self._compose_landing_screen()
                yield from self._compose_setup_screen()
                yield from self._compose_game_screen()

    def on_mount(self) -> None:
        self.title = "Worldsim"
        self.sub_title = "Living world simulator"
        self._set_panel_titles()

    def _compose_landing_screen(self) -> ComposeResult:
        with Container(id="landing-screen"):
            with Vertical(id="landing-card"):
                yield Static(self._elsewhere_banner(), id="landing-title")
                yield Static(self._save_blurb(), id="save-summary")
                with Horizontal(id="landing-actions"):
                    yield Button("Continue Save", id="continue-button", variant="primary", disabled=self.loaded_session is None)
                    yield Button("New Game", id="new-game-button")
                    yield Button("Quit", id="quit-button")

    def _compose_setup_screen(self) -> ComposeResult:
        subtitle = (
            "Create a wanderer for a new campaign. The world and compact memory will persist locally."
            if self.loaded_session is None
            else "A campaign save exists. Continue from the game screen or start over here."
        )
        with Container(id="setup-screen"):
            with Vertical(id="setup-card"):
                yield Static(self._elsewhere_banner(), id="setup-title")
                yield Static(subtitle, id="setup-subtitle")
                with Vertical(id="setup-inputs"):
                    yield Input(placeholder="Name", value="Rowan", id="name-input")
                    yield Input(
                        placeholder="World theme: workplace sitcom, haunted suburb, space noir, coastal mystery...",
                        value="",
                        id="theme-input",
                    )
                with Vertical(id="character-select"):
                    with Horizontal(id="selection-grid"):
                        with Vertical(id="class-options-panel"):
                            yield Static("Classes", id="class-options-title")
                            for index in range(6):
                                yield Button("Class", id=f"class-option-{index}", compact=True)
                        with Vertical(id="homeland-options-panel"):
                            yield Static("Homelands", id="homeland-options-title")
                            for index in range(8):
                                yield Button("Homeland", id=f"homeland-option-{index}", compact=True)
                        yield Static("", id="class-detail-panel")
                with Horizontal(id="setup-actions"):
                    yield Button("Generate World", id="start-button", variant="primary")
                    yield Button("X", id="setup-close-button")
                yield Static("", id="setup-error")

    def _compose_game_screen(self) -> ComposeResult:
        with Container(id="game-screen"):
            yield Static(id="topbar")
            with TabbedContent(initial="tab-world"):
                with TabPane("WORLD", id="tab-world"):
                    with Horizontal(classes="row"):
                        yield MapPanel(id="map-panel", classes="panel world-map")
                        with Vertical(classes="world-main"):
                            yield RichLog(id="local-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            with Vertical(id="director-panel", classes="panel"):
                                yield RichLog(id="director-text", auto_scroll=False, highlight=False, wrap=True)
                                with Horizontal(id="adventure-command-bar"):
                                    yield Static(">", id="adventure-prompt")
                                    yield Input(placeholder="Type an action, dialogue, or command", id="adventure-command-input")
                                    yield Button("Send", id="adventure-send-button", compact=True)
                                    yield Button("Quit", id="game-quit-button", compact=True)
                                with Horizontal(id="inventory-quick-bar"):
                                    yield Static(id="selected-item-panel")
                                    yield Button("Prev Item", id="selected-inventory-prev", compact=True)
                                    yield Button("Next Item", id="selected-inventory-next", compact=True)
                                    yield Button("Use Item", id="use-selected-inventory", compact=True, variant="primary")
                                    yield Button("Inspect Item", id="inspect-selected-inventory", compact=True)
                            with Vertical(id="actions-panel", classes="panel"):
                                yield Static("Choices", id="choice-title")
                                for index in range(4):
                                    yield ChoiceCard(index, "Choice", id=f"choice-card-{index}")
                        with Vertical(classes="world-sidebar"):
                            yield RichLog(id="region-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="events-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="alerts-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="summary-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                with TabPane("MAP", id="tab-map"):
                    with Vertical(classes="map-tab"):
                        yield MapPanel(id="overview-map-panel", classes="panel")
                        yield Static("Press `m` to return to the world view.", id="map-tab-note")
                with TabPane("CHARACTER", id="tab-character"):
                    with Horizontal(classes="row"):
                        with Vertical(classes="stack"):
                            yield RichLog(id="player-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="hooks-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="resources-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                        with Vertical(classes="stack"):
                            with Vertical(id="inventory-panel", classes="panel"):
                                for index in range(8):
                                    yield Button("Item", id=f"inventory-item-{index}", compact=True)
                            yield RichLog(id="loadout-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="packs-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                        with Vertical(classes="stack"):
                            with Vertical(id="skills-panel", classes="panel"):
                                for index in range(8):
                                    yield Button("Skill", id=f"skill-btn-{index}", compact=True)
                            yield RichLog(id="progression-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="traits-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                            yield RichLog(id="milestones-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
                with TabPane("SYSTEM", id="tab-system"):
                    yield RichLog(id="system-panel", classes="panel", auto_scroll=False, highlight=False, wrap=True)
            yield RichLog(id="console-panel", classes="panel", auto_scroll=True, highlight=False, wrap=True, min_width=20)
            yield Static(self._footer_text(), id="footer-note")

    def _set_panel_titles(self) -> None:
        titles = {
            "#map-panel": "WORLD MAP",
            "#region-panel": "SELECTED REGION",
            "#events-panel": "RECENT EVENTS",
            "#alerts-panel": "ALERTS",
            "#summary-panel": "WORLD SUMMARY",
            "#player-panel": "PLAYER",
            "#hooks-panel": "QUEST HOOKS",
            "#actions-panel": "ACTIONS",
            "#local-panel": "LOCAL DETAILS",
            "#director-panel": "DIRECTOR",
            "#inventory-panel": "INVENTORY",
            "#resources-panel": "RESOURCES",
            "#loadout-panel": "LOADOUT",
            "#packs-panel": "PACKS",
            "#skills-panel": "SKILL TREE",
            "#progression-panel": "PROGRESSION",
            "#traits-panel": "ARCHETYPE TRAITS",
            "#milestones-panel": "NEXT MILESTONES",
            "#system-panel": "SYSTEM NOTES",
            "#console-panel": "CONSOLE",
            "#overview-map-panel": "FULL MAP",
        }
        for selector, title in titles.items():
            widget = self.query_one(selector)
            widget.border_title = title

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "continue-button":
            self._continue_save()
            return
        if button_id == "new-game-button":
            self.action_show_setup()
            return
        if button_id in {"quit-button", "setup-close-button"}:
            if button_id == "setup-close-button":
                self._return_to_landing()
            else:
                self.exit()
            return
        if button_id == "game-quit-button":
            self.exit()
            return
        if button_id == "adventure-send-button":
            self._submit_command_input("#adventure-command-input")
            return
        if button_id == "selected-inventory-prev":
            self._select_inventory_step(-1)
            return
        if button_id == "selected-inventory-next":
            self._select_inventory_step(1)
            return
        if button_id == "use-selected-inventory":
            self._use_selected_inventory_item()
            return
        if button_id == "inspect-selected-inventory":
            self._inspect_selected_inventory_item()
            return
        if button_id == "start-button":
            self._start_new_campaign()
            return
        if button_id.startswith("class-option-"):
            self._select_generated_class(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id.startswith("homeland-option-"):
            self._select_generated_homeland(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id.startswith("choice-card-"):
            self._select_story_choice(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id.startswith("inventory-item-"):
            self._select_inventory_item(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id.startswith("skill-btn-"):
            self._select_skill(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id.startswith("area-btn-"):
            self._select_area(int(button_id.rsplit("-", 1)[1]))
            return
        if button_id == "area-enter":
            self._enter_selected_area()
            return
        if button_id == "area-leave":
            self._leave_area()
            return
        if button_id == "area-forward":
            self._move_within_area(1)
            return
        if button_id == "area-back":
            self._move_within_area(-1)
            return
        if button_id == "area-try-leave":
            self._try_leave_area()
            return
        if button_id.startswith("action-"):
            command = button_id.replace("action-", "", 1)
            self._append_transcript(f"> {command}")
            self._handle_command(command)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "adventure-command-input":
            self._submit_command_input(f"#{event.input.id}")

    def _submit_command_input(self, selector: str) -> None:
        command_input = self.query_one(selector, Input)
        entered = command_input.value.strip()
        command_input.value = ""
        command = self._command_from_input(entered)
        if command:
            self._append_transcript(self._input_echo(entered, command))
            self._handle_command(command)

    def action_focus_command(self) -> None:
        if self.session is not None:
            command_input = self.query_one("#adventure-command-input", Input)
            if not command_input.value:
                command_input.value = "/"
                command_input.cursor_position = 1
            command_input.focus()

    def _command_from_input(self, entered: str) -> str:
        return normalize_command_input(entered, dialogue_active=self._dialogue_npc() is not None)

    def _input_echo(self, entered: str, command: str) -> str:
        if command.startswith("say ") and not entered.startswith("/"):
            player_name = self.session.player.name if self.session is not None else "You"
            return f"{player_name}: {entered}"
        return f"> {entered}"

    def _dialogue_npc(self) -> Npc | None:
        if self.session is None:
            return None
        location = self.engine.location_at(self.session.world, self.session.player.position)
        npc = self.engine.npc_at(location, self.session.world)
        dialogue = self.session.world.dialogue_state
        if (
            npc is None
            or dialogue is None
            or not dialogue.active
            or dialogue.npc_id not in {npc.id, npc.name}
        ):
            return None
        return npc

    def action_focus_map(self) -> None:
        if self.session is not None:
            self.query_one("#map-panel", MapPanel).focus()

    def action_show_map_tab(self) -> None:
        if self.session is None:
            return
        self.query_one(TabbedContent).active = "tab-map"
        self.query_one("#overview-map-panel", MapPanel).focus()
        self.call_after_refresh(self._sync_camera_after_layout)

    def action_show_world_tab(self) -> None:
        if self.session is None:
            return
        self.query_one(TabbedContent).active = "tab-world"
        self.query_one("#map-panel", MapPanel).focus()
        self.call_after_refresh(self._sync_camera_after_layout)

    def action_move_north(self) -> None:
        self._handle_command("north")

    def action_move_south(self) -> None:
        self._handle_command("south")

    def action_move_east(self) -> None:
        self._handle_command("east")

    def action_move_west(self) -> None:
        self._handle_command("west")

    def action_pan_up(self) -> None:
        self._pan_map(0, -4)

    def action_pan_down(self) -> None:
        self._pan_map(0, 4)

    def action_pan_left(self) -> None:
        self._pan_map(-6, 0)

    def action_pan_right(self) -> None:
        self._pan_map(6, 0)

    def action_center_map(self) -> None:
        if self.session is None:
            return
        self.follow_player = True
        self._center_camera_on_player()
        self.session.last_message = "Camera recentered and returned to follow mode."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def action_toggle_follow(self) -> None:
        if self.session is None:
            return
        self.follow_player = not self.follow_player
        if self.follow_player:
            self._center_camera_on_player()
            self.session.last_message = "Camera follow mode enabled."
        else:
            self.session.last_message = "Camera follow mode disabled. Manual panning is active."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def action_show_setup(self) -> None:
        self.pending_world = None
        self.selected_archetype = None
        self.selected_homeland = None
        self.query_one("#switcher", ContentSwitcher).current = "setup-screen"
        self.query_one("#start-button", Button).label = "Generate World"
        self.query_one("#character-select").styles.display = "none"
        self.query_one("#class-detail-panel", Static).update("")
        self.query_one("#setup-error", Static).update("")
        self.query_one("#name-input", Input).focus()

    def _return_to_landing(self) -> None:
        self.pending_world = None
        self.selected_archetype = None
        self.selected_homeland = None
        self._stop_loading_animation()
        self.command_in_progress = False
        self.query_one("#switcher", ContentSwitcher).current = "landing-screen"

    def _continue_save(self) -> None:
        if self.loaded_session is None:
            return
        world, player, memory = self.loaded_session
        self.engine.ensure_progression(world)
        self.engine.ensure_navigation(world)
        memory.remember_world_state(world, player)
        self.session = Session(
            world=world,
            player=player,
            memory=memory,
            last_message="Campaign restored from local save. The director is rebuilding context from memory.",
            transcript=["System: Campaign restored from local save."],
            selected_area=None,
        )
        self._center_camera_on_player()
        self.query_one("#switcher", ContentSwitcher).current = "game-screen"
        self._refresh_ui()
        self.call_after_refresh(self._sync_camera_after_layout)
        self.action_focus_map()

    def _save_blurb(self) -> str:
        if self.loaded_session is None:
            return "No saved campaign found.\nCreate a new world to begin."
        world, player, memory = self.loaded_session
        self.engine.ensure_progression(world)
        self.engine.ensure_navigation(world)
        modified = "unknown"
        if self.store.path.exists():
            modified = datetime.fromtimestamp(self.store.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        location = self.engine.location_at(world, player.position)
        place = location.name if location is not None else "the open map"
        return "\n".join(
            [
                f"{world.campaign_title}",
                f"{player.name}, {player.archetype}, at {place}",
                f"Tick {world.tick} | {len(memory.entries)} memories | Last played {modified}",
                f"Quest: {world.overarching_quest}",
            ]
        )

    def _elsewhere_banner(self) -> Text:
        banner = "\n".join(
            [
                " _______ _       _______ _______ _______ _     _ _______ _______ _______ _______",
                "|______| |      |   |   |_____| |______ |_____| |_____| |______ |______ |______",
                "|       | |_____ |   |   |     | ______| |   |   ______| |______ |______ ______|",
                "",
                "  _______  _______  _______  _______  _______  _______  _______  _______",
                " |   ____||   ____||   ____||   ____||   ____||   ____||   ____||   ____|",
                " |  |____ |  |____ |  |____ |  |____ |  |____ |  |____ |  |____ |  |____",
                " |____   ||____   ||____   ||____   ||____   ||____   ||____   ||____   |",
                "  ____|  | ____|  | ____|  | ____|  | ____|  | ____|  | ____|  | ____|  |",
            ]
        )
        return Text(banner, style="bold #f8d774")

    def _start_new_campaign(self) -> None:
        if self.command_in_progress:
            return
        if self.pending_world is not None:
            self._begin_generated_campaign()
            return
        name = self.query_one("#name-input", Input).value.strip() or "Rowan"
        del name
        theme = self.query_one("#theme-input", Input).value.strip() or "character-driven adventure"

        self.engine = WorldEngine()
        self.director = director_from_env(self.engine.seed, self.debug_logger)
        self.synced_usage = UsageTotals()
        self.command_in_progress = True
        self.stream_buffer = ""
        self.query_one("#setup-error", Static).update("")
        self._start_loading_animation(theme)
        self.run_worker(
            lambda: self._generate_world_worker(theme),
            thread=True,
            exclusive=True,
        )

    def _generate_world_worker(self, theme: str) -> None:
        self._set_stream_callback("LLM world generation stream")
        world = self.engine.create_world(self.director, theme)
        if not world.homeland_options:
            world.homeland_options = [location.name for location in world.locations[:6]]
        self.call_from_thread(self._finish_world_generation, world)

    def _finish_world_generation(self, world: World) -> None:
        self._clear_stream_callback()
        self._stop_loading_animation()
        self._sync_usage_totals(world)
        self.pending_world = world
        self.selected_archetype = world.player_archetype_options[0] if world.player_archetype_options else "ranger"
        self.selected_homeland = world.homeland_options[0] if world.homeland_options else world.locations[0].name
        self.query_one("#setup-subtitle", Static).update(
            f"{world.campaign_title}\nChoose a class and homeland, then begin."
        )
        self.query_one("#character-select").styles.display = "block"
        self._refresh_character_selection()
        self.query_one("#start-button", Button).label = "Begin Campaign"
        self.query_one("#setup-error", Static).update(self._generation_status_text(world))
        self.command_in_progress = False

    def _begin_generated_campaign(self) -> None:
        assert self.pending_world is not None
        name = self.query_one("#name-input", Input).value.strip() or "Rowan"
        archetype = (self.selected_archetype or "").lower()
        if archetype not in self.pending_world.player_archetype_options:
            archetype = self.pending_world.player_archetype_options[0] if self.pending_world.player_archetype_options else "ranger"
        homeland = self.selected_homeland or ""
        if homeland not in self.pending_world.homeland_options:
            homeland = self.pending_world.homeland_options[0] if self.pending_world.homeland_options else self.pending_world.locations[0].name
        self.command_in_progress = True
        self._start_loading_animation("Opening scene", context="Introducing campaign")
        self.run_worker(
            lambda: self._start_new_campaign_worker(self.pending_world, name, archetype, homeland),
            thread=True,
            exclusive=True,
        )

    def _start_new_campaign_worker(self, world: World, name: str, archetype: str, homeland: str) -> None:
        player = self.engine.create_player(world, name, archetype, homeland)
        memory = CampaignMemory()
        memory.remember_world_state(world, player)
        for quest in world.quests:
            memory.remember_hook(f"{quest.title}: {quest.goal}", world.tick)
        last_message = self.director.introduce_world(world, player, memory.relevant_context(world, player))
        session = Session(
            world=world,
            player=player,
            memory=memory,
            last_message=last_message,
            transcript=[f"DM: {last_message}"],
            selected_area=None,
        )
        self.call_from_thread(self._finish_new_campaign, session)

    def _finish_new_campaign(self, session: Session) -> None:
        self._clear_stream_callback()
        self._stop_loading_animation()
        self.pending_world = None
        self.session = session
        self._sync_usage_totals(session.world)
        self.store.save(session.world, session.player, session.memory)
        self.command_in_progress = False
        self.follow_player = True
        self._center_camera_on_player()
        self.query_one("#switcher", ContentSwitcher).current = "game-screen"
        self._refresh_ui()
        self.call_after_refresh(self._sync_camera_after_layout)
        self.action_focus_map()

    def _select_generated_class(self, index: int) -> None:
        if self.pending_world is None or not 0 <= index < len(self.pending_world.player_archetype_options):
            return
        self.selected_archetype = self.pending_world.player_archetype_options[index]
        self._refresh_character_selection()

    def _select_generated_homeland(self, index: int) -> None:
        if self.pending_world is None or not 0 <= index < len(self.pending_world.homeland_options):
            return
        self.selected_homeland = self.pending_world.homeland_options[index]
        self._refresh_character_selection()

    def _refresh_character_selection(self) -> None:
        if self.pending_world is None:
            return
        for index in range(6):
            button = self.query_one(f"#class-option-{index}", Button)
            if index < len(self.pending_world.player_archetype_options):
                archetype = self.pending_world.player_archetype_options[index]
                label = self._button_label(archetype, 20)
                button.label = f"> {label}" if archetype == self.selected_archetype else label
                button.disabled = False
            else:
                button.label = "Unavailable"
                button.disabled = True
        for index in range(8):
            button = self.query_one(f"#homeland-option-{index}", Button)
            if index < len(self.pending_world.homeland_options):
                homeland = self.pending_world.homeland_options[index]
                label = self._button_label(homeland, 20)
                button.label = f"> {label}" if homeland == self.selected_homeland else label
                button.disabled = False
            else:
                button.label = "Unavailable"
                button.disabled = True
        self.query_one("#class-detail-panel", Static).update(self._selected_class_detail())

    def _selected_class_detail(self) -> str:
        if self.pending_world is None or self.selected_archetype is None:
            return "Generate a world to choose a class."
        archetype = self.selected_archetype
        blurb = self.pending_world.player_archetype_blurbs.get(archetype, "A flexible adventurer shaped by this world.")
        boosts = self.pending_world.player_archetype_boosts.get(archetype, {})
        lines = [archetype.title(), "", blurb, "", "Skill Bonuses:"]
        if boosts:
            lines.extend(
                f"- {self._skill_label(skill)} {self._format_bonus(value)}"
                for skill, value in sorted(boosts.items())
                if value
            )
        else:
            lines.append("- No specialized bonuses listed.")
        lines.extend(["", "Homeland:", self.selected_homeland or "Choose a homeland."])
        if self.selected_homeland:
            description = self.pending_world.homeland_descriptions.get(self.selected_homeland)
            if description:
                lines.extend(["", description])
        return "\n".join(lines)

    def _skill_label(self, skill: str) -> str:
        return skill.replace("_", " ").title()

    def _button_label(self, value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(1, limit - 1)].rstrip() + "..."

    def _wrap_paragraphs(self, text: str, width: int) -> str:
        paragraphs = text.split("\n")
        wrapped: list[str] = []
        for paragraph in paragraphs:
            if not paragraph.strip():
                wrapped.append("")
                continue
            wrapped.append(textwrap.fill(paragraph, width=width, break_long_words=False, break_on_hyphens=False))
        return "\n".join(wrapped)

    def _generation_status_text(self, world: World) -> str:
        if not getattr(self.director, "last_used_fallback", False):
            return "LLM world generation succeeded."
        error = getattr(self.director, "last_error", "unknown error")
        debug_path = str(self.debug_logger.path.resolve()) if self.debug_logger is not None else "debug log disabled"
        return (
            "LLM world generation fell back to defaults.\n"
            f"Reason: {error}\n"
            f"Debug log: {debug_path}"
        )

    def _start_loading_animation(self, theme: str, context: str = "Generating world") -> None:
        self.loading_theme = theme
        self.loading_context = context
        self.loading_verb = self.loading_random.choice(THINKING_VERBS)
        self.loading_step = 0
        self._render_loading_animation()
        if self.loading_timer is not None:
            self.loading_timer.stop()
        self.loading_timer = self.set_interval(0.35, self._advance_loading_animation)

    def _stop_loading_animation(self) -> None:
        if self.loading_timer is not None:
            self.loading_timer.stop()
            self.loading_timer = None
        self.loading_context = ""
        self.loading_verb = ""

    def _advance_loading_animation(self) -> None:
        self.loading_step += 1
        if self.loading_step % 4 == 0:
            choices = [verb for verb in THINKING_VERBS if verb != self.loading_verb]
            self.loading_verb = self.loading_random.choice(choices or THINKING_VERBS)
        self._render_loading_animation()

    def _render_loading_animation(self) -> None:
        if not self.loading_context:
            return
        if self.session is None:
            self.query_one("#setup-subtitle", Static).update(self._loading_text())
            return
        world = self.session.world
        self.query_one("#topbar", Static).update(self._topbar_text(world))
        self._write_panel("#director-text", self._thinking_text(include_context=True))
        self.query_one("#footer-note", Static).update(self._thinking_text())

    def _loading_text(self) -> Text:
        text = self._thinking_text(include_context=True)
        if self.loading_theme:
            text.append("\n")
            text.append(self.loading_theme, style="dim #9ca3af")
        return text

    def _thinking_text(self, include_context: bool = False) -> Text:
        frames = ["|", "/", "-", "\\", "·", "*"]
        frame = frames[self.loading_step % len(frames)]
        model = self._model_label()
        context = self.loading_context or "Dungeon Master"
        verb = self.loading_verb or "thinking"
        text = Text()
        text.append(frame, style="bold #facc15")
        text.append(" ")
        text.append("DM ", style="bold #f9a8d4")
        text.append(verb, style="bold #67e8f9")
        if include_context:
            text.append(f" - {context}", style="#c4b5fd")
        text.append(f" [{model}]", style="dim #9ca3af")
        return text

    def _handle_command(self, command: str) -> None:
        if self.session is None:
            return
        if self.command_in_progress:
            self._append_transcript("System: Still waiting on the current LLM response.")
            return
        self.session.last_command = command
        self.command_in_progress = True
        self.stream_buffer = ""
        self._start_loading_animation(command, context="Resolving turn")
        self.run_worker(lambda: self._handle_command_worker(command), thread=True, exclusive=True)

    def _handle_command_worker(self, command: str) -> None:
        if self.session is None:
            self.call_from_thread(self._finish_command_without_result)
            return
        self._set_stream_callback(f"LLM command stream: {command}")
        result = self.engine.resolve_command(
            command,
            self.session.world,
            self.session.player,
            self.director,
            self.session.memory,
        )
        self.call_from_thread(self._finish_command, result)

    def _finish_command_without_result(self) -> None:
        self._clear_stream_callback()
        self._stop_loading_animation()
        self.command_in_progress = False
        self.query_one("#footer-note", Static).update(self._footer_text())

    def _finish_command(self, result) -> None:
        self._clear_stream_callback()
        self._stop_loading_animation()
        self.command_in_progress = False
        if self.session is None:
            return
        self._sync_usage_totals(self.session.world)
        self.session.last_message = result.message
        self._append_transcript(f"DM: {result.message}")
        self.store.save(self.session.world, self.session.player, self.session.memory)
        if self.follow_player:
            self._track_player_in_view()
        self._refresh_ui()
        if self.session.player.hp <= 0:
            self.session.last_message = "You fall, and the story closes around you."
            self._append_transcript(f"System: {self.session.last_message}")
            self._refresh_ui()
        if result.should_quit:
            self.exit()

    def _set_stream_callback(self, label: str) -> None:
        if not hasattr(self.director, "on_stream_delta"):
            return
        self.call_from_thread(self._begin_stream, label)
        self.director.on_stream_delta = lambda delta: self.call_from_thread(self._append_stream_delta, delta)

    def _clear_stream_callback(self) -> None:
        if hasattr(self.director, "on_stream_delta"):
            self.director.on_stream_delta = None

    def _begin_stream(self, label: str) -> None:
        self.stream_buffer = ""
        if self.session is not None:
            self.session.last_message = f"{label}..."
            live = self._thinking_text(include_context=True)
            live.append("\n")
            live.append(label, style="dim #9ca3af")
            self._refresh_console_panel(live)
            self._write_panel("#director-text", self._thinking_text(include_context=True))

    def _append_stream_delta(self, delta: str) -> None:
        self.stream_buffer += delta
        if self.session is not None:
            narration = _partial_json_string_field(self.stream_buffer, "narration")
            if narration:
                live = self._thinking_text(include_context=True)
                live.append("\n")
                live.append(narration[-2400:], style="#e5e7eb")
                self._refresh_console_panel(live)
            else:
                live = self._thinking_text(include_context=True)
                live.append("\nWaiting for narration...", style="dim #9ca3af")
                self._refresh_console_panel(live)

    def on_resize(self, event: Resize) -> None:
        del event
        if self.session is None:
            return
        if self.follow_player:
            self._track_player_in_view()
        self._refresh_ui()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if self.session is None or event.pane.id not in {"tab-world", "tab-map"}:
            return
        self.call_after_refresh(self._sync_camera_after_layout)

    def _sync_camera_after_layout(self) -> None:
        if self.session is None:
            return
        if self.follow_player:
            self._center_camera_on_player()
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        if self.session is None:
            return

        world = self.session.world
        player = self.session.player
        memory = self.session.memory
        location = self.engine.location_at(world, player.position)
        npc = self.engine.active_npc(world, player)
        self._sync_area_state(location)

        self.query_one("#topbar", Static).update(
            self._topbar_text(world)
        )
        self._refresh_map_panels(world)
        self._write_panel("#region-panel", self._region_text(location))
        self._write_panel("#events-panel", self._events_text(world))
        self._write_panel("#alerts-panel", self._alerts_text(world))
        self._write_panel("#summary-panel", self._summary_text(world))
        self._write_panel("#player-panel", self._player_text(player))
        self._write_panel("#hooks-panel", self._hooks_text(world))
        self._refresh_choice_buttons()
        self._write_panel("#local-panel", self._local_text(location, npc, memory))
        self._write_panel("#director-text", self.session.last_message)
        self.query_one("#selected-item-panel", Static).update(self._selected_inventory_text(player))
        self._refresh_inventory_buttons(player)
        self._write_panel("#resources-panel", self._resources_text(player, world))
        self._write_panel("#loadout-panel", self._inventory_detail_text(player))
        self._write_panel("#packs-panel", self._packs_text(player))
        self._refresh_skill_buttons(player)
        self._write_panel("#progression-panel", self._progression_text(player))
        self._write_panel("#traits-panel", self._skill_detail_text(player))
        self._write_panel("#milestones-panel", self._milestones_text(player))
        self._write_panel("#system-panel", self._system_text(memory))
        if not self.command_in_progress:
            self.query_one("#footer-note", Static).update(self._footer_text())
        self._refresh_console_panel()

    def _refresh_console_panel(self, live_stream: str | Text | None = None) -> None:
        if self.session is None:
            return
        console_panel = self.query_one("#console-panel", RichLog)
        console_panel.clear()
        for line in self.session.transcript[-14:]:
            console_panel.write(self._render_transcript_line(line))
        if live_stream:
            console_panel.write(live_stream)

    def _write_panel(self, selector: str, content: str | Text) -> None:
        panel = self.query_one(selector, RichLog)
        panel.clear()
        panel.write(content)

    def _render_transcript_line(self, line: str) -> Text:
        if self.session is None:
            return Text.from_markup(escape(line))
        text = Text.from_markup(escape(line))
        for term, style in self._transcript_highlight_terms():
            if not term:
                continue
            pattern = rf"(?i)(?<!\w){re.escape(term)}(?!\w)"
            text.highlight_regex(pattern, style)
        return text

    def _transcript_highlight_terms(self) -> list[tuple[str, str]]:
        if self.session is None:
            return []
        world = self.session.world
        player = self.session.player
        location = self.engine.location_at(world, player.position)
        terms: list[tuple[str, str]] = []
        for item in self.engine.scene_objects_at(world, player.position):
            terms.append((item, "bold #34d399"))
        for item in player.inventory:
            terms.append((item, "bold #67e8f9"))
        if location is not None:
            terms.append((location.name, "bold #f8d774"))
        npc = self.engine.npc_at(location, world)
        if npc is not None:
            terms.append((npc.name, "bold #93c5fd"))
        terms.sort(key=lambda pair: len(pair[0]), reverse=True)
        return terms

    def _build_map_renderable(self) -> Text:
        assert self.session is not None
        world = self.session.world
        player = self.session.player
        text = Text(no_wrap=True)
        view_width, view_height = self._viewport_tile_size()
        local = (
            world.active_scene is not None
            and world.active_scene.mode == SceneMode.LOCAL
        )
        if local:
            composition = self.ascii_renderer.compose_local(
                world,
                player,
                view_width,
                view_height,
            )
            start_x = 0
            start_y = 0
            end_x = composition.width
            end_y = composition.height
        else:
            composition = self.ascii_renderer.compose_overworld(world, player)
            if (
                self.follow_player
                and not self._player_in_camera_view(
                    world,
                    player,
                    view_width,
                    view_height,
                )
            ):
                self._track_player_in_view()
            start_x = self._clamp_camera(
                self.camera_x,
                world.width,
                view_width,
            )
            start_y = self._clamp_camera(
                self.camera_y,
                world.height,
                view_height,
            )
            end_x = min(world.width, start_x + view_width)
            end_y = min(world.height, start_y + view_height)

        for y in range(start_y, end_y):
            for x in range(start_x, end_x):
                cell = composition.cell(Position(x, y))
                text.append(
                    cell.token,
                    style=self._map_role_style(cell.role),
                )
            if y < end_y - 1:
                text.append("\n")
        return text

    def _map_role_style(self, role: str) -> str:
        return {
            "player": "bold #111827 on #fde047",
            "npc": "bold #dbeafe on #2563eb",
            "object": "bold #052e16 on #34d399",
            "hazard": "bold #fff7ed on #dc2626",
            "quest": "bold #2e1065 on #facc15",
            "label": "bold #fce7f3 on #831843",
            "landmark": "bold #ffe4f2 on #ec4899",
            "local_landmark": "bold #fde68a on #78350f",
            "road": "bold #fef3c7 on #78350f",
            "trail": "#fde68a on #3f2d20",
            "ferry": "bold #e0f2fe on #075985",
            "river": "bold #7dd3fc on #082f49",
            "coast": "bold #bae6fd on #0c4a6e",
            "water": "bold #60a5fa on #0b1f3a",
            "plain": "#d6d3b3 on #1b1f1a",
            "forest": "bold #4ade80 on #0d2417",
            "hill": "bold #fbbf24 on #2b1f12",
            "mountain": "bold #e5e7eb on #2a2f3a",
            "swamp": "bold #a3e635 on #21301a",
            "terrain_boundary": "#e5d8a8 on #25221a",
            "local_wall": "bold #cbd5e1 on #334155",
            "local_floor": "#a8a29e on #1c1917",
            "local_path": "#fde68a on #422006",
            "exit": "bold #ecfccb on #3f6212",
        }.get(role, "#d1d5db on #111827")

    def _region_text(self, location: Location | None) -> str:
        assert self.session is not None
        player = self.session.player
        world = self.session.world
        lines = [
            f"Position: {player.position.x},{player.position.y}",
            f"Terrain: {self.engine.biome_at(world, player.position).value}",
        ]
        if location is None:
            lines.append("Location: Unmapped scene")
            lines.append("No named location anchors this ground.")
        else:
            locations = {
                item.id: item.name
                for item in world.locations
            }
            route_names = [
                locations[destination_id]
                for destination_id in self.engine.navigation.neighbor_ids(
                    world,
                    location.id,
                )
                if destination_id in locations
            ]
            lines.extend(
                [
                    f"Location: {location.name}",
                    f"Danger: {location.danger}/9",
                    location.summary,
                    "Routes: " + (", ".join(route_names) or "none"),
                ]
            )
        return "\n".join(lines)

    def _events_text(self, world: World) -> str:
        return "\n\n".join(f"[{event.tick}] {event.text}" for event in world.recent_events) or "No events recorded."

    def _alerts_text(self, world: World) -> str:
        alerts = list(world.alerts)
        if world.active_quest:
            alerts.insert(0, f"Quest: {world.active_quest}")
        movement_lock = self.engine.movement_lock_reason(world)
        if movement_lock:
            alerts.insert(0, f"Movement locked: {movement_lock}")
        if world.last_roll:
            alerts.append(world.last_roll)
        return "\n".join(f"- {alert}" for alert in alerts) or "No immediate alerts."

    def _summary_text(self, world: World) -> str:
        counts = self.engine.summary_counts(world)
        return self._wrap_paragraphs(
            "\n".join(
                [
                f"Campaign: {world.campaign_title}",
                f"Status: {world.campaign_status.value}",
                f"World Age: {world.tick} turns",
                f"Map Size: {world.width} x {world.height}",
                f"Locations: {counts['locations']}",
                f"Routes: {counts['routes']}",
                f"NPCs: {counts['npcs']}",
                f"Hooks: {counts['hooks']}",
                f"Weather: {world.weather}",
                f"Stability: {world.stability}%",
                f"Camera: {'FOLLOW' if self.follow_player else 'FREE'}",
                f"Activity: {world.current_activity or 'open travel'}",
                ]
            ),
            34,
        )

    def _player_text(self, player: Player) -> str:
        homeland_description = ""
        if self.session is not None:
            homeland_description = self.session.world.homeland_descriptions.get(player.homeland, "")
        return self._wrap_paragraphs(
            "\n".join(
                [line for line in [
                f"Name: {player.name}",
                f"Class: {player.archetype.title()}",
                f"Homeland: {player.homeland}",
                homeland_description,
                f"HP: {player.hp}/{player.max_hp}",
                f"Gold: {player.gold}",
                f"XP: {player.xp}",
                "Inventory:",
                ", ".join(player.inventory),
                ] if line]
            ),
            34,
        )

    def _refresh_inventory_buttons(self, player: Player) -> None:
        if self.selected_inventory_index >= len(player.inventory):
            self.selected_inventory_index = max(0, len(player.inventory) - 1)
        for index in range(8):
            button = self.query_one(f"#inventory-item-{index}", Button)
            if index < len(player.inventory):
                item = player.inventory[index]
                prefix = "> " if index == self.selected_inventory_index else ""
                category = self._inventory_category(item)
                button.label = f"{prefix}{item.title()} [{category}]"
                button.disabled = False
            else:
                button.label = "Empty"
                button.disabled = True

    def _select_inventory_step(self, delta: int) -> None:
        if self.session is None or not self.session.player.inventory:
            return
        self.selected_inventory_index = (self.selected_inventory_index + delta) % len(self.session.player.inventory)
        self._refresh_ui()

    def _select_inventory_item(self, index: int) -> None:
        if self.session is None or not 0 <= index < len(self.session.player.inventory):
            return
        self.selected_inventory_index = index
        self._refresh_ui()

    def _selected_inventory_item(self) -> str | None:
        if self.session is None or not self.session.player.inventory:
            return None
        if self.selected_inventory_index >= len(self.session.player.inventory):
            self.selected_inventory_index = 0
        return self.session.player.inventory[self.selected_inventory_index]

    def _use_selected_inventory_item(self) -> None:
        item = self._selected_inventory_item()
        if item is None:
            return
        self._append_transcript(f"> use {item}")
        self._handle_command(f"use {item}")

    def _inspect_selected_inventory_item(self) -> None:
        item = self._selected_inventory_item()
        if item is None:
            return
        self._append_transcript(f"> inspect {item}")
        self._handle_command(f"inspect {item}")

    def _refresh_map_panels(self, world: World) -> None:
        for selector in ("#map-panel", "#overview-map-panel"):
            try:
                map_panel = self.query_one(selector, Static)
            except Exception:
                continue
            map_panel.border_subtitle = self._map_status(world)
            map_panel.update(self._build_map_renderable())

    def _inventory_detail_text(self, player: Player) -> str:
        if not player.inventory:
            return "No item selected."
        item = player.inventory[self.selected_inventory_index]
        descriptions = self.session.world.inventory_descriptions if self.session is not None else {}
        category = self._inventory_category(item)
        lines = [
            f"[bold #f8d774]{item.title()}[/] [dim]({category})[/]",
            "",
            descriptions.get(item, "A carried item with no special notes yet."),
            "",
            "[bold]Actions:[/] use, inspect, drop, or mention the item in a command.",
        ]
        return Text.from_markup("\n".join(lines))

    def _selected_inventory_text(self, player: Player) -> Text:
        if not player.inventory:
            return Text.from_markup("[dim]No carried items.[/]")
        item = player.inventory[self.selected_inventory_index]
        description = ""
        if self.session is not None:
            description = self.session.world.inventory_descriptions.get(item, "")
        lines = [
            f"[bold #f8d774]Selected:[/] {self._styled_item_name(item, inventory=True)}",
            f"[dim]Category:[/] {escape(self._inventory_category(item))}",
        ]
        if description:
            lines.append(f"[dim]{escape(description)}[/]")
        lines.append("[dim]Use it with the button or type `use <item>`.[/]")
        return Text.from_markup("\n".join(lines))

    def _choice_card_text(self, choice: str, index: int) -> Text:
        text = Text()
        text.append(f"{index + 1}. ", style="bold #f8d774")
        text.append(choice, style="bold #e5e7eb")
        return text

    def _styled_item_name(self, item: str, inventory: bool = False) -> str:
        category = self._inventory_category(item)
        if category == "quest":
            style = "bold #34d399"
        elif category == "light":
            style = "bold #fbbf24"
        elif category == "consumable":
            style = "bold #f472b6"
        elif category == "tool":
            style = "bold #93c5fd"
        elif inventory:
            style = "bold #e5e7eb"
        else:
            style = "bold #67e8f9"
        return f"[{style}]{escape(item.title())}[/]"

    def _inventory_category(self, item: str) -> str:
        token = item.lower()
        if any(keyword in token for keyword in {"key", "map", "ledger", "note", "sigil", "badge", "token", "relic"}):
            return "quest"
        if any(keyword in token for keyword in {"torch", "lamp", "light"}):
            return "light"
        if any(keyword in token for keyword in {"rations", "snack", "food", "water", "drink"}):
            return "consumable"
        if any(keyword in token for keyword in {"rope", "hook", "kit", "lockpick", "tool"}):
            return "tool"
        return "utility"

    def _resources_text(self, player: Player, world: World) -> str:
        return self._wrap_paragraphs(
            "\n".join(
                [
                f"Gold: {player.gold}",
                f"HP: {player.hp}/{player.max_hp}",
                f"XP: {player.xp}",
                f"World Tick: {world.tick}",
                f"Weather: {world.weather}",
                f"Stability: {world.stability}%",
                ]
            ),
            34,
        )

    def _loadout_text(self, player: Player) -> str:
        defaults = {
            "warrior": ["Primary: Iron blade", "Off-hand: Buckler", "Armor: Mail shirt"],
            "rogue": ["Primary: Knives", "Off-hand: Hook tool", "Armor: Shadow leathers"],
            "mage": ["Primary: Ash staff", "Focus: Rune charm", "Armor: Woven mantle"],
            "ranger": ["Primary: Longbow", "Sidearm: Hatchet", "Armor: Field coat"],
        }
        lines = defaults.get(player.archetype, ["Primary: Improvised kit"])
        return self._wrap_paragraphs("\n".join(lines + ["", "Ready Items:", ", ".join(player.inventory[:3]) or "None"]), 34)

    def _packs_text(self, player: Player) -> str:
        tags = {
            "torch": "light",
            "rations": "survival",
            "bedroll": "camp",
        }
        grouped: dict[str, list[str]] = {"camp": [], "survival": [], "utility": [], "light": []}
        for item in player.inventory:
            grouped[tags.get(item, "utility")].append(item.title())
        lines: list[str] = []
        for label in ("camp", "survival", "utility", "light"):
            entries = grouped[label]
            if entries:
                lines.append(f"{label.title()}:")
                lines.extend(entries)
                lines.append("")
        return self._wrap_paragraphs("\n".join(lines).strip() or "No pack categories available.", 34)

    def _refresh_skill_buttons(self, player: Player) -> None:
        skills = self._skill_entries(player)
        if self.selected_skill_index >= len(skills):
            self.selected_skill_index = max(0, len(skills) - 1)
        for index in range(8):
            button = self.query_one(f"#skill-btn-{index}", Button)
            if index < len(skills):
                name, value = skills[index]
                prefix = "> " if index == self.selected_skill_index else ""
                button.label = f"{prefix}{self._skill_label(name)} {self._format_bonus(value)}"
                button.disabled = False
            else:
                button.label = "No Skill"
                button.disabled = True

    def _select_skill(self, index: int) -> None:
        if self.session is None or not 0 <= index < len(self._skill_entries(self.session.player)):
            return
        self.selected_skill_index = index
        self._refresh_ui()

    def _skill_detail_text(self, player: Player) -> str:
        skills = self._skill_entries(player)
        if not skills:
            return self._traits_text(player)
        name, value = skills[self.selected_skill_index]
        return self._wrap_paragraphs(
            "\n".join(
                [
                self._skill_label(name),
                f"Bonus: {self._format_bonus(value)}",
                "",
                self._skill_description(name),
                ]
            ),
            34,
        )

    def _skill_entries(self, player: Player) -> list[tuple[str, int]]:
        return [(key, value) for key, value in sorted(player.boosts.items()) if value]

    def _skill_description(self, skill: str) -> str:
        descriptions = {
            "tracking": "Used for following trails, reading terrain, and finding hidden movement.",
            "stealth": "Used for quiet movement, ambushes, infiltration, and avoiding notice.",
            "investigation": "Used for clues, mechanisms, contradictions, and careful searches.",
            "courtly_etiquette": "Used for formal audiences, political manners, and reading status.",
            "spirit_lore": "Used for shrines, omens, curses, and supernatural traditions.",
            "dueling": "Used for single combat and precise weapon exchanges.",
            "archery": "Used for ranged attacks and careful shots under pressure.",
            "command": "Used for leadership, battlefield orders, and forceful presence.",
        }
        if self.session is not None:
            generated = self.session.world.skill_descriptions.get(skill)
            if generated:
                return generated
        return descriptions.get(skill, "Used when the situation calls for this specialty.")

    def _format_bonus(self, value: int) -> str:
        return f"+{value}" if value > 0 else str(value)

    def _progression_text(self, player: Player) -> str:
        level = self._player_level(player)
        current_floor = self._level_xp_floor(level)
        next_floor = self._level_xp_floor(level + 1)
        remaining = max(0, next_floor - player.xp)
        return self._wrap_paragraphs(
            "\n".join(
                [
                f"Current Level: {level}",
                f"XP Total: {player.xp}",
                f"XP Into Level: {player.xp - current_floor}",
                f"Next Level At: {next_floor}",
                f"XP Remaining: {remaining}",
                "",
                "Power growth is tied to XP, inventory, skill bonuses, and archetype milestones.",
                ]
            ),
            34,
        )

    def _traits_text(self, player: Player) -> str:
        traits = {
            "warrior": [
                "Battle-hardened: higher front-line resilience",
                "Weapon discipline: better attack reliability",
                "Hold the line: threat control in dangerous regions",
            ],
            "rogue": [
                "Cunning approach: better improvised solutions",
                "Shadowstep: excels at infiltration and escapes",
                "Quick hands: item and trap utility",
            ],
            "mage": [
                "Arcane insight: stronger mystery and ritual play",
                "Will focus: handles dangerous unknowns",
                "Spell shaping: flexible scene manipulation",
            ],
            "ranger": [
                "Trail sense: better wilderness navigation",
                "Field craft: efficient exploration and scouting",
                "Hunter's calm: steady performance under pressure",
            ],
        }
        return self._wrap_paragraphs("\n\n".join(traits.get(player.archetype, ["No archetype traits defined yet."])), 34)

    def _milestones_text(self, player: Player) -> str:
        level = self._player_level(player)
        upcoming = [node for node in self._skill_nodes(player) if node["level"] > level][:3]
        if not upcoming:
            return "All current milestones unlocked."
        lines = []
        for node in upcoming:
            needed = max(0, self._level_xp_floor(node["level"]) - player.xp)
            lines.append(f"L{node['level']} {node['name']}")
            lines.append(f"{node['text']}")
            lines.append(f"Needs {needed} more XP")
            lines.append("")
        return self._wrap_paragraphs("\n".join(lines).strip(), 34)

    def _hooks_text(self, world: World) -> str:
        active = next((quest for quest in world.quests if quest.id == world.active_quest_id), None)
        lines = [
            f"Theme: {world.theme_prompt}",
            "",
            f"Campaign Quest: {world.overarching_quest}",
            f"Campaign Status: {world.campaign_status.value}",
            "",
            f"Current Objective: {world.active_quest or 'None selected'}",
            "",
        ]
        if active is not None:
            stage = active.stages[min(active.current_stage, len(active.stages) - 1)] if active.stages else active.goal
            lines.extend(
                [
                    f"Quest: {active.title}",
                    f"Quest Status: {active.status.value}",
                    f"Stage: {stage.description if hasattr(stage, 'description') else stage}",
                    "",
                ]
            )
            if active.discoveries:
                lines.append("Discoveries:")
                lines.extend(active.discoveries[-4:])
                lines.append("")
        active_clocks = [clock for clock in world.clocks if clock.status == "active"]
        if active_clocks:
            lines.append("Clocks:")
            lines.extend(f"{clock.title}: {clock.value}/{clock.max_value}" for clock in active_clocks[:4])
            lines.append("")
        lines.append("Loose Threads:")
        lines.extend(world.quest_hooks[:5])
        if world.epilogue:
            lines.extend(["", "Epilogue:", world.epilogue])
        return self._wrap_paragraphs("\n\n".join(line for line in lines if line) or "No active hooks.", 34)

    def _areas_text(self, location: Location | None) -> str:
        assert self.session is not None
        player = self.session.player
        scene = self.session.world.active_scene
        entered_area = scene.area_name if scene is not None and scene.mode == SceneMode.LOCAL else None
        lines = [
            f"Rowan Position: {player.position.x},{player.position.y}" if player.name == "Rowan" else f"{player.name} Position: {player.position.x},{player.position.y}",
            f"Current Terrain: {self.engine.biome_at(self.session.world, player.position).value}",
        ]
        if location is not None:
            lines.append(f"Current Region: {location.name}")
        else:
            lines.append("Current Region: Unmapped scene")
        lines.append(f"Selected Area: {self._display_area(self.session.selected_area)}")
        lines.append(f"Entered Area: {self._display_area(entered_area)}")
        lines.append("")
        lines.append("Selection is UI-only; entering or moving through an area advances time.")
        return self._wrap_paragraphs("\n".join(lines), 34)

    def _local_text(self, location: Location | None, npc: Npc | None, memory: CampaignMemory) -> str:
        assert self.session is not None
        world = self.session.world
        player = self.session.player
        scope = location.name if location is not None else None
        memory_lines = memory.relevant_context(world, player, scope, limit=3)
        lines = []
        scene = world.active_scene
        if scene is not None and scene.mode == SceneMode.LOCAL:
            lines.extend(
                [
                    f"[bold #f8d774]Entered Area:[/] [bold #67e8f9]{escape(self._display_area(scene.area_name))}[/]",
                    f"[dim]{escape(self._area_scene_text(location))}[/]",
                    "",
                ]
            )
        elif self.session.selected_area is not None:
            lines.extend(
                [
                    f"[bold #f8d774]Selected Area:[/] [bold #67e8f9]{escape(self._display_area(self.session.selected_area))}[/]",
                    "[dim]Press Enter Area or click the button to drop into this scene.[/]",
                    "",
                ]
            )
        if npc is not None:
            lines.extend(
                [
                    f"[bold #f8d774]NPC:[/] [bold #93c5fd]{escape(npc.name)}[/]",
                    f"Role: {escape(npc.role)}",
                    f"Disposition: {escape(npc.disposition)}",
                    "",
                ]
            )
            history = world.conversations.get(npc.name, [])[-6:]
            if history:
                lines.append("[bold #f8d774]Conversation:[/]")
                lines.extend(escape(line) for line in history)
                lines.append("")
        visible_objects = world.scene_objects.get(f"{player.position.x},{player.position.y}", [])
        if visible_objects:
            lines.append("[bold #a7f3d0]Visible objects:[/]")
            lines.extend(f"- {self._styled_item_name(item)}" for item in visible_objects)
            lines.append("")
        if player.inventory:
            lines.append("[bold #a7f3d0]Inventory:[/]")
            for item in player.inventory[:6]:
                lines.append(f"- {self._styled_item_name(item, inventory=True)} [dim]({self._inventory_category(item)})[/]")
            lines.append("")
        lines.append("[bold #f8d774]Relevant memory:[/]")
        lines.extend(escape(line) for line in (memory_lines or ["No strong local memories yet."]))
        lines.append("")
        lines.append("[dim]Try:[/] take <item>, inspect <item>, use <item>, drop <item>.")
        return Text.from_markup("\n".join(lines))

    def _chronicle_text(self, world: World) -> str:
        lines = []
        for event in world.recent_events:
            lines.append(f"[tick {event.tick}] {event.category.upper()}")
            lines.append(event.text)
            lines.append("")
        return "\n".join(lines).strip() or "The chronicle is empty."

    def _memory_text(self, memory: CampaignMemory, world: World, player: Player) -> str:
        relevant = memory.relevant_context(world, player, limit=6)
        latest = memory.latest_lines(limit=6)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in relevant + latest:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return "\n\n".join(deduped[:8]) or "No persistent memories yet."

    def _system_text(self, memory: CampaignMemory) -> str:
        save_target = Path(self.store.path).resolve()
        state_target = Path(self.store.state_path).resolve()
        director_status = getattr(self.director, "status_line", f"Director: {type(self.director).__name__}")
        debug_path = str(self.debug_logger.path.resolve()) if self.debug_logger is not None else "disabled"
        return "\n".join(
            [
                "Canonical engine state is stored locally.",
                f"Save file: {save_target}",
                f"State file: {state_target}",
                f"Debug log: {debug_path}",
                f"Tracked memory entries: {len(memory.entries)}",
                director_status,
                "",
                "LLM boundary:",
                "- engine owns rolls, HP, movement, state mutation",
                "- director owns framing, names, hooks, scene narration",
                "- local LLM mode reads compact JSON context and returns JSON beats",
                "- memory retrieval supplies compact context instead of raw full history",
                "",
                "Controls:",
                "- m or esc focuses the map",
                "- arrow keys move",
                "- talk starts or advances NPC dialogue",
                "- while speaking with an NPC, bare input is dialogue",
                "- quit, help, inventory, and end conversation stay global",
                "- prefix other commands with / during NPC dialogue",
                "- shift+arrows pan the map",
                "- c recenters on the player",
                "- f toggles follow/free camera mode",
                "- ctrl+n opens new campaign setup",
                "- / focuses the command line",
            ]
        )

    def _topbar_text(self, world: World) -> Text:
        model = self._model_label()
        usage_text = self._usage_summary(world)
        text = Text()
        text.append(f"Model: {model}", style="bold #a7f3d0")
        text.append(f"  |  {usage_text}", style="#bae6fd")
        if self.command_in_progress and self.loading_context:
            text.append("  |  ", style="dim #64748b")
            text.append_text(self._thinking_text(include_context=True))
        text.append("\n")
        text.append(world.campaign_title, style="bold #f9fafb")
        text.append(
            f"  |  {world.campaign_status.value.upper()}  |  "
            f"Tick {world.tick:,}  |  Stability {world.stability}%",
            style="#cbd5e1",
        )
        return text

    def _usage_summary(self, world: World) -> str:
        usage = self._usage_tracker()
        if usage is None or usage.request_count == 0:
            session_text = "session pending"
        else:
            cost_text = f"${usage.estimated_cost:.4f}" if usage.estimated_cost else "cost unknown"
            session_text = f"session {format_tokens(usage.total_tokens)} tokens / {cost_text}"
        if world.usage_totals.request_count == 0:
            save_text = "save pending"
        else:
            save_cost = f"${world.usage_totals.estimated_cost:.4f}" if world.usage_totals.estimated_cost else "cost unknown"
            save_text = f"save {format_tokens(world.usage_totals.total_tokens)} tokens / {save_cost}"
        return f"{session_text}  |  {save_text}"

    def _footer_text(self) -> str:
        return "m/Esc map | arrows move | talk dialogue | / command line | shift+arrows pan | f follow"

    def _model_label(self) -> str:
        client = getattr(self.director, "client", None)
        config = getattr(client, "config", None)
        model = getattr(config, "model", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
        return type(self.director).__name__

    def _usage_text(self) -> list[str]:
        usage = self._usage_tracker()
        if usage is None:
            return ["No LLM usage tracker is attached to the active director."]
        lines = [usage.summary_line()]
        last = usage.last_record
        if last is not None:
            last_cost = f"${last.estimated_cost:.5f}" if last.estimated_cost is not None else "unknown"
            lines.append(
                f"Last request: {format_tokens(last.total_tokens)} tokens "
                f"({format_tokens(last.prompt_tokens)} in / {format_tokens(last.completion_tokens)} out), "
                f"est. {last_cost}"
            )
            if last.cached_prompt_tokens:
                lines.append(f"Cached input tokens last request: {format_tokens(last.cached_prompt_tokens)}")
        lines.append("Cost is estimated from reported tokens and the local pricing table/env overrides.")
        return lines

    def _usage_tracker(self) -> TokenUsageTracker | None:
        client = getattr(self.director, "client", None)
        tracker = getattr(client, "usage_tracker", None)
        return tracker if isinstance(tracker, TokenUsageTracker) else None

    def _sync_usage_totals(self, world: World) -> None:
        usage = self._usage_tracker()
        if usage is None:
            return
        current = usage.snapshot()
        world.usage_totals.request_count += max(0, current.request_count - self.synced_usage.request_count)
        world.usage_totals.prompt_tokens += max(0, current.prompt_tokens - self.synced_usage.prompt_tokens)
        world.usage_totals.completion_tokens += max(0, current.completion_tokens - self.synced_usage.completion_tokens)
        world.usage_totals.total_tokens += max(0, current.total_tokens - self.synced_usage.total_tokens)
        world.usage_totals.cached_prompt_tokens += max(0, current.cached_prompt_tokens - self.synced_usage.cached_prompt_tokens)
        world.usage_totals.estimated_cost += max(0.0, current.estimated_cost - self.synced_usage.estimated_cost)
        self.synced_usage = current

    def _player_level(self, player: Player) -> int:
        thresholds = [0, 5, 15, 30, 50, 75, 105]
        level = 1
        for index, threshold in enumerate(thresholds, start=1):
            if player.xp >= threshold:
                level = index
        return level

    def _level_xp_floor(self, level: int) -> int:
        thresholds = [0, 5, 15, 30, 50, 75, 105, 140]
        level = max(1, min(level, len(thresholds)))
        return thresholds[level - 1]

    def _skill_nodes(self, player: Player) -> list[dict[str, object]]:
        trees = {
            "warrior": [
                {"level": 1, "name": "Guard Stance", "text": "Stabilize under pressure and hold contested ground."},
                {"level": 2, "name": "Driving Blow", "text": "Break hostile momentum during direct engagements."},
                {"level": 3, "name": "Iron Will", "text": "Resist fear, injury, and escalating battlefield chaos."},
                {"level": 4, "name": "Banner Call", "text": "Project authority and rally allies in the field."},
            ],
            "rogue": [
                {"level": 1, "name": "Soft Step", "text": "Move cleanly through tense spaces and avoid notice."},
                {"level": 2, "name": "Quick Fingers", "text": "Manipulate locks, gear, and fragile opportunities."},
                {"level": 3, "name": "False Face", "text": "Support deception, social infiltration, and cover stories."},
                {"level": 4, "name": "Ghost Exit", "text": "Recover from bad plans before the room collapses."},
            ],
            "mage": [
                {"level": 1, "name": "Spark Lore", "text": "Recognize occult traces and unstable magical residue."},
                {"level": 2, "name": "Warded Mind", "text": "Hold form against curses, visions, and psychic strain."},
                {"level": 3, "name": "Pattern Break", "text": "Disrupt dangerous rituals and arcane mechanisms."},
                {"level": 4, "name": "Deep Invocation", "text": "Call on rarer effects when the story earns it."},
            ],
            "ranger": [
                {"level": 1, "name": "Trail Sense", "text": "Read tracks, routes, and pressure lines in the wild."},
                {"level": 2, "name": "Field Medicine", "text": "Patch wounds and steady long expeditions."},
                {"level": 3, "name": "Hunter's Mark", "text": "Isolate threats and pursue them across regions."},
                {"level": 4, "name": "Frontier Instinct", "text": "Anticipate ambushes and shifting environmental danger."},
            ],
        }
        return trees.get(player.archetype, [])

    def _map_status(self, world: World) -> str:
        view_width, view_height = self._viewport_tile_size()
        start_x = self._clamp_camera(self.camera_x, world.width, view_width)
        start_y = self._clamp_camera(self.camera_y, world.height, view_height)
        player_status = "PLAYER VISIBLE" if self._player_in_camera_view(world, self.session.player, view_width, view_height) else "PLAYER OFFSCREEN"
        return (
            f"{'FOLLOW' if self.follow_player else 'FREE'}  "
            f"{player_status}  "
            f"view {start_x},{start_y}  "
            f"size {view_width}x{view_height}"
        )

    def _viewport_tile_size(self) -> tuple[int, int]:
        panel = self.query_one("#map-panel", Static)
        width = max(8, panel.region.width - 4)
        height = max(6, panel.region.height - 2)
        return min(32, max(10, width // 4)), min(18, max(8, height // 3))

    def _center_camera_on_player(self) -> None:
        if self.session is None:
            return
        view_width, view_height = self._viewport_tile_size()
        player = self.session.player
        self.camera_x = self._clamp_camera(player.position.x - view_width // 2, self.session.world.width, view_width)
        self.camera_y = self._clamp_camera(player.position.y - view_height // 2, self.session.world.height, view_height)

    def _track_player_in_view(self) -> None:
        if self.session is None:
            return
        world = self.session.world
        player = self.session.player
        view_width, view_height = self._viewport_tile_size()
        camera_x = self._clamp_camera(self.camera_x, world.width, view_width)
        camera_y = self._clamp_camera(self.camera_y, world.height, view_height)
        margin_x = max(6, view_width // 5)
        margin_y = max(4, view_height // 4)
        left_limit = camera_x + margin_x
        right_limit = camera_x + view_width - margin_x - 1
        top_limit = camera_y + margin_y
        bottom_limit = camera_y + view_height - margin_y - 1

        if player.position.x < left_limit:
            camera_x = player.position.x - margin_x
        elif player.position.x > right_limit:
            camera_x = player.position.x - view_width + margin_x + 1

        if player.position.y < top_limit:
            camera_y = player.position.y - margin_y
        elif player.position.y > bottom_limit:
            camera_y = player.position.y - view_height + margin_y + 1

        self.camera_x = self._clamp_camera(camera_x, world.width, view_width)
        self.camera_y = self._clamp_camera(camera_y, world.height, view_height)

    def _pan_map(self, dx: int, dy: int) -> None:
        if self.session is None:
            return
        self.follow_player = False
        view_width, view_height = self._viewport_tile_size()
        world = self.session.world
        self.camera_x = self._clamp_camera(self.camera_x + dx, world.width, view_width)
        self.camera_y = self._clamp_camera(self.camera_y + dy, world.height, view_height)
        self.session.last_message = f"Manual camera pan to {self.camera_x},{self.camera_y}."
        self._refresh_ui()

    def _clamp_camera(self, value: int, world_extent: int, view_extent: int) -> int:
        return max(0, min(value, max(0, world_extent - view_extent)))

    def _player_in_camera_view(self, world: World, player: Player, view_width: int, view_height: int) -> bool:
        start_x = self._clamp_camera(self.camera_x, world.width, view_width)
        start_y = self._clamp_camera(self.camera_y, world.height, view_height)
        end_x = min(world.width, start_x + view_width)
        end_y = min(world.height, start_y + view_height)
        return start_x <= player.position.x < end_x and start_y <= player.position.y < end_y

    def _append_transcript(self, line: str) -> None:
        if self.session is None:
            return
        self.session.transcript.append(line)
        self.session.transcript = self.session.transcript[-14:]

    def _console_text(self) -> str:
        if self.session is None or not self.session.transcript:
            return "No command output yet."
        return "\n".join(self.session.transcript[-14:])

    def _sync_area_state(self, location: Location | None) -> None:
        assert self.session is not None
        self.area_choices = self._area_choices(location)
        if self.session.selected_area not in self.area_choices:
            self.session.selected_area = self.area_choices[0] if self.area_choices else None

    def _refresh_area_buttons(self) -> None:
        for index in range(3):
            button = self.query_one(f"#area-btn-{index}", Button)
            if index < len(self.area_choices):
                area = self.area_choices[index]
                prefix = "> " if self.session and self.session.selected_area == area else ""
                button.label = f"{prefix}{area}"
                button.disabled = False
            else:
                button.label = "Unavailable"
                button.disabled = True

    def _refresh_action_buttons(self) -> None:
        assert self.session is not None
        enter_button = self.query_one("#area-enter", Button)
        leave_button = self.query_one("#area-leave", Button)
        forward_button = self.query_one("#area-forward", Button)
        back_button = self.query_one("#area-back", Button)
        try_leave_button = self.query_one("#area-try-leave", Button)
        world = self.session.world
        scene = world.active_scene
        local = scene is not None and scene.mode == SceneMode.LOCAL
        locked = self.engine.movement_lock_reason(world) is not None
        enter_button.disabled = locked or self.session.selected_area is None or local
        leave_button.disabled = not local
        forward_button.disabled = locked or not local or scene.step >= 2
        back_button.disabled = locked or not local or scene.step <= 0
        try_leave_button.disabled = not local

    def _refresh_choice_buttons(self) -> None:
        assert self.session is not None
        location = self.engine.location_at(self.session.world, self.session.player.position)
        choices = self._visible_choice_labels(location)
        for index in range(4):
            button = self.query_one(f"#choice-card-{index}", ChoiceCard)
            if index < len(choices):
                button.update(self._choice_card_text(choices[index], index))
                button.disabled = False
            else:
                button.update("No choice")
                button.disabled = True

    def _select_story_choice(self, index: int) -> None:
        if self.session is None:
            return
        location = self.engine.location_at(self.session.world, self.session.player.position)
        choices = self._visible_choice_labels(location)
        if not 0 <= index < len(choices):
            return
        choice = choices[index]
        self._append_transcript(f"> {choice}")
        if self.engine.is_local_scene(self.session.world):
            self._handle_area_choice(choice)
        else:
            self._handle_command(choice)

    def action_select_choice(self, index: int) -> None:
        self._select_story_choice(index)

    def _select_area(self, index: int) -> None:
        if self.session is None or not (0 <= index < len(self.area_choices)):
            return
        self.session.selected_area = self.area_choices[index]
        self.session.last_message = f"Selected area: {self.session.selected_area}."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def _enter_selected_area(self) -> None:
        if self.session is None or self.session.selected_area is None:
            return
        self._append_transcript(f"> enter area {self.session.selected_area}")
        self._handle_command(f"enter area {self.session.selected_area}")

    def _leave_area(self) -> None:
        if self.session is None:
            return
        self._append_transcript("> leave area")
        self._handle_command("leave area")

    def _area_choices(self, location: Location | None) -> list[str]:
        assert self.session is not None
        return self.engine.available_areas(self.session.world, self.session.player)

    def _display_area(self, area_name: str | None) -> str:
        return area_name if area_name is not None else "None"

    def _area_scene_text(self, location: Location | None) -> str:
        assert self.session is not None
        return self.engine.scene_description(self.session.world, self.session.player)

    def _move_within_area(self, delta: int) -> None:
        if self.session is None:
            return
        command = "push deeper" if delta > 0 else "pull back"
        self._append_transcript(f"> {command}")
        self._handle_command(command)

    def _try_leave_area(self) -> None:
        if self.session is None:
            return
        self._append_transcript("> force exit")
        self._handle_command("force exit")

    def _visible_choice_labels(self, location: Location | None) -> list[str]:
        assert self.session is not None
        scene = self.session.world.active_scene
        if scene is None or scene.mode != SceneMode.LOCAL:
            visible = self._scene_object_choice_labels(location)
            current = [choice for choice in self.session.world.current_choices if not self._is_generic_choice(choice)]
            fallback = [choice for choice in self.session.world.current_choices if self._is_generic_choice(choice)]
            merged: list[str] = []
            for choice in visible + current + fallback:
                cleaned = " ".join(choice.split())
                if not cleaned or cleaned in merged:
                    continue
                merged.append(cleaned)
                if len(merged) >= 4:
                    break
            return merged
        area_choices = list(scene.available_actions)
        scene_choices = self._scene_object_choice_labels(location)
        merged: list[str] = []
        filtered_current = [choice for choice in self.session.world.current_choices if not self._is_generic_choice(choice)]
        for choice in area_choices + scene_choices + filtered_current + list(self.session.world.current_choices):
            cleaned = " ".join(choice.split())
            if not cleaned or cleaned in merged:
                continue
            merged.append(cleaned)
            if len(merged) >= 4:
                break
        return merged

    def _scene_object_choice_labels(self, location: Location | None) -> list[str]:
        assert self.session is not None
        visible = self.engine.scene_objects_at(self.session.world, self.session.player.position)
        choices: list[str] = []
        for item in visible[:3]:
            cleaned = item.strip()
            if not cleaned:
                continue
            choices.append(f"take {cleaned}")
            choices.append(f"inspect {cleaned}")
        return choices

    def _is_generic_choice(self, choice: str) -> bool:
        cleaned = " ".join(choice.lower().split())
        return cleaned in {
            "look around",
            "move on",
            "wait",
            "search carefully",
            "follow the clue",
            "inspect the scene",
            "inspect the find",
            "look again",
            "change tactics",
            "fall back",
            "press deeper",
            "change approach",
            "reassess",
            "keep watch",
            "pack up",
            "push onward",
            "leave for now",
            "return to the road",
            "hold your ground",
            "retreat",
            "brace for another strike",
            "stand down",
        }

    def _area_choice_labels(self, location: Location | None) -> list[str]:
        assert self.session is not None
        scene = self.session.world.active_scene
        if scene is None or scene.mode != SceneMode.LOCAL:
            return []
        return list(scene.available_actions)

    def _handle_area_choice(self, choice: str) -> None:
        cleaned = " ".join(choice.lower().split())
        if cleaned == "leave area":
            self._handle_command("leave area")
            return
        if cleaned == "force the exit":
            self._handle_command("force exit")
            return
        if cleaned == "push deeper":
            self._handle_command("push deeper")
            return
        if cleaned == "pull back":
            self._handle_command("pull back")
            return
        if cleaned in {"inspect the scene", "search carefully", "look around"}:
            self._handle_command("look")
            return
        if cleaned == "talk":
            self._handle_command("talk")
            return
        if cleaned == "press the advantage":
            self._handle_command("attack")
            return
        if cleaned == "reassess":
            self._handle_command("look")
            return
        self._handle_command(choice)
