from decimal import Decimal
import sqlite3
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope
from bossman.cost_control.store import SQLiteBudgetStore

def test_restart_preserves_active_reservation(tmp_path):
    path=tmp_path/"b.db";s=SQLiteBudgetStore(path)
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("1")))
    d=s.reserve(BudgetContext(day_utc="2026-08-29"),"0.4",idempotency_key="x")
    s2=SQLiteBudgetStore(path)
    assert Decimal(s2.snapshots()[0]["reserved_usd"])==Decimal("0.400000")
    s2.release(d.reservation.id)
    assert Decimal(s2.snapshots()[0]["reserved_usd"])==0

def test_expired_reservation_releases_held_money(tmp_path):
    path=tmp_path/"b.db";s=SQLiteBudgetStore(path)
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("1")))
    d=s.reserve(BudgetContext(day_utc="2026-08-29"),"0.4",idempotency_key="x")
    with sqlite3.connect(path) as c:c.execute("UPDATE reservations SET expires_at=0 WHERE id=?",(d.reservation.id,))
    assert s.cleanup_expired()==1
    assert Decimal(s.snapshots()[0]["reserved_usd"])==0
