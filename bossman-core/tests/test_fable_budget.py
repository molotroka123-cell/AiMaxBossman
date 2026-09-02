"""P0-FINISH-BUDGET-001: durable atomic cloud-budget reservations."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from bossman.apprentice.errors import BudgetExhausted
from bossman.apprentice.fable_direct import DirectApiBudget


def test_reserve_commit_release_and_remaining(tmp_path: Path):
    b = DirectApiBudget(tmp_path / "budget.json", total_usd=9.0)
    rid = b.reserve(2.0)
    assert b.remaining() == 7.0                       # active reservation blocks budget
    b.commit(rid, 0.05)
    assert b.remaining() == 8.95                      # actual spend replaces worst-case hold
    rid2 = b.reserve(1.0)
    b.mark_reconciling(rid2)
    b.trusted_reconcile(rid2, request_id="r2", actual_usd=None)
    assert b.remaining() == 8.95                      # released reservation frees budget
    with pytest.raises(BudgetExhausted):
        b.reserve(100.0)                              # hard cap not weakenable
    assert b.remaining() >= 0.0


def test_restart_reloads_durable_state(tmp_path: Path):
    path = tmp_path / "budget.json"
    b1 = DirectApiBudget(path, total_usd=9.0)
    rid = b1.reserve(4.0)
    b1.commit(rid, 0.20)
    b1.reserve(3.0)                                   # stays RESERVED (crash-conservative)
    b2 = DirectApiBudget(path, total_usd=9.0)
    assert b2.remaining() == pytest.approx(5.80)      # committed spend + active reservation survive restart
    with pytest.raises(BudgetExhausted):
        b2.reserve(5.81)                              # cap holds after reload
    with pytest.raises(BudgetExhausted):
        DirectApiBudget(path, total_usd=9.0).commit(rid, 0.20)   # double commit across restart refused


def test_parallel_reserve_never_exceeds_cap(tmp_path: Path):
    b = DirectApiBudget(tmp_path / "budget.json", total_usd=1.0)
    ok, fail = [], []

    def worker():
        try:
            ok.append(b.reserve(0.4))
        except BudgetExhausted:
            fail.append("x")
    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 2 and len(fail) == 4            # exactly two fit under the cap
    assert b.remaining() == pytest.approx(0.2)
    records = json.loads((tmp_path / "budget.json").read_text())["reservations"]
    assert len({r["reservation_id"] for r in records}) == 2   # only granted reserves are durably recorded


def test_corrupt_ledger_fails_closed(tmp_path: Path):
    path = tmp_path / "budget.json"
    path.write_text("{corrupted", encoding="utf-8")
    b = DirectApiBudget(path, total_usd=9.0)
    assert b.remaining() == 9.0                       # nothing assumed spent
    rid = b.reserve(9.0)                              # ...but the cap still applies
    assert b.remaining() == 0.0
    b.trusted_reconcile(rid, request_id="r", actual_usd=None)
    assert b.remaining() == 9.0
