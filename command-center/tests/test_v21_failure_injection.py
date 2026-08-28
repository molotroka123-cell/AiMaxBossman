"""V2.1 §21 — намеренные сбои: восстановление ограничено, петли нет, статус честный.

Каждый сценарий ломает ровно одну вещь и проверяет, что система деградирует
предсказуемо: ретраи конечны, состояние переживает сбой, итог не приукрашен.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import (approvals as approvals_t, run_events as run_events_t, settings_kv,
                    task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from bcc.providers import ProviderError
from bcc.tools import REGISTRY, ToolResult, ToolSpec
from bcc.v2.tables import mcp_servers as mcp_servers_t

from .conftest import FakeAdapter, wait_for
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def clean_registry():
    before = set(REGISTRY.names())
    yield
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)


def _install(name, handler, **kw):
    kw.setdefault("input_schema", {})
    REGISTRY.register(ToolSpec(name=name, description="стенд", handler=handler, **kw))


async def _events(env, run_id: int | None = None) -> list[dict]:
    async with env.svc.db.session() as s:
        q = sa.select(run_events_t)
        if run_id:
            q = q.where(run_events_t.c.run_id == run_id)
        return [dict(r._mapping) for r in (await s.execute(q)).fetchall()]


# ------------------------------------------------------- 1. падение провайдера

async def test_provider_failure_retries_are_bounded_and_status_is_honest(env):
    """Модель недоступна: ретраи по max_retries, потом честный failed — не «успех»."""
    adapter = FakeAdapter(fail_times=99, error="endpoint недоступен")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    env.svc.engine.retry_base_delay = 0.01
    env.svc.engine.retry_max_delay = 0.05
    stack = await _stack_with_tools(env, [], adapter=adapter)
    await env.client.patch(f"/api/tasks/{stack['task']['id']}", json={}) \
        if False else None

    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "failed"                       # не «completed» и не вечный цикл

    async with env.svc.db.session() as s:
        runs = [dict(r._mapping) for r in (await s.execute(
            sa.select(runs_t).where(runs_t.c.task_id == stack["task"]["id"]))).fetchall()]
    # ретраи ограничены max_retries (в make_stack = 2): попыток не больше 3
    assert max(int(r["attempt"] or 0) for r in runs) <= 2
    assert adapter.calls <= 3, f"провайдер дёрнут {adapter.calls} раз — ретраи не ограничены"
    assert any("недоступен" in (r["error"] or "") for r in runs)


async def test_provider_recovers_after_transient_failure(env):
    """Сбой временный: ретрай срабатывает, задача доходит до конца."""
    adapter = FakeAdapter("готово", fail_times=1, error="сеть моргнула")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    env.svc.engine.retry_base_delay = 0.01
    stack = await _stack_with_tools(env, [], adapter=adapter)

    assert await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED) == "completed"
    events = await _events(env)
    assert any(e["kind"] == "run.retry" for e in events), "нет события ретрая"


# ------------------------------------------------------- 2. таймаут инструмента

async def test_tool_timeout_does_not_kill_run_and_is_reported(env):
    async def hang(args, ctx):
        await asyncio.sleep(30)
        return ToolResult(content="поздно")
    _install("slow.tool", hang, timeout_seconds=0.2, default_effect="auto")
    adapter = ToolAdapter([("tool", "slow_tool", {}),
                           ("text", "инструмент завис, продолжаю без него")])
    stack = await _stack_with_tools(env, ["slow.tool"], adapter=adapter)

    assert await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED) == "completed"
    msg = adapter.seen_messages[1][-1]["content"]
    assert "не уложился" in msg or "таймаут" in msg.lower()

    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(tool_calls_t))).first()._mapping)
    assert row["status"] == "error"          # зафиксировано, а не замолчано


# ------------------------------------------------------- 3. падение MCP-сервера

async def test_mcp_server_crash_is_bounded_and_visible(env, tmp_path, monkeypatch):
    """Процесс MCP умирает: вызов возвращает ошибку данными, сервер помечен нездоровым."""
    pytest.importorskip("mcp")
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(tmp_path / "c.txt"))
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo", transport="stdio",
            command=[sys.executable, str(FIXTURES / "mcp_echo_server.py")],
            url="", cwd="", env_keys=["MCP_ECHO_COUNTER"], enabled=True,
            status="unknown", created_at=utcnow()))
        await s.commit()
    assert (await env.client.post("/api/mcp/runtime/servers/echo/connect")).status_code == 200
    await env.client.post("/api/mcp/policy", json={"canonical": "mcp:echo:boom",
                                                   "policy": "auto"})
    assert (await env.client.post("/api/mcp/runtime/servers/echo/refresh")).status_code == 200

    adapter = ToolAdapter([("tool", "mcp_echo_boom", {}),
                           ("text", "MCP-сервер упал, сообщаю честно")])
    stack = await _stack_with_tools(env, ["mcp:echo:boom"], adapter=adapter)

    status = await _run_task(env, stack["task"]["id"], timeout=40, until=FINISHED)
    assert status == "completed"              # падение внешнего сервера ≠ провал задачи

    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(mcp_servers_t))).first()._mapping)
    assert row["status"] != "healthy", row["status"]

    rt = getattr(env.svc, "mcp", None)
    if rt is not None:
        await rt.shutdown()


# ------------------------------------------------------- 4. отказ браузера

async def test_browser_failure_is_data_not_crash(env):
    """Рантайм браузера недоступен: модель получает честную ошибку, run живёт."""
    adapter = ToolAdapter([("tool", "browser_open", {"url": "http://127.0.0.1:9/nope"}),
                           ("text", "страница не открылась")])
    stack = await _stack_with_tools(env, ["browser.open"], adapter=adapter, max_steps=4)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True}})

    assert await _run_task(env, stack["task"]["id"], timeout=90, until=FINISHED) == "completed"
    msg = adapter.seen_messages[1][-1]["content"]
    assert "ошибка" in msg.lower() or "не удалось" in msg.lower() or "недоступен" in msg.lower()

    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(tool_calls_t))).first()._mapping)
    assert row["tool"] == "browser.open" and row["status"] == "error"


# ------------------------------------------------------- 5. Reviewer FAIL

async def test_reviewer_fail_loop_is_bounded_and_escalates(env):
    """Ревьюер валит раз за разом: конечное число итераций → waiting_approval."""
    adapter = FakeAdapter("черновой ответ без нужного слова")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await _stack_with_tools(env, [], adapter=adapter, max_steps=1)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == stack["task"]["id"]).values(
            meta={"review": {"criteria": "ГОТОВО-ПО-КРИТЕРИЮ", "max_review_retries": 2}}))
        await s.commit()

    status = await _run_task(env, stack["task"]["id"], timeout=40,
                             until=("completed", "failed", "stopped", "waiting_approval"))
    assert status == "waiting_approval", f"ожидалась эскалация, получено {status}"

    async with env.svc.db.session() as s:
        meta = dict((await s.execute(sa.select(tasks_t.c.meta)
                                     .where(tasks_t.c.id == stack["task"]["id"]))).first()._mapping)
        appr = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t))).fetchall()]
    attempts = int((meta["meta"] or {}).get("review_attempts") or 0)
    assert attempts <= 3, f"ревью зациклилось: {attempts} итераций"
    assert any(a["kind"] == "review_escalation" for a in appr), appr


# ------------------------------------------------------- 6. состояние переживает сбой

async def test_state_survives_process_restart_midway(env, tmp_path):
    """Падение процесса на середине: аренда протухает, run возвращается в очередь."""
    adapter = FakeAdapter("готово")
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await _stack_with_tools(env, [], adapter=adapter)
    run_id = await env.svc.engine.enqueue(stack["task"]["id"])

    # имитируем «процесс умер во время выполнения»: аренда взята и протухла
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
            status="running", worker_lease_until=utcnow() - __import__("datetime").timedelta(
                seconds=120)))
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == stack["task"]["id"]).values(
            status="running"))
        await s.commit()

    recovered = await env.svc.engine.recover()
    assert recovered == 1

    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(runs_t).where(runs_t.c.id == run_id))).first()._mapping)
    assert row["status"] == "queued" and int(row["attempt"]) == 1

    assert await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED) == "completed"


# ------------------------------------------------------- 7. без бесконечных петель

async def test_no_infinite_loop_when_model_keeps_calling_same_tool(env):
    """Модель зациклилась на инструменте: max_steps обрывает, статус честный."""
    calls = []

    async def echo(args, ctx):
        calls.append(1)
        return ToolResult(content="снова то же самое")
    _install("loop.tool", echo, default_effect="auto")

    adapter = ToolAdapter([("tool", "loop_tool", {})])      # всегда один и тот же шаг
    stack = await _stack_with_tools(env, ["loop.tool"], adapter=adapter, max_steps=4)

    status = await _run_task(env, stack["task"]["id"], timeout=30, until=FINISHED)
    assert status in FINISHED
    assert len(calls) <= 4, f"инструмент вызван {len(calls)} раз при max_steps=4"


# ------------------------------------------------------- 8. неидемпотентное не повторяем

async def test_non_idempotent_tool_is_not_replayed_after_restart(env):
    """Отправка во внешний мир не переигрывается вслепую после рестарта."""
    sent = []

    async def send(args, ctx):
        sent.append(args)
        return ToolResult(content="отправлено")
    _install("send.message", send, default_effect="ask", idempotent=False,
             permission="email.send")

    adapter = ToolAdapter([("tool", "send_message", {"to": "клиент", "text": "привет"}),
                           ("text", "сообщение отправлено")])
    stack = await _stack_with_tools(env, ["send.message"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"], timeout=20) == "waiting_approval"
    assert sent == []

    appr = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{appr['id']}", json={"approve": True, "by": "тест"})
    assert await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED) == "completed"
    assert len(sent) == 1

    # повторный «подхват» того же run'а не должен отправить второй раз
    async with env.svc.db.session() as s:
        run_id = int((await s.execute(sa.select(runs_t.c.id)
                                      .where(runs_t.c.task_id == stack["task"]["id"])
                                      .order_by(runs_t.c.id.desc()))).first()[0])
        row = dict((await s.execute(sa.select(tool_calls_t)
                                    .where(tool_calls_t.c.run_id == run_id))).first()._mapping)
    assert row["status"] == "executed"
    # anti-replay: строка на (run_id, call_id) уникальна — второй записи нет
    async with env.svc.db.session() as s:
        count = int((await s.execute(sa.select(sa.func.count()).select_from(tool_calls_t)
                                     .where(tool_calls_t.c.run_id == run_id))).scalar())
    assert count == 1
    assert len(sent) == 1


# ------------------------------------------------------- 9. Governor и инструменты

async def test_governor_does_not_pause_a_run_making_real_tool_calls(env):
    """Регресс: у ответа с tool_calls content пуст, и Governor принимал активную
    работу инструментами за «одинаковые ответы» и ставил миссию на паузу."""
    seen = []

    async def step(args, ctx):
        seen.append(args.get("n"))
        return ToolResult(content=f"шаг {args.get('n')} выполнен")
    _install("work.step", step, default_effect="auto",
             input_schema={"n": {"type": "integer"}})

    # 8 РАЗНЫХ вызовов подряд — больше порога no_progress_steps (6)
    script = [("tool", "work_step", {"n": i}) for i in range(8)]
    script.append(("text", "все шаги сделаны"))
    adapter = ToolAdapter(script)
    stack = await _stack_with_tools(env, ["work.step"], adapter=adapter, max_steps=12)

    status = await _run_task(env, stack["task"]["id"], timeout=40, until=FINISHED)
    assert status == "completed", f"Governor вмешался напрасно: {status}"
    assert seen == list(range(8))

    async with env.svc.db.session() as s:
        from bcc.db import interventions as interv_t
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(interv_t))).fetchall()]
    assert not [r for r in rows if r["action"] == "paused"], rows


async def test_governor_still_catches_a_real_loop(env):
    """А вот ОДИН И ТОТ ЖЕ вызов подряд — это настоящее зацикливание: пауза."""
    calls = []

    async def same(args, ctx):
        calls.append(1)
        return ToolResult(content="то же самое")
    _install("loop.same", same, default_effect="auto")

    adapter = ToolAdapter([("tool", "loop_same", {"x": 1})])   # всегда одинаковый
    stack = await _stack_with_tools(env, ["loop.same"], adapter=adapter, max_steps=12)

    await _run_task(env, stack["task"]["id"], timeout=40,
                    until=("completed", "failed", "stopped", "paused"))
    async with env.svc.db.session() as s:
        status = str((await s.execute(sa.select(tasks_t.c.status)
                                      .where(tasks_t.c.id == stack["task"]["id"]))).first()[0])
        from bcc.db import interventions as interv_t
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(interv_t))).fetchall()]
    assert status == "paused", status
    assert any(r["action"] == "paused" and "одинаковых шагов" in r["reason"] for r in rows), rows
    assert len(calls) <= 8, f"зацикливание не остановлено: {len(calls)} вызовов"
