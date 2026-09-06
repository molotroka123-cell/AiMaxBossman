from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from bossman_v3.execution.telemetry import journal_record


def _step(status="DONE", finished=True):
    return SimpleNamespace(status=status, updated_at="2026-09-06T12:00:10+00:00", finished=finished,
                           signature_valid=lambda task_id: finished)


def test_verified_terminal_journal_becomes_passed_sample():
    j = SimpleNamespace(task_id="real-1", created_at="2026-09-06T12:00:00+00:00",
                        plan_digest="abc", steps=[_step(), _step()])
    r = journal_record(j, completed=True, context={"concurrency": 3, "retries": 1})
    assert r["status"] == "passed"
    assert r["verified"] is True
    assert r["duration_s"] == 10.0
    assert r["concurrency"] == 3
    assert r["retries"] == 1


def test_failed_journal_never_claims_verified_success():
    j = SimpleNamespace(task_id="real-2", created_at="2026-09-06T12:00:00+00:00",
                        plan_digest="def", steps=[_step("FAILED", False)])
    r = journal_record(j, completed=False)
    assert r["status"] == "failed"
    assert r["verified"] is False
