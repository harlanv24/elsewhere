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
        if location is None:
            text = "The wilderness is quiet here, but not empty. Tracks and weather argue over which story matters most."
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
            )

        if action == "attack":
            return DirectorBeat(
                title="Violence",
                narration="Steel settles the question that words left unresolved." + memory_line,
                mechanical_request="combat_check",
                difficulty=10 + (location.danger if location else 3),
                tags=["combat"],
                follow_up_hook="Victory here will reshape how this place speaks about you.",
            )

        if action == "rest":
            return DirectorBeat(
                title="Camp",
                narration="You take a careful pause, listening for the difference between silence and danger." + memory_line,
                mechanical_request=None,
                tags=["rest"],
            )

        return DirectorBeat(
            title="Passing Time",
            narration="The world keeps moving, whether watched closely or not." + memory_line,
            mechanical_request=None,
            tags=["time"],
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
        del world, player, memory_context
        place = location.name if location else "the frontier"
        return DirectorBeat(
            title="Improvised Action",
            narration=f"You try to {action} around {place}. The moment shifts, but nothing certain gives way yet.",
            mechanical_request=None,
            tags=["freeform"],
            scene_objects=[],
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
        return f"LLM director: {config.model} at {config.base_url}"

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
        raw = self.client.complete_streaming(_llm_system_prompt(), user, self.on_stream_delta)
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
        "Read the JSON task and context from the user message. "
        "Return only one JSON object that matches response_schema. "
        "For generate_world_details, rewrite the provided indexed locations and NPCs while preserving their indexes. "
        "Location names must be evocative fantasy place names, not the placeholders from context. "
        "Quest hooks must be concrete unresolved adventure rumors. "
        "Keep generate_world_details compact: summaries and hooks should each be one sentence. "
        "For respond_to_dialogue, write only the NPC's in-character reply and make it respond directly to player_dialogue. "
        "NPCs may ask one specific follow-up question when useful. "
        "Use state_ledger.npc_conversation_history as authoritative conversation state; do not repeat a prior NPC answer unless the player asks for repetition. "
        "Never return text that closely matches state_ledger.npc_prior_replies. "
        "If the player changes topic, answer the new topic directly. "
        "For respond_to_action with action talk, vary the NPC's opener using active_dialogue_history and include a question or clear conversational hook. "
        "For respond_to_freeform_action, interpret action as the player's exact attempted action. "
        "Treat state_ledger as authoritative world state. Do not reintroduce objects marked destroyed, removed, or in_inventory. "
        "Do not offer inventory_add for an item already present in state_ledger.player_inventory. "
        "Use visible_scene_objects to decide whether taking, reading, opening, searching, using, or moving an object makes sense. "
        "Do not restate the setup from memory_context. Start at the moment the action happens and describe the consequence. "
        "If action repeats a recent action in memory_context, make the world react to the repetition instead of reusing the old wording. "
        "If action targets a visible object, directly resolve what happens to that object. "
        "If action is destructive, describe whether the target is damaged, changed, protected, or destroyed. "
        "If the action reveals objects, list them in scene_objects. "
        "If the player successfully takes a small portable object, list it in inventory_add. "
        "If the player uses or consumes an inventory item, list it in inventory_remove. "
        "Do not wrap the JSON in Markdown. "
        "Do not add commentary before or after the JSON. "
        "Use null for absent optional fields. "
        "Keep narration to one or two vivid paragraphs."
    )


def _world_generation_context(world: World) -> dict[str, object]:
    return {
        "world": {
            "seed": world.seed,
            "tick": world.tick,
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
        "style": {
            "genre": "grounded fantasy frontier",
            "tone": "mysterious, playable, concise",
            "avoid": ["generic medieval filler", "modern slang", "placeholder names"],
        },
    }
