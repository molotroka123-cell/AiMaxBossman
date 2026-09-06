"""PHASE 2 / PHASE 12 — approval · resume · crash matrix.

Инвариант, который здесь доказывается по пунктам:

  авторизация и идентичность перепроверяются В МОМЕНТ ЭФФЕКТА, одобрение
  привязано к ровно тому действию, которое одобрили, проверка исхода идёт
  ПОСЛЕ эффекта, и ни один эффект не исполняется дважды.

14 случаев (по тесту на случай):

  1  одобренный инструмент исполняется ровно один раз
  2  отклонённый — не исполняется никогда
  3  одобрение переживает перезапуск процесса (новый Services на той же SQLite)
  4  устаревший (уже израсходованный) approval_id не исполняет
  5  одобрение ДРУГОГО run'а не исполняет
  6  одобрение ДРУГОЙ задачи не исполняет
  7  реализация подменена после одобрения (новое поколение) — не исполняет
  8  аргументы подменены в pending-checkpoint после одобрения — не исполняет
  9  выдача инструмента отозвана после одобрения — не исполняет
  10 политика (авторизация) изменена после одобрения на DENY — не исполняет
  11 двойной resume одного одобрения — эффект один
  12 крах ПОСЛЕ решения человека, но ДО диспетча — эффекта нет, восстановимо
  13 крах ПОСЛЕ эффекта, но ДО записи журнала/receipt — на replay НЕТ дубля
  14 крах во время проверки исхода — задача не становится completed

Плюс два варианта, найденных при разборе этих же случаев:
  3b перезапуск, при котором ДО нашего инструмента зарегистрировался чужой
     (MCP-discovery): порядок чужих регистраций не отменяет одобрения
  13b тот же краш «между эффектом и журналом» на AUTO-пути, где одобрения не
     было вовсе и строки намерения раньше не существовало

Честность симуляций (важно для чтения отчёта):
  * «перезапуск процесса» — это реальный `Services.stop()` и новый `Services`
    на том же файле SQLite; реестр инструментов пересобирается так же, как в
    свежем процессе (счётчики поколений с нуля — см. _fresh_process_registry).
  * «крах» — исключение внутри самого пути (журнал эффекта, проверка исхода)
    либо истёкшая аренда + recover, как в test_fence_fl01. БД руками в
    недостижимое состояние НЕ приводится.
  * подмена approval_id / аргументов в pending-checkpoint — это не «краш», а
    модель угрозы: токен одобрения обязан быть привязан к своему вызову.
    Ровно так же тампер-тест устроен в test_secrem_f013_approval_identity.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc.db import (agents as agents_t, approvals as approvals_t, fetch_one,
                    task_runs as runs_t, tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from bcc.tools import REGISTRY, ToolResult

from .conftest import SimpleEnv, client_for, make_settings, start_app, wait_for
from .test_v21_tool_loop import FINISHED, ToolAdapter, _install, _run_task, _stack_with_tools


# ---------------------------------------------------------------- помощники

@pytest.fixture(autouse=True)
def clean_registry():
    before = set(REGISTRY.names())
    yield
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)


async def _park(env, *, calls=None, command="git push", tool="terminal.run",
                max_steps=4, install=True):
    """Довести задачу до ASK: инструмент выдан, run припаркован в БД."""
    if install:
        _install(tool, calls=calls, permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter([("tool", "terminal_run", {"command": command}), ("text", "готово")])
    stack = await _stack_with_tools(env, [tool], adapter=adapter, max_steps=max_steps)
    assert await _run_task(env, stack["task"]["id"]) == "waiting_approval"
    stack["adapter"] = adapter
    return stack


async def _park_another(env, agent_id: int, *, command="git push", title="вторая"):
    """Ещё одна задача ТОГО ЖЕ агента, тоже доведённая до ASK (свой approval)."""
    adapter = ToolAdapter([("tool", "terminal_run", {"command": command}), ("text", "готово")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    task = (await env.client.post("/api/tasks", json={
        "title": title, "prompt": "сделай ещё", "agent_id": agent_id,
        "run_now": True, "max_retries": 2})).json()["task"]
    assert await _run_task(env, task["id"]) == "waiting_approval"
    return task, adapter


async def _decide_first(env, approve: bool, by: str = "владелец") -> dict:
    appr = (await env.client.get("/api/approvals")).json()
    tool_appr = [a for a in appr if a["kind"] == "tool"]
    assert tool_appr, appr
    await env.client.post(f"/api/approvals/{tool_appr[0]['id']}",
                          json={"approve": approve, "by": by})
    return tool_appr[0]


async def _decide(env, approval_id: int, approve: bool = True, by: str = "владелец") -> None:
    await env.client.post(f"/api/approvals/{approval_id}", json={"approve": approve, "by": by})


async def _rows(env, task_id: int | None = None) -> list[dict]:
    async with env.svc.db.session() as s:
        stmt = sa.select(tool_calls_t).order_by(tool_calls_t.c.id)
        if task_id is not None:
            stmt = stmt.where(tool_calls_t.c.task_id == task_id)
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


async def _run_row(env, task_id: int) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id)
                               .order_by(runs_t.c.id.desc()))).first()
    return dict(row._mapping)


async def _pending_of(env, task_id: int) -> tuple[int, dict, dict]:
    run = await _run_row(env, task_id)
    cp = dict(run["checkpoint"] or {})
    pend = dict(cp.get("pending_tool_call") or {})
    assert pend, f"в checkpoint нет pending_tool_call: {cp}"
    return int(run["id"]), cp, pend


async def _write_pending(env, run_id: int, cp: dict, pend: dict) -> None:
    cp = dict(cp)
    cp["pending_tool_call"] = pend
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(checkpoint=cp))
        await s.commit()


async def _task_and_agent(env, task_id: int) -> tuple[dict, dict]:
    async with env.svc.db.session() as s:
        task = await fetch_one(s, tasks_t, task_id)
        agent = await fetch_one(s, agents_t, task["agent_id"])
    return task, agent


async def _pump_until(env, check, timeout: float = 8.0):
    """Крутить воркер и подписку на approvals, пока не наступит условие."""
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        return await wait_for(check, timeout=timeout)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def _expire_lease(env, run_id: int) -> None:
    """«Процесс умер»: аренда протухла (как в test_fence_fl01._expire_lease)."""
    async with env.svc.db.session() as s:
        await s.execute(sa.update(runs_t).where(runs_t.c.id == run_id).values(
            worker_lease_until=utcnow() - timedelta(seconds=5)))
        await s.commit()


def _tool_texts(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "tool")


async def _assert_denied_mutation_truth(env, task_id):
    # Merge contract correction, not a runtime weakening: the fixture requests
    # git push and the model still says "готово" after denial. No declared
    # post-state can rescue it. The canonical finalizer therefore records failed,
    # retains the answer and never creates an impossible review-approval loop.
    task, _ = await _task_and_agent(env, task_id)
    assert not (task.get("meta") or {}).get("required_effects")
    assert not ((task.get("meta") or {}).get("review") or {}).get("evidence")
    run = await _run_row(env, task_id)
    assert run["status"] == "failed" and run["result"] == "готово"
    assert "effectful" in run["error"]
    async with env.svc.db.session() as session:
        reviews = (await session.execute(sa.select(approvals_t).where(
            approvals_t.c.task_id == task_id, approvals_t.c.kind == "review_escalation"))).all()
    assert not reviews


# ---------------------------------------------------------------- 1, 2

async def test_case01_approved_tool_executes_exactly_once(env):
    """1. Одобрено → эффект ровно один, строка executed, кто одобрил — записан."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    await _decide_first(env, True)
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "completed"
    assert calls == [{"command": "git push"}]
    rows = await _rows(env, stack["task"]["id"])
    assert [r["status"] for r in rows] == ["executed"]
    assert rows[0]["approved_by"] == "владелец" and rows[0]["effect"] == "ask"


async def test_case02_rejected_tool_never_executes(env):
    """2. Отклонено → эффекта нет; сообщение «готово» не делает мутацию успехом."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    await _decide_first(env, False)
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) == "failed"
    await _assert_denied_mutation_truth(env, stack["task"]["id"])
    assert calls == []
    rows = await _rows(env, stack["task"]["id"])
    assert [r["status"] for r in rows] == ["rejected"]
    assert "отклонено" in _tool_texts(stack["adapter"].seen_messages[-1])


# ---------------------------------------------------------------- 3

class _Boots:
    """Несколько последовательных «процессов» на ОДНОМ файле SQLite."""

    def __init__(self, settings):
        self.settings = settings
        self.open_envs: list[SimpleEnv] = []

    async def boot(self, adapter=None) -> SimpleEnv:
        app, svc = await start_app(self.settings, start_workers=False)
        if adapter is not None:
            svc.registry.adapter_factory = lambda m, p: adapter
        env = SimpleEnv(app=app, svc=svc, client=client_for(app, svc), settings=self.settings)
        self.open_envs.append(env)
        return env

    async def shutdown(self, env: SimpleEnv) -> None:
        await env.client.aclose()
        await env.svc.stop()
        if env in self.open_envs:
            self.open_envs.remove(env)


@pytest.fixture
async def boots(tmp_path):
    b = _Boots(make_settings(tmp_path))
    yield b
    for env in list(b.open_envs):
        await b.shutdown(env)


def _fresh_process_registry(*names: str) -> None:
    """Счётчики поколений как в ТОЛЬКО ЧТО стартовавшем процессе.

    Реестр инструментов — модульный глобал: в одном тестовом процессе он не
    «перезапускается» вместе с Services. Свежий процесс начинает нумерацию с
    нуля и регистрирует инструменты в setup() фич — эмулируем ровно это, а не
    состояние, которого код не мог бы породить.
    """
    REGISTRY._generation = 0
    gens = getattr(REGISTRY, "_gen_by_name", None)
    if isinstance(gens, dict):
        for name in names:
            gens.pop(name, None)


async def test_case03_approval_survives_process_restart(boots):
    """3. Одобрение переживает перезапуск: ASK в процессе A, решение и эффект — в B."""
    first: list[dict] = []
    env_a = await boots.boot()
    _fresh_process_registry("terminal.run")
    spec_a = _install("terminal.run", calls=first, permission="terminal.run",
                      default_effect="ask")
    stack = await _park(env_a, calls=first, install=False)
    task_id = stack["task"]["id"]
    await boots.shutdown(env_a)                       # процесс A умер

    second: list[dict] = []
    env_b = await boots.boot(adapter=ToolAdapter([("text", "готово")]))
    _fresh_process_registry("terminal.run")
    spec_b = _install("terminal.run", calls=second, permission="terminal.run",
                      default_effect="ask")
    assert spec_b.generation == spec_a.generation, (
        "тот же код в свежем процессе обязан дать тот же номер поколения, иначе "
        "законное одобрение не переживает перезапуск")

    assert (await env_b.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"] \
        == "waiting_approval"
    await _decide_first(env_b, True)
    assert await _run_task(env_b, task_id, until=FINISHED) == "completed"
    assert first == [], "эффект не должен был случиться в умершем процессе"
    assert second == [{"command": "git push"}], "одобрение не пережило перезапуск"
    assert [r["status"] for r in await _rows(env_b, task_id)] == ["executed"]


async def test_case03b_restart_with_extra_tool_registered_first(boots):
    """3b. Перезапуск, при котором ДО нашего инструмента зарегистрирован ещё один
    (так делает MCP-discovery). Порядок регистрации ЧУЖИХ инструментов не имеет
    отношения к идентичности одобренного действия — одобрение остаётся в силе."""
    first: list[dict] = []
    env_a = await boots.boot()
    _fresh_process_registry("terminal.run")
    _install("terminal.run", calls=first, permission="terminal.run", default_effect="ask")
    stack = await _park(env_a, calls=first, install=False)
    task_id = stack["task"]["id"]
    await boots.shutdown(env_a)

    second: list[dict] = []
    env_b = await boots.boot(adapter=ToolAdapter([("text", "готово")]))
    _fresh_process_registry("terminal.run", "mcp:disc:tool")
    _install("mcp:disc:tool", permission="", default_effect="auto", source="mcp")
    _install("terminal.run", calls=second, permission="terminal.run", default_effect="ask")

    await _decide_first(env_b, True)
    assert await _run_task(env_b, task_id, until=FINISHED) == "completed"
    assert second == [{"command": "git push"}], (
        "одобрение отвергнуто только из-за порядка регистрации посторонних инструментов")


# ---------------------------------------------------------------- 4, 5, 6

async def test_case04_stale_approval_id_cannot_execute(env):
    """4. Устаревший approval_id не исполняет.

    (a) одобрение, уже израсходованное (`Approvals.consume` → consumed), предъявлено
        своим же вызовом — не исполняет;
    (b) тот же id, которым уже авторизован один эффект, подставлен в pending
        ДРУГОГО вызова — не исполняет (переиспользование токена одобрения).
    """
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    appr = (await env.client.get("/api/approvals")).json()[0]
    await _decide(env, appr["id"], True)

    # (a) одобрение израсходовано другим путём раньше, чем run добрался до resume
    ok = await env.svc.approvals.consume(appr["id"], kind="tool", preview=appr["preview"])
    assert ok, "одобрение не удалось израсходовать — тест не про то"
    assert await _run_task(env, task_id, until=FINISHED) == "failed"
    await _assert_denied_mutation_truth(env, task_id)
    assert calls == [], "израсходованное одобрение исполнило эффект"
    assert [r["status"] for r in await _rows(env, task_id)] == ["rejected"]

    # (b) тот же токен предъявлен вторым, другим вызовом: владелец своё «нет»
    # сказал (одобрение №2 отклонено), но в pending подставлен уже сработавший
    # токен №1 — эффект не имеет права случиться.
    calls2: list[dict] = []
    _install("terminal.run", calls=calls2, permission="terminal.run", default_effect="ask")
    task2, _ = await _park_another(env, stack["agent"]["id"], command="git push --force")
    async with env.svc.db.session() as s:
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == appr["id"])
                        .values(status="approved"))     # снова «действующий» токен
        await s.commit()
    run_id, cp, pend = await _pending_of(env, task2["id"])
    appr2 = next(a for a in (await env.client.get("/api/approvals")).json()
                 if a["task_id"] == task2["id"])
    await _write_pending(env, run_id, cp, {**pend, "approval_id": appr["id"]})
    await _decide(env, appr2["id"], False)             # человек отказал
    assert await _run_task(env, task2["id"], until=FINISHED) == "failed"
    assert calls2 == [], "устаревший approval_id авторизовал отклонённый эффект"


async def test_case05_approval_from_another_run_cannot_execute(env):
    """5. Одобрение выдано run'у A; исполнить его под run'ом B (той же задачи) нельзя.

    Оба run'а настоящие (второй создан engine.enqueue), approval настоящий,
    решение настоящее. Вопрос ровно один: привязано ли одобрение к прогону,
    которому оно выдано.
    """
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    appr = await _decide_first(env, True)
    run_a, _, pend = await _pending_of(env, task_id)
    assert int(pend["approval_id"]) == appr["id"]

    run_b = await env.svc.engine.enqueue(task_id)
    assert run_b is not None and run_b != run_a
    task, agent = await _task_and_agent(env, task_id)
    messages: list[dict] = []
    await env.svc.engine._resume_pending_tool(run_b, task, agent, messages, pend, [])
    assert calls == [], "одобрение run'а A исполнило эффект под run'ом B"
    text = _tool_texts(messages).lower()
    assert "не выполнено" in text or "отклонено" in text, text


async def test_case06_approval_from_another_task_cannot_execute(env):
    """6. Одобрение выдано задаче B; подставлено в pending задачи A — не исполняет."""
    calls_a: list[dict] = []
    stack_a = await _park(env, calls=calls_a)
    task_a = stack_a["task"]["id"]
    run_a, cp_a, pend_a = await _pending_of(env, task_a)

    task_b, _ = await _park_another(env, stack_a["agent"]["id"])
    appr_b = next(a for a in (await env.client.get("/api/approvals")).json()
                  if a["task_id"] == task_b["id"])
    await _decide(env, appr_b["id"], True)

    # у задачи A её собственный (нерешённый) digest, но чужой токен
    pend_a = {**pend_a, "approval_id": appr_b["id"]}
    await _write_pending(env, run_a, cp_a, pend_a)
    task, agent = await _task_and_agent(env, task_a)
    messages: list[dict] = []
    await env.svc.engine._resume_pending_tool(run_a, task, agent, messages, pend_a, [])
    assert calls_a == [], "одобрение задачи B авторизовало эффект в задаче A"


# ---------------------------------------------------------------- 7, 8

async def test_case07_reregistered_impl_after_approval_does_not_execute(env):
    """7. Одобрили → реализация переустановлена (новое поколение) → ни одна не идёт."""
    old: list[dict] = []
    new: list[dict] = []
    stack = await _park(env, calls=old)

    async def replacement(args, ctx):
        new.append(args)
        return ToolResult(content="подменённая реализация")

    _install("terminal.run", handler=replacement, permission="terminal.run",
             default_effect="ask")
    await _decide_first(env, True)
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) in FINISHED
    assert old == [] and new == []
    rows = await _rows(env, stack["task"]["id"])
    assert rows[0]["status"] == "rejected"
    assert rows[0]["approved_by"] == "system:identity_mismatch"


async def test_case08_args_tampered_after_approval_do_not_execute(env):
    """8. Аргументы в pending подменены после одобрения → эффекта нет."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    run_id, cp, pend = await _pending_of(env, task_id)
    tampered = {**pend["call"], "arguments": {"command": "rm -rf /"},
                "raw_arguments": '{"command": "rm -rf /"}'}
    await _write_pending(env, run_id, cp, {**pend, "call": tampered})
    await _decide_first(env, True)
    assert await _run_task(env, task_id, until=FINISHED) in FINISHED
    assert calls == [], f"исполнены подменённые аргументы: {calls}"


# ---------------------------------------------------------------- 9, 10

async def test_case09_revoked_capability_after_approval_does_not_execute(env):
    """9. Инструмент отозван у агента после одобрения → эффект не исполняется.

    Одобрение — это «да» на конкретное действие, а не обход выдачи: выдача
    перепроверяется в момент эффекта, а не только в момент запроса.
    """
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    await _decide_first(env, True)
    # владелец отзывает инструмент у агента, пока задача ждала решения
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"tools": []})
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) in FINISHED
    assert calls == [], "инструмент, отозванный у агента, всё равно исполнился"
    rows = await _rows(env, stack["task"]["id"])
    assert rows and rows[0]["status"] in ("rejected", "denied"), rows


async def test_case10_authorization_changed_to_deny_after_approval(env):
    """10. Политика агента изменена на DENY после одобрения → эффекта нет."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    await _decide_first(env, True)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        "permissions": {"tool_rules": [
            {"tool": "terminal.run", "resource": "*", "effect": "deny",
             "reason": "владелец запретил после одобрения"}]}})
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) in FINISHED
    assert calls == [], "запрещённое политикой действие исполнилось по старому одобрению"
    rows = await _rows(env, stack["task"]["id"])
    assert rows and rows[0]["status"] in ("rejected", "denied"), rows


# ---------------------------------------------------------------- 11

async def test_case11_double_resume_yields_single_effect(env):
    """11. Повторный resume того же одобрения — эффект остаётся один."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    run_id, _, pend = await _pending_of(env, task_id)
    await _decide_first(env, True)
    assert await _run_task(env, task_id, until=FINISHED) == "completed"
    assert calls == [{"command": "git push"}]

    task, agent = await _task_and_agent(env, task_id)
    messages: list[dict] = []
    await env.svc.engine._resume_pending_tool(run_id, task, agent, messages, pend, [])
    assert calls == [{"command": "git push"}], "повторный resume исполнил эффект второй раз"
    assert [r["status"] for r in await _rows(env, task_id)] == ["executed"]


# ---------------------------------------------------------------- 12, 13, 14

async def test_case12_crash_after_decision_before_dispatch_is_recoverable(env):
    """12. Решение принято, процесс умер ДО диспетча → эффекта нет, replay доводит
    дело до конца ровно одним эффектом."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    await _decide_first(env, True)
    assert calls == []
    assert [r["status"] for r in await _rows(env, task_id)] == ["pending_approval"], \
        "до диспетча эффекта нет"

    # процесс умер здесь: решение записано, run возвращён в очередь, checkpoint цел.
    # Следующий процесс обязан довести одобренное до конца — ровно один раз.
    assert await _run_task(env, task_id, until=FINISHED) == "completed"
    assert calls == [{"command": "git push"}]
    assert [r["status"] for r in await _rows(env, task_id)] == ["executed"]


async def test_case13_crash_after_effect_before_journal_has_no_duplicate(env):
    """13. Эффект исполнен, журнал записать не успел → на replay дубля НЕТ.

    Краш настоящий: исключение внутри `_record_tool_call` (коммит receipt'а) уже
    ПОСЛЕ вызова обработчика инструмента. Дальше — протухшая аренда и recover,
    как при падении процесса; строки в БД руками не правятся.
    """
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    await _decide_first(env, True)

    engine = env.svc.engine
    real_record = engine._record_tool_call
    crashed = {"n": 0}

    async def crash_on_effect_journal(*a, **kw):
        if kw.get("status") in ("executed", "error") and not crashed["n"]:
            crashed["n"] += 1
            raise RuntimeError("процесс умер до коммита receipt'а")
        return await real_record(*a, **kw)

    engine._record_tool_call = crash_on_effect_journal        # type: ignore[assignment]

    async def crashed_once():
        return True if crashed["n"] else None

    try:
        await _pump_until(env, crashed_once)
    finally:
        engine._record_tool_call = real_record                # type: ignore[assignment]
    assert calls == [{"command": "git push"}], "эффект должен был произойти ровно один раз"
    rows = await _rows(env, task_id)
    assert [r["status"] for r in rows] != ["executed"], (
        "журнал не должен был успеть записаться — тест не воспроизводит краш")

    # «перезапуск»: аренда протухла, движок делает recover и берёт run заново
    run_id = (await _run_row(env, task_id))["id"]
    await _expire_lease(env, run_id)
    assert await env.svc.engine.recover() >= 1
    await _run_task(env, task_id, until=FINISHED)
    assert calls == [{"command": "git push"}], (
        f"эффект исполнился повторно после краха журнала: {calls}")
    # исход эффекта помечен неизвестным, а не «выполнено» и не «отклонено»:
    # объявленные обязательства такой строкой не закрываются (bcc/finalize.py
    # считает не-executed эффект несостоявшимся), проверять — наблюдателю.
    rows = await _rows(env, task_id)
    assert [r["status"] for r in rows] == ["uncertain"], rows
    assert rows[0]["approved_by"] == "system:effect_outcome_unknown"


async def test_case13b_auto_path_crash_between_effect_and_journal(env):
    """13b. То же самое на AUTO-пути (одобрение не требовалось).

    Тут строки намерения не было вовсе: инструмент исполнялся, и только потом
    писалась строка исхода. Падение между этими двумя моментами не оставляло
    следа, и следующая попытка честно отправляла неидемпотентный эффект второй
    раз. Проверяется именно неидемпотентный инструмент — идемпотентный повторять
    можно по объявлению.
    """
    from types import SimpleNamespace

    from bcc.engine import TaskEngine
    from bcc.tools import ToolSpec

    from .helpers import make_stack

    stack = await make_stack(env.client, max_retries=3)
    task_id = stack["task"]["id"]
    sent: list[dict] = []

    async def handler(args, ctx):
        sent.append(dict(args))
        return ToolResult(content=f"отправлено #{len(sent)}", one_line="ок")

    spec = ToolSpec(name="mail.send", description="", handler=handler, permission="",
                    default_effect="auto", idempotent=False)
    a = env.svc.engine
    run_id = await a.claim()
    task = {"id": task_id, "workspace_path": ""}
    agent = {"name": "аналитик", "permissions": {}}

    def call(cid: str):
        return SimpleNamespace(id=cid, name="mail_send", arguments={"to": "a@b"},
                               raw_arguments='{"to": "a@b"}')

    real_record = a._record_tool_call

    async def crash_on_effect_journal(*args, **kw):
        if kw.get("status") in ("executed", "error"):
            raise RuntimeError("процесс умер до коммита receipt'а")
        return await real_record(*args, **kw)

    a._record_tool_call = crash_on_effect_journal            # type: ignore[assignment]
    try:
        await a._run_tool_now(run_id, task, agent, [], call("x1"), spec, 0)
    except RuntimeError:
        pass
    finally:
        a._record_tool_call = real_record                    # type: ignore[assignment]
    assert sent == [{"to": "a@b"}], "эффект должен был случиться ровно один раз"

    # процесс умер: аренда протухла, следующий движок делает recover и берёт run
    await _expire_lease(env, run_id)
    b = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry, lease_seconds=1,
                   heartbeat_seconds=1)
    b.services = env.svc
    assert await b.recover() == 1
    assert await b.claim() == run_id
    messages: list[dict] = []
    await b._run_tool_now(run_id, task, agent, messages, call("x2"), spec, 0)
    assert sent == [{"to": "a@b"}], f"неидемпотентный эффект повторился: {sent}"
    assert "не записан" in _tool_texts(messages), _tool_texts(messages)
    statuses = [r["status"] for r in await _rows(env, task_id)]
    assert statuses == ["dispatched", "replayed"], statuses


async def test_case14_crash_during_verification_is_not_a_completion(env):
    """14. Проверка исхода (критичный гейт) падает → задача НЕ completed, эффект один."""
    calls: list[dict] = []
    stack = await _park(env, calls=calls)
    task_id = stack["task"]["id"]
    await _decide_first(env, True)

    async def broken_verification(task, run_id, answer):
        raise RuntimeError("наблюдатель пост-состояния упал")

    env.svc.engine.add_hook("gate_completion", broken_verification)

    async def escalated():
        """Эффект уже случился, проверка упала, и решение отдано человеку.

        Проверяется именно эта тройка, а не один статус: `waiting_approval`
        задача носит и ДО решения, так что сам по себе он ничего не доказывает.
        """
        kinds = [a["kind"] for a in (await env.client.get("/api/approvals")).json()]
        if "review_escalation" not in kinds:
            return None
        if [r["status"] for r in await _rows(env, task_id)] != ["executed"]:
            return None
        state = (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]
        return state if state != "running" else None

    try:
        status = await _pump_until(env, escalated)
    finally:
        env.svc.engine.hooks["gate_completion"].remove(broken_verification)
    assert status != "completed", "упавшая проверка не имеет права завершить задачу"
    assert status == "waiting_approval", status
    assert calls == [{"command": "git push"}], "эффект должен был случиться ровно один раз"
    assert [r["status"] for r in await _rows(env, task_id)] == ["executed"]
