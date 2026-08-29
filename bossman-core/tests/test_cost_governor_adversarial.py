from decimal import Decimal
from bossman.cost_control.governor import CostGovernor
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope,DecisionKind,HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore

def test_cloud_never_beats_budget_override(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("100"),hard_action=HardLimitAction.ASK))
    events=[]
    g=CostGovernor(s,lambda kind,**data:events.append((kind,data)))
    d=g.reserve_cloud_call(BudgetContext(day_utc="2026-08-29"),"0.01",
        idempotency_key="x",cloud_allowed=False)
    assert d.kind is DecisionKind.DENY
    assert not s.snapshots()

def test_wrong_context_override_fails(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("0.1"),hard_action=HardLimitAction.ASK))
    c1=BudgetContext(task_id="a",day_utc="2026-08-29")
    c2=BudgetContext(task_id="b",day_utc="2026-08-29")
    grant=s.issue_override(c1,"1")
    d=s.reserve(c2,"0.2",idempotency_key="x",override_token=grant.token)
    assert d.kind is DecisionKind.REQUIRE_APPROVAL

def test_idempotency_cannot_double_reserve(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("1")))
    c=BudgetContext(day_utc="2026-08-29")
    a=s.reserve(c,"0.4",idempotency_key="same")
    b=s.reserve(c,"0.4",idempotency_key="same")
    assert a.allowed and b.kind is DecisionKind.DENY
    assert Decimal(s.snapshots()[0]["reserved_usd"])==Decimal("0.400000")
