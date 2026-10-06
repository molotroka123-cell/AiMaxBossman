"""authored_by_lane (opsplug): bossman.cost_control.subsystem - boot seeding + the expiry janitor."""
import asyncio
from decimal import Decimal

import pytest

from bossman.cost_control import runtime, subsystem
from bossman.cost_control.models import BudgetScope, HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SQLiteBudgetStore(tmp_path / "b.db")
    monkeypatch.setattr(runtime, "STORE", s)
    for name in ("BOSSMAN_BUDGET_RUN_USD", "BOSSMAN_BUDGET_TASK_USD", "BOSSMAN_BUDGET_PROJECT_USD",
                 "BOSSMAN_BUDGET_DAILY_USD", "BOSSMAN_BUDGET_HARD_ACTION", "BOSSMAN_BUDGET_WARNING_FRACTION"):
        monkeypatch.delenv(name, raising=False)
    return s


def test_it_is_a_critical_subsystem_the_core_must_not_boot_without():
    sub = subsystem.build_subsystem()
    assert sub.name == "cost_control" and sub.critical is True


async def test_validate_seeds_env_limits_once_and_never_overwrites_the_owner_choice(store, monkeypatch):
    monkeypatch.setenv("BOSSMAN_BUDGET_DAILY_USD", "3.00")
    monkeypatch.setenv("BOSSMAN_BUDGET_RUN_USD", "0.25")
    monkeypatch.setenv("BOSSMAN_BUDGET_HARD_ACTION", "ask")
    await subsystem.build_subsystem().validate()
    seeded = {p.scope: p for p in store.list_policies()}
    assert set(seeded) == {BudgetScope.DAILY_GLOBAL, BudgetScope.RUN}
    assert seeded[BudgetScope.DAILY_GLOBAL].hard_limit_usd == Decimal("3.00")
    assert seeded[BudgetScope.RUN].hard_action is HardLimitAction.ASK
    monkeypatch.setenv("BOSSMAN_BUDGET_DAILY_USD", "99")           # env changes later: the stored policy wins
    await subsystem.build_subsystem().validate()
    assert {p.scope: p.hard_limit_usd for p in store.list_policies()}[BudgetScope.DAILY_GLOBAL] == Decimal("3.00")


async def test_validate_with_no_env_creates_no_policy(store):
    await subsystem.build_subsystem().validate()
    assert store.list_policies() == []


async def test_janitor_runs_and_stop_cancels_it_cleanly(store, monkeypatch):
    from bossman.cost_control.governor import CostGovernor
    calls = []
    real = store.cleanup_expired
    monkeypatch.setattr(store, "cleanup_expired", lambda: (calls.append(1), real())[1])   # observe, still run the real sweep
    monkeypatch.setattr(subsystem, "GOVERNOR", CostGovernor(store, lambda *a, **k: None))
    sub = subsystem.build_subsystem()
    await sub.start()
    await asyncio.sleep(0.05)
    assert calls, "janitor never ran cleanup_expired"
    await sub.stop()
    assert sub._task is None
    await sub.stop()                                                # idempotent
