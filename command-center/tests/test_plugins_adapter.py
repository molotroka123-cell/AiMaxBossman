"""Targeted-тесты plugin-адаптеров: политика, безопасность, границы.

Проверяет, что адаптеры используют СУЩЕСТВУЮЩУЮ authority (реестр/политика/
anti-replay/секреты) и что закрыты слабые места из bundle-аудита.
"""
from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import httpx
import pytest

import bcc.features.plugins as P
from bcc.plugin_security import (
    PluginSecurityError,
    confine_path,
    redact,
    resolve_pinned_ip,
    safe_get,
    validate_url,
)
from bcc.tools import REGISTRY, decide_effect


@pytest.fixture(autouse=True)
async def registered():
    await P.setup(None)
    yield


def eff(name, agent=None):
    spec = REGISTRY.get(name)
    assert spec is not None, f"{name} не зарегистрирован"
    return decide_effect(spec, {}, agent or {})[0]


# ---------- регистрация / политика ----------

def test_capabilities_registered_into_existing_registry():
    assert len(P.REGISTERED) >= 20
    # это ИМЕННО существующий реестр (тот же объект, что у остальных фич)
    assert REGISTRY.get("plugin:http.get") is not None
    assert all(REGISTRY.get(n).source == "plugin" for n in P.REGISTERED)


def test_unknown_capability_is_denied_by_absence():
    # write-капабилити SQL намеренно не существует → resolve её не вернёт
    assert REGISTRY.get("plugin:sql.write") is None
    assert REGISTRY.resolve(["plugin:sql.write"]) == []


async def test_duplicate_capability_not_double_registered():
    before = len(P.REGISTERED)
    await P.setup(None)
    assert len(P.REGISTERED) == before      # повторный setup не удваивает


def test_read_is_auto_write_and_send_are_ask():
    assert eff("plugin:http.get") == "auto"
    assert eff("plugin:github.repo_read") == "auto"
    assert eff("plugin:gmail.search") == "auto"
    assert eff("plugin:gmail.send") == "ask"
    assert eff("plugin:calendar.create") == "ask"
    assert eff("plugin:drive.write") == "ask"
    assert eff("plugin:telegram.send") == "ask"
    assert eff("plugin:n8n.workflow_run") == "ask"
    assert eff("plugin:mcp.tool_call") == "ask"
    assert eff("plugin:openrouter.chat") == "ask"


def test_destructive_send_is_not_auto_replayable():
    # неидемпотентное не переигрывается автоматически (anti-replay движка)
    assert REGISTRY.get("plugin:gmail.send").idempotent is False
    assert REGISTRY.get("plugin:telegram.send").idempotent is False
    assert REGISTRY.get("plugin:mcp.tool_call").idempotent is False


def test_default_agent_gets_no_plugins():
    # без явной выдачи агенту plugin-инструменты недоступны (deny-by-default)
    assert REGISTRY.resolve(None) == []


def test_ollama_capability_declares_local_only():
    cap = next(c for c in P.MANIFEST if c.tool_name == "plugin:ollama.chat")
    assert cap.scope == "llm.local"
    assert "cloud_policy=never" in cap.description


def test_openrouter_capability_routes_through_cost_governor_authority():
    cap = next(c for c in P.MANIFEST if c.tool_name == "plugin:openrouter.chat")
    assert cap.risk == "ask"                 # облако → подтверждение
    assert "Cost Governor" in cap.description


# ---------- SQL read-only ----------

@pytest.mark.parametrize("sql", [
    "SELECT 1", "select * from t where x=1", "WITH a AS (SELECT 1) SELECT * FROM a",
    "PRAGMA table_info(t)", "  select id from users  ",
])
def test_sql_read_allowed(sql):
    assert P.sql_read_only_ok(sql) is True


@pytest.mark.parametrize("sql", [
    "INSERT INTO t VALUES (1)", "update t set x=1", "delete from t", "drop table t",
    "ALTER TABLE t ADD c int", "CREATE TABLE t(x)", "ATTACH DATABASE 'x' AS y",
    "PRAGMA journal_mode=WAL", "REPLACE INTO t VALUES(1)", "VACUUM",
    "SELECT 1; DROP TABLE t", "select 1;delete from t",
])
def test_sql_write_denied(sql):
    assert P.sql_read_only_ok(sql) is False


# ---------- SSRF ----------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://169.254.169.254/latest/meta-data",
    "http://localhost/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://[::1]/", "http://0.0.0.0/", "ftp://host/x", "http://u:p@host/x",
    "http://foo.local/",
])
def test_ssrf_literal_targets_blocked(url):
    with pytest.raises(PluginSecurityError):
        validate_url(url)


def test_ssrf_public_literal_allowed():
    u, host = validate_url("https://api.github.com/repos")
    assert host == "api.github.com"


def test_ssrf_dns_rebinding_blocked(monkeypatch):
    # имя, которое резолвится в приватный адрес, должно отсекаться на резолве
    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(PluginSecurityError):
        resolve_pinned_ip("evil-rebind.example")


async def test_safe_get_rejects_redirect_to_private(monkeypatch):
    # публичный хост, но redirect ведёт на приватный → отказ на следующем hop
    def fake_getaddrinfo(host, *a, **k):
        ip = "1.2.3.4" if host == "public.example" else "127.0.0.1"
        return [(socket.AF_INET, None, None, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request):
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://internal.local/secret"})
        return httpx.Response(200, text="SHOULD NOT REACH")

    real = httpx.AsyncClient

    def patched(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        k.pop("follow_redirects", None)
        return real(*a, follow_redirects=False, **{kk: vv for kk, vv in k.items() if kk != "follow_redirects"})

    monkeypatch.setattr(httpx, "AsyncClient", patched)
    with pytest.raises(PluginSecurityError):
        await safe_get("http://public.example/start")


async def test_safe_get_follows_safe_and_returns(monkeypatch):
    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, None, None, "", ("1.2.3.4", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    def handler(request):
        return httpx.Response(200, text="hello-public")

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler),
                                             follow_redirects=False,
                                             **{kk: vv for kk, vv in k.items() if kk != "follow_redirects"}))
    r = await safe_get("http://ok.example/x")
    assert r.status_code == 200 and "hello-public" in r.text


# ---------- path confinement ----------

def test_path_read_inside_root(tmp_path):
    (tmp_path / "note.md").write_text("hi", encoding="utf-8")
    p = confine_path(tmp_path, "note.md", must_exist=True)
    assert p.read_text() == "hi"


@pytest.mark.parametrize("bad", ["../escape", "../../etc/passwd", "/etc/passwd"])
def test_path_traversal_denied(tmp_path, bad):
    with pytest.raises(PluginSecurityError):
        confine_path(tmp_path, bad)


def test_symlink_escape_denied(tmp_path):
    outside = Path(tempfile.mkdtemp()) / "secret.txt"
    outside.write_text("S", encoding="utf-8")
    link = tmp_path / "link.md"
    os.symlink(outside, link)
    with pytest.raises(PluginSecurityError):
        confine_path(tmp_path, "link.md", must_exist=True)


# ---------- redaction ----------

def test_redaction_by_key_and_value():
    payload = {"authorization": "Bearer abc", "note": "leak TOKENVAL9 here",
               "nested": {"api_key": "k", "ok": "fine"}}
    out = redact(payload, secret_values={"TOKENVAL9"})
    assert out["authorization"] == "***REDACTED***"
    assert "TOKENVAL9" not in out["note"] and "***REDACTED***" in out["note"]
    assert out["nested"]["api_key"] == "***REDACTED***"
    assert out["nested"]["ok"] == "fine"


# ---------- credential gating (reject/skip → zero side effect) ----------

async def test_external_without_credential_skips_no_side_effect(monkeypatch):
    monkeypatch.delenv("GMAIL_OAUTH", raising=False)
    spec = REGISTRY.get("plugin:gmail.send")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c1", "step": 0})()
    res = await spec.handler({"to": "x", "subject": "s", "body": "b"}, ctx)
    assert res.error and "SKIP_EXTERNAL_CREDENTIAL" in res.content


async def test_http_get_blocks_ssrf_at_handler(monkeypatch):
    spec = REGISTRY.get("plugin:http.get")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c1", "step": 0})()
    res = await spec.handler({"url": "http://169.254.169.254/latest"}, ctx)
    assert res.error and "blocked" in res.one_line


# ---------- status endpoint is non-destructive ----------

async def test_status_endpoint_reports_no_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_shouldnotappear")
    out = await P.list_plugins()
    blob = repr(out)
    assert "ghp_shouldnotappear" not in blob        # сырой секрет не отдаётся
    gh = next(p for p in out["plugins"] if p["plugin"] == "github")
    assert gh["credential"] in {"configured", "missing"}


# ---------- real read-only SQL execution (POLISH: validation-only -> real) ----------

async def test_sql_read_executes_real_readonly_query(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "d.db"
    con = sqlite3.connect(db); con.execute("CREATE TABLE t(id int, name text)")
    con.execute("INSERT INTO t VALUES (1,'a'),(2,'b')"); con.commit(); con.close()
    monkeypatch.setenv("SQL_PLUGIN_DSN", f"sqlite:///{db}")
    spec = REGISTRY.get("plugin:sql.read")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"sql": "SELECT name FROM t ORDER BY id"}, ctx)
    assert not res.error and res.data["rows"] == [{"name": "a"}, {"name": "b"}]


async def test_sql_write_blocked_before_execution(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "d2.db"
    con = sqlite3.connect(db); con.execute("CREATE TABLE t(id int)"); con.commit(); con.close()
    monkeypatch.setenv("SQL_PLUGIN_DSN", f"sqlite:///{db}")
    spec = REGISTRY.get("plugin:sql.read")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"sql": "INSERT INTO t VALUES (9)"}, ctx)
    assert res.error and "read-only" in res.content
    # и на уровне БД mode=ro тоже запретил бы — данные не изменились
    con = sqlite3.connect(db); n = con.execute("SELECT count(*) FROM t").fetchone()[0]; con.close()
    assert n == 0


async def test_sql_no_dsn_is_skip(monkeypatch):
    monkeypatch.delenv("SQL_PLUGIN_DSN", raising=False)
    spec = REGISTRY.get("plugin:sql.read")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"sql": "SELECT 1"}, ctx)
    assert res.error and "SKIP_EXTERNAL_CREDENTIAL" in res.content


# ---------- real Obsidian write execution (POLISH: local authority reuse) ----------

async def test_obsidian_write_executes_and_confines(tmp_path, monkeypatch):
    vault = tmp_path / "vault"; vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    spec = REGISTRY.get("plugin:obsidian.write")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"path": "notes/x.md", "content": "hello"}, ctx)
    assert not res.error
    assert (vault / "notes" / "x.md").read_text("utf-8") == "hello"


async def test_obsidian_write_blocks_escape(tmp_path, monkeypatch):
    vault = tmp_path / "vault"; vault.mkdir()
    monkeypatch.setenv("OBSIDIAN_VAULT", str(vault))
    spec = REGISTRY.get("plugin:obsidian.write")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"path": "../escape.md", "content": "x"}, ctx)
    assert res.error and "blocked" in res.one_line
    assert not (tmp_path / "escape.md").exists()


async def test_obsidian_write_no_cred_is_skip(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    spec = REGISTRY.get("plugin:obsidian.write")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"path": "x.md", "content": "y"}, ctx)
    assert res.error and "SKIP_EXTERNAL_CREDENTIAL" in res.content
