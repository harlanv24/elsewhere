"""Bounded, persisted situation graphs. AI proposes; engine capabilities execute.

Jev can supply Director.choose_graph_action with a typed selection. Graph
planning/expansion remains generative; neither
provider owns checks, effects, or graph commits.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from worldsim.models import (
    ActionIntent, CheckKind, CommandResult, ConditionKind, EffectKind,
    RejectedEffect, StateEffect, TurnOutcome, TurnRecord,
)

if TYPE_CHECKING:
    from worldsim.engine import WorldEngine
    from worldsim.models import World, Player
    from worldsim.director import Director
    from worldsim.memory import CampaignMemory


def obj(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


SHORT = {"type": "string", "minLength": 1, "maxLength": 120}
PROSE = {"type": "string", "minLength": 1, "maxLength": 400}
NODE_SCHEMA = obj({"id": SHORT, "description": PROSE, "terminal": {"type": "boolean"}})
EDGE_SCHEMA = obj({"id": SHORT, "source": SHORT, "label": SHORT,
                   "operation_id": SHORT, "success": SHORT, "failure": SHORT})
PLAN_SCHEMA = obj({
    "title": SHORT, "start": SHORT,
    "nodes": {"type": "array", "minItems": 2, "maxItems": 12, "items": NODE_SCHEMA},
    "edges": {"type": "array", "minItems": 1, "maxItems": 20, "items": EDGE_SCHEMA},
})
DECISION_SCHEMA = obj({
    "edge_id": {"type": "string", "maxLength": 120},
    "expand": {"type": "boolean"},
})
EXPANSION_SCHEMA = obj({
    "label": SHORT, "operation_id": SHORT,
    "success_description": PROSE, "failure_description": PROSE,
})


@dataclass(frozen=True)
class Operation:
    id: str
    label: str
    action: str
    check_kind: CheckKind | None = None
    difficulty: int = 10
    effect: StateEffect | None = None

    def context(self) -> dict:
        return {"id": self.id, "label": self.label, "action": self.action,
                "check_kind": self.check_kind.value if self.check_kind else None,
                "difficulty": self.difficulty,
                "effect": asdict(self.effect) if self.effect else None}


def validate_graph(graph: dict) -> None:
    """Reject dead ends, cycles, dangling edges, and unbounded provider output."""
    from worldsim.contracts import validate_payload

    # Runtime graphs can grow beyond the initial plan, within a fixed budget.
    schema = deepcopy(PLAN_SCHEMA)
    schema["properties"]["nodes"]["maxItems"] = 24
    schema["properties"]["edges"]["maxItems"] = 48
    validate_payload({key: graph[key] for key in PLAN_SCHEMA["properties"]}, schema)
    nodes = {node["id"]: node for node in graph["nodes"]}
    if len(nodes) != len(graph["nodes"]) or graph["start"] not in nodes:
        raise ValueError("Graph requires unique nodes and a valid start.")
    if nodes[graph["start"]]["terminal"]:
        raise ValueError("A situation must start with an actionable state.")
    if graph.get("current", graph["start"]) not in nodes:
        raise ValueError("Unknown current graph state.")
    edges = graph["edges"]
    if len({edge["id"] for edge in edges}) != len(edges):
        raise ValueError("Graph edge IDs must be unique.")
    outgoing = {key: [] for key in nodes}
    for edge in edges:
        if any(edge[key] not in nodes for key in ("source", "success", "failure")):
            raise ValueError("Graph edge references an unknown state.")
        if nodes[edge["source"]]["terminal"]:
            raise ValueError("Terminal states cannot have outgoing actions.")
        outgoing[edge["source"]].append(edge)
    visiting, visited = set(), set()

    def walk(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("Situation cycles require explicit policy; unsupported in this version.")
        if node_id in visited:
            return
        visiting.add(node_id)
        if not nodes[node_id]["terminal"] and not outgoing[node_id]:
            raise ValueError("Nonterminal state has no continuation.")
        labels = [edge["label"].casefold() for edge in outgoing[node_id]]
        if len(set(labels)) != len(labels):
            raise ValueError("Action labels must be distinct at each state.")
        for edge in outgoing[node_id]:
            walk(edge["success"])
            walk(edge["failure"])
        visiting.remove(node_id)
        visited.add(node_id)

    walk(graph["start"])
    if visited != set(nodes):
        raise ValueError("Graph contains unreachable states.")


def validated_snapshot(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Action graph snapshot must be an object.")
    graph = deepcopy(payload)
    for key in ("id", "scene_id", "current"):
        if not isinstance(graph.get(key), str) or not graph[key]:
            raise ValueError(f"Action graph requires {key}.")
    if type(graph.get("expansions", 0)) is not int or not 0 <= graph.get("expansions", 0) <= 4:
        raise ValueError("Invalid graph expansion count.")
    validate_graph(graph)
    return graph


class ActionGraphService:
    def __init__(self, engine: WorldEngine) -> None:
        self.engine = engine

    def current(self, world: World) -> dict | None:
        scene = world.active_scene
        return world.action_graphs.get(scene.id) if scene else None

    def terminal(self, graph: dict) -> bool:
        return next(node["terminal"] for node in graph["nodes"] if node["id"] == graph["current"])

    def operations(self, world: World, player: Player) -> dict[str, Operation]:
        """Build capabilities from current authoritative state, never model text."""
        operations = {
            "observe": Operation("observe", "Study the situation", "study the situation", CheckKind.EXPLORATION, 11),
            "reassess": Operation("reassess", "Take time to reassess", "take time to reassess"),
            "withdraw": Operation("withdraw", "Set this situation aside", "set this situation aside"),
        }
        # Situation withdrawal never unlocks an encounter or moves the player.
        if self.engine.movement_lock_reason(world):
            return {key: operations[key] for key in ("reassess", "withdraw")}
        location = self.engine.location_at(world, player.position)
        quest = self.engine.progression.active_quest(world)
        if quest and quest.current_stage < len(quest.stages):
            stage = quest.stages[quest.current_stage]
            local = not quest.related_locations or (location and location.id in quest.related_locations)
            for index, condition in enumerate(stage.conditions):
                if not local or self.engine.progression.condition_satisfied(condition, world, player, location):
                    continue
                effect, check, label = None, CheckKind.EXPLORATION, stage.description
                if condition.kind == ConditionKind.FACT_DISCOVERED:
                    effect = StateEffect(EffectKind.FACT_DISCOVERED, target_id=condition.target_id)
                elif condition.kind == ConditionKind.NPC_RECRUITED:
                    npc = self.engine.active_npc(world, player)
                    if npc and npc.id == condition.target_id:
                        effect = StateEffect(EffectKind.NPC_DISPOSITION, target_id=npc.id, value=condition.expected or "allied")
                        check = CheckKind.SOCIAL
                elif condition.kind == ConditionKind.CHOICE_COMMITTED:
                    effect = StateEffect(EffectKind.CHOICE_COMMIT, target_id=condition.target_id)
                    check = None
                if effect:
                    key = f"quest:{stage.id}:{index}"
                    operations[key] = Operation(key, label[:120], label, check, 11, effect)
        for item in self.engine.scene_objects_at(world, player.position)[:4]:
            # Full names are retained; no unstable positional IDs or name truncation.
            for verb, kind, value in (("take", EffectKind.INVENTORY_ADD, None),
                                      ("open", EffectKind.OBJECT_STATUS, "open")):
                state = self.engine._object_state_for_target(world, player.position, item)
                if verb == "open" and state and state.get("status") == "open":
                    continue
                key = f"{verb}:{item}"
                operations[key] = Operation(key, f"{verb} {item}", f"{verb} {item}",
                                            CheckKind.EXPLORATION if verb == "open" else None, 11,
                                            StateEffect(kind, target_id=item, value=value))
        return operations

    def _context(self, world: World, player: Player, operations: dict[str, Operation]) -> dict:
        scene = world.active_scene
        location = self.engine.location_at(world, player.position)
        npc = self.engine.active_npc(world, player)
        return {"scene": {"id": scene.id, "area": scene.area_name, "hazard": scene.hazard},
                "location": {"id": location.id, "name": location.name, "summary": location.summary} if location else None,
                "npc": {"id": npc.id, "name": npc.name, "disposition": npc.disposition} if npc else None,
                "operations": [operation.context() for operation in operations.values()],
                "inventory": list(player.inventory[:12]),
                "visible_objects": self.engine.scene_objects_at(world, player.position)[:8]}

    def ensure(self, world: World, player: Player, director: Director, *, replace: bool = False) -> dict:
        existing = self.current(world)
        if existing is not None and not replace:
            return existing
        operations = self.operations(world, player)
        context = self._context(world, player, operations)
        plan = director.plan_action_graph(context)
        try:
            graph = self._install_plan(plan, world, operations)
        except (ValueError, KeyError, TypeError):
            graph = self._install_plan(self._fallback_plan(operations), world, operations)
        world.action_graphs[graph["scene_id"]] = graph
        world.current_choices = self.choices(world, player)
        return graph

    def _fallback_plan(self, operations: dict[str, Operation]) -> dict:
        candidates = [op for op in operations.values() if op.id not in {"withdraw", "reassess", "observe"}][:3]
        if not candidates:
            candidates = [operations.get("observe", operations["reassess"])]
        nodes = [{"id": "opening", "description": "Choose how to approach the immediate situation.", "terminal": False},
                 {"id": "setback", "description": "That approach failed. Reassess what you know or set this situation aside.", "terminal": False},
                 {"id": "assessed", "description": "You have reassessed this approach. Other scene actions remain available; unresolved objectives remain unresolved.", "terminal": True}]
        edges = [{"id": "reassess", "source": "setback", "label": "Take time to reassess",
                  "operation_id": "reassess", "success": "assessed", "failure": "assessed"}]
        for index, operation in enumerate(candidates):
            destination = f"result-{index}"
            nodes.append({"id": destination, "description": f"Your attempt to {operation.action} is resolved.", "terminal": True})
            edges.append({"id": f"attempt-{index}", "source": "opening", "label": operation.label,
                          "operation_id": operation.id, "success": destination,
                          "failure": "setback" if operation.check_kind else destination})
        # A no-check-only plan otherwise leaves the shared setback unreachable.
        if not any(edge["failure"] == "setback" for edge in edges):
            nodes = [node for node in nodes if node["id"] not in {"setback", "assessed"}]
            edges = [edge for edge in edges if edge["source"] != "setback"]
        return {"title": "A local opportunity", "start": "opening", "nodes": nodes, "edges": edges}

    def _install_plan(self, plan: dict | None, world: World, operations: dict[str, Operation]) -> dict:
        from worldsim.contracts import validate_payload
        validate_payload(plan, PLAN_SCHEMA)
        graph = deepcopy(plan)
        for edge in graph["edges"]:
            operation = operations.get(edge["operation_id"])
            if operation is None or operation.id == "withdraw":
                raise ValueError("Unknown or reserved graph operation.")
            if operation.check_kind is None and edge["failure"] != edge["success"]:
                raise ValueError("An action without a check has only one outcome.")
            if operation.check_kind and edge["failure"] == edge["success"]:
                raise ValueError("A check needs distinct success and failure states.")
        validate_graph(graph)
        if any(node["id"].startswith("_engine") for node in graph["nodes"]):
            raise ValueError("Reserved state ID.")
        if any(edge["id"].startswith("_engine") for edge in graph["edges"]):
            raise ValueError("Reserved edge ID.")
        graph.update(id=f"situation:{world.active_scene.id}:{world.tick}", scene_id=world.active_scene.id,
                     current=graph["start"], expansions=0, offered_operations=sorted(operations))
        graph["nodes"].append({"id": "_engine_exit", "description": "You set this situation aside. Existing dangers and objectives remain.", "terminal": True})
        self._add_exits(graph)
        validate_graph(graph)
        return graph

    def _add_exits(self, graph: dict) -> None:
        for node in graph["nodes"]:
            edge_id = f"_engine_exit:{node['id']}"
            if not node["terminal"] and not any(edge["id"] == edge_id for edge in graph["edges"]):
                graph["edges"].append({"id": edge_id, "source": node["id"],
                    "label": "Set this situation aside", "operation_id": "withdraw",
                    "success": "_engine_exit", "failure": "_engine_exit"})

    def available(self, world: World, player: Player, graph: dict | None = None) -> list[dict]:
        graph = graph or self.current(world)
        if graph is None:
            return []
        operations = self.operations(world, player)
        return [edge for edge in graph["edges"] if edge["source"] == graph["current"]
                and edge["operation_id"] in operations]

    def choices(self, world: World, player: Player) -> list[str]:
        return [f"choose {edge['label']}" for edge in self.available(world, player)]

    def describe(self, world: World, player: Player) -> str:
        graph = self.current(world)
        if graph is None:
            return "No situation is active. Type `situation` to prepare one."
        node = next(node for node in graph["nodes"] if node["id"] == graph["current"])
        choices = self.choices(world, player)
        suffix = "\n" + "\n".join(f"- {choice}" for choice in choices) if choices else "\nSituation resolved. Continue exploring or leave; use `next situation` when new opportunities become available."
        return f"{graph['title']}: {node['description']}{suffix}"

    def command(self, command: str, world: World, player: Player, director: Director,
                memory: CampaignMemory) -> CommandResult | None:
        text = command.strip().casefold()
        if text == "next situation":
            graph = self.current(world)
            if graph and not self.terminal(graph):
                return CommandResult("Finish or set aside the current situation first.\n" + self.describe(world, player))
            if graph and graph.get("offered_operations") == sorted(self.operations(world, player)):
                return CommandResult("No new opportunities are available here yet. Explore, speak with someone, or travel to change the situation.")
            self.ensure(world, player, director, replace=True)
            return CommandResult(self.describe(world, player))
        if text == "situation":
            self.ensure(world, player, director)
            world.current_choices = self.choices(world, player)
            return CommandResult(self.describe(world, player))
        if text.startswith("choose ") or text.startswith("try "):
            if self.current(world) is None:
                return None  # Preserve ordinary freeform commands outside situations.
            return self.act(command, world, player, director, memory)
        return None

    def act(self, command: str, world: World, player: Player, director: Director,
            memory: CampaignMemory) -> CommandResult:
        graph = self.current(world)
        if self.terminal(graph):
            return CommandResult(self.describe(world, player))
        edges = self.available(world, player)
        text = command.strip()
        if text.casefold().startswith(("choose ", "try ")):
            text = text.split(" ", 1)[1].strip()
        edge = next((edge for edge in edges if text.casefold() in {edge["id"].casefold(), edge["label"].casefold()}), None)
        candidate = deepcopy(graph)
        if edge is None:
            operations = self.operations(world, player)
            context = self._context(world, player, operations)
            context.update(action=text, current_state=next(node for node in graph["nodes"] if node["id"] == graph["current"]), available_actions=edges)
            decision = director.choose_graph_action(context)
            if decision and not decision.get("expand"):
                edge = next((edge for edge in edges if edge["id"] == decision.get("edge_id")), None)
            elif decision and decision.get("expand"):
                if decision.get("operation_id"):
                    # Jev selected a specific capability. The prose planner may
                    # describe that operation but cannot substitute another one.
                    selected_id = decision["operation_id"]
                    operations = {key: op for key, op in operations.items() if key == selected_id}
                    context["operations"] = [op.context() for op in operations.values()]
                proposal = director.expand_action_graph(context)
                try:
                    candidate, edge = self._expand(graph, proposal, operations)
                except (ValueError, KeyError, TypeError):
                    edge = None
        if edge is None:
            return CommandResult("That approach has no supported transition yet. Choose an available action or describe another approach.\n\n" + self.describe(world, player))
        return self._resolve(command, candidate, edge, world, player, director, memory)

    def _expand(self, graph: dict, proposal: dict | None, operations: dict[str, Operation]) -> tuple[dict, dict]:
        from worldsim.contracts import validate_payload
        validate_payload(proposal, EXPANSION_SCHEMA)
        if graph["expansions"] >= 4:
            raise ValueError("Situation expansion budget exhausted.")
        operation = operations.get(proposal["operation_id"])
        if operation is None or operation.id == "withdraw":
            raise ValueError("New branches must use a supported operation.")
        candidate = deepcopy(graph)
        index = candidate["expansions"] + 1
        prefix = f"_branch{index}"
        if any(node["id"].startswith(prefix) for node in candidate["nodes"]):
            raise ValueError("Branch ID collision.")
        success, failure = f"{prefix}:success", f"{prefix}:failure"
        candidate["nodes"].append({"id": success, "description": proposal["success_description"], "terminal": True})
        if operation.check_kind:
            candidate["nodes"].append({"id": failure, "description": proposal["failure_description"], "terminal": False})
            candidate["edges"].append({"id": f"{prefix}:recover", "source": failure,
                "label": "Take time to reassess", "operation_id": "reassess",
                "success": "_engine_exit", "failure": "_engine_exit"})
        else:
            failure = success
        edge = {"id": prefix, "source": graph["current"], "label": proposal["label"],
                "operation_id": operation.id, "success": success, "failure": failure}
        candidate["edges"].append(edge)
        candidate["expansions"] = index
        self._add_exits(candidate)
        validate_graph(candidate)
        return candidate, edge

    def _resolve(self, command: str, graph: dict, edge: dict, world: World, player: Player,
                 director: Director, memory: CampaignMemory) -> CommandResult:
        operation = self.operations(world, player).get(edge["operation_id"])
        if operation is None:
            return CommandResult("That action is no longer available.\n" + self.describe(world, player))
        turn_id = f"turn-{world.tick:06d}-{len(world.turn_records) + 1:04d}"
        intent = ActionIntent(id=turn_id, raw_input=command, title=edge["label"],
                              stakes=f"Success: {edge['success']}; failure: {edge['failure']}",
                              check_kind=operation.check_kind, difficulty=operation.difficulty,
                              proposed_effects=[operation.effect] if operation.effect else [], tags=["action_graph"])
        # Validate the engine-owned normalized action, preserving raw input in the record.
        validation_intent = deepcopy(intent)
        validation_intent.raw_input = operation.action
        for effect in intent.proposed_effects:
            error = self.engine.turn_effects.validate(effect, validation_intent, world, player)
            if error:
                return CommandResult(f"That action cannot proceed: {error}.\n" + self.describe(world, player))
        world_before, player_before = deepcopy(world), deepcopy(player)
        random_before = self.engine.random.getstate()
        check = self.engine.resolve_typed_check(world, player, operation.difficulty, operation.check_kind) if operation.check_kind else None
        if check is None:
            world.last_roll = None
        accepted = list(intent.proposed_effects) if check is None or check.success else []
        rejected = [RejectedEffect(effect, "requires a successful check") for effect in intent.proposed_effects] if check and not check.success else []
        destination = edge["success"] if check is None or check.success else edge["failure"]
        graph["current"] = destination
        graph = validated_snapshot(graph)
        staged_memory = deepcopy(memory)
        location = self.engine.location_at(world, player.position)
        # Effects and graph advancement share rollback; generated branches are never
        # installed until the entire transition has committed successfully.
        try:
            self.engine.state_reducer.apply_accepted(world, player, accepted,
                lambda effect: self.engine.turn_effects.commit(effect, validation_intent, world, player, staged_memory, location))
            world.action_graphs[graph["scene_id"]] = graph
            self.engine.progression.evaluate(world, player, location, staged_memory)
        except Exception:
            self.engine.state_reducer._restore(world, world_before)
            self.engine.state_reducer._restore(player, player_before)
            self.engine.random.setstate(random_before)
            raise
        memory.entries = staged_memory.entries
        node = next(node for node in graph["nodes"] if node["id"] == destination)
        summary = self.engine.turn_effects.summarize(check, accepted, rejected)
        summary = f"{summary} Situation state: {node['description']}"
        choices = self.choices(world, player)
        world.current_choices = choices
        record = TurnRecord(turn_id, world.tick, command, intent, check,
                            TurnOutcome(check.success if check else None, accepted, rejected, summary),
                            "", choices, deepcopy(graph))
        # Persist before narration so a provider failure cannot lose or repeat effects.
        world.turn_records.append(record)
        del world.turn_records[:-100]
        try:
            record.narration = director.narrate_turn_outcome(world, player, location,
                self.engine.active_npc(world, player), record,
                memory.relevant_context(world, player, location.name if location else None))
        except Exception:
            record.narration = summary
        self.engine.remember_state_fact(world, summary, world.tick)
        memory.remember("situation", turn_id, summary, world.tick, importance=6, tags=["action_graph"])
        self.engine.advance_world(world, player, director, memory, "situation")
        memory.remember_world_state(world, player)
        suffix = f"\n\n{check.summary}" if check else ""
        return CommandResult(f"{record.narration}{suffix}\n\n{self.describe(world, player)}", advance_time=True)
