"""Continuity adapters over the existing mission/task tables and TaskGraph.

No executor, policy engine, evidence store or completion writer lives here.
Canonical finalization is the authority for a child's `completed` status.
The digest detects drift in trusted persistence; it is NOT an authorization.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .v2.task_graph import GraphValidationError, TaskGraph, mark_running, mark_succeeded, ready_nodes

BINDING = "continuity"
TERMINAL = frozenset({"completed", "failed", "cancelled", "stopped"})
ACTIVE = frozenset({"queued", "running", "waiting_approval", "paused"})
MAX_TASKS = 128


def _json(value) -> str:
    # Reuse the same finite/exact JSON boundary as Mission IR, no repr coercion.
    from bossman_shared.mission_ir import _canonical, _json_types
    _json_types(value)
    return _canonical(value)


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(_json(plan).encode("utf-8")).hexdigest()


def compile_plan(plan: dict) -> list[dict]:
    """Normalize stable node IDs, then validate/order with the canonical DAG."""
    try:
        plan = json.loads(_json(plan))
    except (ValueError, TypeError, RecursionError) as exc:
        raise GraphValidationError("plan must be bounded finite JSON") from exc
    if type(plan) is not dict or type(plan.get("tasks")) is not list:
        raise GraphValidationError("plan.tasks must be an array")
    if not 1 <= len(plan["tasks"]) <= MAX_TASKS:
        raise GraphValidationError(f"plan requires 1..{MAX_TASKS} tasks")
    tasks, nodes = [], []
    for i, raw in enumerate(plan["tasks"]):
        if type(raw) is not dict or type(raw.get("prompt")) is not str or not raw["prompt"].strip():
            raise GraphValidationError("each task needs a nonempty prompt")
        t = dict(raw)
        t.setdefault("node_id", f"t{i+1}")
        t.setdefault("kind", "generic")
        t.setdefault("depends_on", [])
        if (type(t["node_id"]) is not str or not t["node_id"].strip()
                or type(t["kind"]) is not str or not t["kind"].strip()):
            raise GraphValidationError("canonical node_id and kind required")
        if (type(t["depends_on"]) is not list
                or any(type(d) is not str for d in t["depends_on"])
                or len(t["depends_on"]) != len(set(t["depends_on"]))):
            raise GraphValidationError("depends_on must contain unique node IDs")
        meta = t.get("meta", {})
        if type(meta) is not dict or not set(meta) <= {"required_effects", "allowed_tools"}:
            raise GraphValidationError("plan metadata may declare only effects and selected tools")
        if "allowed_tools" in meta and (type(meta["allowed_tools"]) is not list
                or any(type(n) is not str or not n for n in meta["allowed_tools"])):
            raise GraphValidationError("allowed_tools must be an explicit name list")
        if "required_effects" in meta:
            from .v2.verification import parse_expected
            required = meta["required_effects"]
            if (type(required) is not list or not required or len(required) > 32
                    or any(type(e) is not dict or set(e) != {"kind", "target", "expect"}
                           or type(e.get("expect")) is not dict or not e["expect"] for e in required)
                    or len(parse_expected(required)) != len(required)):
                raise GraphValidationError("each required effect needs a typed post-state expectation")
        if "agent_id" in t and t["agent_id"] is not None and (
                type(t["agent_id"]) is not int or t["agent_id"] <= 0):
            raise GraphValidationError("agent_id must be a positive integer")
        nodes.append({"node_id": t["node_id"], "action_type": t["kind"],
                      "depends_on": t["depends_on"], "input": {"index": i}})
        tasks.append(t)
    graph = TaskGraph.from_list(nodes)
    ordered = []
    while ready := ready_nodes(graph):
        for node in ready:
            mark_running(graph, node.node_id)
            mark_succeeded(graph, node.node_id)
            ordered.append(tasks[node.input["index"]])
    return ordered


@dataclass(frozen=True)
class DispatchState:
    ready: bool
    reason: str
    waiting_for: tuple[int, ...] = ()


def bindings(mission: dict, tasks: list[dict]) -> tuple[dict[int, dict], str]:
    """Resolve authoritative task IDs from the parent's persisted binding.

    Old flat missions remain compatible. Old unbound DAGs must be explicitly
    migrated/replanned, never guessed from row insertion order.
    """
    plan = mission.get("plan") or {}
    if not isinstance(plan, dict):
        return {}, "PLAN_BINDING_INVALID"
    meta = mission.get("meta") or {}
    binding = meta.get(BINDING) if isinstance(meta, dict) else None
    if binding is None:
        if (any(isinstance(t.get("meta"), dict) and t["meta"].get(BINDING) for t in tasks)
                or any(isinstance(t, dict) and t.get("depends_on") for t in plan.get("tasks", []))):
            return {}, "LEGACY_DAG_UNBOUND"
        return {}, "LEGACY_FLAT"
    try:
        ordered = compile_plan(plan)
        digest = plan_digest(plan)
        if (type(binding) is not dict or set(binding) != {"version", "plan_digest", "nodes"}
                or type(binding["version"]) is not int or binding["version"] != 1
                or binding["plan_digest"] != digest or type(binding["nodes"]) is not dict):
            return {}, "PLAN_BINDING_INVALID"
        nodes = binding["nodes"]
        if set(nodes) != {t["node_id"] for t in ordered}:
            return {}, "PLAN_NODES_CHANGED"
        ids = list(nodes.values())
        if (any(type(i) is not int or i <= 0 for i in ids)
                or len(set(ids)) != len(ids) or set(ids) != {t["id"] for t in tasks}):
            return {}, "PLAN_CHILDREN_CHANGED"
        rows = {t["id"]: t for t in tasks}
        result = {}
        for node in ordered:
            tid = nodes[node["node_id"]]
            row = rows[tid]
            task_meta = row.get("meta") or {}
            expected = {"mission_id": mission["id"], "node_id": node["node_id"], "plan_digest": digest}
            if (row.get("mission_id") != mission["id"] or task_meta.get(BINDING) != expected
                    or row.get("prompt") != node["prompt"] or row.get("kind") != node["kind"]
                    or any(task_meta.get(k) != v for k, v in node.get("meta", {}).items())):
                return {}, "CHILD_CONTRACT_CHANGED"
            result[tid] = {"node_id": node["node_id"],
                           "dependencies": tuple(nodes[d] for d in node["depends_on"])}
        return result, "BOUND"
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return {}, "PLAN_BINDING_INVALID"


def dispatch_state(mission: dict, tasks: list[dict], task_id: int) -> DispatchState:
    rows = {t["id"]: t for t in tasks}
    if task_id not in rows:
        return DispatchState(False, "CHILD_MISSING")
    bound, state = bindings(mission, tasks)
    if state not in {"BOUND", "LEGACY_FLAT"}:
        return DispatchState(False, state)
    if mission["status"] != "running":
        return DispatchState(False, "MISSION_" + str(mission["status"]).upper())
    if rows[task_id]["status"] in TERMINAL:
        return DispatchState(False, "CHILD_TERMINAL")
    deps = bound.get(task_id, {}).get("dependencies", ())
    if any(rows[d]["status"] in TERMINAL - {"completed"} for d in deps):
        return DispatchState(False, "DEPENDENCY_FAILED")
    waiting = tuple(d for d in deps if rows[d]["status"] != "completed")
    return DispatchState(not waiting, "DEPENDENCIES_PENDING" if waiting else "READY", waiting)


def projection(mission: dict, tasks: list[dict]) -> dict:
    """Owner-visible scheduling facts only, never a new proof or a green score."""
    bound, state = bindings(mission, tasks)
    children = []
    for t in tasks:
        d = dispatch_state(mission, tasks, t["id"])
        children.append({"task_id": t["id"], "node_id": bound.get(t["id"], {}).get("node_id"),
                         "status": t["status"], "dispatch_ready": d.ready,
                         "reason_code": d.reason, "waiting_for": list(d.waiting_for)})
    return {"binding_state": state, "status": mission["status"], "children": children,
            "completion_authority": "bcc.finalize", "is_effect_evidence": False}


async def tool_block_reason(db, task: dict) -> str | None:
    """Re-read parent at the canonical tool boundary, including V3 adapters.

    Pause stops NEW children, not an already running child's operations. Stop
    is terminal and denies its next tool dispatch. Effects already sent to an
    external system still require the existing recovery/reconciliation path.
    """
    if not task.get("mission_id"):
        return None
    import sqlalchemy as sa
    from .db import missions as parents, tasks as children, utcnow
    async with db.session() as session:
        current = (await session.execute(sa.select(children).where(children.c.id == task["id"]))).first()
        row = (await session.execute(sa.select(parents).where(parents.c.id == task["mission_id"]))).first()
        rows = (await session.execute(sa.select(children).where(children.c.mission_id == task["mission_id"]))).fetchall()
    if current is None or row is None:
        return "MISSION_OR_CHILD_MISSING"
    current, mission = dict(current._mapping), dict(row._mapping)
    if current.get("mission_id") != task["mission_id"]:
        return "CHILD_MISSION_CHANGED"
    if current["status"] != "running":
        return "CHILD_NOT_RUNNING"
    if mission["status"] == "paused":
        mission = {**mission, "status": "running"}  # scheduling pause only
    state = dispatch_state(mission, [dict(r._mapping) for r in rows], current["id"])
    if not state.ready:
        return state.reason
    if (mission.get("duration_minutes") and mission.get("started_at")
            and (utcnow() - mission["started_at"]).total_seconds() >= mission["duration_minutes"] * 60):
        return "MISSION_DEADLINE_EXPIRED"
    return None
