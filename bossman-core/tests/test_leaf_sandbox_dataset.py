"""authored_by_lane (opsplug): bossman.sandbox.dataset - the Dataset Gate.

Behaviour under test (no mocks): raw trajectories never reach training without a named human approval.
"""
import json

import pytest

from bossman.sandbox.dataset import CandidateState, DatasetGate

EVENTS = [
    {"kind": "tool_call", "name": "pytest", "sandbox_id": "sbx_1", "ts": 1, "grant_id": "g", "lease_id": "l"},
    {"kind": "shell", "cmd": "echo hi", "token": "sk-proj-THISISNOTAREALSECRET123456"},  # ci-secret-scan: allow
    {"kind": "chatter", "text": "not a training signal"},
    {"kind": "artifact"},  # useful kind but empty payload
    "not-a-dict",
]


def test_sanitize_drops_service_ids_and_redacts_secrets():
    out = DatasetGate().sanitize(EVENTS)
    assert len(out) == 4                       # the non-dict is gone
    flat = json.dumps(out)
    assert "sbx_1" not in flat and "grant_id" not in flat and "lease_id" not in flat
    assert "THISISNOTAREALSECRET123456" not in flat


def test_validate_keeps_only_useful_kinds_and_explains_rejections():
    gate = DatasetGate(min_samples=3)
    kept, reasons = gate.validate(gate.sanitize(EVENTS))
    assert [s["kind"] for s in kept] == ["tool_call", "shell"]
    assert any("empty payload: artifact" in r for r in reasons)
    assert any("too few samples: 2 < 3" in r for r in reasons)


def test_candidate_is_not_trainable_until_a_human_approves():
    cand = DatasetGate().build_candidate("sbx_1", EVENTS)
    assert cand.state is CandidateState.CANDIDATE and not cand.approved
    with pytest.raises(PermissionError, match="raw logs -> fine-tune is forbidden"):
        DatasetGate.training_samples(cand)
    DatasetGate.approve(cand, by="owner")
    assert cand.approved and cand.decided_by == "owner"
    assert len(DatasetGate.training_samples(cand)) == len(cand.samples) >= 1


def test_approval_needs_an_identity_and_a_non_empty_candidate():
    cand = DatasetGate().build_candidate("sbx_1", EVENTS)
    with pytest.raises(ValueError):
        DatasetGate.approve(cand, by="")
    empty = DatasetGate().build_candidate("sbx_2", [{"kind": "chatter", "x": 1}])
    with pytest.raises(ValueError, match="empty candidate"):
        DatasetGate.approve(empty, by="owner")


def test_rejected_candidate_stays_untrainable_and_keeps_the_reason():
    cand = DatasetGate().build_candidate("sbx_1", EVENTS)
    DatasetGate.reject(cand, by="owner", reason="noisy")
    assert cand.state is CandidateState.REJECTED and "noisy" in cand.reasons
    with pytest.raises(PermissionError):
        DatasetGate.training_samples(cand)


def test_from_trajectory_file_skips_corrupt_lines_and_missing_file(tmp_path):
    p = tmp_path / "traj.jsonl"
    p.write_text(json.dumps({"kind": "shell", "cmd": "ls"}) + "\n{broken\n"
                 + json.dumps({"kind": "failure", "e": "x"}) + "\n", encoding="utf-8")
    cand = DatasetGate().from_trajectory_file(p, "sbx_f")
    assert [s["kind"] for s in cand.samples] == ["shell", "failure"]
    missing = DatasetGate().from_trajectory_file(tmp_path / "nope.jsonl", "sbx_m")
    assert missing.samples == [] and any("too few samples" in r for r in missing.reasons)
