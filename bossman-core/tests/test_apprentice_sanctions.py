"""Technical sanctions, scoped reliability, circuit breaker."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.errors import CircuitOpen  # noqa: E402
from bossman.apprentice.sanctions import (CircuitBreaker, ReliabilityKey, ReliabilityLedger, SanctionEngine, SanctionKind,  # noqa: E402
                                          TRUST_THRESHOLD, error_signature)
from bossman.apprentice.teacher import AcceptanceBinding, FallbackReason, TeacherStatus, TeacherVerdict  # noqa: E402
from fixtures.apprentice.teacher_sim import BUGGY, FakeGovernor, FakeWorkspace  # noqa: E402
import test_apprentice_teacher as tt  # noqa: E402

KEY = dict(model_id="claude-code", model_version="1.2", task_type="bugfix", repository="repo:calc", now=1_000.0)


def _verdict(status: str, reasons=("r",), attempt=1, violation="", critique="c"):
    return TeacherVerdict(status, list(reasons), [], critique, violation, attempt=attempt)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(flags.MASTER, "1")
    monkeypatch.setenv(flags.CLAUDE_CODE_FALLBACK, "1")


def test_rejected_sanction_rolls_back_blocks_learning_and_allows_one_retry():
    eng = SanctionEngine()
    d1 = eng.apply(_verdict("TEACHER_OUTPUT_REJECTED", ("acceptance tests failing: [t1]",), attempt=1), **KEY)
    assert d1.kind == SanctionKind.TEACHER_OUTPUT_REJECTED.value and d1.rollback and d1.learning_blocked and d1.retry_allowed and not d1.stop
    d2 = eng.apply(_verdict("TEACHER_OUTPUT_REJECTED", ("acceptance tests failing: [t2]",), attempt=2), **KEY)
    assert not d2.retry_allowed and d2.reliability_delta == -0.1
    key = ReliabilityKey.make(**KEY)
    assert eng.reliability.get(key) == pytest.approx(0.3) and eng.reliability.samples(key) == 2


def test_quarantine_requires_owner_and_forbids_promotion_without_secrets():
    eng = SanctionEngine()
    v = _verdict("TEACHER_OUTPUT_QUARANTINED", ("secret added in app/x.py: token=BOSSMAN_TEST_SECRET_zz9",), violation="secret added")
    d = eng.apply(v, trace_ids=("ep_1", "ep_2"), **KEY)
    assert d.stop and d.owner_approval_required and d.learning_blocked and d.rollback and d.violation_type == "secret added"
    assert eng.promotion_forbidden("ep_1") and not eng.promotion_forbidden("ep_9")
    assert "BOSSMAN_TEST_SECRET" not in str(eng.register.entries) and eng.register.entries[0]["violation_type"] == "secret added"


def test_tampering_rejects_entirely_and_lowers_scoped_reliability():
    eng = SanctionEngine()
    d = eng.apply(_verdict("ACCEPTANCE_TAMPERING", ("patch modifies acceptance tests ['tests/test_calc.py']",), violation="acceptance_tampering"), **KEY)
    assert d.stop and d.tests_restored and not d.retry_allowed and d.adversarial_entry["violation_type"] == "acceptance_tampering"
    key = ReliabilityKey.make(**KEY)
    assert eng.reliability.get(key) == pytest.approx(0.1) and not eng.reliability.trusted(key)
    other = ReliabilityKey.make(**{**KEY, "task_type": "ui"})
    assert eng.reliability.get(other) == 0.5                       # scoped: other task types unaffected


def test_reliability_is_windowed_and_bounded():
    led = ReliabilityLedger()
    k1 = ReliabilityKey.make(**KEY)
    for _ in range(10):
        led.update(k1, -0.4)
    assert led.get(k1) == 0.0
    k2 = ReliabilityKey.make(**{**KEY, "now": KEY["now"] + 8 * 24 * 3600})          # next window: not a permanent blacklist
    assert led.get(k2) == 0.5 and k2.window != k1.window
    led.update(k2, +0.1)
    assert led.get(k2) == pytest.approx(0.6) and led.trusted(k2)                         # one success != unconditional trust
    for _ in range(10):
        led.update(k2, +0.1)
    assert led.get(k2) == 1.0 and TRUST_THRESHOLD == 0.6


def test_circuit_breaker_opens_on_repeated_error():
    cb = CircuitBreaker(max_repeats=2)
    assert not cb.record("sig-a") and not cb.record("sig-b")          # different errors reset the streak
    assert cb.record("sig-b") and cb.is_open() and "Options for the owner" in cb.report()
    cb.reset(); assert not cb.is_open()
    assert error_signature(_verdict("TEACHER_OUTPUT_ACCEPTED")) == ""
    assert error_signature(_verdict("TEACHER_OUTPUT_REJECTED", ("x",), attempt=1)) == error_signature(_verdict("TEACHER_OUTPUT_REJECTED", ("x",), attempt=2))


def test_circuit_breaker_stops_teacher_calls_and_spend(on):
    gov = FakeGovernor(limit_usd=10.0)
    eng = SanctionEngine()
    fb, ws, sim = tt._fallback("error_repeat", governor=gov, budget_context={}, estimated_usd=0.5, sanctions=eng, max_calls=5)
    acc = AcceptanceBinding.bind(ws, ("tests/test_calc.py",))
    res = fb.request(reason=FallbackReason.TESTS_STILL_FAILING, task=tt._task(), bundle=tt._bundle(ws), acceptance=acc, binding=tt._binding())
    assert res.calls == 2 and eng.breaker.is_open() and "circuit breaker open" in res.report and gov.spent == 1.0
    assert res.status == TeacherStatus.TEACHER_OUTPUT_REJECTED.value and ws.read("app/calc.py") == BUGGY
    with pytest.raises(CircuitOpen):
        fb.request(reason=FallbackReason.TESTS_STILL_FAILING, task=tt._task(), bundle=tt._bundle(ws), acceptance=acc, binding=tt._binding())
    assert len(sim.calls) == 2 and gov.spent == 1.0                   # no new call, no new spend


def test_sanctions_wired_into_fallback_outcomes(on):
    for mode, kind, stop in (("tamper", SanctionKind.ACCEPTANCE_TAMPERING.value, True), ("security", SanctionKind.TEACHER_OUTPUT_QUARANTINED.value, True),
                             ("good", SanctionKind.NONE.value, False)):
        eng = SanctionEngine()
        res, ws, _ = tt._run(mode, sanctions=eng)
        assert eng.log[-1].kind == kind and eng.log[-1].stop is stop and res.calls == 1
        if stop:
            assert res.denied_reason and res.strategy is None and ws.applied == []
