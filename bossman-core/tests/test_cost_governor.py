from decimal import Decimal
import pytest
from bossman.cost_control.models import BudgetContext,BudgetPolicy,BudgetScope,DecisionKind,HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore,BudgetExtensionRequired

def store(tmp_path,scope=BudgetScope.DAILY_GLOBAL,limit="1.00",action=HardLimitAction.STOP):
    s=SQLiteBudgetStore(tmp_path/"budget.db")
    s.set_policy(BudgetPolicy(scope=scope,hard_limit_usd=Decimal(limit),hard_action=action))
    return s

def test_reserve_commit_release_exact_decimal(tmp_path):
    s=store(tmp_path)
    c=BudgetContext(task_id="t1",day_utc="2026-08-29")
    d=s.reserve(c,"0.333333",idempotency_key="a")
    assert d.allowed
    s.commit(d.reservation.id,"0.123456")
    snap=s.snapshots()[0]
    assert Decimal(snap["spent_usd"])==Decimal("0.123456")
    assert Decimal(snap["reserved_usd"])==0

def test_hard_stop_counts_inflight_reserved(tmp_path):
    s=store(tmp_path)
    c=BudgetContext(day_utc="2026-08-29")
    a=s.reserve(c,"0.60",idempotency_key="a")
    b=s.reserve(c,"0.50",idempotency_key="b")
    assert a.allowed
    assert b.kind is DecisionKind.DENY

def test_warning_crossing_only_once(tmp_path):
    s=store(tmp_path)
    c=BudgetContext(day_utc="2026-08-29")
    a=s.reserve(c,"0.81",idempotency_key="a")
    assert len(a.warnings)==1
    s.commit(a.reservation.id,"0.81")
    b=s.reserve(c,"0.01",idempotency_key="b")
    assert len(b.warnings)==0

def test_ask_override_is_bounded_and_single_use(tmp_path):
    s=store(tmp_path,action=HardLimitAction.ASK)
    c=BudgetContext(task_id="t",day_utc="2026-08-29")
    a=s.reserve(c,"0.95",idempotency_key="a");s.commit(a.reservation.id,"0.95")
    blocked=s.reserve(c,"0.10",idempotency_key="b")
    assert blocked.kind is DecisionKind.REQUIRE_APPROVAL
    grant=s.issue_override(c,"0.10",ttl_s=60)
    ok=s.reserve(c,"0.10",idempotency_key="b",override_token=grant.token)
    assert ok.allowed
    again=s.reserve(c,"0.10",idempotency_key="c",override_token=grant.token)
    assert again.kind is DecisionKind.REQUIRE_APPROVAL

def test_actual_over_reservation_requires_extension(tmp_path):
    s=store(tmp_path,limit="2.00")
    c=BudgetContext(day_utc="2026-08-29")
    d=s.reserve(c,"0.20",idempotency_key="x")
    with pytest.raises(BudgetExtensionRequired):
        s.commit(d.reservation.id,"0.21")
    e=s.extend_reservation(d.reservation.id,"0.05")
    assert e.allowed
    s.commit(d.reservation.id,"0.21")

def test_release_idempotent(tmp_path):
    s=store(tmp_path)
    c=BudgetContext(day_utc="2026-08-29")
    d=s.reserve(c,"0.3",idempotency_key="x")
    s.release(d.reservation.id);s.release(d.reservation.id)
    assert Decimal(s.snapshots()[0]["reserved_usd"])==0

def test_run_task_project_and_day_scopes(tmp_path):
    s=SQLiteBudgetStore(tmp_path/"b.db")
    for scope in BudgetScope:
        s.set_policy(BudgetPolicy(scope=scope,hard_limit_usd=Decimal("5")))
    c=BudgetContext(run_id=1,task_id=2,project_id=3,day_utc="2026-08-29")
    d=s.reserve(c,"1",idempotency_key="all")
    assert d.allowed
    assert {x["scope"] for x in s.snapshots()}=={x.value for x in BudgetScope}

def test_day_rollover_is_new_bucket(tmp_path):
    s=store(tmp_path)
    a=s.reserve(BudgetContext(day_utc="2026-08-29"),"0.9",idempotency_key="a")
    s.commit(a.reservation.id,"0.9")
    b=s.reserve(BudgetContext(day_utc="2026-08-30"),"0.9",idempotency_key="b")
    assert b.allowed
