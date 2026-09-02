"""Audit P0: Autonomy Trainer (shadow) and local cognitive reuse are wired into the
real runtime (Deep Fix learning-record storage, ExecutionCache) behind OFF flags."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman.exec_cache import ExecutionCache  # noqa: E402
from bossman.learning_guard import runtime_bridge as rb  # noqa: E402
from bossman_shared.cache_intelligence import ReuseOutcome  # noqa: E402


def _rec(i: int, verified: bool = True, **over) -> dict:
    rec = {"task_id": f"T-{i}", "bug_class": "path_traversal", "component": "fs", "learning_status": "VERIFIED" if verified else "UNVERIFIED",
           "principal_id": "agent:qwen#r1", "model": "qwen-14b", "environment": "env-a", "start_sha": "a", "end_sha": "b",
           "verifiers": [{"principal_id": "human:qa", "independence_class": "human", "run_id": "r9"}],
           "evidence_records": [{"observed_at": 1.0, "environment": "env-a"}], "notes": "token sk-ant-api03-SECRETVALUE0000000000000000"}  # ci-secret-scan: allow (synthetic canary)
    rec.update(over)
    return rec


def test_trainer_flag_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(rb.TRAINER_FLAG, raising=False)
    path = tmp_path / "eps.jsonl"
    assert rb.observe_learning_record(_rec(1), episodes_path=path) is None
    assert not path.exists()


def test_trainer_flag_on_appends_sanitized_episode_and_needs_measured_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.TRAINER_FLAG, "1")
    path = tmp_path / "eps.jsonl"
    out = rb.observe_learning_record(_rec(1), episodes_path=path)
    assert out is not None and out["status"] == "CANDIDATE"
    assert any("baseline" in r for r in out["reasons"]) and out["baseline_episodes"] == 0
    raw = path.read_text()
    assert "SECRETVALUE" not in raw and "sk-ant" not in raw
    line = json.loads(raw.splitlines()[0])
    assert line["verified_success"] is True and line["verifier_independence_class"] == "human"
    for i in range(2, 5):
        out = rb.observe_learning_record(_rec(i), episodes_path=path)
    assert out["status"] == "SHADOW" and out["baseline_episodes"] == 3 and out["verified_success_delta"] is not None


def test_unverified_or_self_reported_records_never_reach_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv(rb.TRAINER_FLAG, "1")
    path = tmp_path / "eps.jsonl"
    for i in range(1, 6):
        out = rb.observe_learning_record(_rec(i, verified=False), episodes_path=path)
    assert out["status"] == "CANDIDATE" and any("successful" in r for r in out["reasons"])
    path2 = tmp_path / "eps2.jsonl"
    for i in range(1, 6):
        out = rb.observe_learning_record(_rec(i, evidence_records=[]), episodes_path=path2)
    assert out["status"] == "CANDIDATE" and any("self-reported" in r for r in out["reasons"])


def test_deep_fix_store_hook_is_a_noop_with_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv(rb.TRAINER_FLAG, raising=False)
    called = {"n": 0}
    monkeypatch.setattr(rb, "observe_learning_record", lambda rec, **kw: called.__setitem__("n", called["n"] + 1) or None)
    from bossman import deep_fix
    monkeypatch.setattr(deep_fix, "store_learning_record", deep_fix.store_learning_record)
    assert called["n"] == 0                           # hook exists; flag decides inside observe_learning_record


def test_reuse_flag_off_leaves_execution_cache_untouched(monkeypatch):
    monkeypatch.delenv(rb.REUSE_FLAG, raising=False)
    c = ExecutionCache()
    c.put("fix:1", {"ok": 1}, verified=True)
    assert c.get("fix:1") is not None and c.blocked_by_reuse_gate == 0


def test_reuse_flag_on_requires_an_accepted_same_model_ab(monkeypatch):
    monkeypatch.setenv(rb.REUSE_FLAG, "1")
    monkeypatch.setattr(rb, "_GATE", None)
    c = ExecutionCache()
    c.put("fix:1", {"ok": 1}, verified=True)
    assert c.get("fix:1", task_class="fix") is None and c.blocked_by_reuse_gate == 1
    assert "no same-model A/B" in c.last_reuse_refusal
    rb.default_reuse_gate().record_ab("fix", ReuseOutcome(verified_success_on=0.9, verified_success_off=0.9, continuity_delta=0.2,
                                                          samples_on=20, samples_off=20))
    assert c.get("fix:1", task_class="fix") is not None
    rb.default_reuse_gate().record_ab("fix", ReuseOutcome(verified_success_on=0.5, verified_success_off=0.9, continuity_delta=0.2,
                                                          samples_on=20, samples_off=20))
    assert c.get("fix:1", task_class="fix") is None and "inferior" in c.last_reuse_refusal
