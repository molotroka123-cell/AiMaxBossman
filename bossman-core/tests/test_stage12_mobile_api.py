from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bossman.remote_client.auth import Principal
from bossman.remote_client import mobile_api


@pytest.fixture
def principal():
    return Principal(device_id="dev-1", scopes=frozenset({"chat", "events", "approve"}), name="phone", session_id="s1")


@pytest.mark.asyncio
async def test_non_admin_task_list_is_device_scoped(monkeypatch, principal):
    seen = {}
    async def fake_fetch(sql, *args):
        seen["sql"], seen["args"] = sql, args
        return []
    monkeypatch.setattr(mobile_api.db, "fetch", fake_fetch)
    out = await mobile_api.mobile_list_tasks(status=None, limit=50, principal=principal)
    assert out == []
    assert "source=$1" in seen["sql"]
    assert seen["args"][0] == "remote:dev-1"


@pytest.mark.asyncio
async def test_admin_can_see_all_tasks(monkeypatch):
    p = Principal(device_id="owner", scopes=frozenset({"chat", "admin"}), name="owner", session_id="s")
    seen = {}
    async def fake_fetch(sql, *args):
        seen["sql"] = sql
        return []
    monkeypatch.setattr(mobile_api.db, "fetch", fake_fetch)
    await mobile_api.mobile_list_tasks(status=None, limit=50, principal=p)
    assert "source=" not in seen["sql"]


@pytest.mark.asyncio
async def test_create_rejects_unknown_agent(monkeypatch, principal):
    monkeypatch.setattr(mobile_api, "load_all", lambda: {"coder": object()})
    with pytest.raises(HTTPException) as exc:
        await mobile_api.mobile_create_task(mobile_api.MobileTaskIn(text="x", agent="nope"), principal)
    assert exc.value.status_code == 422


def test_approval_view_never_returns_payload_or_raw_secret():
    row = {"id": 1, "status": "pending", "preview": "Authorization: Bearer abcdefghijklmnop", "payload": {"password": "x"}}
    out = mobile_api._approval_view(row)
    assert "payload" not in out
    assert "abcdefghijklmnop" not in out["preview"]


def test_static_asset_allowlist_blocks_traversal():
    assert "../secret" not in mobile_api._ASSETS
    assert set(mobile_api._ASSETS) == {"app.js", "remote-core.mjs", "styles.css", "manifest.webmanifest", "sw.js"}
