"""V2.1 фаза D — MCP реально исполняет протокол и работает как инструмент модели.

Никаких моков протокола: поднимается НАСТОЯЩИЙ MCP-сервер на официальном SDK
(`tests/fixtures/mcp_echo_server.py`, транспорт stdio, отдельный процесс).
Проверяется цепочка целиком: connect → discovery → persist → реестр → tool-loop
движка → права (AUTO/ASK/DENY) → падение сервера.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import tool_calls as tool_calls_t, utcnow
from bcc.features.tools_mcp import unregister_server_tools
from bcc.tools import REGISTRY
from bcc.v2.mcp_runtime import sdk_available
from bcc.v2.tables import mcp_servers as mcp_servers_t, mcp_tools as mcp_tools_t

from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"
SERVER = "echo"

pytestmark = pytest.mark.skipif(not sdk_available(),
                                reason="официальный MCP SDK (pip install mcp) не установлен")


# ---------------------------------------------------------------- обвязка

@pytest.fixture(autouse=True)
def clean_registry():
    before = set(REGISTRY.names())
    yield
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)
    unregister_server_tools(SERVER)


@pytest.fixture
def counter(tmp_path, monkeypatch):
    """Файл-счётчик вызовов: пишет сам процесс сервера, читает тест."""
    path = tmp_path / "mcp-calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(path))
    return path


def calls_of(counter: Path, tool: str) -> int:
    if not counter.exists():
        return 0
    return sum(1 for line in counter.read_text(encoding="utf-8").splitlines()
               if line.strip() == tool)


async def add_server(env, name: str = SERVER) -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(mcp_servers_t).values(
            name=name, transport="stdio",
            command=[sys.executable, str(FIXTURE)], url="", cwd="",
            env_keys=["MCP_ECHO_COUNTER"], enabled=True, status="unknown",
            created_at=utcnow()))
        await s.commit()
        return int(res.inserted_primary_key[0])


@pytest.fixture
async def mcp(env, counter):
    """Сервер зарегистрирован; после теста соединение закрывается."""
    sid = await add_server(env)
    yield sid
    rt = getattr(env.svc, "mcp", None)
    if rt is not None:
        await rt.shutdown()


async def set_policy(env, canonical: str, decision: str):
    r = await env.client.post("/api/mcp/policy",
                              json={"canonical": canonical, "policy": decision})
    assert r.status_code == 200, r.text


async def connect_and_discover(env) -> list[dict]:
    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/connect")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "healthy"
    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/refresh")
    assert r.status_code == 200, r.text
    return r.json()["tools"]


# ---------------------------------------------------------------- 1. связь

async def test_server_starts_and_connects(env, mcp):
    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "healthy" and body["connected"] is True

    health = (await env.client.get(f"/api/mcp/runtime/servers/{SERVER}/health")).json()
    assert health["status"] == "healthy"
    assert health["tools"] >= 4          # echo/write_note/secret/boom

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(mcp_servers_t.c.status))).first()
    assert row[0] == "healthy"

    status = (await env.client.get("/api/mcp/runtime")).json()
    assert status["sdk"] == "mcp" and status["sdk_available"] is True
    assert status["sdk_version"]


async def test_disconnect_stops_server(env, mcp):
    await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/connect")
    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/disconnect")
    assert r.status_code == 200 and r.json()["status"] == "stopped"
    # после отключения ручной вызов не проходит молча — честная ошибка
    r = await env.client.get(f"/api/mcp/runtime/servers/{SERVER}/tools")
    assert r.status_code == 503


# ---------------------------------------------------------------- 2. discovery

async def test_tools_discovered_and_persisted(env, mcp):
    tools = await connect_and_discover(env)
    names = {t["tool"] for t in tools}
    assert {"echo", "write_note", "secret", "boom"} <= names

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(mcp_tools_t))).fetchall()
    persisted = {r._mapping["name"]: dict(r._mapping) for r in rows}
    assert {"echo", "write_note", "secret", "boom"} <= set(persisted)
    echo = persisted["echo"]
    assert echo["description"]
    assert echo["input_schema"]["properties"]["text"]["type"] == "string"

    # канонические имена и наличие в общем реестре инструментов
    assert REGISTRY.get("mcp:echo:echo") is not None
    spec = REGISTRY.get("mcp:echo:echo")
    assert spec.source == "mcp" and spec.external_output is True
    assert spec.api_name == "mcp_echo_echo"
    assert spec.input_schema["text"]["type"] == "string"
    assert spec.required == ["text"]

    # ресурсы/промпты сервер тоже отдаёт (у фикстуры они пустые)
    assert (await env.client.get(f"/api/mcp/runtime/servers/{SERVER}/resources")).json() == []
    assert (await env.client.get(f"/api/mcp/runtime/servers/{SERVER}/prompts")).json() == []


async def test_manual_call_returns_real_result(env, mcp, counter):
    await connect_and_discover(env)
    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/call",
                              json={"tool": "echo", "arguments": {"text": "привет"}})
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "эхо: привет"
    assert calls_of(counter, "echo") == 1


async def test_restore_registry_survives_restart(env, mcp):
    """После рестарта процесса инструменты в реестре есть без запуска серверов."""
    await connect_and_discover(env)
    unregister_server_tools(SERVER)
    assert REGISTRY.get("mcp:echo:echo") is None

    from bcc.features.tools_mcp import restore_registry
    total = await restore_registry(env.svc)
    assert total >= 4
    assert REGISTRY.get("mcp:echo:echo") is not None


# ---------------------------------------------------------------- 3–4. контекст

async def test_unassigned_mcp_tool_never_reaches_model(env, mcp):
    """Главное требование по контексту: модель видит ТОЛЬКО выданное."""
    await connect_and_discover(env)
    await set_policy(env, "mcp:echo:echo", "auto")
    await connect_and_discover(env)          # перерегистрация с новой политикой

    # оба инструмента ЕСТЬ в каталоге-реестре — фильтрует именно выдача агенту
    assert REGISTRY.get("mcp:echo:secret") is not None
    assert REGISTRY.get("mcp:echo:echo") is not None

    adapter = ToolAdapter([("text", "ничего вызывать не нужно")])
    stack = await _stack_with_tools(env, ["mcp:echo:echo"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"

    schemas = adapter.seen_tools[0]
    assert schemas is not None
    names = [s["function"]["name"] for s in schemas]
    assert names == ["mcp_echo_echo"]                       # выданный — на месте
    assert "mcp_echo_secret" not in names                   # невыданный — отсутствует
    assert "mcp_echo_boom" not in names
    # и в самих схемах нет описания секретного инструмента
    assert "секрет" not in str(schemas).lower()


def test_registry_glob_selects_one_server():
    """`mcp:<server>:*` выбирает инструменты одного сервера (без запуска процессов)."""
    from bcc.tools import ToolSpec

    async def noop(args, ctx):
        return None
    for name in ("mcp:echo:echo", "mcp:echo:secret", "mcp:other:echo"):
        REGISTRY.register(ToolSpec(name=name, description="", handler=noop))
    assert [t.name for t in REGISTRY.resolve(["mcp:echo:*"])] == \
        ["mcp:echo:echo", "mcp:echo:secret"]
    assert [t.name for t in REGISTRY.resolve(["mcp:echo:echo"])] == ["mcp:echo:echo"]
    assert REGISTRY.resolve(None) == []


# ---------------------------------------------------------------- 5. AUTO

async def test_auto_mcp_tool_executes_and_returns_real_result(env, mcp, counter):
    await set_policy(env, "mcp:echo:echo", "auto")
    await connect_and_discover(env)

    adapter = ToolAdapter([("tool", "mcp_echo_echo", {"text": "привет"}),
                           ("text", "MCP ответил, задача решена")])
    stack = await _stack_with_tools(env, ["mcp:echo:echo"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"

    assert calls_of(counter, "echo") == 1                   # сервер реально вызван
    tool_msg = adapter.seen_messages[1][-1]
    assert tool_msg["role"] == "tool"
    assert "эхо: привет" in tool_msg["content"]
    # вывод MCP — внешние данные, а не команды
    assert tool_msg["content"].startswith("Ниже — внешние данные")

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t))).fetchall()
    rec = dict(rows[0]._mapping)
    assert rec["tool"] == "mcp:echo:echo"
    assert rec["status"] == "executed" and rec["effect"] == "auto"


# ---------------------------------------------------------------- 6–7. ASK

async def test_ask_mcp_tool_creates_approval(env, mcp, counter):
    await connect_and_discover(env)          # write_note без политики → ask
    assert REGISTRY.get("mcp:echo:write_note").default_effect == "ask"

    adapter = ToolAdapter([("tool", "mcp_echo_write_note", {"text": "заметка"}),
                           ("text", "готово")])
    stack = await _stack_with_tools(env, ["mcp:echo:write_note"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "waiting_approval"
    assert calls_of(counter, "write_note") == 0             # без человека не исполняем

    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and appr[0]["kind"] == "tool"
    assert "mcp:echo:write_note" in appr[0]["preview"]
    assert "заметка" in appr[0]["preview"]

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t))).first()
    rec = dict(row._mapping)
    assert rec["status"] == "pending_approval" and rec["effect"] == "ask"
    assert rec["approval_id"] == appr[0]["id"]


async def test_approved_mcp_tool_executes_exactly_once(env, mcp, counter):
    await connect_and_discover(env)
    adapter = ToolAdapter([("tool", "mcp_echo_write_note", {"text": "заметка"}),
                           ("text", "записано")])
    stack = await _stack_with_tools(env, ["mcp:echo:write_note"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "waiting_approval"

    appr = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{appr['id']}",
                          json={"approve": True, "by": "оператор"})
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "completed"

    assert calls_of(counter, "write_note") == 1             # РОВНО один раз
    assert "записано #1: заметка" in adapter.seen_messages[1][-1]["content"]

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t))).fetchall()
    assert len(rows) == 1
    rec = dict(rows[0]._mapping)
    assert rec["status"] == "executed" and rec["approved_by"] == "оператор"


# ---------------------------------------------------------------- 8. DENY

async def test_denied_mcp_tool_never_reaches_server(env, mcp, counter):
    await set_policy(env, "mcp:echo:write_note", "auto")   # политика хаба разрешает…
    await connect_and_discover(env)

    adapter = ToolAdapter([("tool", "mcp_echo_write_note", {"text": "нельзя"}),
                           ("text", "понял, запрещено")])
    stack = await _stack_with_tools(env, ["mcp:echo:write_note"], adapter=adapter)
    # …а правило агента запрещает: канонический слой прав побеждает
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        "permissions": {"tool_rules": [
            {"tool": "mcp:echo:*", "effect": "deny", "reason": "MCP-запись запрещена"}]}})

    assert await _run_task(env, stack["task"]["id"]) == "completed"
    assert calls_of(counter, "write_note") == 0
    assert "запрещено политикой" in adapter.seen_messages[1][-1]["content"]

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t))).first()
    assert dict(row._mapping)["status"] == "denied"


# ---------------------------------------------------------------- 9. падение

async def test_server_crash_marks_unhealthy_and_emits_event(env, mcp):
    await connect_and_discover(env)

    r = await env.client.post(f"/api/mcp/runtime/servers/{SERVER}/call",
                              json={"tool": "boom", "arguments": {}})
    assert r.status_code == 502                     # честная ошибка, не 500

    health = (await env.client.get(f"/api/mcp/runtime/servers/{SERVER}/health")).json()
    assert health["status"] == "unhealthy"
    assert health["connected"] is False

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(mcp_servers_t.c.status,
                                         mcp_servers_t.c.status_detail))).first()
    assert row[0] == "unhealthy" and row[1]

    kinds = [e["kind"] for e in await env.svc.bus.recent(50)]
    assert "mcp.unhealthy" in kinds                 # видно в ленте активности
    assert "mcp.call_failed" in kinds               # сигнал Governor/Self-Healing


async def test_crashed_server_reports_error_to_model_not_run_failure(env, mcp):
    """Падение сервера в середине run'а — данные для модели, run не падает."""
    await set_policy(env, "mcp:echo:boom", "auto")
    await connect_and_discover(env)

    adapter = ToolAdapter([("tool", "mcp_echo_boom", {}),
                           ("text", "инструмент MCP упал, сообщаю")])
    stack = await _stack_with_tools(env, ["mcp:echo:boom"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"

    content = adapter.seen_messages[1][-1]["content"]
    assert "ошибка MCP" in content or "недоступен" in content

    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tool_calls_t))).first()
    assert dict(row._mapping)["status"] == "error"

    assert env.svc.mcp.health(SERVER).status == "unhealthy"


async def test_missing_server_is_honest_404(env):
    r = await env.client.post("/api/mcp/runtime/servers/no-such/connect")
    assert r.status_code == 404


async def test_broken_command_reports_unhealthy(env):
    """Сервер, который не запускается, не роняет приложение — 503 + событие."""
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="broken", transport="stdio",
            command=[sys.executable, str(Path(os.devnull))], url="", cwd="",
            env_keys=[], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()
    r = await env.client.post("/api/mcp/runtime/servers/broken/connect")
    assert r.status_code == 503
    kinds = [e["kind"] for e in await env.svc.bus.recent(50)]
    assert "mcp.unhealthy" in kinds
    rt = getattr(env.svc, "mcp", None)
    if rt is not None:
        await rt.shutdown()
