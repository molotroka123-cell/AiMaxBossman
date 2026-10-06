"""authored_by_lane (opsplug): bossman.cost_control.routes - budget policy API (read = chat scope, write = admin).

Real SQLite budget store in a temp dir, real bearer tokens; the router is exercised through the real core app.
"""
from decimal import Decimal

import pytest

from bossman.cost_control import routes as budget_routes
from bossman.cost_control.models import BudgetScope, HardLimitAction
from bossman.cost_control.store import SQLiteBudgetStore
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT

from tests.leaf_route_helpers import Devices, bearer, client, new_app


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SQLiteBudgetStore(tmp_path / "budget.db")
    monkeypatch.setattr(budget_routes, "STORE", s)
    return s


async def test_admin_sets_a_policy_and_chat_scope_can_read_but_not_write(store):
    body = {"scope": "daily_global", "subject": "*", "hard_limit_usd": "2.500000", "warning_fraction": "0.5",
            "hard_action": "ask"}
    with Devices() as dev:
        admin, chat = await dev.token(SCOPE_ADMIN), await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            denied = await c.put("/budget/policies", json=body, headers=bearer(chat))
            anon = await c.put("/budget/policies", json=body)
            assert denied.status_code == 403 and anon.status_code in (401, 403)
            assert store.list_policies() == []                       # nothing was written by the refused calls
            ok = await c.put("/budget/policies", json=body, headers=bearer(admin))
            assert ok.status_code == 200 and ok.json() == {"ok": True}
            rows = (await c.get("/budget/policies", headers=bearer(chat))).json()
    assert rows == [{"scope": "daily_global", "subject": "*", "hard_limit_usd": "2.500000", "warning_fraction": "0.5",
                     "hard_action": "ask", "enabled": True}]
    p = store.list_policies()[0]
    assert p.scope is BudgetScope.DAILY_GLOBAL and p.hard_limit_usd == Decimal("2.50") and p.hard_action is HardLimitAction.ASK


@pytest.mark.parametrize("bad", [
    {"scope": "daily_global", "hard_limit_usd": "0"},
    {"scope": "daily_global", "hard_limit_usd": "-1"},
    {"scope": "nonsense", "hard_limit_usd": "1"},
    {"scope": "run", "hard_limit_usd": "1", "warning_fraction": "1"},
    {"scope": "run", "hard_limit_usd": "1", "subject": ""},
])
async def test_invalid_policies_are_rejected_and_never_stored(store, bad):
    with Devices() as dev:
        admin = await dev.token(SCOPE_ADMIN)
        async with client(new_app()) as c:
            r = await c.put("/budget/policies", json=bad, headers=bearer(admin))
    assert r.status_code == 422
    assert store.list_policies() == []


async def test_status_shows_buckets_to_chat_scope(store):
    with Devices() as dev:
        chat = await dev.token(SCOPE_CHAT)
        async with client(new_app()) as c:
            r = await c.get("/budget/status", headers=bearer(chat))
            anon = await c.get("/budget/status")
    assert r.status_code == 200 and r.json() == {"buckets": []}
    assert anon.status_code in (401, 403)
