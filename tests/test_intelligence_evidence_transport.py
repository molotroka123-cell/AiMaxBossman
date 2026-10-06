"""Synthetic transport fixtures test the boundary, never model performance."""
from __future__ import annotations

import copy
import json

import pytest

from tools.intelligence_evidence_transport import (MARKER, TransportError, _canonical, _digest,
                                                    prepare, select_comment)
from tools.intelligence_preservation_gate import CORE_METRICS, MODES, REQUIRED_METRICS

SHA = "a" * 40


def fixture():
    tasks, items = [], []
    for metric in REQUIRED_METRICS:
        for n in range(189 if metric in CORE_METRICS else 20):
            task_id = f"{metric}-{n}"
            tasks.append({"task_id": task_id, "metric": metric, "prompt": f"private prompt {task_id}",
                          "context": "private context", "expect": {"kind": "equals", "value": "private answer"}})
            items.append({"task_id": task_id, "metric": metric, **{m: True for m in MODES}})
    modes = {}
    for mode in MODES:
        modes[mode] = {}
        for metric in REQUIRED_METRICS:
            item = {"score": 0.0 if metric == "hallucination_rate" else 1.0,
                    "samples": 189 if metric in CORE_METRICS else 20}
            if mode != "raw":
                item["paired"] = {"lost": 0, "gained": 0}
            modes[mode][metric] = item
    traces = {t["task_id"]: {"turns": 1} for t in tasks}
    traces[tasks[0]["task_id"]]["turns"] = 2
    traces[tasks[0]["task_id"]]["executed_tools"] = ["fs.read"]
    return {"model": "private-model-name", "dataset_id": "private-corpus-name",
            "evaluated_sha": SHA, "diagnostic_only": False,
            "source": {"evaluated_sha": SHA, "tracked_files_verified": 3869},
            "corpus": {"file_sha256": "b" * 64, "tasks_sha256": _digest(_canonical(tasks)), "tasks": tasks},
            "model_identity": {"provider": "ollama", "model": "private-model-name"},
            "prompt_templates": {"full_system": "owner-only prompt"},
            "items": items, "traces": {"full": traces}, "modes": modes,
            "lanes": {"full": {"kind": "production_execution_loop", "executes_tools": True,
                               "observed": {"model_turns": len(tasks) + 1, "executed": 1,
                                            "declined": 0, "items_with_executed_tool_call": 1}}}}


def test_public_comment_is_sha_bound_and_contains_no_private_corpus_or_traces():
    raw = fixture()
    request = prepare(_canonical(raw), SHA)
    body = request["body"]
    assert body.startswith(MARKER)
    for private in ("private prompt", "private context", "private answer", "owner-only prompt",
                    "private-model-name", "private-corpus-name", "fs.read"):
        assert private not in body
    comment = {"id": 5, "commit_id": SHA, "user": {"login": "Owner"}, "body": body}
    assert select_comment([comment], "owner", SHA)["evaluated_sha"] == SHA
    with pytest.raises(TransportError, match="no owner-authored"):
        select_comment([{**comment, "user": {"login": "stranger"}}], "owner", SHA)
    with pytest.raises(TransportError, match="no owner-authored"):
        select_comment([comment], "owner", "c" * 40)


def test_private_tampering_or_insufficient_run_cannot_be_transport_pass():
    raw = fixture()
    raw["modes"]["full"]["reasoning_accuracy"]["score"] = 0.9
    with pytest.raises(TransportError, match="private score differs"):
        prepare(_canonical(raw), SHA)
    raw = fixture()
    raw["diagnostic_only"] = True
    with pytest.raises(TransportError, match="diagnostic"):
        prepare(_canonical(raw), SHA)
    raw = fixture()
    raw["items"][0]["full"] = False
    with pytest.raises(TransportError, match="private score differs"):
        prepare(_canonical(raw), SHA)
    with pytest.raises(TransportError, match="private source"):
        prepare(_canonical(fixture()), "c" * 40)


def test_latest_owner_comment_cannot_hide_invalid_evidence():
    valid = prepare(_canonical(fixture()), SHA)["body"]
    good = {"id": 1, "commit_id": SHA, "user": {"login": "owner"}, "body": valid}
    bad = copy.deepcopy(good)
    bad["id"] = 2
    bad["body"] = MARKER + json.dumps({"evaluated_sha": SHA})
    with pytest.raises(TransportError, match="public summary fields"):
        select_comment([good, bad], "owner", SHA)
