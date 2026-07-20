from __future__ import annotations

import json
import os
import random
from abc import ABC, abstractmethod
from typing import Callable

from worldsim.context import ContextMetrics, ContextSelector
from worldsim.contracts import (
    SchemaValidationError,
    contract_for,
    repair_system_prompt,
    task_system_prompt,
    validate_payload,
)
from worldsim.debug import DebugLogger
from worldsim.llm_client import LLMClient, LLMClientError
from worldsim.models import (
    ActionIntent,
    CheckKind,
    DirectorBeat,
    EffectKind,
    Location,
    Npc,
    Player,
    StateEffect,
    TurnRecord,
    World,
)
from worldsim.schemas import (
    action_intent_from_payload,
    director_beat_from_payload,
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

For interpret_freeform_action, return an ActionIntent-shaped proposal without
claiming whether the attempt succeeds. For narrate_turn_outcome, treat the
resolved turn, check result, and accepted effects as authoritative and narrate
only what actually happened.
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

    def interpret_freeform_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        intent_id: str,
        memory_context: list[str] | None = None,
    ) -> ActionIntent:
        """Compatibility adapter for directors that still return DirectorBeat."""

        beat = self.respond_to_freeform_action(world, player, action, location, npc, memory_context)
        return _intent_from_beat(intent_id, action, beat, world)

    def narrate_turn_outcome(
        self,
        world: World,
        player: Player,
        location: Location | None,
        npc: Npc | None,
        record: TurnRecord,
        memory_context: list[str] | None = None,
    ) -> str:
        """Outcome-aware fallback used by mock and legacy director implementations."""

        del world, location, npc, memory_context
        if record.outcome.success is True:
            lead = f"{player.name} succeeds at {record.intent.raw_input}."
        elif record.outcome.success is False:
            lead = f"{player.name} attempts to {record.intent.raw_input}, but fails."
        else:
            lead = f"{player.name} follows through: {record.intent.raw_input}."
        summary = record.outcome.authoritative_summary
        return f"{lead} {summary}".strip()

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
    ) -> DirectorBeat | str:
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
    ) -> DirectorBeat:
        del world, player, location, memory_context, dialogue_history
        replies = [
            f"{npc.name} weighs your words, then says, \"That changes what I am willing to tell you.\"",
            f"{npc.name} answers carefully: \"Ask that too loudly and the wrong people will hear.\"",
            f"{npc.name} studies you for a moment. \"Maybe you are useful after all.\"",
        ]
        if "help" in player_dialogue.lower():
            narration = f"{npc.name} says, \"Help has a price, but I can point you toward trouble worth solving.\""
        else:
            narration = self.random.choice(replies)
        return DirectorBeat(
            title="Conversation",
            narration=narration,
            tags=["dialogue", "social"],
            choices=["press for details", "offer help", "end conversation"],
        )


class LocalLLMDirector(Director):
    """Director backed by a local OpenAI-compatible chat completions server."""

    def __init__(
        self,
        client: LLMClient,
        fallback: Director,
        debug_logger: DebugLogger | None = None,
        context_selector: ContextSelector | None = None,
        repair_attempts: int | None = None,
    ) -> None:
        self.client = client
        self.fallback = fallback
        self.debug_logger = debug_logger
        self.context_selector = context_selector or ContextSelector()
        if repair_attempts is None:
            try:
                configured_repairs = int(
                    os.getenv("WORLDSIM_LLM_REPAIR_ATTEMPTS", "1")
                )
            except ValueError:
                configured_repairs = 1
        else:
            configured_repairs = repair_attempts
        self.repair_attempts = max(0, min(2, configured_repairs))
        self.last_error: str | None = None
        self.last_task: str | None = None
        self.last_used_fallback = False
        self.last_payload: dict[str, object] | None = None
        self.last_context_metrics: ContextMetrics | None = None
        self.context_metrics: list[ContextMetrics] = []
        self.last_repair_count = 0
        self.on_stream_delta: Callable[[str], None] | None = None

    @property
    def status_line(self) -> str:
        config = self.client.config
        if self.last_error:
            return f"LLM director: fallback after {self.last_task or 'request'} failed ({self.last_error})"
        auth = "API key present" if config.api_key else "no API key"
        context = (
            f", context {self.last_context_metrics.estimated_tokens}/"
            f"{self.last_context_metrics.budget_tokens} est. tokens"
            if self.last_context_metrics is not None
            else ""
        )
        return f"LLM director: {config.model} at {config.base_url} ({auth}{context})"

    def introduce_world(self, world: World, player: Player, memory_context: list[str] | None = None) -> str:
        try:
            context = self._context(
                "introduce_world",
                world,
                player=player,
                memory_context=memory_context,
            )
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
        try:
            context = self._context(
                "describe_location",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
            )
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
        try:
            context = self._context(
                "respond_to_action",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
                action=action,
            )
            return self._request_beat("respond_to_action", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("respond_to_action", exc)
            return self.fallback.respond_to_action(world, player, action, location, npc, memory_context)

    def ambient_world_event(self, world: World) -> str:
        try:
            context = self._context("ambient_world_event", world)
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
        try:
            context = self._context(
                "respond_to_freeform_action",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
                action=action,
            )
            return self._request_beat("respond_to_freeform_action", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("respond_to_freeform_action", exc)
            return self.fallback.respond_to_freeform_action(world, player, action, location, npc, memory_context)

    def interpret_freeform_action(
        self,
        world: World,
        player: Player,
        action: str,
        location: Location | None,
        npc: Npc | None,
        intent_id: str,
        memory_context: list[str] | None = None,
    ) -> ActionIntent:
        try:
            context = self._context(
                "interpret_freeform_action",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
                action=action,
            )
            payload = self._request_json("interpret_freeform_action", context)
            return action_intent_from_payload(payload, intent_id, action)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("interpret_freeform_action", exc)
            return self.fallback.interpret_freeform_action(
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
        world: World,
        player: Player,
        location: Location | None,
        npc: Npc | None,
        record: TurnRecord,
        memory_context: list[str] | None = None,
    ) -> str:
        try:
            context = self._context(
                "narrate_turn_outcome",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
                action=record.command,
                turn_record=record,
            )
            return self._request_text("narrate_turn_outcome", context)
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("narrate_turn_outcome", exc)
            return self.fallback.narrate_turn_outcome(
                world,
                player,
                location,
                npc,
                record,
                memory_context,
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
    ) -> DirectorBeat | str:
        try:
            context = self._context(
                "respond_to_dialogue",
                world,
                player=player,
                location=location,
                npc=npc,
                memory_context=memory_context,
                player_dialogue=player_dialogue,
                dialogue_history=dialogue_history,
            )
            return self._request_beat("respond_to_dialogue", context)
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
        try:
            context = self._context("generate_world_details", world)
            payload = self._request_json("generate_world_details", context)
            details = world_details_from_payload(payload)
            if not details.get("locations") and not details.get("quest_hooks"):
                raise ValueError("World details response did not include usable locations or hooks.")
            return details
        except (LLMClientError, ValueError, json.JSONDecodeError) as exc:
            self._record_fallback("generate_world_details", exc)
            return None

    def _request_text(self, task: str, context: dict[str, object]) -> str:
        payload = self._request_json(task, context)
        return text_from_payload(payload)

    def _request_beat(self, task: str, context: dict[str, object]) -> DirectorBeat:
        payload = self._request_json(task, context)
        return director_beat_from_payload(payload)

    def _request_json(
        self,
        task: str,
        context: dict[str, object],
    ) -> dict[str, object]:
        contract = contract_for(task)
        schema = contract.schema
        user_payload = {
            "task": task,
            "context": context,
            "response_schema": schema,
        }
        system = task_system_prompt(task, LLM_ENGINE_CONTRACT)
        user = json.dumps(user_payload, separators=(",", ":"), ensure_ascii=False)
        self._log(
            "director_prompt",
            task=task,
            system=system,
            user_payload=user_payload,
        )
        self.last_repair_count = 0
        raw = self.client.complete_streaming(
            system,
            user,
            self.on_stream_delta,
            response_schema=schema,
        )
        self._log("director_raw_response", task=task, raw=raw)
        try:
            payload = validate_payload(parse_json_object(raw), schema)
        except (SchemaValidationError, ValueError, json.JSONDecodeError) as exc:
            payload = self._repair_response(
                task,
                context,
                schema,
                raw,
                exc,
            )
        self._log("director_parsed_response", task=task, payload=payload)
        self.last_error = None
        self.last_task = task
        self.last_used_fallback = False
        self.last_payload = payload
        return payload

    def _repair_response(
        self,
        task: str,
        context: dict[str, object],
        schema: dict[str, object],
        raw: str,
        initial_error: Exception,
    ) -> dict[str, object]:
        error: Exception = initial_error
        invalid_response = raw
        for attempt in range(1, self.repair_attempts + 1):
            errors = (
                error.errors
                if isinstance(error, SchemaValidationError)
                else [str(error)]
            )
            repair_payload = {
                "task": task,
                "context": context,
                "invalid_response": invalid_response[:4000],
                "validation_errors": errors[:12],
                "response_schema": schema,
            }
            self._log(
                "director_repair_attempt",
                task=task,
                attempt=attempt,
                validation_errors=errors,
            )
            repaired_raw = self.client.complete_streaming(
                repair_system_prompt(task, LLM_ENGINE_CONTRACT),
                json.dumps(
                    repair_payload,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
                None,
                response_schema=schema,
            )
            self.last_repair_count = attempt
            self._log(
                "director_repair_response",
                task=task,
                attempt=attempt,
                raw=repaired_raw,
            )
            try:
                return validate_payload(
                    parse_json_object(repaired_raw),
                    schema,
                )
            except (
                SchemaValidationError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                error = exc
                invalid_response = repaired_raw
        raise error

    def _context(
        self,
        task: str,
        world: World,
        **kwargs: object,
    ) -> dict[str, object]:
        selection = self.context_selector.select(task, world, **kwargs)
        self.last_context_metrics = selection.metrics
        self.context_metrics.append(selection.metrics)
        del self.context_metrics[:-100]
        self._log(
            "director_context_budget",
            task=task,
            estimated_tokens=selection.metrics.estimated_tokens,
            budget_tokens=selection.metrics.budget_tokens,
            character_count=selection.metrics.character_count,
            dropped_items=selection.metrics.dropped_items,
            truncated_strings=selection.metrics.truncated_strings,
            within_budget=selection.metrics.within_budget,
        )
        return selection.context

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


def _intent_from_beat(intent_id: str, action: str, beat: DirectorBeat, world: World) -> ActionIntent:
    effects = [
        StateEffect(kind=EffectKind.SCENE_OBJECT_ADD, target_id=item)
        for item in beat.scene_objects
    ]
    effects.extend(
        StateEffect(kind=EffectKind.INVENTORY_ADD, target_id=item)
        for item in beat.inventory_add
    )
    effects.extend(
        StateEffect(kind=EffectKind.INVENTORY_REMOVE, target_id=item)
        for item in beat.inventory_remove
    )
    if beat.follow_up_hook:
        effects.append(StateEffect(kind=EffectKind.QUEST_HOOK_ADD, value=beat.follow_up_hook))
    effects.extend(
        StateEffect(kind=EffectKind.FACT_DISCOVERED, target_id=fact)
        for fact in beat.facts_discovered
    )
    effects.extend(
        StateEffect(
            kind=EffectKind.NPC_DISPOSITION,
            target_id=change.get("npc_id"),
            value=change.get("disposition"),
        )
        for change in beat.npc_disposition_changes
        if change.get("npc_id") and change.get("disposition")
    )
    effects.extend(
        StateEffect(kind=EffectKind.CHOICE_COMMIT, target_id=choice_id)
        for choice_id in beat.choices_committed
    )
    if beat.quest_progress_delta or beat.complete_current_stage or beat.facts_discovered:
        effects.append(
            StateEffect(
                kind=EffectKind.QUEST_PROGRESS,
                target_id=world.active_quest_id,
                value=beat.progress_summary,
                amount=beat.quest_progress_delta,
                flag=beat.complete_current_stage,
            )
        )
    for raw_effect in beat.clock_effects:
        clock_id = raw_effect.get("clock_id")
        delta = raw_effect.get("delta")
        reason = raw_effect.get("reason")
        if isinstance(clock_id, str) and isinstance(delta, int):
            effects.append(
                StateEffect(
                    kind=EffectKind.CLOCK_DELTA,
                    target_id=clock_id,
                    value=reason if isinstance(reason, str) else None,
                    amount=delta,
                )
            )
    try:
        check_kind = CheckKind(beat.mechanical_request) if beat.mechanical_request is not None else None
    except ValueError:
        check_kind = None
    return ActionIntent(
        id=intent_id,
        raw_input=action,
        title=beat.title,
        stakes=beat.narration,
        check_kind=check_kind,
        difficulty=beat.difficulty,
        proposed_effects=effects,
        tags=list(beat.tags),
        choices=list(beat.choices),
    )
