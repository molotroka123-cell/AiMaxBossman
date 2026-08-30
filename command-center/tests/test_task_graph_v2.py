"""Tests: executable task graph (spec Part F, §51)."""
from __future__ import annotations

import time

import pytest

from bcc.v2.task_graph import (
    GraphValidationError,
    NODE_STATUSES,
    TaskGraph,
    TaskNode,
    graph_context_view,
    mark_failed,
    mark_running,
    mark_succeeded,
    ready_nodes,
    skip_with_dependents,
    validate_graph,
)

ALLOWED = {"shell", "edit", "test", "git", "llm"}


def _mk(nodes: list[dict], **kw) -> TaskGraph:
    return TaskGraph.from_list(nodes, **kw)


def _chain(spec: dict[str, list[str]]) -> list[dict]:
    return [{"node_id": nid, "action_type": "shell", "depends_on": deps}
            for nid, deps in spec.items()]


def test_valid_dag_from_list():
    g = _mk(_chain({"a": [], "b": ["a"], "c": ["b"]}))
    assert validate_graph(g) == []
    assert set(g.nodes) == {"a", "b", "c"}
    assert g.nodes["b"].depends_on == ["a"]
    assert g.nodes["a"].status == "PENDING"
    # round-trip
    again = TaskGraph.from_list(g.to_list())
    assert again.to_dict() == g.to_dict()


def test_cycle_rejected():
    with pytest.raises(GraphValidationError) as ei:
        _mk(_chain({"a": ["c"], "b": ["a"], "c": ["b"]}))
    assert "INVALID_GRAPH" in str(ei.value)
    assert "cycle" in str(ei.value)


def test_missing_dependency_rejected():
    with pytest.raises(GraphValidationError) as ei:
        _mk(_chain({"a": ["ghost"]}))
    assert "unknown node" in str(ei.value)
    # и validate_graph без from_list тоже сообщает
    g = TaskGraph({"a": TaskNode("a", "shell", depends_on=["nope"])})
    errs = validate_graph(g)
    assert any("unknown node" in e for e in errs)


def test_ready_nodes_independent_both_ready():
    g = _mk(_chain({"a": [], "b": [], "c": ["a"]}))
    ready = ready_nodes(g)
    assert [n.node_id for n in ready] == ["a", "b"]  # обе независимые, входной порядок
    mark_succeeded(g, "a")
    assert [n.node_id for n in ready_nodes(g)] == ["b", "c"]


def test_dependency_failure_blocks_dependents():
    g = _mk(_chain({"a": [], "b": ["a"], "c": ["b"]}))
    mark_succeeded(g, "a")
    mark_running(g, "b")
    mark_failed(g, "b", error="boom")  # retry_limit=2 → ещё PENDING
    assert g.nodes["b"].status == "PENDING"
    assert g.nodes["b"].attempts == 1
    assert g.nodes["b"].error == "boom"
    mark_failed(g, "b")
    mark_failed(g, "b")  # attempts=3 > 2 → FAILED, c → BLOCKED
    assert g.nodes["b"].status == "FAILED"
    assert g.nodes["c"].status == "BLOCKED"
    assert ready_nodes(g) == []


def test_bounded_retry_attempts_increment_then_failed():
    g = _mk([{"node_id": "a", "action_type": "shell", "retry_limit": 1}])
    mark_failed(g, "a")
    assert g.nodes["a"].status == "PENDING" and g.nodes["a"].attempts == 1
    mark_failed(g, "a")
    assert g.nodes["a"].status == "FAILED" and g.nodes["a"].attempts == 2


def test_skip_cascades_to_dependents():
    g = _mk(_chain({"a": [], "b": ["a"], "c": ["b"], "d": []}))
    mark_succeeded(g, "d")
    skip_with_dependents(g, "a")
    assert g.nodes["a"].status == "SKIPPED"
    assert g.nodes["b"].status == "SKIPPED"
    assert g.nodes["c"].status == "SKIPPED"
    assert g.nodes["d"].status == "SUCCEEDED"  # завершённый не трогаем


def test_graph_context_view_fields():
    g = _mk([{"node_id": "a", "action_type": "shell"},
             {"node_id": "b", "action_type": "shell", "depends_on": ["a"],
              "retry_limit": 0},
             {"node_id": "c", "action_type": "shell", "depends_on": ["b"]}])
    mark_succeeded(g, "a")
    mark_failed(g, "b", error="x")
    view = graph_context_view(g, "c")
    assert view["current_node"] == "c"
    assert view["dependencies"] == {"b": "FAILED"}
    assert view["completed_nodes"] == ["a"]
    assert view["failed_nodes"] == ["b"]
    assert view["next_ready_nodes"] == []  # c заблокирован упавшим b, a уже готов
    assert set(view) == {"current_node", "dependencies", "completed_nodes",
                         "failed_nodes", "next_ready_nodes"}
    assert graph_context_view(g)["current_node"] is None


def test_unknown_action_type_with_allowlist():
    g = TaskGraph.from_list(_chain({"a": [], "b": ["a"]}),
                            known_action_types={"shell"})
    assert validate_graph(g, known_action_types={"shell"}) == []
    errs = validate_graph(g, known_action_types={"edit"})
    assert any("unknown action type" in e for e in errs)
    # None = проверка выключена
    g2 = TaskGraph.from_list([{"node_id": "a", "action_type": "deploy"}])
    assert validate_graph(g2, known_action_types=None) == []


def test_retry_limit_out_of_bounds_invalid():
    for bad in (11, -1):
        with pytest.raises(GraphValidationError) as ei:
            TaskGraph.from_list([{"node_id": "a", "action_type": "shell",
                                  "retry_limit": bad}])
        assert "retry_limit" in str(ei.value)
    g = TaskGraph({"a": TaskNode("a", "shell", retry_limit=11)})
    assert validate_graph(g)  # validate_graph тоже ловит


def test_statuses_and_mark_running_guard():
    assert NODE_STATUSES == ("PENDING", "READY", "RUNNING", "SUCCEEDED",
                             "FAILED", "BLOCKED", "SKIPPED")
    g = _mk(_chain({"a": []}))
    mark_running(g, "a")
    assert g.nodes["a"].status == "RUNNING"
    with pytest.raises(GraphValidationError):
        mark_running(g, "a")  # из RUNNING нельзя стартовать снова
    with pytest.raises(GraphValidationError):
        mark_succeeded(g, "ghost")


def test_perf_200_node_chain_validate_and_ready_under_1s():
    spec = {str(i): ([str(i - 1)] if i else []) for i in range(200)}
    t0 = time.perf_counter()
    g = _mk(_chain(spec))
    errs = validate_graph(g)
    ready = ready_nodes(g)
    elapsed = time.perf_counter() - t0
    assert errs == []
    assert [n.node_id for n in ready] == ["0"]
    assert elapsed < 1.0
