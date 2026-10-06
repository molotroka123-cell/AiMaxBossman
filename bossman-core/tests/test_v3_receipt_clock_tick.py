"""RC19 audit tail: test_v3_cross_layer_e2e failed on Windows with an ActionReceipt
whose started_at == finished_at == observed_at.

Root cause: the Windows wall clock advances in ticks (0.5-15.6 ms). A fast write and
its fresh observation can share one reading, and ActionReceipt.fresh() correctly
refuses "observed before/at execution start". The observer, not the freshness rule,
must change: it observes only after the clock has moved past the execution start.
Nothing is back-dated or bumped; a clock that never advances still fails closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bossman._shared  # noqa: F401
from bossman_shared.action_receipt import ActionReceipt
from bossman_v3 import contracts
from bossman_v3.adapters import command_center as cc
from bossman_v3.contracts import ExecutionReceipt, TypedAction

T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
TICK = timedelta(milliseconds=15.625)


class CoarseClock:
    """Wall clock that only moves when time passes (sleep), one Windows tick at a time."""

    def __init__(self):
        self.now, self.sleeps = T0, 0

    def read(self):
        return self.now

    def sleep(self, _s):
        self.sleeps += 1
        self.now += TICK


class _Rt:
    def call(self, coro, timeout=None):
        coro.close()
        return {"status": "VERIFIED", "reason": "", "evidence": ["file:a.txt=VERIFIED"]}


def _receipt(obs, receipt):
    return ActionReceipt.from_v3(
        task_id="t", step_id="s1", action_type="file.write", effect_type="IDEMPOTENT_WRITE",
        args={"path": "a.txt"}, started_at=receipt.started_at, finished_at=receipt.completed_at,
        observed_at=obs.observed_at, executor_status="executed", observation_type="post_state",
        observation_ref=obs.source, verification_status="VERIFIED", verification_reason="")


def _patch(monkeypatch, clock):
    monkeypatch.setattr(contracts, "utcnow", clock.read)
    monkeypatch.setattr(cc, "_now", clock.read)
    monkeypatch.setattr(contracts.time, "sleep", clock.sleep)


def test_observation_in_the_same_clock_tick_as_execution_is_still_fresh(monkeypatch):
    clock = CoarseClock()
    _patch(monkeypatch, clock)
    receipt = ExecutionReceipt("file.write", clock.read(), clock.read(), effect_id="v3-1")
    action = TypedAction("file.write", {"path": "a.txt", "expect": {"kind": "file", "target": "a.txt"}})
    obs = cc.CommandCenterObserver(_Rt(), svc=None, task={}).observe_fresh(action, receipt)
    assert obs.observed_at > receipt.started_at and obs.observed_at >= receipt.completed_at
    rec = _receipt(obs, receipt)
    assert rec.fresh() == (True, "fresh") and rec.verified()


def test_no_wait_when_the_clock_already_moved_on(monkeypatch):
    clock = CoarseClock()
    _patch(monkeypatch, clock)
    assert contracts.utcnow_after(T0 - TICK) == T0 and clock.sleeps == 0


def test_a_clock_that_never_advances_still_fails_closed(monkeypatch):
    clock = CoarseClock()
    monkeypatch.setattr(contracts, "utcnow", clock.read)
    monkeypatch.setattr(contracts.time, "sleep", lambda _s: None)   # frozen clock
    reading = contracts.utcnow_after(T0, limit_s=0.01)
    assert reading == T0                                             # never back-dated or bumped
    receipt = ExecutionReceipt("file.write", T0, T0)
    obs = contracts.Observation(reading, "bcc.v2.verification", {"status": "VERIFIED"})
    rec = _receipt(obs, receipt)
    assert rec.fresh()[0] is False and not rec.verified()
