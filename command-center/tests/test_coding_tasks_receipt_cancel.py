"""Branch 9 (agents): coding-task receipts and cancellation, offline (no sidecar, no model)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bcc.features import coding_tasks as ct

BASE = {"id": "deadbeef0002", "status": "completed", "evidence": {"head_before": "a", "head_after": "a"},
        "evidence_digest": "sha256:1", "verify_tests": ["tests/test_x.py"]}


def _verified(**extra) -> bool:
    return ct.mission_receipt({**BASE, **extra})["postcondition_verified"]


def test_a_real_zero_exit_code_verifies():
    assert _verified(verification={"ran": True, "passed": True, "exit_code": 0}) is True


@pytest.mark.parametrize("code", [False, 0.0, "0", None])
def test_a_receipt_needs_a_real_integer_exit_code(code):
    assert _verified(verification={"ran": True, "passed": True, "exit_code": code}) is False


@pytest.mark.parametrize("verification", [
    {"ran": True, "passed": "yes", "exit_code": 0},          # "passed" must be the boolean True
    {"ran": False, "passed": True, "exit_code": 0},          # a run that did not happen verifies nothing
])
def test_a_receipt_needs_a_real_passed_run(verification):
    assert _verified(verification=verification) is False


def test_a_failed_bossman_test_run_is_not_rescued_by_another_check():
    assert _verified(verification={"ran": True, "passed": False, "exit_code": 0},
                     postcondition_check={"passed": True, "exit_code": 0}) is False


def test_a_postcondition_check_alone_still_verifies_with_a_real_exit_code():
    assert _verified(verify_tests=[], postcondition_check={"passed": True, "exit_code": 0}) is True
    assert _verified(verify_tests=[], postcondition_check={"passed": True, "exit_code": False}) is False


# ---------------------------------------------------------------- cancel / STOP during Bossman's own test run
class _FakeOc:
    class OpenHandsError(Exception):
        pass

    class OpenHandsRequest:
        def __init__(self, *a, **k):
            pass

    class OpenHandsClient:
        def __init__(self, command=None, on_process=None, env=None):
            pass

        def run(self, request):
            return SimpleNamespace(status="completed", changed_files=["a.py"], files={}, sidecar={},
                                   diff="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n")


class _FakeWt:
    class IsolatedWorktree:
        def __init__(self, repo):
            self.repo = repo

        def create(self):
            return str(self.repo)

        def derive_evidence(self):
            return {"head_before": "a", "head_after": "a"}

        def cleanup(self):
            pass

        def cleanup_state(self):
            return {"removed": True}


@pytest.mark.parametrize("how", ["cancel", "stop"])
def test_a_cancel_or_stop_during_the_verification_run_wins_over_its_result(tmp_path, monkeypatch, how):
    tid = "abcdefabcdef"
    data = tmp_path / "data"

    def verify_then_interrupt(root, tests, timeout):
        if how == "cancel":
            ct._CANCELLED.add(tid)                       # POST /coding-tasks/{id}/cancel during the test run
        else:
            (data / "computer").mkdir(parents=True, exist_ok=True)
            (data / "computer" / "STOP").write_text("{}", encoding="utf-8")
        return {"ran": True, "passed": True, "exit_code": 0}
    monkeypatch.setattr(ct, "_runtime", lambda: (_FakeOc, _FakeWt, ""))
    monkeypatch.setattr(ct, "_verify_in_sandbox", verify_then_interrupt)
    monkeypatch.delenv("BCC_DATA_DIR", raising=False)
    body = ct.TaskIn(instruction="x", source_repo=str(tmp_path), allowed_paths=["a.py"], verify_tests=["t.py"])
    out = ct._execute({"id": tid, "status": "running"}, tmp_path, body, data_dir=data)
    assert (out["status"], out.get("outcome"), out["diff"]) == ("failed", "CANCELLED", ""), out
    assert out["receipt"]["postcondition_verified"] is False and out["receipt"]["stop_status"] == "CANCELLED"
    assert tid not in ct._CANCELLED
