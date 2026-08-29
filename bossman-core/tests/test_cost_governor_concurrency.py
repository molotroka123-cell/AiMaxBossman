from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope
from bossman.cost_control.store import SQLiteBudgetStore

def test_parallel_reservations_cannot_overspend(tmp_path):
    path=tmp_path/"budget.db"
    setup=SQLiteBudgetStore(path)
    setup.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("1.00")))
    ctx=BudgetContext(day_utc="2026-08-29")
    def one(i):
        # Separate store objects prove DB transaction, not only Python lock, is the boundary.
        s=SQLiteBudgetStore(path)
        return s.reserve(ctx,"0.10",idempotency_key=f"k{i}").allowed
    with ThreadPoolExecutor(max_workers=20) as ex:
        results=list(ex.map(one,range(20)))
    assert sum(results)==10
    snap=setup.snapshots()[0]
    assert Decimal(snap["reserved_usd"])==Decimal("1.000000")
