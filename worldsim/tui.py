from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Resize
from textual.widgets import Button, Input, RichLog, Static
from textual.widgets._content_switcher import ContentSwitcher
from textual.widgets._tabbed_content import TabPane, TabbedContent

from worldsim import area
from worldsim.debug import DebugLogger
from worldsim.director import director_from_env
from worldsim.engine import WorldEngine
from worldsim.memory import CampaignMemory, CampaignStore
from worldsim.models import Biome, Location, Npc, Player, Position, World

if TYPE_CHECKING:
    from textual.events import Click


@dataclass
class Session:
    world: World
    player: Player
    memory: CampaignMemory
    last_message: str
    transcript: list[str] = field(default_factory=list)
    selected_area: str | None = None
    entered_area: str | None = None
    entered_area_step: int = 0
    entered_area_tension: int = 0
    entered_area_theme: str | None = None
    entered_area_hazard: str | None = None
    entered_area_npc: str | None = None


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


class WorldSimApp(App[None]):
    CSS_PATH = "worldsim.tcss"
    BINDINGS = [
        Binding("ctrl+n", "show_setup", "New Campaign", show=False),
        Binding("m", "focus_map", "Map Focus", show=False),
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

    def compose(self) -> ComposeResult:
        with Container(id="root"):
            with ContentSwitcher(
                id="switcher",
                initial="game-screen" if self.loaded_session else "setup-screen",
            ):
                yield from self._compose_setup_screen()
                yield from self._compose_game_screen()

    def on_mount(self) -> None:
        self.title = "Worldsim"
        self.sub_title = "Living world simulator"
        self._set_panel_titles()
        if self.loaded_session is not None:
            world, player, memory = self.loaded_session
            memory.remember_world_state(world, player)
            self.session = Session(
                world=world,
                player=player,
                memory=memory,
                last_message="Campaign restored from local save. The director is rebuilding context from memory.",
                transcript=["System: Campaign restored from local save."],
                selected_area=None,
                entered_area=None,
                entered_area_step=0,
                entered_area_tension=0,
                entered_area_theme=None,
                entered_area_hazard=None,
                entered_area_npc=None,
            )
            self._center_camera_on_player()
            self._refresh_ui()
            self.call_after_refresh(self._sync_camera_after_layout)
            self.action_focus_map()

    def _compose_setup_screen(self) -> ComposeResult:
        subtitle = (
            "Create a wanderer for a new campaign. The world and compact memory will persist locally."
            if self.loaded_session is None
            else "A campaign save exists. Continue from the game screen or start over here."
        )
        with Container(id="setup-screen"):
            with Vertical(id="setup-card"):
                yield Static("WORLDGEN // ADVENTURE CAMPAIGN", id="setup-title")
                yield Static(subtitle, id="setup-subtitle")
                yield Input(placeholder="Name", value="Rowan", id="name-input")
                yield Input(
                    placeholder="Class: warrior / rogue / mage / ranger",
                    value="ranger",
                    id="class-input",
                )
                yield Input(placeholder="Homeland", value="Northreach", id="home-input")
                with Horizontal(id="setup-actions"):
                    yield Button("Start New Campaign", id="start-button", variant="primary")

    def _compose_game_screen(self) -> ComposeResult:
        with Container(id="game-screen"):
            yield Static(id="topbar")
            with TabbedContent(initial="tab-world"):
                with TabPane("WORLD", id="tab-world"):
                    with Horizontal(classes="row"):
                        yield MapPanel(id="map-panel", classes="panel")
                        with Vertical(classes="stack world-sidebar"):
                            yield Static(id="region-panel", classes="panel")
                            yield Static(id="events-panel", classes="panel")
                            yield Static(id="alerts-panel", classes="panel")
                            yield Static(id="summary-panel", classes="panel")
                with TabPane("ADVENTURE", id="tab-adventure"):
                    with Horizontal(classes="row"):
                        with Vertical(classes="stack"):
                            yield Static(id="player-panel", classes="panel")
                            yield Static(id="hooks-panel", classes="panel")
                        with Vertical(classes="stack"):
                            yield Static(id="areas-panel", classes="panel")
                            with Vertical(id="area-buttons", classes="panel"):
                                yield Button("Area Slot 1", id="area-btn-0", compact=True)
                                yield Button("Area Slot 2", id="area-btn-1", compact=True)
                                yield Button("Area Slot 3", id="area-btn-2", compact=True)
                            with Vertical(id="actions-panel", classes="panel"):
                                yield Button("Enter Area", id="area-enter", variant="primary", compact=True)
                                yield Button("Leave Area", id="area-leave", compact=True)
                                yield Button("Step Deeper", id="area-forward", compact=True)
                                yield Button("Step Back", id="area-back", compact=True)
                                yield Button("Try Leave", id="area-try-leave", compact=True)
                                yield Button("Look", id="action-look", compact=True)
                                yield Button("Explore", id="action-explore", compact=True)
                                yield Button("Talk", id="action-talk", compact=True)
                                yield Button("Attack", id="action-attack", compact=True)
                                yield Button("Rest", id="action-rest", compact=True)
                                yield Button("Wait", id="action-wait", compact=True)
                        with Vertical(classes="stack"):
                            yield Static(id="local-panel", classes="panel")
                            yield Static(id="director-panel", classes="panel")
                with TabPane("INVENTORY", id="tab-inventory"):
                    with Horizontal(classes="row"):
                        with Vertical(classes="stack"):
                            yield Static(id="inventory-panel", classes="panel")
                            yield Static(id="resources-panel", classes="panel")
                        with Vertical(classes="stack"):
                            yield Static(id="loadout-panel", classes="panel")
                            yield Static(id="packs-panel", classes="panel")
                with TabPane("SKILLS", id="tab-skills"):
                    with Horizontal(classes="row"):
                        with Vertical(classes="stack"):
                            yield Static(id="skills-panel", classes="panel")
                            yield Static(id="progression-panel", classes="panel")
                        with Vertical(classes="stack"):
                            yield Static(id="traits-panel", classes="panel")
                            yield Static(id="milestones-panel", classes="panel")
                with TabPane("CHRONICLE", id="tab-chronicle"):
                    with Horizontal(classes="row"):
                        yield Static(id="chronicle-panel", classes="panel")
                        yield Static(id="memory-panel", classes="panel")
                with TabPane("SYSTEM", id="tab-system"):
                    yield Static(id="system-panel", classes="panel")
            yield RichLog(id="console-panel", classes="panel", auto_scroll=True, highlight=False, wrap=True, min_width=20)
            with Horizontal(id="command-bar"):
                yield Static("worldsim >", id="prompt")
                yield Input(placeholder="Enter a command or use arrow keys to move", id="command-input")
            yield Static(
                "Press 'm' or Esc to focus map. Arrows move. Use talk, then say <message> to converse.",
                id="footer-note",
            )

    def _set_panel_titles(self) -> None:
        titles = {
            "#map-panel": "WORLD MAP",
            "#region-panel": "SELECTED REGION",
            "#events-panel": "RECENT EVENTS",
            "#alerts-panel": "ALERTS",
            "#summary-panel": "WORLD SUMMARY",
            "#player-panel": "PLAYER",
            "#hooks-panel": "QUEST HOOKS",
            "#areas-panel": "AREA OVERVIEW",
            "#area-buttons": "AREA CHOICES",
            "#actions-panel": "AREA ACTIONS",
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
            "#chronicle-panel": "SIMULATION LOG",
            "#memory-panel": "MEMORY INDEX",
            "#system-panel": "SYSTEM NOTES",
            "#console-panel": "CONSOLE",
        }
        for selector, title in titles.items():
            widget = self.query_one(selector)
            widget.border_title = title

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "start-button":
            self._start_new_campaign()
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
            if self.session and self.session.entered_area is not None:
                self._handle_area_action(command)
            else:
                self._handle_command(command)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "command-input":
            command = event.value.strip()
            event.input.value = ""
            if command:
                self._append_transcript(f"> {command}")
                self._handle_command(command)

    def action_focus_command(self) -> None:
        if self.session is not None:
            self.query_one("#command-input", Input).focus()

    def action_focus_map(self) -> None:
        if self.session is not None:
            self.query_one("#map-panel", MapPanel).focus()

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
        self.query_one("#switcher", ContentSwitcher).current = "setup-screen"
        self.query_one("#name-input", Input).focus()

    def _start_new_campaign(self) -> None:
        if self.command_in_progress:
            return
        name = self.query_one("#name-input", Input).value.strip() or "Rowan"
        archetype = self.query_one("#class-input", Input).value.strip().lower() or "ranger"
        if archetype not in {"warrior", "rogue", "mage", "ranger"}:
            archetype = "ranger"
        homeland = self.query_one("#home-input", Input).value.strip() or "Northreach"

        self.command_in_progress = True
        self.stream_buffer = ""
        self.run_worker(
            lambda: self._start_new_campaign_worker(name, archetype, homeland),
            thread=True,
            exclusive=True,
        )

    def _start_new_campaign_worker(self, name: str, archetype: str, homeland: str) -> None:
        self._set_stream_callback("LLM world generation stream")
        world = self.engine.create_world(self.director)
        player = self.engine.create_player(world, name, archetype, homeland)
        memory = CampaignMemory()
        memory.remember_world_state(world, player)
        for hook in world.quest_hooks:
            memory.remember_hook(hook, world.tick)
        last_message = self.director.introduce_world(world, player, memory.relevant_context(world, player))
        session = Session(
            world=world,
            player=player,
            memory=memory,
            last_message=last_message,
            transcript=[f"DM: {last_message}"],
            selected_area=None,
            entered_area=None,
            entered_area_step=0,
            entered_area_tension=0,
            entered_area_theme=None,
            entered_area_hazard=None,
            entered_area_npc=None,
        )
        self.call_from_thread(self._finish_new_campaign, session)

    def _finish_new_campaign(self, session: Session) -> None:
        self._clear_stream_callback()
        self.session = session
        self.store.save(session.world, session.player, session.memory)
        self.command_in_progress = False
        self.follow_player = True
        self._center_camera_on_player()
        self.query_one("#switcher", ContentSwitcher).current = "game-screen"
        self._refresh_ui()
        self.call_after_refresh(self._sync_camera_after_layout)
        self.action_focus_map()

    def _handle_command(self, command: str) -> None:
        if self.session is None:
            return
        if self.command_in_progress:
            self._append_transcript("System: Still waiting on the current LLM response.")
            return
        self.command_in_progress = True
        self.stream_buffer = ""
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
        self.command_in_progress = False

    def _finish_command(self, result) -> None:
        self._clear_stream_callback()
        self.command_in_progress = False
        if self.session is None:
            return
        self.session.last_message = result.message
        self._append_transcript(f"DM: {result.message}")
        self.store.save(self.session.world, self.session.player, self.session.memory)
        if self.follow_player:
            self._track_player_in_view()
        self._refresh_ui()
        if self.session.player.hp <= 0:
            self.session.last_message = "You fall, and the frontier closes over your story."
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
            self._refresh_console_panel(f"{label}\n")
            self.query_one("#director-panel", Static).update("LLM response is streaming to the console.")

    def _append_stream_delta(self, delta: str) -> None:
        self.stream_buffer += delta
        if self.session is not None:
            self._refresh_console_panel(f"LLM raw stream:\n{self.stream_buffer[-2400:]}")

    def on_resize(self, event: Resize) -> None:
        del event
        if self.session is None:
            return
        if self.follow_player:
            self._track_player_in_view()
        self._refresh_ui()

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
        npc = self.engine.npc_at(location, world)
        self._sync_area_state(location)

        self.query_one("#topbar", Static).update(
            f"WORLDSIM v0.2  |  Seed {world.seed}  |  Tick {world.tick:,}  |  Weather: {world.weather}"
        )
        map_panel = self.query_one("#map-panel", Static)
        map_panel.border_subtitle = self._map_status(world)
        map_panel.update(self._build_map_renderable())
        self.query_one("#region-panel", Static).update(self._region_text(location))
        self.query_one("#events-panel", Static).update(self._events_text(world))
        self.query_one("#alerts-panel", Static).update(self._alerts_text(world))
        self.query_one("#summary-panel", Static).update(self._summary_text(world))
        self.query_one("#player-panel", Static).update(self._player_text(player))
        self.query_one("#hooks-panel", Static).update(self._hooks_text(world))
        self.query_one("#areas-panel", Static).update(self._areas_text(location))
        self._refresh_area_buttons()
        self._refresh_action_buttons()
        self.query_one("#local-panel", Static).update(self._local_text(location, npc, memory))
        self.query_one("#director-panel", Static).update(self.session.last_message)
        self.query_one("#inventory-panel", Static).update(self._inventory_text(player))
        self.query_one("#resources-panel", Static).update(self._resources_text(player, world))
        self.query_one("#loadout-panel", Static).update(self._loadout_text(player))
        self.query_one("#packs-panel", Static).update(self._packs_text(player))
        self.query_one("#skills-panel", Static).update(self._skills_text(player))
        self.query_one("#progression-panel", Static).update(self._progression_text(player))
        self.query_one("#traits-panel", Static).update(self._traits_text(player))
        self.query_one("#milestones-panel", Static).update(self._milestones_text(player))
        self.query_one("#chronicle-panel", Static).update(self._chronicle_text(world))
        self.query_one("#memory-panel", Static).update(self._memory_text(memory, world, player))
        self.query_one("#system-panel", Static).update(self._system_text(memory))
        self._refresh_console_panel()

    def _refresh_console_panel(self, live_stream: str | None = None) -> None:
        if self.session is None:
            return
        console_panel = self.query_one("#console-panel", RichLog)
        console_panel.clear()
        for line in self.session.transcript[-14:]:
            console_panel.write(line)
        if live_stream:
            console_panel.write(live_stream)

    def _build_map_renderable(self) -> Text:
        assert self.session is not None
        world = self.session.world
        player = self.session.player
        location_positions = {location.position: location.name[0].upper() for location in world.locations}
        text = Text(no_wrap=True)
        view_width, view_height = self._viewport_tile_size()
        if self.follow_player and not self._player_in_camera_view(world, player, view_width, view_height):
            self._track_player_in_view()
        start_x = self._clamp_camera(self.camera_x, world.width, view_width)
        start_y = self._clamp_camera(self.camera_y, world.height, view_height)
        end_x = min(world.width, start_x + view_width)
        end_y = min(world.height, start_y + view_height)

        for y in range(start_y, end_y):
            for x in range(start_x, end_x):
                pos = Position(x, y)
                if pos == player.position:
                    text.append("@@", style="bold #111827 on #fde047")
                elif pos in location_positions:
                    label = location_positions[pos]
                    text.append(f"{label}*", style="bold #ffe4f2 on #ec4899")
                else:
                    glyph, style = self._tile_token(world, x, y)
                    text.append(glyph, style=style)
            if y < end_y - 1:
                text.append("\n")
        return text

    def _tile_token(self, world: World, x: int, y: int) -> tuple[str, str]:
        biome = world.tiles[y][x]
        variant = (x * 17 + y * 31 + world.seed) % 4
        near_water = self._has_neighbor(world, x, y, Biome.WATER)
        near_mountain = self._has_neighbor(world, x, y, Biome.MOUNTAIN)

        if biome == Biome.WATER:
            tokens = ["~~", "~.", ".~", "=="] if near_water else ["..", " .", "..", " ."]
            styles = ["bold #60a5fa on #0b1f3a", "bold #38bdf8 on #082032", "bold #7dd3fc on #0a2540", "bold #93c5fd on #092844"]
            return tokens[variant], styles[variant]
        if biome == Biome.PLAIN:
            tokens = ["..", " .", " ,", ",."] if not near_water else ["' ", ".'", ", ", " ."]
            styles = ["#d6d3b3 on #1b1f1a", "#cbd5a1 on #1d2318", "#e5d8a8 on #21251a", "#d1d5b2 on #191f18"]
            return tokens[variant], styles[variant]
        if biome == Biome.FOREST:
            tokens = ["tt", "YY", "||", "tt"] if not near_mountain else ["t^", "Y^", "t|", "Y|"]
            styles = ["bold #4ade80 on #0d2417", "bold #86efac on #0f2a19", "bold #22c55e on #0c2013", "bold #65a30d on #15240f"]
            return tokens[variant], styles[variant]
        if biome == Biome.HILL:
            tokens = ["^^", "n^", "^^", "~^"]
            styles = ["bold #fbbf24 on #2b1f12", "bold #f59e0b on #31200f", "bold #fcd34d on #38240f", "bold #f59e0b on #2f2214"]
            return tokens[variant], styles[variant]
        if biome == Biome.MOUNTAIN:
            tokens = ["/\\", "A^", "MM", "/^"]
            styles = ["bold #e5e7eb on #2a2f3a", "bold #f8fafc on #313948", "bold #cbd5e1 on #252b35", "bold #dbeafe on #2e3440"]
            return tokens[variant], styles[variant]
        tokens = [";;", "::", ",;", ";,"]
        styles = ["bold #a3e635 on #21301a", "bold #84cc16 on #1d2a14", "bold #65a30d on #1b2618", "bold #bef264 on #233117"]
        return tokens[variant], styles[variant]

    def _region_text(self, location: Location | None) -> str:
        assert self.session is not None
        player = self.session.player
        world = self.session.world
        lines = [
            f"Position: {player.position.x},{player.position.y}",
            f"Terrain: {self.engine.biome_at(world, player.position).value}",
        ]
        if location is None:
            lines.append("Location: Untamed frontier")
            lines.append("No settlement claims this ground.")
        else:
            lines.extend(
                [
                    f"Location: {location.name}",
                    f"Danger: {location.danger}/9",
                    location.summary,
                ]
            )
        return "\n".join(lines)

    def _events_text(self, world: World) -> str:
        return "\n\n".join(f"[{event.tick}] {event.text}" for event in world.recent_events) or "No events recorded."

    def _alerts_text(self, world: World) -> str:
        return "\n".join(f"- {alert}" for alert in world.alerts) or "No immediate alerts."

    def _summary_text(self, world: World) -> str:
        counts = self.engine.summary_counts(world)
        return "\n".join(
            [
                f"World Age: {world.tick} turns",
                f"Map Size: {world.width} x {world.height}",
                f"Locations: {counts['locations']}",
                f"NPCs: {counts['npcs']}",
                f"Hooks: {counts['hooks']}",
                f"Stability: {world.stability}%",
                f"Camera: {'FOLLOW' if self.follow_player else 'FREE'}",
            ]
        )

    def _player_text(self, player: Player) -> str:
        return "\n".join(
            [
                f"Name: {player.name}",
                f"Class: {player.archetype.title()}",
                f"Homeland: {player.homeland}",
                f"HP: {player.hp}/{player.max_hp}",
                f"Gold: {player.gold}",
                f"XP: {player.xp}",
                "Inventory:",
                ", ".join(player.inventory),
            ]
        )

    def _inventory_text(self, player: Player) -> str:
        inventory_lines = [f"{index + 1}. {item.title()}" for index, item in enumerate(player.inventory)]
        if not inventory_lines:
            inventory_lines = ["No items carried."]
        return "\n".join(inventory_lines)

    def _resources_text(self, player: Player, world: World) -> str:
        return "\n".join(
            [
                f"Gold: {player.gold}",
                f"HP: {player.hp}/{player.max_hp}",
                f"XP: {player.xp}",
                f"World Tick: {world.tick}",
                f"Weather: {world.weather}",
                f"Stability: {world.stability}%",
            ]
        )

    def _loadout_text(self, player: Player) -> str:
        defaults = {
            "warrior": ["Primary: Iron blade", "Off-hand: Buckler", "Armor: Mail shirt"],
            "rogue": ["Primary: Knives", "Off-hand: Hook tool", "Armor: Shadow leathers"],
            "mage": ["Primary: Ash staff", "Focus: Rune charm", "Armor: Woven mantle"],
            "ranger": ["Primary: Longbow", "Sidearm: Hatchet", "Armor: Field coat"],
        }
        lines = defaults.get(player.archetype, ["Primary: Improvised kit"])
        return "\n".join(lines + ["", "Ready Items:", ", ".join(player.inventory[:3]) or "None"])

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
        return "\n".join(lines).strip() or "No pack categories available."

    def _skills_text(self, player: Player) -> str:
        level = self._player_level(player)
        lines = [f"Level {level} {player.archetype.title()}", ""]
        for node in self._skill_nodes(player):
            marker = "[x]" if level >= node["level"] else "[ ]"
            lines.append(f"{marker} L{node['level']} {node['name']}")
            lines.append(node["text"])
            lines.append("")
        return "\n".join(lines).strip()

    def _progression_text(self, player: Player) -> str:
        level = self._player_level(player)
        current_floor = self._level_xp_floor(level)
        next_floor = self._level_xp_floor(level + 1)
        remaining = max(0, next_floor - player.xp)
        return "\n".join(
            [
                f"Current Level: {level}",
                f"XP Total: {player.xp}",
                f"XP Into Level: {player.xp - current_floor}",
                f"Next Level At: {next_floor}",
                f"XP Remaining: {remaining}",
                "",
                "Power growth is tied to XP and archetype milestones.",
            ]
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
        return "\n\n".join(traits.get(player.archetype, ["No archetype traits defined yet."]))

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
        return "\n".join(lines).strip()

    def _hooks_text(self, world: World) -> str:
        return "\n\n".join(world.quest_hooks[:5]) or "No active hooks."

    def _areas_text(self, location: Location | None) -> str:
        assert self.session is not None
        player = self.session.player
        lines = [
            f"Rowan Position: {player.position.x},{player.position.y}" if player.name == "Rowan" else f"{player.name} Position: {player.position.x},{player.position.y}",
            f"Current Terrain: {self.engine.biome_at(self.session.world, player.position).value}",
        ]
        if location is not None:
            lines.append(f"Current Region: {location.name}")
        else:
            lines.append("Current Region: Untamed frontier")
        lines.append(f"Selected Area: {self._display_area(self.session.selected_area)}")
        lines.append(f"Entered Area: {self._display_area(self.session.entered_area)}")
        lines.append("")
        lines.append("Selecting or entering an area does not advance time.")
        return "\n".join(lines)

    def _local_text(self, location: Location | None, npc: Npc | None, memory: CampaignMemory) -> str:
        assert self.session is not None
        world = self.session.world
        player = self.session.player
        scope = location.name if location is not None else None
        memory_lines = memory.relevant_context(world, player, scope, limit=3)
        lines = []
        if self.session.entered_area is not None:
            lines.extend(
                [
                    f"Entered Area: {self._display_area(self.session.entered_area)}",
                    self._area_scene_text(location),
                    "",
                ]
            )
        elif self.session.selected_area is not None:
            lines.extend(
                [
                    f"Selected Area: {self._display_area(self.session.selected_area)}",
                    "Press Enter Area or click the button to drop into this scene.",
                    "",
                ]
            )
        if npc is not None:
            lines.extend(
                [
                    f"NPC: {npc.name}",
                    f"Role: {npc.role}",
                    f"Disposition: {npc.disposition}",
                    "",
                ]
            )
            history = world.conversations.get(npc.name, [])[-6:]
            if history:
                lines.append("Conversation:")
                lines.extend(history)
                lines.append("")
        visible_objects = world.scene_objects.get(f"{player.position.x},{player.position.y}", [])
        if visible_objects:
            lines.append("Visible objects:")
            lines.extend(f"- {item}" for item in visible_objects)
            lines.append("")
        lines.append("Relevant memory:")
        lines.extend(memory_lines or ["No strong local memories yet."])
        return "\n".join(lines)

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
                "- say <message> replies to the current NPC",
                "- shift+arrows pan the map",
                "- c recenters on the player",
                "- f toggles follow/free camera mode",
                "- ctrl+n opens new campaign setup",
                "- / focuses the command line",
            ]
        )

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
        return max(8, width // 2), height

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
        if self.session.entered_area not in self.area_choices:
            self.session.entered_area = None

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
        enter_button.disabled = self.session.selected_area is None or self.session.entered_area == self.session.selected_area
        leave_button.disabled = self.session.entered_area is None
        forward_button.disabled = self.session.entered_area is None or self.session.entered_area_step >= 2
        back_button.disabled = self.session.entered_area is None or self.session.entered_area_step <= 0
        try_leave_button.disabled = self.session.entered_area is None

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
        self.session.entered_area = self.session.selected_area
        self.session.entered_area_step = 0
        self.session.entered_area_tension = self._initial_area_tension()
        self.session.entered_area_theme = self._area_theme()
        self.session.entered_area_hazard = self._area_hazard()
        self.session.entered_area_npc = self._area_npc()
        self.session.last_message = f"You enter {self.session.entered_area}."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def _leave_area(self) -> None:
        if self.session is None or self.session.entered_area is None:
            return
        area_name = self.session.entered_area
        self.session.entered_area = None
        self.session.entered_area_step = 0
        self.session.entered_area_tension = 0
        self.session.entered_area_theme = None
        self.session.entered_area_hazard = None
        self.session.entered_area_npc = None
        self.session.last_message = f"You leave {area_name}."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def _area_choices(self, location: Location | None) -> list[str]:
        assert self.session is not None
        biome = self.engine.biome_at(self.session.world, self.session.player.position)
        return area.area_choices(location, biome)

    def _wilderness_areas(self) -> list[str]:
        assert self.session is not None
        biome = self.engine.biome_at(self.session.world, self.session.player.position)
        return area.wilderness_areas(biome)

    def _display_area(self, area_name: str | None) -> str:
        return area.display_area(area_name)

    def _area_scene_text(self, location: Location | None) -> str:
        assert self.session is not None
        return area.scene_text(
            location,
            self.session.entered_area,
            self.session.entered_area_step,
            self.session.entered_area_theme,
            self.session.entered_area_hazard,
            self.session.entered_area_npc,
        )

    def _move_within_area(self, delta: int) -> None:
        if self.session is None or self.session.entered_area is None:
            return
        self.session.entered_area_step = max(0, min(2, self.session.entered_area_step + delta))
        if delta > 0:
            self.session.entered_area_tension = min(9, self.session.entered_area_tension + 1)
            self.session.last_message = f"You push deeper into {self.session.entered_area}."
        else:
            self.session.last_message = f"You pull back toward the edge of {self.session.entered_area}."
        self._append_transcript(f"System: {self.session.last_message}")
        self._refresh_ui()

    def _try_leave_area(self) -> None:
        if self.session is None or self.session.entered_area is None:
            return
        difficulty = self.session.entered_area_tension + self.session.entered_area_step
        roll = self.engine.random.randint(1, 10)
        if roll >= difficulty:
            self._leave_area()
            return
        self.session.last_message = (
            f"The way out of {self.session.entered_area} tightens. "
            f"{self.session.entered_area_hazard or 'The place itself'} keeps you committed for now."
        )
        self.session.entered_area_tension = min(9, self.session.entered_area_tension + 1)
        self._append_transcript(f"DM: {self.session.last_message}")
        self._refresh_ui()

    def _handle_area_action(self, command: str) -> None:
        if self.session is None:
            return
        if command == "look":
            self.session.last_message = self._area_scene_text(self.engine.location_at(self.session.world, self.session.player.position))
            self._append_transcript(f"DM: {self.session.last_message}")
            self._refresh_ui()
            return
        if command == "talk" and self.session.entered_area_npc is None:
            self.session.last_message = "No one answers. The area only returns weather, structure, and tension."
            self._append_transcript(f"DM: {self.session.last_message}")
            self._refresh_ui()
            return
        self._handle_command(command)

    def _area_zone_name(self) -> str:
        assert self.session is not None
        return area.zone_name(self.session.entered_area_step)

    def _initial_area_tension(self) -> int:
        assert self.session is not None
        location = self.engine.location_at(self.session.world, self.session.player.position)
        return area.initial_tension(location)

    def _area_theme(self) -> str:
        assert self.session is not None
        biome = self.engine.biome_at(self.session.world, self.session.player.position)
        return area.area_theme(biome)

    def _area_hazard(self) -> str:
        assert self.session is not None
        biome = self.engine.biome_at(self.session.world, self.session.player.position)
        return area.area_hazard(biome, self._area_hash())

    def _area_npc(self) -> str | None:
        return area.area_npc(self._area_hash())

    def _area_hash(self) -> int:
        assert self.session is not None
        return area.stable_area_hash(self.session.world.seed, self.session.selected_area, self.session.entered_area)

    def _has_neighbor(self, world: World, x: int, y: int, biome: Biome) -> bool:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx = x + dx
                ny = y + dy
                if 0 <= nx < world.width and 0 <= ny < world.height and world.tiles[ny][nx] == biome:
                    return True
        return False
