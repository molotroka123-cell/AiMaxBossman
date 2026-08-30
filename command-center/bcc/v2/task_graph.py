"""Executable task graph (spec Part F) — узлы, зависимости, статусы.

Чистый детерминированный модуль без I/O и БД. UI-граф (agent_graph.py) и
orchestration_schema.py — визуализация/валидация конфига, а не исполняемый
DAG; исполняемый — этот модуль.

Жизненный цикл узла: PENDING → RUNNING → SUCCEEDED | (retry: PENDING) |
FAILED (attempts > retry_limit, зависимые уходят в BLOCKED) | SKIPPED.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

NODE_STATUSES = ("PENDING", "READY", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "SKIPPED")
MAX_RETRY_LIMIT = 10  # retry_limit ограничен сверху, чтобы граф не крутился вечно


class GraphValidationError(ValueError):
    """Схема или структура графа невалидны (цикл, неизвестная зависимость и т.п.)."""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


@dataclass(slots=True)
class TaskNode:
    node_id: str
    action_type: str
    status: str = "PENDING"
    depends_on: list[str] = field(default_factory=list)
    input: dict = field(default_factory=dict)
    success_condition: str = ""
    retry_limit: int = 2
    attempts: int = 0
    error: str = ""


@dataclass(slots=True)
class TaskGraph:
    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def add(self, node: TaskNode) -> None:
        self.nodes[node.node_id] = node

    @classmethod
    def from_list(cls, nodes: list[dict], *,
                  known_action_types: set[str] | None = None) -> "TaskGraph":
        """Схема-валидация каждого узла + validate_graph; при ошибках — GraphValidationError
        с префиксом INVALID_GRAPH."""
        errors: list[str] = []
        if not isinstance(nodes, list):
            raise GraphValidationError("INVALID_GRAPH: nodes must be a list")
        built: dict[str, TaskNode] = {}
        for i, raw in enumerate(nodes):
            where = f"node[{i}]"
            if not isinstance(raw, dict):
                errors.append(f"{where}: not a dict")
                continue
            node_id = raw.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                errors.append(f"{where}: node_id must be a non-empty string")
                continue
            where = f"node {node_id!r}"
            if node_id in built:
                errors.append(f"node {node_id!r}: duplicate node_id")
                continue
            if not isinstance(raw.get("action_type"), str) or not raw["action_type"]:
                errors.append(f"node {node_id!r}: action_type must be a non-empty string")
                continue
            status = raw.get("status", "PENDING")
            if status not in NODE_STATUSES:
                errors.append(f"node {node_id!r}: unknown status {status!r}")
                continue
            depends_on = raw.get("depends_on", [])
            if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
                errors.append(f"node {node_id!r}: depends_on must be a list of str")
                continue
            node_input = raw.get("input", {})
            if not isinstance(node_input, dict):
                errors.append(f"node {node_id!r}: input must be a dict")
                continue
            cond = raw.get("success_condition", "")
            if not isinstance(cond, str):
                errors.append(f"node {node_id!r}: success_condition must be a string")
                continue
            retry_limit = raw.get("retry_limit", 2)
            if not _is_int(retry_limit) or retry_limit < 0 or retry_limit > MAX_RETRY_LIMIT:
                errors.append(f"node {node_id!r}: retry_limit must be int in [0, {MAX_RETRY_LIMIT}]")
                continue
            attempts = raw.get("attempts", 0)
            if not _is_int(attempts) or attempts < 0:
                errors.append(f"node {node_id!r}: attempts must be a non-negative int")
                continue
            error = raw.get("error", "")
            if not isinstance(error, str):
                errors.append(f"node {node_id!r}: error must be a string")
                continue
            built[node_id] = TaskNode(
                node_id=node_id, action_type=raw["action_type"], status=status,
                depends_on=list(depends_on), input=dict(node_input),
                success_condition=cond, retry_limit=retry_limit,
                attempts=attempts, error=error)
        graph = cls(built)
        errors.extend(validate_graph(graph, known_action_types=known_action_types))
        if errors:
            raise GraphValidationError("INVALID_GRAPH: " + "; ".join(errors))
        return graph

    def to_list(self) -> list[dict]:
        return [asdict(n) for n in self.nodes.values()]

    def to_dict(self) -> dict:
        return {"nodes": self.to_list()}


def validate_graph(graph: TaskGraph,
                   known_action_types: set[str] | None = None) -> list[str]:
    """Ошибки графа строками; пустой список = валиден. known_action_types=None →
    проверка action_type пропускается."""
    errors: list[str] = []
    nodes = graph.nodes
    for node_id, node in nodes.items():
        if not isinstance(node, TaskNode):
            errors.append(f"node {node_id!r}: not a TaskNode")
            continue
        if not isinstance(node.node_id, str) or not node.node_id:
            errors.append(f"node {node_id!r}: bad node_id")
        if not isinstance(node.action_type, str) or not node.action_type:
            errors.append(f"node {node_id!r}: bad action_type")
        if node.status not in NODE_STATUSES:
            errors.append(f"node {node_id!r}: unknown status {node.status!r}")
        if not isinstance(node.depends_on, list) or not all(
                isinstance(d, str) for d in node.depends_on):
            errors.append(f"node {node_id!r}: depends_on must be a list of str")
        if not isinstance(node.input, dict):
            errors.append(f"node {node_id!r}: input must be a dict")
        if not isinstance(node.error, str):
            errors.append(f"node {node_id!r}: error must be a string")
        if not _is_int(node.retry_limit) or node.retry_limit < 0 or node.retry_limit > MAX_RETRY_LIMIT:
            errors.append(f"node {node_id!r}: retry_limit must be int in [0, {MAX_RETRY_LIMIT}]")
        if not _is_int(node.attempts) or node.attempts < 0:
            errors.append(f"node {node_id!r}: attempts must be a non-negative int")
        if known_action_types is not None and node.action_type not in known_action_types:
            errors.append(f"node {node_id!r}: unknown action type {node.action_type!r}")
        for dep in node.depends_on:
            if dep not in nodes:
                errors.append(f"node {node_id!r}: depends on unknown node {dep!r}")
    errors.extend(_cycle_errors(nodes))
    return errors


def _cycle_errors(nodes: dict[str, TaskNode]) -> list[str]:
    """Итеративный DFS: A→B→C→A — цикл. Без рекурсии: цепочки бывают длинными."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    errors: list[str] = []
    for start in nodes:
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack: list[tuple[str, Any]] = [(start, iter(nodes[start].depends_on))]
        while stack:
            node_id, deps_iter = stack[-1]
            advanced = False
            for dep in deps_iter:
                if dep not in nodes:
                    continue  # отсутствующая зависимость — отдельная ошибка
                if color[dep] == GRAY:
                    errors.append(f"cycle detected through node {dep!r}")
                elif color[dep] == WHITE:
                    color[dep] = GRAY
                    stack.append((dep, iter(nodes[dep].depends_on)))
                    advanced = True
                    break
            if not advanced:
                color[node_id] = BLACK
                stack.pop()
    return errors


def ready_nodes(graph: TaskGraph) -> list[TaskNode]:
    """PENDING-узлы, у которых все зависимости SUCCEEDED. Независимые узлы
    готовы одновременно; порядок стабилен (входной порядок узлов)."""
    out: list[TaskNode] = []
    for node in graph.nodes.values():
        if node.status != "PENDING":
            continue
        if all(graph.nodes[d].status == "SUCCEEDED"
               for d in node.depends_on if d in graph.nodes):
            out.append(node)
    return out


def _require_node(graph: TaskGraph, node_id: str) -> TaskNode:
    node = graph.nodes.get(node_id)
    if node is None:
        raise GraphValidationError(f"unknown node {node_id!r}")
    return node


def mark_running(graph: TaskGraph, node_id: str) -> None:
    node = _require_node(graph, node_id)
    if node.status not in ("PENDING", "READY"):
        raise GraphValidationError(
            f"node {node_id!r}: cannot start from status {node.status}")
    node.status = "RUNNING"


def mark_succeeded(graph: TaskGraph, node_id: str) -> None:
    _require_node(graph, node_id).status = "SUCCEEDED"


def mark_failed(graph: TaskGraph, node_id: str, error: str = "") -> None:
    """attempts += 1; attempts <= retry_limit → PENDING (retry разрешён),
    иначе FAILED, прямые зависимые (ещё не завершённые) → BLOCKED."""
    node = _require_node(graph, node_id)
    node.attempts += 1
    if error:
        node.error = error
    if node.attempts > node.retry_limit:
        node.status = "FAILED"
        for other in graph.nodes.values():
            if node_id in other.depends_on and other.status == "PENDING":
                other.status = "BLOCKED"
    else:
        node.status = "PENDING"


def skip_with_dependents(graph: TaskGraph, node_id: str) -> None:
    """SKIPPED каскадом по транзитивным зависимым; завершённые узлы не трогаем."""
    nodes = graph.nodes
    _require_node(graph, node_id)
    cascade = [node_id]
    seen = {node_id}
    while cascade:
        cur = cascade.pop()
        nodes[cur].status = "SKIPPED"
        for other in nodes.values():
            if (cur in other.depends_on and other.node_id not in seen
                    and other.status in ("PENDING", "READY", "BLOCKED")):
                seen.add(other.node_id)
                cascade.append(other.node_id)


def graph_context_view(graph: TaskGraph,
                       current_node_id: str | None = None) -> dict:
    """Компактный вид для LLM (spec §36): только локальный контекст шага,
    весь граф в промпт не уезжает."""
    nodes = graph.nodes
    current = nodes.get(current_node_id) if current_node_id else None
    return {
        "current_node": current_node_id,
        "dependencies": {d: nodes[d].status
                         for d in (current.depends_on if current else [])
                         if d in nodes},
        "completed_nodes": [n.node_id for n in nodes.values() if n.status == "SUCCEEDED"],
        "failed_nodes": [n.node_id for n in nodes.values() if n.status == "FAILED"],
        "next_ready_nodes": [n.node_id for n in ready_nodes(graph)],
    }
