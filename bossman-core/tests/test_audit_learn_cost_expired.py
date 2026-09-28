"""Audit 2026-09-28: a reservation that expired while its call was still running
must still be charged when the call settles (the provider billed it)."""
from decimal import Decimal
import sqlite3

import pytest

from bossman.cost_control.models import BudgetContext, BudgetPolicy, BudgetScope, DecisionKind, ReservationStatus
from bossman.cost_control.store import BudgetError, SQLiteBudgetStore


def _expire(path, rid):
    with sqlite3.connect(path) as c:
        c.execute("UPDATE reservations SET expires_at=0 WHERE id=?", (rid,))


def test_commit_after_ttl_expiry_charges_the_real_spend(tmp_path):
    path = tmp_path / "b.db"
    s = SQLiteBudgetStore(path)
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("1")))
    ctx = BudgetContext(day_utc="2026-09-28")
    d = s.reserve(ctx, "0.90", idempotency_key="k1")
    _expire(path, d.reservation.id)
    assert s.cleanup_expired() == 1                                   # hold released by the TTL sweep
    res = s.commit(d.reservation.id, "0.90")                          # ...then the long call settles
    assert res.status is ReservationStatus.COMMITTED
    snap = s.snapshots()[0]
    assert Decimal(snap["spent_usd"]) == Decimal("0.9") and Decimal(snap["reserved_usd"]) == 0
    assert s.reserve(ctx, "0.90", idempotency_key="k2").kind is DecisionKind.DENY   # money already gone
    assert s.commit(d.reservation.id, "0.90").status is ReservationStatus.COMMITTED   # idempotent
    assert Decimal(s.snapshots()[0]["spent_usd"]) == Decimal("0.9")


def test_released_reservation_still_cannot_be_committed(tmp_path):
    s = SQLiteBudgetStore(tmp_path / "b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL, Decimal("1")))
    d = s.reserve(BudgetContext(day_utc="2026-09-28"), "0.4", idempotency_key="k")
    s.release(d.reservation.id)
    with pytest.raises(BudgetError, match="cannot commit released"):
        s.commit(d.reservation.id, "0.4")
