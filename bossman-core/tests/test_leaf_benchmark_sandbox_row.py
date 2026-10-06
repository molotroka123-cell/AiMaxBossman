"""authored_by_lane (opsplug): bossman.benchmark.sandbox_row - a case can never award itself a pass."""
import copy

import pytest

from bossman.benchmark.sandbox_row import CONTRACT, CaseProbe, Check, verify_row


def _good() -> CaseProbe:
    p = CaseProbe("c1", "approvals", seed=7)
    p.positive("did the work", 3, 3)
    p.refused("refused hostile input", lambda: (_ for _ in ()).throw(PermissionError("denied: x")), PermissionError,
              contains="denied")
    return p


def test_a_case_with_a_positive_and_a_negative_check_is_verified():
    row = _good().finish()
    assert row["verified"] is True and row["verification_reasons"] == []
    assert row["coverage"] == {"positive": 1, "negative": 1, "failed": 0}
    assert row["contract"] == CONTRACT and row["mode"] == "REAL_SANDBOX" and row["training_eligible"] is False
    assert row["actions"] == 1 and row["refused"] == 1
    assert row["provider_evidence"] is False and row["estimated_cost_usd"] == 0.0


def test_a_case_cannot_set_verified_itself():
    row = _good().finish(verified=True)
    assert row["verified"] is True                       # earned by the checks, and...
    bad = CaseProbe("c2", "x", 1).positive("only happy path", 1, 1).finish(verified=True)
    assert bad["verified"] is False                      # ...a self-reported flag does not rescue a bad case
    assert any("no negative check" in r for r in bad["verification_reasons"])


def test_mutating_the_row_after_finish_is_corrected_by_the_second_verification():
    row = CaseProbe("c3", "x", 1).positive("p", 1, 1).finish()
    row["verified"] = True
    row["verification_reasons"] = []
    assert verify_row(row)["verified"] is False


@pytest.mark.parametrize("build,needle", [
    (lambda p: p, "no observed checks"),
    (lambda p: p.negative("n", 1, 1), "no positive check"),
    (lambda p: p.positive("p", 1, 1), "no negative check"),
    (lambda p: p.positive("p", 1, 2).negative("n", 1, 1), "failed checks: p"),
])
def test_missing_or_failed_coverage_is_reported_with_a_reason(build, needle):
    row = build(CaseProbe("c", "x", 0)).finish()
    assert row["verified"] is False and any(needle in r for r in row["verification_reasons"])


def test_refused_records_the_real_exception_class_and_message_match():
    p = CaseProbe("c", "x", 0)
    p.refused("swallowed", lambda: None, ValueError)                                   # no exception at all
    p.refused("wrong type", lambda: (_ for _ in ()).throw(KeyError("k")), ValueError)   # different failure mode
    p.refused("wrong text", lambda: (_ for _ in ()).throw(ValueError("other")), ValueError, contains="needle")
    p.refused("exact", lambda: (_ for _ in ()).throw(ValueError("has needle")), ValueError, contains="needle")
    ok = [c.ok for c in p.checks]
    assert ok == [False, False, False, True]


def test_tokens_or_cost_without_provider_evidence_make_the_row_unverified():
    p = _good()
    row = p.finish(tokens_in=10)
    assert row["verified"] is False and any("without provider evidence" in r for r in row["verification_reasons"])
    assert _good().finish(tokens_in=10, provider_evidence=True)["verified"] is True


def test_check_projection_is_json_safe_and_counters_accumulate():
    c = Check("n", "positive", {1, 2}, object)
    d = c.as_dict()
    assert d["ok"] is False and isinstance(d["actual"], list) and isinstance(d["expected"], str)
    p = _good().count(effects=2).count(effects=3, recoveries=1).tag("a", "b")
    row = p.finish()
    assert row["effects"] == 5 and row["recoveries"] == 1 and row["tags"] == ["a", "b"]
    assert copy.deepcopy(row) == row
