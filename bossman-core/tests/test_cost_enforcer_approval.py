from decimal import Decimal
import pytest
from bossman.cost_control.enforcer import BudgetApprovalRejected,BudgetEnforcer
from bossman.cost_control.governor import CostGovernor
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope,HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore

@pytest.mark.asyncio
async def test_ask_uses_existing_approval_and_grants_only_one_request(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("0.10"),hard_action=HardLimitAction.ASK))
    # consume most budget
    c=BudgetContext(task_id="1",day_utc="2026-08-29")
    d=s.reserve(c,"0.09",idempotency_key="seed");s.commit(d.reservation.id,"0.09")
    created=[]
    async def create(kind,preview,**kw):created.append((kind,preview,kw));return 7
    async def wait(aid):return {"status":"approved"}
    e=BudgetEnforcer(CostGovernor(s,lambda *a,**k:None),create,wait)
    res=await e.reserve(c,"0.02",idempotency_key="one",cloud_allowed=True)
    assert res is not None and created[0][0]=="budget_override"
    # approval didn't permanently raise budget
    d2=s.reserve(c,"0.02",idempotency_key="two")
    assert not d2.allowed

@pytest.mark.asyncio
async def test_rejected_ask_never_reserves(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    s.set_policy(BudgetPolicy(BudgetScope.DAILY_GLOBAL,Decimal("0.01"),hard_action=HardLimitAction.ASK))
    async def create(*a,**k):return 1
    async def wait(_):return {"status":"rejected"}
    e=BudgetEnforcer(CostGovernor(s,lambda *a,**k:None),create,wait)
    with pytest.raises(BudgetApprovalRejected):
        await e.reserve(BudgetContext(day_utc="2026-08-29"),"0.02",idempotency_key="x",cloud_allowed=True)
    assert Decimal(s.snapshots()[0]["reserved_usd"])==0
