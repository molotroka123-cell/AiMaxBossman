"""V2.6 Phase 3 (bcc) — Task Compiler V2: план миссии компилируется через
существующий DAG-движок (bcc/v2/task_graph) до постановки задач.

Первый production-потребитель ранее UNWIRED task_graph.py: схема/циклы/битые
зависимости → 400, топологический порядок постановки; плоский план — как раньше.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from bcc.features.missions import _compile_plan


def test_flat_plan_passes_unchanged_order():
    plan = {"tasks": [{"title": f"t{i}", "prompt": "p", "kind": "research"}
                      for i in range(3)]}
    out = _compile_plan(plan)
    assert [t["title"] for t in out] == ["t0", "t1", "t2"]


def test_dependencies_reorder_topologically():
    plan = {"tasks": [
        {"node_id": "send", "title": "отправить", "prompt": "p", "kind": "generic",
         "depends_on": ["report"]},
        {"node_id": "research", "title": "исследовать", "prompt": "p", "kind": "research"},
        {"node_id": "report", "title": "отчёт", "prompt": "p", "kind": "generic",
         "depends_on": ["research"]},
    ]}
    out = _compile_plan(plan)
    assert [t["node_id"] for t in out] == ["research", "report", "send"]


def test_cycle_rejected_with_400():
    plan = {"tasks": [
        {"node_id": "a", "prompt": "p", "depends_on": ["b"]},
        {"node_id": "b", "prompt": "p", "depends_on": ["a"]},
    ]}
    with pytest.raises(HTTPException) as e:
        _compile_plan(plan)
    assert e.value.status_code == 400


def test_unknown_dependency_rejected():
    plan = {"tasks": [{"node_id": "a", "prompt": "p", "depends_on": ["ghost"]}]}
    with pytest.raises(HTTPException):
        _compile_plan(plan)


def test_duplicate_node_ids_rejected():
    plan = {"tasks": [{"node_id": "a", "prompt": "p"},
                      {"node_id": "a", "prompt": "p"}]}
    with pytest.raises(HTTPException):
        _compile_plan(plan)
