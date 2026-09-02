"""P0-04: хуки безопасности fail-closed (engine._call_hooks).

Раньше любое исключение хука уходило в worker.error и run шёл дальше — упавший
ревьюер/approval/Deep Fix gate давал задаче completed. Теперь критичный хук
(before_run, gate_completion, pick_model) при исключении, таймауте или битом
результате НЕ даёт задаче завершиться; телеметрия (on_step/after_run/on_failure)
деградирует мягко (hook.degraded) и задачу не трогает.

Инъекция сбоев — через env.svc.engine.add_hook(...); движок крутится вручную
claim()/execute() (как в test_feat_governor_review.py).
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from bcc.db import task_runs as runs_t
from bcc.engine import CriticalHookFailure
from bcc.tools import REGISTRY, ToolResult, ToolSpec

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import ToolAdapter, _stack_with_tools

NOT_DONE = ("waiting_approval", "failed")


@pytest.fixture(autouse=True)
def clean_registry():
    before = set(REGISTRY.names())
    yield
    for name in set(REGISTRY.names()) - before:
        REGISTRY.unregister(name)


async def _drive(env, task_id: int, rounds: int = 8) -> str:
    """Прокрутить движок вручную до пустой очереди; вернуть статус задачи."""
    for _ in range(rounds):
        rid = await env.svc.engine.claim()
        if rid is None:
            break
        await env.svc.engine.execute(rid)
    return await _status(env, task_id)


async def _status(env, task_id: int) -> str:
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


async def _runs(env, task_id: int) -> list[dict]:
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id))).fetchall()
    return [dict(r._mapping) for r in rows]


async def _events(env, kind: str) -> list[dict]:
    return [e for e in await env.svc.bus.recent(100) if e["kind"] == kind]


async def _pending_approvals(env) -> list[dict]:
    return (await env.client.get("/api/approvals?status=pending")).json()


def _fake(env, text: str = "готово") -> FakeAdapter:
    adapter = FakeAdapter(text)
    env.svc.registry.adapter_factory = lambda m, p: adapter
    return adapter


def _assert_never_completed(status: str, runs: list[dict]) -> None:
    assert status in NOT_DONE, status
    assert all(r["status"] != "completed" for r in runs), runs
    assert all(not r.get("result") for r in runs), "результат записан как выполненный"


# ------------------------------------------------------------ (a) gate падает

async def test_gate_hook_exception_does_not_complete_task(env):
    _fake(env)

    async def reviewer_down(task, run_id, answer):
        raise RuntimeError("reviewer backend down")

    env.svc.engine.add_hook("gate_completion", reviewer_down)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    runs = await _runs(env, stack["task"]["id"])
    _assert_never_completed(status, runs)
    # эскалация человеку: approval review_escalation, в превью — имя упавшего хука
    assert status == "waiting_approval"
    appr = [a for a in await _pending_approvals(env) if a["kind"] == "review_escalation"]
    assert appr and "reviewer_down" in appr[0]["preview"], appr
    assert "reviewer backend down" in appr[0]["preview"]
    assert runs[0]["status"] == "queued" and runs[0]["checkpoint"]["note"] == "gate_hook_failed"
    ev = await _events(env, "hook.critical_failure")
    assert ev and ev[0]["data"]["hook"] == "gate_completion"
    assert ev[0]["data"]["error"] == "RuntimeError"
    assert "reviewer_down" in ev[0]["data"]["fn"]
    # в событии — ни аргументов хука, ни промпта
    assert "посчитай" not in str(ev[0]["data"])
    assert not await _events(env, "task.completed")


# -------------------------------------------- (b) сама эскалация тоже падает

async def test_approval_db_failure_during_escalation_fails_task(env, monkeypatch):
    _fake(env)

    async def reviewer_down(task, run_id, answer):
        raise RuntimeError("reviewer backend down")

    async def approvals_broken(**kw):
        raise OSError("approvals table locked")

    env.svc.engine.add_hook("gate_completion", reviewer_down)
    monkeypatch.setattr(env.svc.approvals, "create", approvals_broken)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    runs = await _runs(env, stack["task"]["id"])
    _assert_never_completed(status, runs)
    assert status == "failed" and runs[0]["status"] == "failed"
    assert "gate_completion" in runs[0]["error"] and "escalation failed" in runs[0]["error"]
    assert await _events(env, "hook.critical_failure")
    assert await _events(env, "hook.escalation_failed")
    assert await _events(env, "task.failed")
    assert not await _events(env, "task.completed")


# ------------------------------------------------------ (c) таймаут верификатора

async def test_gate_hook_timeout_is_critical_failure(env):
    _fake(env)

    async def verifier_hangs(task, run_id, answer):
        await asyncio.sleep(999)

    env.svc.engine.hook_timeout_s = 0.2
    env.svc.engine.add_hook("gate_completion", verifier_hangs)
    stack = await make_stack(env.client)
    status = await asyncio.wait_for(_drive(env, stack["task"]["id"]), 10)
    _assert_never_completed(status, await _runs(env, stack["task"]["id"]))
    ev = await _events(env, "hook.critical_failure")
    assert ev and ev[0]["data"]["error"] == "TimeoutError"
    assert "timeout" in ev[0]["data"]["reason"]
    # зависший хук отменён wait_for'ом — висящих задач не осталось
    assert [t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
            and "verifier_hangs" in repr(t)] == []


# ------------------------------------------------------- (d) битый результат gate

@pytest.mark.parametrize("bad", ["PASS", {"verdict": "maybe"}, 42, ["pass"]])
async def test_malformed_gate_result_does_not_complete_task(env, bad):
    _fake(env)

    async def sloppy_gate(task, run_id, answer):
        return bad

    env.svc.engine.add_hook("gate_completion", sloppy_gate)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    _assert_never_completed(status, await _runs(env, stack["task"]["id"]))
    ev = await _events(env, "hook.critical_failure")
    assert ev and ev[0]["data"]["error"] == "MalformedResult"


async def test_gate_dict_without_verdict_is_still_no_opinion(env):
    """Контракт сохранён: dict без verdict — «мнения нет», задача завершается."""
    _fake(env)

    async def no_opinion(task, run_id, answer):
        return {"note": "не моя задача"}

    env.svc.engine.add_hook("gate_completion", no_opinion)
    stack = await make_stack(env.client)
    assert await _drive(env, stack["task"]["id"]) == "completed"
    assert not await _events(env, "hook.critical_failure")


# ------------------------------------------------- (e) телеметрия деградирует мягко

async def test_telemetry_hook_exception_degrades_and_task_completes(env):
    _fake(env)

    async def on_step_broken(task, run_id, info):
        raise ValueError("metrics sink gone")

    async def after_run_broken(task_id, run_id, status):
        raise KeyError("no such counter")

    env.svc.engine.add_hook("on_step", on_step_broken)
    env.svc.engine.add_hook("after_run", after_run_broken)
    stack = await make_stack(env.client)
    assert await _drive(env, stack["task"]["id"]) == "completed"
    degraded = await _events(env, "hook.degraded")
    assert {e["data"]["hook"] for e in degraded} >= {"on_step", "after_run"}
    assert {e["data"]["error"] for e in degraded} >= {"ValueError", "KeyError"}
    assert not await _events(env, "hook.critical_failure")
    assert await _events(env, "task.completed")


async def test_explicit_critical_flag_overrides_default(env):
    """critical= задаётся фичей явно: on_step critical=True роняет run,
    gate critical=False (осознанный opt-out) — деградирует и не блокирует."""
    _fake(env)

    async def on_step_guard(task, run_id, info):
        raise RuntimeError("guard unavailable")

    env.svc.engine.add_hook("on_step", on_step_guard, critical=True)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    assert status == "failed"
    assert "on_step" in (await _runs(env, stack["task"]["id"]))[0]["error"]

    env.svc.engine.hooks["on_step"].remove(on_step_guard)

    async def advisory_gate(task, run_id, answer):
        raise RuntimeError("advisory only")

    env.svc.engine.add_hook("gate_completion", advisory_gate, critical=False)
    task2 = (await env.client.post("/api/tasks", json={
        "title": "вторая", "prompt": "посчитай 3+3", "agent_id": stack["agent"]["id"],
        "run_now": True})).json()["task"]
    assert await _drive(env, task2["id"]) == "completed"
    assert any(e["data"]["hook"] == "gate_completion" for e in await _events(env, "hook.degraded"))


# ------------------------------------------------ (f) несколько gate: 1-й PASS, 2-й падает

async def test_second_gate_failure_after_first_pass_blocks_completion(env):
    _fake(env)
    order: list[str] = []

    async def gate_ok(task, run_id, answer):
        order.append("ok")
        return {"verdict": "pass", "reasons": "всё хорошо"}

    async def gate_crash(task, run_id, answer):
        order.append("crash")
        raise RuntimeError("deep fix gate crashed")

    env.svc.engine.add_hook("gate_completion", gate_ok)
    env.svc.engine.add_hook("gate_completion", gate_crash)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    _assert_never_completed(status, await _runs(env, stack["task"]["id"]))
    assert order == ["ok", "crash"]
    assert not await _events(env, "task.completed")


# ------------------------------------------- (g) повторный прогон без дублей эффекта

async def test_redriving_after_gate_failure_causes_no_duplicate_effect(env):
    effects: list[dict] = []

    async def side_effect(args, ctx):
        effects.append(dict(args))
        return ToolResult(content="записано", one_line="effect: ок")

    REGISTRY.register(ToolSpec(name="test.effect", description="внешний эффект",
                               handler=side_effect, input_schema={},
                               default_effect="auto"))
    adapter = ToolAdapter([("tool", "test_effect", {}), ("text", "сделано")])
    stack = await _stack_with_tools(env, ["test.effect"], adapter=adapter)

    async def gate_crash(task, run_id, answer):
        raise RuntimeError("gate crashed after the effect")

    env.svc.engine.add_hook("gate_completion", gate_crash)
    status = await _drive(env, stack["task"]["id"])
    _assert_never_completed(status, await _runs(env, stack["task"]["id"]))
    assert len(effects) == 1

    # повторные прогоны воркера и crash-recovery ничего не подхватывают
    assert await env.svc.engine.claim() is None
    await env.svc.engine.recover()
    assert await _drive(env, stack["task"]["id"]) == status
    assert len(effects) == 1, "эффект инструмента выполнен повторно"
    assert adapter.calls == 2


# ------------------------------------------------- before_run / pick_model критичны

async def test_before_run_hook_exception_fails_task(env):
    adapter = _fake(env)

    async def resource_brain_down(task, run):
        raise RuntimeError("gpu probe crashed")

    env.svc.engine.add_hook("before_run", resource_brain_down)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    runs = await _runs(env, stack["task"]["id"])
    assert status == "failed" and runs[0]["status"] == "failed"
    assert "before_run" in runs[0]["error"]
    assert adapter.calls == 0, "модель вызвана несмотря на упавший before_run"
    ev = await _events(env, "hook.critical_failure")
    assert ev and ev[0]["data"]["hook"] == "before_run"


@pytest.mark.parametrize("mode", ["raise", "malformed"])
async def test_pick_model_hook_failure_fails_task(env, mode):
    adapter = _fake(env)

    async def router(task, agent):
        if mode == "raise":
            raise RuntimeError("router index corrupt")
        return {"model_id": "not-an-id"}

    env.svc.engine.add_hook("pick_model", router)
    stack = await make_stack(env.client)
    status = await _drive(env, stack["task"]["id"])
    runs = await _runs(env, stack["task"]["id"])
    assert status == "failed" and "pick_model" in runs[0]["error"]
    assert adapter.calls == 0
    ev = await _events(env, "hook.critical_failure")
    assert ev and ev[0]["data"]["hook"] == "pick_model"


# ------------------------------------------------------------ контракт _call_hooks

async def test_call_hooks_unit_contract(env):
    eng = env.svc.engine
    assert eng.hook_is_critical("gate_completion", object()) is True
    assert eng.hook_is_critical("after_run", object()) is False

    async def ok(*a):
        return {"verdict": "pass"}

    async def bad(*a):
        raise RuntimeError("x")

    eng.add_hook("gate_completion", ok)
    eng.add_hook("gate_completion", bad)
    with pytest.raises(CriticalHookFailure) as ei:
        await eng._call_hooks("gate_completion", {"id": 0}, 0, "")
    assert ei.value.name == "gate_completion" and "bad" in ei.value.hook
    assert ei.value.reason.startswith("RuntimeError")
    with pytest.raises(KeyError):
        eng.add_hook("no_such_hook", ok)
