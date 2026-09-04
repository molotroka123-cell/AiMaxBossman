"""RC-хардненинг plugin security (Lane-1): детерминированные regression.

SQL_GATE_CTE_*        — data-modifying CTE запрещён на уровне application gate.
SQL_GATE_LITERAL_*    — строковые литералы не триггерят false-positives.
SQL_DRIVER_READONLY_* — драйверный бэкстоп mode=ro.
DNS_REBIND_PINNED     — коннект идёт на проверенный IP (второго резолва нет).
SSRF_*                — private/redirect/private-redirect deny.
HTTP_MAX_BYTES_*      — bounded streaming, abort без полной аллокации тела.
REDACT_*              — известные секретные значения не проходят в outputs.
"""
from __future__ import annotations

import asyncio
import inspect
import socket
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

import bcc.features.plugins as P
from bcc.plugin_security import (
    PluginSecurityError,
    PinnedTransport,
    resolve_pinned_ip,
    safe_get,
)


# ---------------------------------------------------------------- SQL gate

@pytest.mark.parametrize("sql", [
    "WITH a AS (SELECT 1) DELETE FROM t",
    "WITH a AS (SELECT 1) INSERT INTO t VALUES (1)",
    "WITH a AS (SELECT 1) UPDATE t SET x=1",
])
def test_sql_gate_cte_write_denied(sql):
    assert P.sql_read_only_ok(sql) is False


def test_sql_gate_literal_false_positive():
    assert P.sql_read_only_ok("SELECT * FROM t WHERE x = 'delete from u'") is True
    assert P.sql_read_only_ok('SELECT "update" AS k FROM t') is True


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1", True),
    ("WITH a AS (SELECT 1) SELECT * FROM a", True),
    ("PRAGMA table_info(t)", True),
    ("PRAGMA index_list(t)", True),
    ("SELECT 1; DROP TABLE t", False),
    ("select 1;delete from t", False),
    ("PRAGMA journal_mode=WAL", False),
    ("PRAGMA main.table_info(t)", False),   # fail-closed: только непоименованные RO-формы
    ("VACUUM", False),
    ("", False),
    (None, False),
])
def test_sql_gate_matrix(sql, expected):
    assert P.sql_read_only_ok(sql) is expected


def test_sql_driver_readonly_backstop(tmp_path):
    db = tmp_path / "d.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(id int)")
    con.commit()
    con.close()
    with pytest.raises(sqlite3.OperationalError) as exc:
        P._run_sqlite_read(str(db), "DELETE FROM t", None, 10)
    assert "readonly" in str(exc.value).lower()


# ---------------------------------------------------------------- DNS / SSRF

def test_ssrf_private_ip_denied(monkeypatch):
    def fake(host, *a, **k):
        return [(socket.AF_INET, 1, 6, "", ("10.0.0.9", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(PluginSecurityError):
        resolve_pinned_ip("internal.example")


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _start_local_server():
    class H(_Quiet):
        def do_GET(self):
            body = b"pinned-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def test_dns_rebind_pinned_connect(monkeypatch):
    """CASE A: первый resolve валиден, второй — запрещён. После фикса hostname
    резолвится РОВНО один раз: TCP коннект идёт на проверенный IP."""
    srv = _start_local_server()
    calls = {"n": 0}
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host == "rebind.test":
            calls["n"] += 1
            if calls["n"] > 1:
                raise socket.gaierror(8, "rebind: second resolve must not happen")
            return [(socket.AF_INET, 1, 6, "", ("127.0.0.1", 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    try:
        r = await safe_get(f"http://rebind.test:{srv.server_address[1]}/x",
                           allow_private=True, timeout=10)
        assert r.status_code == 200 and r.text == "pinned-ok"
    finally:
        srv.shutdown()
    assert calls["n"] == 1


def test_tls_verify_preserved():
    """CASE D: pinning не отключает TLS-проверку — ни verify=False, ни кастомного
    отключённого контекста в коде нет."""
    src = inspect.getsource(inspect.getmodule(PinnedTransport))
    assert "verify=False" not in src
    assert "check_hostname=False" not in src
    t = PinnedTransport({"h": "127.0.0.1"})
    assert t._pool is not None


# ---------------------------------------------------------------- max_bytes

def _mock_client_factory(monkeypatch, body: bytes, state: dict):
    def handler(request):
        state["served"] = True
        return httpx.Response(200, content=body)

    real = httpx.AsyncClient

    class TrackingTransport(httpx.MockTransport):
        async def aclose(self):
            state["closed"] = True
            return await super().aclose()

    def factory(*a, **k):
        return real(*a, transport=TrackingTransport(handler), follow_redirects=False)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_http_max_bytes_matrix(monkeypatch):
    body = b"x" * 3000
    state = {"served": False, "closed": False}
    _mock_client_factory(monkeypatch, body, state)

    def fake_dns(host, *a, **k):
        return [(socket.AF_INET, 1, 6, "", ("1.2.3.4", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    # HTTP_SMALL_BODY
    r = await safe_get("http://ok.example/small", max_bytes=5000)
    assert r.status_code == 200 and r.content == body
    # HTTP_MAX_BYTES_EXACT
    r = await safe_get("http://ok.example/exact", max_bytes=3000)
    assert r.content == body
    # HTTP_MAX_BYTES_EXCEEDED — abort без полной аллокации тела
    with pytest.raises(PluginSecurityError) as exc:
        await safe_get("http://ok.example/big", max_bytes=1000)
    assert "max_bytes" in str(exc.value)
    # HTTP_RESPONSE_CLOSED_ON_LIMIT — клиент закрыт после abort
    assert state["closed"] is True


# ---------------------------------------------------------------- redaction

@pytest.fixture
async def plugin_tools():
    """Регистрация инструментов плагинов — своя, а не чужая.

    Оба теста ниже берут спеку из общего реестра. Регистрацию делает setup()
    фичи plugins, и раньше эти тесты работали только потому, что setup успел
    отработать в СОСЕДНЕМ тестовом модуле. Файл, запущенный отдельно, падал с
    AttributeError на None вместо спеки — то есть проверка секретов молча
    зависела от порядка запуска.
    """
    await P.setup(None)
    return P.REGISTRY


async def test_redact_outputs_no_secret_values(monkeypatch, plugin_tools):
    secret = "ghp_LANE1TESTSECRET"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    state = {"served": False, "closed": False}
    _mock_client_factory(monkeypatch, b"page body with ghp_LANE1TESTSECRET inside", state)

    def fake_dns(host, *a, **k):
        return [(socket.AF_INET, 1, 6, "", ("1.2.3.4", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    spec = P.REGISTRY.get("plugin:http.get")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"url": "http://ok.example/leak"}, ctx)
    blob = repr({"content": res.content, "one_line": res.one_line, "data": res.data})
    assert secret not in blob
    assert "***REDACTED***" in res.content          # REDACT_CONTENT
    assert secret not in res.one_line               # REDACT_ONE_LINE
    assert secret not in repr(res.data)             # REDACT_DATA


async def test_redact_error_path(monkeypatch, plugin_tools):
    """Error-путь generic-коннектора не содержит значение креда (только имя)."""
    secret = "sk-lane1-error-secret"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    spec = P.REGISTRY.get("plugin:openrouter.chat")
    ctx = type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                         "workspace": "", "call_id": "c", "step": 0})()
    res = await spec.handler({"model": "m", "messages": []}, ctx)
    blob = repr({"content": res.content, "one_line": res.one_line, "data": res.data})
    assert secret not in blob                       # REDACT_ERROR_PATH


async def test_dns_rebind_pinned_connect_survives_a_trailing_dot(monkeypatch):
    """Точка в конце имени отключала всю защиту от rebinding'а.

    validate_url снимает завершающую точку и пинит «rebind.test», а httpx
    передаёт транспорту «rebind.test.» — ключ не совпадает, и промах означал
    соединение ПО ИМЕНИ, то есть второй резолв уже мимо проверки. Ровно то
    окно, ради закрытия которого pinned-транспорт и написан: проверили один
    адрес, пошли по другому. Достаточно было приписать точку.
    """
    srv = _start_local_server()
    calls = {"n": 0}
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host in ("rebind.test", "rebind.test."):
            calls["n"] += 1
            if calls["n"] > 1:
                raise socket.gaierror(8, "второй резолв не имеет права случиться")
            return [(socket.AF_INET, 1, 6, "", ("127.0.0.1", 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    try:
        r = await safe_get(f"http://rebind.test.:{srv.server_address[1]}/x",
                           allow_private=True, timeout=10)
        assert r.status_code == 200 and r.text == "pinned-ok"
    finally:
        srv.shutdown()
    assert calls["n"] == 1, "имя резолвилось дважды — pinning обошли завершающей точкой"


async def test_a_host_that_was_never_pinned_is_refused_not_resolved():
    """Промах ключа обязан быть отказом, а не «соединимся по имени».

    Тихий откат на резолв по имени — это fail-open в защите, которая
    существует ровно для того, чтобы имя между проверкой и коннектом больше
    не участвовало. Любая форма записи хоста, до которой мы не додумались,
    должна ломать запрос, а не защиту.
    """
    from bcc.plugin_security import _PinnedBackend

    backend = _PinnedBackend({"known.test": "127.0.0.1"})
    with pytest.raises(PluginSecurityError):
        await backend.connect_tcp("unknown.test", 80)
