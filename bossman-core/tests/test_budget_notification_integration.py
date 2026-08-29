from decimal import Decimal
from bossman.cost_control.governor import CostGovernor
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope
from bossman.cost_control.store import SQLiteBudgetStore
from bossman.notifications.policy import NotificationPolicy

def test_budget_warning_becomes_phone_notification(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("1")))
    events=[]
    g=CostGovernor(s,lambda kind,**data:events.append({"kind":kind,**data}))
    d=g.reserve_cloud_call(BudgetContext(day_utc="2026-08-29"),"0.81",
                           idempotency_key="x",cloud_allowed=True)
    assert d.allowed
    notes=[NotificationPolicy().from_event(e) for e in events]
    assert any(n and n.event_type=="budget.warning" for n in notes)
