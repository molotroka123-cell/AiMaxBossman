"""Stage 8 — инструменты агента и персистентный Secret Broker."""
from __future__ import annotations

import time

import pytest

from bossman import errors
from bossman.sandbox import PostgresSecretBroker
from bossman.sandbox.secrets import DDL
from bossman.toolkit import REGISTRY, ToolContext


# ---------- инструменты агента ----------

def _reg():
    from bossman.sandbox.tools import register_tools
    register_tools()
    return REGISTRY


def test_tools_registered_with_approval_on_consequential():
    r = _reg()
    for n in ("sandbox.create", "sandbox.run", "sandbox.status", "sandbox.collect", "sandbox.destroy"):
        assert n in r, n
    # консеквентные действия — под подтверждением (approvals НАД песочницей)
    assert r["sandbox.create"].confirm_default is True
    assert r["sandbox.run"].confirm_default is True
    # чтение — без подтверждения
    assert r["sandbox.status"].confirm_default is False


@pytest.mark.asyncio
async def test_tools_report_disabled_when_feature_off(monkeypatch):
    monkeypatch.delenv("BOSSMAN_SANDBOX_ENABLED", raising=False)
    r = _reg()
    res = await r["sandbox.create"].handler({"task": "t"}, ToolContext(agent="a"))
    assert res.error and "SANDBOX_DISABLED" in res.content


@pytest.mark.asyncio
async def test_create_tool_refuses_shell_string_argv(monkeypatch):
    monkeypatch.setenv("BOSSMAN_SANDBOX_ENABLED", "1")
    r = _reg()
    res = await r["sandbox.create"].handler(
        {"task": "t", "argv": "/bin/echo hi; rm -rf /"}, ToolContext(agent="a"))
    assert res.error and "argv must be a list" in res.content


@pytest.mark.asyncio
async def test_status_tool_reads_without_enabling(monkeypatch):
    monkeypatch.delenv("BOSSMAN_SANDBOX_ENABLED", raising=False)
    r = _reg()
    res = await r["sandbox.status"].handler({}, ToolContext(agent="a"))
    assert not res.error and '"enabled": false' in res.content.lower()


# ---------- Postgres Secret Broker (фейковая БД, без живого PG) ----------

class _FakeDB:
    """Минимальная имитация bossman.db поверх словаря."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.executed: list[str] = []

    async def execute(self, sql, *a):
        self.executed.append(sql)
        if sql.strip().upper().startswith("INSERT"):
            gid, sid, scope, issued, ttl = a
            self.rows[gid] = {"id": gid, "sandbox_id": sid, "scope": scope,
                              "issued_at": issued, "ttl_seconds": ttl, "revoked": False}
        return "OK"

    async def fetchrow(self, sql, *a):
        s = sql.strip().upper()
        if s.startswith("UPDATE"):
            gid = a[0]
            row = self.rows.get(gid)
            if row and not row["revoked"]:
                row["revoked"] = True
                return {"id": gid}
            return None
        gid = a[0]
        return self.rows.get(gid)

    async def fetch(self, sql, *a):
        sid = a[0]
        hit = [r for r in self.rows.values() if r["sandbox_id"] == sid and not r["revoked"]]
        for r in hit:
            r["revoked"] = True
        return [{"id": r["id"]} for r in hit]


def _broker(db):
    material = {"openrouter": "sk-REAL-SECRET-DO-NOT-LEAK"}
    return PostgresSecretBroker(lambda s: material.get(s), db=db,
                                allowed_scopes=frozenset({"openrouter"}))


@pytest.mark.asyncio
async def test_pg_broker_grant_redeem_revoke():
    db = _FakeDB()
    b = _broker(db)
    await b.ensure_schema()
    assert any("CREATE TABLE IF NOT EXISTS sandbox_secret_grants" in s for s in db.executed)
    g = await b.grant("sbx1", "openrouter", 60)
    assert await b.redeem(g.id, "sbx1") == "sk-REAL-SECRET-DO-NOT-LEAK"
    assert await b.revoke(g.id) is True
    with pytest.raises(errors.SecretDenied):
        await b.redeem(g.id, "sbx1")


@pytest.mark.asyncio
async def test_pg_broker_never_stores_secret_material():
    db = _FakeDB()
    b = _broker(db)
    g = await b.grant("sbx1", "openrouter", 60)
    # В строке гранта только scope — сам секрет в базе отсутствует.
    assert "sk-REAL-SECRET-DO-NOT-LEAK" not in str(db.rows[g.id])
    assert db.rows[g.id]["scope"] == "openrouter"


@pytest.mark.asyncio
async def test_pg_broker_binding_and_ttl():
    db = _FakeDB()
    b = _broker(db)
    g = await b.grant("sbx1", "openrouter", 60)
    with pytest.raises(errors.SecretDenied):
        await b.redeem(g.id, "other-sandbox")     # привязка
    g2 = await b.grant("sbx2", "openrouter", 0.01)
    time.sleep(0.02)
    with pytest.raises(errors.SecretDenied):
        await b.redeem(g2.id, "sbx2")             # TTL


@pytest.mark.asyncio
async def test_pg_broker_revoke_sandbox_and_scope_guard():
    db = _FakeDB()
    b = _broker(db)
    await b.grant("sbx1", "openrouter", 60)
    await b.grant("sbx1", "openrouter", 60)
    assert await b.revoke_sandbox("sbx1") == 2
    with pytest.raises(errors.SecretDenied):
        await b.grant("sbx1", "github_pat", 60)   # scope не разрешён


# ---------- выдача инструментов агенту ----------

def test_coder_agent_has_sandbox_tools_with_approval():
    """Инструменты выданы агенту coder, консеквентные — под подтверждением."""
    from bossman.agents import load_all
    a = load_all()["coder"]
    assert a.grant("sandbox.create").confirm is True
    assert a.grant("sandbox.run").confirm is True
    for n in ("sandbox.status", "sandbox.collect", "sandbox.destroy"):
        assert a.grant(n) is not None, n
    # строка «name: confirm» не должна утечь как имя инструмента
    assert a.grant("sandbox.create: confirm") is None


def test_agents_without_grant_cannot_use_sandbox():
    """Остальным агентам песочница не выдана — доступ не появляется сам собой."""
    from bossman.agents import load_all
    for name in ("analyst", "fresh-vibes"):
        a = load_all().get(name)
        if a is None:
            continue
        assert a.grant("sandbox.create") is None, name
