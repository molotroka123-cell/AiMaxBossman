"""Branch 9 (agents): coding-task receipts and cancellation, offline (no sidecar, no model)."""
from __future__ import annotations

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
