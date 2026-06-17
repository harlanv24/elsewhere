from __future__ import annotations

import json
import os
import random
from abc import ABC, abstractmethod
from typing import Callable

from worldsim.debug import DebugLogger
from worldsim.llm_client import LLMClient, LLMClientError
from worldsim.models import DirectorBeat, Location, Npc, Player, World
from worldsim.schemas import (
    DIRECTOR_BEAT_SCHEMA,
    TEXT_RESPONSE_SCHEMA,
    WORLD_DETAILS_SCHEMA,
    director_beat_from_payload,
    director_context,
    parse_json_object,
    text_from_payload,
    world_details_from_payload,
)


LLM_ENGINE_CONTRACT = """
You are the world director, not the rules engine.

You may:
- name locations, NPCs, landmarks, factions, relics, rumors
- frame scenes and present opportunities
- suggest a check, risk, or consequence using structured intent
- treat visible scene objects and carried inventory as first-class story elements
- mention how items, tools, and objects matter in the current situation when they are present

You may not:
- decide dice outcomes
- modify HP, gold, XP, inventory, or map coordinates
- invalidate the established world state

Return structured beats with:
- title
- narration
- mechanical_request
- difficulty
- tags
- follow_up_hook
- choices
- progress_summary
- quest_progress_delta
- complete_current_stage
- clock_effects
""".strip()


class Director(ABC):
    @abstractmethod
    def introduce_world(self, world: World, player: Player, memory_context: list[str] | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def describe_location(
        self,
        world: World,
        player: Player,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def respond_to_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        raise NotImplementedError

    @abstractmethod
    def ambient_world_event(self, world: World) -> str:
        raise NotImplementedError

    def generate_world_details(self, world: World) -> dict[str, object] | None:
        return None

    def respond_to_freeform_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        return DirectorBeat(
            title="Improvised Action",
            narration=f"You try to {action}, but the moment does not clearly change.",
            tags=["freeform"],
        )

    @abstractmethod
    def respond_to_dialogue(
        self,
        world: World,
        player: Player,
        player_dialogue: str,
        location: Location | None,
        npc: Npc,
        memory_context: list[str] | None = None,
        dialogue_history: list[str] | None = None,
    ) -> str:
        raise NotImplementedError


class MockDirector(Director):
    """Template-driven director used until a real LLM backend is wired in."""

    def __init__(self, seed: int) -> None:
        self.random = random.Random(seed)

    def introduce_world(self, world: World, player: Player, memory_context: list[str] | None = None) -> str:
        start = next(location for location in world.locations if location.position == player.position)
        openings = [
            f"{player.name} of {player.homeland} arrives in {start.name}, where rumors travel faster than carts.",
            f"The road ends at {start.name}. Beyond it, the frontier begins writing new history around {player.name}.",
            f"{player.name} steps into {start.name} as if the world has been waiting for the right witness.",
        ]
        intro = self.random.choice(openings)
        if memory_context:
            intro += f" Memory already anchors the scene: {memory_context[0]}"
        return intro

    def describe_location(
        self,
        world: World,
        player: Player,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> str:
        visible_items = world.scene_objects.get(f"{player.position.x},{player.position.y}", [])
        if location is None:
            text = "The wilderness is quiet here, but not empty. Tracks and weather argue over which story matters most."
            if visible_items:
                text += f" Nearby objects: {', '.join(item.title() for item in visible_items[:3])}."
            if player.inventory:
                text += f" You are carrying: {', '.join(item.title() for item in player.inventory[:3])}."
            if memory_context:
                text += f" A remembered thread returns: {memory_context[0]}"
            return text

        details = [
            f"{location.name} sits in the {location.biome.value.lower()}, carrying an air of {location.summary.lower()}",
            f"{location.name} feels lived in and watched. The ground suggests old traffic and newer caution.",
            f"{location.name} is the sort of place where news arrives bent out of shape but still dangerous.",
        ]
        note = self.random.choice(details)
        if npc is not None:
            note += f" {npc.name}, a {npc.disposition} {npc.role}, is nearby."
        if visible_items:
            note += f" Visible objects here: {', '.join(item.title() for item in visible_items[:3])}."
        if player.inventory:
            note += f" You carry: {', '.join(item.title() for item in player.inventory[:3])}."
        if memory_context:
            note += f" You recall: {memory_context[0]}"
        return note

    def respond_to_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        memory_line = f" Memory leans on the moment: {memory_context[0]}" if memory_context else ""
        visible_items = world.scene_objects.get(f"{player.position.x},{player.position.y}", [])
        if action == "explore":
            title = "Field Discovery"
            place = location.name if location else "the wilds"
            narrations = [
                f"While exploring {place}, you find the edge of a story larger than the road itself.",
                f"The land around {place} yields a small secret, as if it expected someone patient enough to notice.",
                f"A detail hidden in plain sight around {place} begins to look deliberate.",
            ]
            hooks = [
                "Fresh boot prints lead away from the scene.",
                "Someone marked the stones with a half-erased sigil.",
                "The clue points toward a larger power moving quietly nearby.",
            ]
            return DirectorBeat(
                title=title,
                narration=self.random.choice(narrations) + memory_line,
                mechanical_request="exploration_check",
                difficulty=9 + (location.danger if location else 2),
                tags=["exploration", "discovery"],
                follow_up_hook=self.random.choice(hooks),
                scene_objects=visible_items[:4],
            )

        if action == "talk":
            title = "Conversation"
            if npc is None:
                return DirectorBeat(
                    title=title,
                    narration="You call into the air, but the frontier answers with weather and distance." + memory_line,
                    mechanical_request=None,
                    tags=["social", "quiet"],
                )
            rumors = [
                f"{npc.name} hints that merchants have started avoiding one of the old roads.",
                f"{npc.name} mentions lights moving where no village stands.",
                f"{npc.name} swears an oath was broken somewhere upriver, and the land remembers.",
            ]
            return DirectorBeat(
                title=title,
                narration=self.random.choice(rumors) + memory_line,
                mechanical_request="social_check",
                difficulty=8,
                tags=["social", "rumor"],
                follow_up_hook=f"{npc.name} might know more if you prove useful.",
                scene_objects=visible_items[:4],
            )

        if action == "attack":
            return DirectorBeat(
                title="Violence",
                narration="Steel settles the question that words left unresolved." + memory_line,
                mechanical_request="combat_check",
                difficulty=10 + (location.danger if location else 3),
                tags=["combat"],
                follow_up_hook="Victory here will reshape how this place speaks about you.",
                scene_objects=visible_items[:4],
            )

        if action == "rest":
            return DirectorBeat(
                title="Camp",
                narration="You take a careful pause, listening for the difference between silence and danger." + memory_line,
                mechanical_request=None,
                tags=["rest"],
                scene_objects=visible_items[:4],
            )

        return DirectorBeat(
            title="Passing Time",
            narration="The world keeps moving, whether watched closely or not." + memory_line,
            mechanical_request=None,
            tags=["time"],
            scene_objects=visible_items[:4],
        )

    def ambient_world_event(self, world: World) -> str:
        subjects = [location.name for location in world.locations[:4]]
        templates = [
            f"A trader from {self.random.choice(subjects)} claims the river route is safer this week.",
            f"Smoke was seen near {self.random.choice(subjects)} after sunset.",
            f"Two households in {self.random.choice(subjects)} are feuding over a debt no one can verify.",
            f"An old banner has been raised again near {self.random.choice(subjects)}.",
        ]
        return self.random.choice(templates)

    def respond_to_freeform_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        visible_items = world.scene_objects.get(f"{player.position.x},{player.position.y}", [])
        place = location.name if location else "the frontier"
        item_clause = ""
        if visible_items:
            item_clause = f" Visible objects include {', '.join(item.title() for item in visible_items[:3])}."
        if player.inventory:
            item_clause += f" Carried items include {', '.join(item.title() for item in player.inventory[:3])}."
        return DirectorBeat(
            title="Improvised Action",
            narration=f"You try to {action} around {place}.{item_clause} The moment shifts, but nothing certain gives way yet.",
            mechanical_request=None,
            tags=["freeform"],
            scene_objects=visible_items[:4],
        )

    def respond_to_dialogue(
        self,
        world: World,
        player: Player,
        player_dialogue: str,
        location: Location | None,
        npc: Npc,
        memory_context: list[str] | None = None,
        dialogue_history: list[str] | None = None,
    ) -> str:
        del world, player, location, memory_context, dialogue_history
        replies = [
            f"{npc.name} weighs your words, then says, \"That changes what I am willing to tell you.\"",
            f"{npc.name} answers carefully: \"Ask that too loudly and the wrong people will hear.\"",
            f"{npc.name} studies you for a moment. \"Maybe you are useful after all.\"",
        ]
        if "help" in player_dialogue.lower():
            return f"{npc.name} says, \"Help has a price, but I can point you toward trouble worth solving.\""
        return self.random.choice(replies)


class LocalLLMDirector(Director):
    """Director backed by a local OpenAI-compatible chat completions server."""

    def __init__(self, client: LLMClient, fallback: Director, debug_logger: DebugLogger | None = None) -> None:
        self.client = client
        self.fallback = fallback
        self.debug_logger = debug_logger
        self.last_error: str | None = None
        self.last_task: str | None = None
        self.last_used_fallback = False
        self.last_payload: dict[str, object] | None = None
        self.on_stream_delta: Callable[[str], None] | None = None

    @property
    def status_line(self) -> str:
        config = self.client.config
        if self.last_error:
            return f"LLM director: fallback after {self.last_task or 'request'} failed ({self.last_error})"
        auth = "API key present" if config.api_key else "no API key"
        return f"LLM director: {config.model} at {config.base_url} ({auth})"

    def introduce_world(self, world: World, player: Player, memory_context: list[str] | None = None) -> str:
        context = director_context(world, player=player, memory_context=memory_context)
        try:
            return self._request_text("introduce_world", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("introduce_world", exc)
            return self.fallback.introduce_world(world, player, memory_context)

    def describe_location(
        self,
        world: World,
        player: Player,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> str:
        context = director_context(world, player=player, location=location, npc=npc, memory_context=memory_context)
        try:
            return self._request_text("describe_location", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("describe_location", exc)
            return self.fallback.describe_location(world, player, location, npc, memory_context)

    def respond_to_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        context = director_context(
            world,
            player=player,
            location=location,
            npc=npc,
            memory_context=memory_context,
            action=action,
        )
        try:
            return self._request_beat("respond_to_action", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("respond_to_action", exc)
            return self.fallback.respond_to_action(world, player, action, location, npc, memory_context)

    def ambient_world_event(self, world: World) -> str:
        context = director_context(world)
        try:
            return self._request_text("ambient_world_event", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("ambient_world_event", exc)
            return self.fallback.ambient_world_event(world)

    def respond_to_freeform_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        memory_context: list[str] | None = None,
    ) -> DirectorBeat:
        context = director_context(
            world,
            player=player,
            location=location,
            npc=npc,
            memory_context=memory_context,
            action=action,
        )
        try:
            return self._request_beat("respond_to_freeform_action", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("respond_to_freeform_action", exc)
            return self.fallback.respond_to_freeform_action(world, player, action, location, npc, memory_context)

    def respond_to_dialogue(
        self,
        world: World,
        player: Player,
        player_dialogue: str,
        location: Location | None,
        npc: Npc,
        memory_context: list[str] | None = None,
        dialogue_history: list[str] | None = None,
    ) -> str:
        context = director_context(
            world,
            player=player,
            location=location,
            npc=npc,
            memory_context=memory_context,
            player_dialogue=player_dialogue,
            dialogue_history=dialogue_history,
        )
        try:
            return self._request_text("respond_to_dialogue", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("respond_to_dialogue", exc)
            return self.fallback.respond_to_dialogue(
                world,
                player,
                player_dialogue,
                location,
                npc,
                memory_context,
                dialogue_history,
            )

    def generate_world_details(self, world: World) -> dict[str, object] | None:
        context = _world_generation_context(world)
        try:
            payload = self._request_json("generate_world_details", context, WORLD_DETAILS_SCHEMA)
            details = world_details_from_payload(payload)
            if not details.get("locations") and not details.get("quest_hooks"):
                raise ValueError("World details response did not include usable locations or hooks.")
            return details
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("generate_world_details", exc)
            return None

    def _request_text(self, task: str, context: dict[str, object]) -> str:
        payload = self._request_json(task, context, TEXT_RESPONSE_SCHEMA)
        return text_from_payload(payload)

    def _request_beat(self, task: str, context: dict[str, object]) -> DirectorBeat:
        payload = self._request_json(task, context, DIRECTOR_BEAT_SCHEMA)
        return director_beat_from_payload(payload)

    def _request_json(
        self,
        task: str,
        context: dict[str, object],
        response_schema: dict[str, object],
    ) -> dict[str, object]:
        user_payload = {
            "task": task,
            "context": context,
            "response_schema": response_schema,
        }
        user = json.dumps(user_payload, indent=2)
        self._log("director_prompt", task=task, system=_llm_system_prompt(), user_payload=user_payload)
        raw = self.client.complete_streaming(
            _llm_system_prompt(),
            user,
            self.on_stream_delta,
            response_schema=response_schema,
        )
        self._log("director_raw_response", task=task, raw=raw)
        payload = parse_json_object(raw)
        self._log("director_parsed_response", task=task, payload=payload)
        self.last_error = None
        self.last_task = task
        self.last_used_fallback = False
        self.last_payload = payload
        return payload

    def _record_fallback(self, task: str, exc: Exception) -> None:
        self.last_task = task
        self.last_error = str(exc)
        self.last_used_fallback = True
        self._log("director_fallback", task=task, error=str(exc), error_type=type(exc).__name__)

    def _log(self, event: str, **fields: object) -> None:
        if self.debug_logger is None:
            return
        self.debug_logger.log(event, **fields)


def director_from_env(seed: int, debug_logger: DebugLogger | None = None) -> Director:
    fallback = MockDirector(seed)
    if os.getenv("WORLDSIM_DIRECTOR", "llm").lower() != "llm":
        return fallback
    return LocalLLMDirector(LLMClient.from_env(debug_logger), fallback, debug_logger)


def _llm_system_prompt() -> str:
    return (
        f"{LLM_ENGINE_CONTRACT}\n\n"
        "Read the JSON task and context from the user message. Return exactly one JSON object matching response_schema. "
        "Use null for absent optional fields and do not wrap JSON in Markdown. "
        "For generate_world_details, preserve provided indexes. Use context.theme_prompt to create a compact campaign_title, overarching_quest, weather, opening_event, named locations, NPCs, quest hooks, five or six lower-case player_archetypes with distinctive rollable skill bonuses, five to eight homelands, starting_inventory, and skill_descriptions. Keep worldbuilding terse and playable. "
        "Player archetypes should not be evenly distributed. Give each one a strong identity: four to eight skills, at least one drawback on most archetypes, rare niche skills, occasional signature +4 bonuses, normal strengths around +1 to +3, and penalties from -1 to -2 where appropriate. "
        "Starting inventory should be three to five mundane, useful, theme-specific items with short descriptions. Skill descriptions should explain how each generated skill works in this specific theme, not generic adventure wording. "
        "Infer genre, tone, stakes, social rules, dialogue style, locations, items, class names, and conflicts from theme_prompt. Theme_prompt overrides every default fantasy or frontier assumption. "
        "Treat biomes and coordinates as an abstract scene scaffold; reinterpret them through the theme instead of mentioning terrain when it does not fit. Keep summaries and hooks to one short sentence. "
        "For narration tasks, write two to five concise sentences grounded in current state and matching the theme's tone. Favor playable information, voice, and choices over lore exposition. Do not decide dice outcomes or mutate engine-owned resources. "
        "Treat context.world.quests and context.world.clocks as the campaign spine. Drive the active quest's current_stage forward instead of replacing it with a tangent. "
        "Use progress_summary for concrete evidence, commitments, discoveries, or consequences worth recording. quest_progress_delta is 0 for color, 1 for meaningful progress, and 2 for major progress. Set complete_current_stage only when the scene satisfies the current stage. "
        "Use clock_effects only for existing clock IDs. Positive deltas worsen pressure; negative deltas reduce pressure. follow_up_hook is only a loose thread or rumor. "
        "Offer two to four short choices for most action, exploration, and dialogue beats. Choices should be concrete player actions. "
        "Request mechanical_request and difficulty for uncertainty: exploration_check for search, traversal, lore, hazards; social_check for persuasion, deception, intimidation, insight, negotiation; combat_check for violence, chases under threat, and direct physical danger. "
        "For dialogue, reply in character to player_dialogue, match the theme's style and social rules, use conversation history as authoritative state, avoid repeated prior NPC replies, and move stalled conversations toward a decision or action. "
        "For freeform actions, resolve the exact attempted action against state_ledger and visible_scene_objects. Do not reintroduce removed, destroyed, or in_inventory objects. "
        "Treat player_inventory_details as concrete carried props and mention them when relevant to the scene. "
        "If an action reveals objects, list them in scene_objects. If the player takes a small visible portable object, list it in inventory_add. If the player uses or consumes an inventory item, list it in inventory_remove. "
        "Prefer choices that reference actual items, scene objects, and location details instead of generic filler."
    )


def _world_generation_context(world: World) -> dict[str, object]:
    return {
        "theme_prompt": world.theme_prompt,
        "world": {
            "seed": world.seed,
            "tick": world.tick,
            "theme_prompt": world.theme_prompt,
            "stability": world.stability,
            "map_size": {"width": world.width, "height": world.height},
            "locations": [
                {
                    "index": index,
                    "biome": location.biome.value,
                    "danger": location.danger,
                    "position": {"x": location.position.x, "y": location.position.y},
                }
                for index, location in enumerate(world.locations)
            ],
            "npc_slots": [
                {
                    "index": index,
                    "location_index": min(index, len(world.locations) - 1),
                }
                for index, _ in enumerate(world.npcs)
            ],
        },
        "style": _style_context(world.theme_prompt),
    }


def _style_context(theme_prompt: str) -> dict[str, object]:
    avoid = ["placeholder names", "generic filler", "ignoring the requested theme"]
    guidance = [
        "Infer genre, tone, stakes, dialogue style, social rules, pacing, and naming from theme_prompt.",
        "The terrain scaffold is abstract; reinterpret it as neighborhoods, rooms, routes, sectors, offices, venues, or other theme-appropriate spaces.",
        "Do not copy default fantasy language unless theme_prompt asks for it.",
    ]
    return {
        "genre": theme_prompt,
        "tone": "infer from theme_prompt; keep it playable and concise",
        "guidance": guidance,
        "avoid": avoid,
    }
