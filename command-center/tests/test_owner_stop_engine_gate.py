"""Class-1 (24.11.2026): the owner's global STOP must keep Bossman stopped, not only kill the work in flight.

Reproduced on 75f7b76 before the fix: with `<data>/computer/STOP` set, a new task was claimed, the model was called
and a tool EXECUTED (status «completed»). Now: claim() holds the queue, the step boundary ends a running task as
«stopped», and execute_tool refuses every effect. Each gate has its negative control (no STOP -> works as before).
"""
from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc import owner_stop as os_mod
from bcc.features import tools_computer as tc
from bcc.tools import ToolContext, ToolResult, ToolSpec, execute_tool

from .conftest import wait_for
from .test_v21_tool_loop import ToolAdapter, _install, _run_task, _stack_with_tools, clean_registry  # noqa: F401


def _stop_file(env) -> Path:
    return Path(env.svc.settings.data_dir) / "computer" / tc.STOP_FILE


def _set_stop(env):
    p = _stop_file(env)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("owner\n0\n", encoding="utf-8")


# ---------------------------------------------------------------- the module

def test_absent_set_and_no_data_dir(tmp_path):
    assert not os_mod.owner_stop(tmp_path)
    assert not os_mod.owner_stop(None) and not os_mod.owner_stop("")
    os_mod.global_stop_path(tmp_path).parent.mkdir(parents=True)
    os_mod.global_stop_path(tmp_path).write_text("x")
    st = os_mod.owner_stop(tmp_path)
    assert st and st.sources == ("computer",) and "owner STOP" in st.reason


def test_autonomy_scope_is_opt_in(tmp_path):
    os_mod.autonomy_stop_path(tmp_path).parent.mkdir(parents=True)
    os_mod.autonomy_stop_path(tmp_path).write_text("x")
    assert not os_mod.owner_stop(tmp_path)                      # the engine's scope: global STOP only
    assert os_mod.owner_stop(tmp_path, include_autonomy=True).sources == ("autonomy",)


def test_unreadable_stop_fails_closed(tmp_path, monkeypatch):
    real = Path.stat

    def stat(self, *a, **kw):
        if self.name == "STOP":
            raise PermissionError(errno.EACCES, "denied")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "stat", stat)
    st = os_mod.owner_stop(tmp_path)
    assert st and st.sources == ("computer:unreadable",)


def test_missing_parent_dir_is_absent_not_unreadable(tmp_path):
    assert not os_mod.owner_stop(tmp_path / "no" / "such")


def test_broken_settings_fail_closed():
    class Broken:
        @property
        def data_dir(self):
            raise RuntimeError("boom")
    assert os_mod.owner_stop_for(SimpleNamespace(settings=Broken()))
    assert not os_mod.owner_stop_for(None)
    assert not os_mod.owner_stop_for(SimpleNamespace())          # bare object without settings: no STOP plane


# ---------------------------------------------------------------- execute_tool gate

async def test_execute_tool_refuses_every_effect_under_stop(tmp_path):
    called = []

    async def handler(args, ctx):
        called.append(args)
        return ToolResult(content="did it")
    spec = ToolSpec(name="test.effect", description="t", handler=handler, default_effect="auto")
    ctx = ToolContext(svc=SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)), task={"id": 1},
                      run_id=1, agent={})
    ok = await execute_tool(spec, {}, ctx)                       # negative control: no STOP -> runs
    assert not ok.error and called == [{}]
    os_mod.global_stop_path(tmp_path).parent.mkdir(parents=True)
    os_mod.global_stop_path(tmp_path).write_text("x")
    res = await execute_tool(spec, {}, ctx)
    assert res.error and "STOP" in res.content and called == [{}]


# ---------------------------------------------------------------- engine gates (real app)

async def test_new_task_waits_while_stop_and_runs_after_owner_clears_it(env):
    seen = []
    _install("test.echo", calls=seen)
    _set_stop(env)
    adapter = ToolAdapter([("tool", "test_echo", {"text": "after STOP"}), ("text", "готово")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    task_id = stack["task"]["id"]

    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    try:
        await asyncio.sleep(0.6)                                 # many claim cycles under STOP
        status = (await env.client.get(f"/api/tasks/{task_id}")).json()
        status = status["task"]["status"] if "task" in status else status["status"]
        assert status == "queued", status
        assert adapter.calls == 0 and seen == []                 # no model call, no tool, no cost
        assert "owner STOP" in env.svc.engine.owner_stop_reason()
        assert env.svc.engine.held_by_owner_stop

        _stop_file(env).unlink()                                 # the owner clears STOP where it was set

        async def done():
            t = (await env.client.get(f"/api/tasks/{task_id}")).json()
            s = t["task"]["status"] if "task" in t else t["status"]
            return s if s in ("completed", "failed", "stopped") else None
        assert await wait_for(done, timeout=6.0) == "completed"
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert seen == [{"text": "after STOP"}] and env.svc.engine.held_by_owner_stop == ""


async def test_stop_set_mid_run_ends_the_task_before_the_next_effect(env):
    effects = []

    async def first(args, ctx):
        effects.append("first")
        _set_stop(env)                                           # owner presses STOP while the agent works
        return ToolResult(content="first done")

    async def second(args, ctx):
        effects.append("second")
        return ToolResult(content="second done")
    _install("test.first", handler=first)
    _install("test.second", handler=second)
    adapter = ToolAdapter([("tool", "test_first", {"text": "1"}), ("tool", "test_second", {"text": "2"}),
                           ("text", "всё")])
    stack = await _stack_with_tools(env, ["test.first", "test.second"], adapter=adapter, max_steps=6)
    status = await _run_task(env, stack["task"]["id"], until=("completed", "failed", "stopped"))
    assert status == "stopped"
    assert effects == ["first"] and adapter.calls == 1           # no second model call, no second effect


async def test_negative_control_without_stop_the_same_script_completes(env):
    effects = []

    async def first(args, ctx):
        effects.append("first")
        return ToolResult(content="first done")

    async def second(args, ctx):
        effects.append("second")
        return ToolResult(content="second done")
    _install("test.first", handler=first)
    _install("test.second", handler=second)
    adapter = ToolAdapter([("tool", "test_first", {"text": "1"}), ("tool", "test_second", {"text": "2"}),
                           ("text", "всё")])
    stack = await _stack_with_tools(env, ["test.first", "test.second"], adapter=adapter, max_steps=6)
    assert await _run_task(env, stack["task"]["id"], until=("completed", "failed", "stopped")) == "completed"
    assert effects == ["first", "second"]


@pytest.mark.parametrize("scope", ["autonomy"])
async def test_autonomy_only_stop_does_not_freeze_owner_tasks(env, scope):
    """Scope is unchanged by this fix: the autonomy-loop STOP (`bossman autonomy stop`) is not the owner's global STOP."""
    seen = []
    _install("test.echo", calls=seen)
    p = Path(env.svc.settings.data_dir) / scope / "STOP"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    adapter = ToolAdapter([("tool", "test_echo", {"text": "ok"}), ("text", "готово")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed" and seen == [{"text": "ok"}]
