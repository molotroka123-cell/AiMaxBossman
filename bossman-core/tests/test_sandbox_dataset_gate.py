"""Stage 8 — Dataset Gate: сырые логи никогда не уходят в обучение напрямую."""
from __future__ import annotations

import json

import pytest

from bossman.sandbox import CandidateState, DatasetGate
from bossman.sandbox.trajectory import TrajectoryRecorder


def _events():
    return [
        {"kind": "tool_call", "ts": 1.0, "sandbox_id": "sbx1", "tool": "shell",
         "api_key": "super-secret-value-1234"},
        {"kind": "test_result", "ts": 2.0, "sandbox_id": "sbx1", "passed": 12},
        {"kind": "lifecycle", "ts": 3.0, "sandbox_id": "sbx1", "state": "RUNNING"},  # не обучающий
    ]


def test_sanitize_strips_secrets_and_ids():
    out = DatasetGate().sanitize(_events())
    blob = json.dumps(out)
    assert "super-secret-value-1234" not in blob
    assert "sandbox_id" not in blob and '"ts"' not in blob


def test_validate_keeps_only_useful_kinds():
    g = DatasetGate()
    kept, _ = g.validate(g.sanitize(_events()))
    kinds = {s["kind"] for s in kept}
    assert kinds == {"tool_call", "test_result"}   # lifecycle отброшен


def test_candidate_starts_as_candidate_not_approved():
    c = DatasetGate().build_candidate("sbx1", _events())
    assert c.state is CandidateState.CANDIDATE
    assert c.approved is False


def test_raw_logs_cannot_reach_training():
    c = DatasetGate().build_candidate("sbx1", _events())
    with pytest.raises(PermissionError):
        DatasetGate.training_samples(c)     # без человека — нельзя


def test_human_approval_unlocks_training_samples():
    g = DatasetGate()
    c = g.build_candidate("sbx1", _events())
    DatasetGate.approve(c, by="timur")
    assert c.state is CandidateState.APPROVED and c.decided_by == "timur"
    samples = DatasetGate.training_samples(c)
    assert samples and "super-secret-value-1234" not in json.dumps(samples)


def test_approval_requires_identity_and_nonempty():
    g = DatasetGate()
    c = g.build_candidate("sbx1", _events())
    with pytest.raises(ValueError):
        DatasetGate.approve(c, by="")
    empty = g.build_candidate("sbx2", [{"kind": "lifecycle", "state": "READY"}])
    with pytest.raises(ValueError):
        DatasetGate.approve(empty, by="timur")


def test_rejected_candidate_stays_locked():
    g = DatasetGate()
    c = g.build_candidate("sbx1", _events())
    DatasetGate.reject(c, by="timur", reason="noisy")
    assert c.state is CandidateState.REJECTED and "noisy" in c.reasons
    with pytest.raises(PermissionError):
        DatasetGate.training_samples(c)


def test_end_to_end_from_trajectory_file(tmp_path):
    rec = TrajectoryRecorder("sbx9", sink_path=tmp_path / "tr.jsonl")
    rec.record("tool_call", tool="http", token="ghp_0123456789abcdefABCDEF")
    rec.record("test_result", passed=3)
    rec.lifecycle("RUNNING")
    c = DatasetGate().from_trajectory_file(tmp_path / "tr.jsonl", "sbx9")
    assert len(c.samples) == 2                      # lifecycle отфильтрован
    assert "ghp_0123456789abcdefABCDEF" not in json.dumps(c.samples)
    assert c.state is CandidateState.CANDIDATE      # автопродвижения нет
