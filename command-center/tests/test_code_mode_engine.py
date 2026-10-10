"""Code mode (bossman_search + bossman_run) through the REAL engine, Services, SQLite and approvals.

What is proven here (the facade must never widen authority):
  * default OFF; ON only by task.meta / agent.permissions, never by the model;
  * the model is shown exactly two schemas, the granted tools stay executable;
  * every `tools.x()` inside the sandbox is a separate engine tool call: journal row, grant check,
    policy DENY, owner approval - the same outcome as a direct call;
  * an approval-needing call stops the snippet, is asked as ITS OWN approval (tool + canonical args,
    never the code), runs exactly once with the engine-issued approval id, and a reject runs nothing;
  * owner STOP aborts the snippet; output is capped; big intermediate data never reaches the model;
  * a guest-bound agent cannot see owner-only / non-perimeter tools through search.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc.code_mode.facade import (FACADE_RUN, FACADE_SEARCH, build_catalog, code_mode_enabled,
                                  facade_specs, visible_specs)
from bcc.db import approvals as approvals_t, tool_calls as tool_calls_t
from bcc.tools import REGISTRY, ToolContext, ToolResult, ToolSpec, execute_tool

from .test_v21_tool_loop import (FINISHED, ToolAdapter, _install, _run_task,  # noqa: F401
                                 _stack_with_tools, clean_registry)


async def _rows(env, table, **where):
    async with env.svc.db.session() as s:
        stmt = sa.select(table)
        for k, v in where.items():
            stmt = stmt.where(getattr(table.c, k) == v)
        return [dict(r._mapping) for r in (await s.execute(stmt)).fetchall()]


async def _enable_code_mode(env, agent_id, **extra):
    perms = {"code_mode": True, **extra}
    r = await env.client.patch(f"/api/agents/{agent_id}", json={"permissions": perms})
    assert r.status_code == 200, r.text


def _tool_names(schemas):
    return [t["function"]["name"] for t in (schemas or [])]


def _run_call(code):
    return ("tool", "bossman_run", {"code": code})


# ------------------------------------------------------------------ presentation

async def test_default_off_the_model_sees_the_granted_schemas(env):
    _install("test.echo")
    adapter = ToolAdapter([("text", "ok")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    assert await _run_task(env, stack["task"]["id"]) == "completed"
    assert _tool_names(adapter.seen_tools[0]) == ["test_echo"]


async def test_enabled_the_model_sees_exactly_two_facade_schemas(env):
    _install("test.echo")
    _install("test.other")
    adapter = ToolAdapter([("text", "ok")])
    stack = await _stack_with_tools(env, ["test.echo", "test.other"], adapter=adapter)
    await _enable_code_mode(env, stack["agent"]["id"])
    assert await _run_task(env, stack["task"]["id"]) == "completed"
    assert _tool_names(adapter.seen_tools[0]) == ["bossman_search", "bossman_run"]


def test_flag_precedence_and_default():
    assert code_mode_enabled({}, {}) is False
    assert code_mode_enabled({"meta": {}}, {"permissions": {"code_mode": True}}) is True
    assert code_mode_enabled({"meta": {"code_mode": False}}, {"permissions": {"code_mode": True}}) is False
    assert code_mode_enabled({"meta": {"code_mode": "yes"}}, {}) is False   # only the literal true
    assert code_mode_enabled({}, {"permissions": ["code_mode"]}) is False  # a permission LIST is not the flag


# ------------------------------------------------------------------ search -> run

async def test_search_then_run_goes_through_the_pipeline(env):
    seen: list = []
    _install("test.echo", calls=seen)
    adapter = ToolAdapter([
        ("tool", "bossman_search", {"query": "echo text"}),
        _run_call("r = tools.test_echo(text='привет')\nprint(r['content'])\nr['ok']"),
        ("text", "готово"),
    ])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter, max_steps=5)
    await _enable_code_mode(env, stack["agent"]["id"])
    assert await _run_task(env, stack["task"]["id"]) == "completed"

    assert seen == [{"text": "привет"}]
    search_result = adapter.seen_messages[1][-1]["content"]
    assert "def test_echo(" in search_result and "text: str" in search_result
    run_result = adapter.seen_messages[2][-1]["content"]
    assert "status: ok" in run_result and "эхо: привет" in run_result
    assert "value: True" in run_result

    rows = {r["call_id"]: r for r in await _rows(env, tool_calls_t, task_id=stack["task"]["id"])}
    assert rows["call_2"]["tool"] == FACADE_RUN and rows["call_2"]["status"] == "executed"
    inner = rows["call_2~1"]
    assert inner["tool"] == "test.echo" and inner["status"] == "executed" and inner["effect"] == "auto"


async def test_a_tool_that_is_not_granted_cannot_be_called_from_the_snippet(env):
    seen: list = []
    _install("test.echo")
    _install("test.secret", calls=seen)            # registered, NOT granted
    adapter = ToolAdapter([_run_call("tools.test_secret(text='x')"), ("text", "понял")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    await _enable_code_mode(env, stack["agent"]["id"])
    await _run_task(env, stack["task"]["id"])
    assert seen == []
    assert "unknown tool: test_secret" in adapter.seen_messages[1][-1]["content"]
    assert [r["tool"] for r in await _rows(env, tool_calls_t, task_id=stack["task"]["id"])] == [FACADE_RUN]


async def test_policy_deny_inside_the_snippet_is_the_same_denial_as_a_direct_call(env):
    seen: list = []
    _install("terminal.run", calls=seen, permission="terminal.run", default_effect="auto")
    adapter = ToolAdapter([
        _run_call("r = tools.terminal_run(text='rm -rf /', command='rm -rf /')\nprint(r['ok'], r['status'])"),
        ("text", "запрещено"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        "permissions": {"code_mode": True, "tool_rules": [
            {"tool": "terminal.run", "resource": "rm -rf*", "effect": "deny", "reason": "деструктивно"}]}})
    await _run_task(env, stack["task"]["id"])
    assert seen == []
    result = adapter.seen_messages[1][-1]["content"]
    assert "False denied" in result
    rows = {r["call_id"]: r for r in await _rows(env, tool_calls_t, task_id=stack["task"]["id"])}
    assert rows["call_1~1"]["status"] == "denied"


async def test_denied_tool_is_not_even_listed_by_search(env):
    _install("test.echo")
    _install("test.blocked")
    adapter = ToolAdapter([("tool", "bossman_search", {"query": "test echo blocked"}), ("text", "ok")])
    stack = await _stack_with_tools(env, ["test.echo", "test.blocked"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={
        "permissions": {"code_mode": True, "tool_rules": [
            {"tool": "test.blocked", "resource": "*", "effect": "deny"}]}})
    await _run_task(env, stack["task"]["id"])
    listing = adapter.seen_messages[1][-1]["content"]
    assert "def test_echo(" in listing and "test_blocked" not in listing


# ------------------------------------------------------------------ approvals

async def _ask_stack(env, calls, *, approvals_seen=None, script=None):
    async def handler(args, ctx):
        calls.append(args)
        if approvals_seen is not None:
            approvals_seen.append(ctx.approval_id)
        return ToolResult(content="exit_code=0\npushed", one_line="terminal: ok")
    _install("terminal.run", handler=handler, permission="terminal.run", default_effect="ask")
    adapter = ToolAdapter(script or [
        _run_call("print('before')\nr = tools.terminal_run(command='git push origin main')\nprint('after')"),
        ("text", "готово"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await _enable_code_mode(env, stack["agent"]["id"])
    return stack, adapter


async def test_an_approval_inside_run_asks_about_the_call_not_the_code(env):
    calls: list = []
    stack, adapter = await _ask_stack(env, calls)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    assert calls == [], "без решения владельца инструмент не исполняется"

    approvals = await _rows(env, approvals_t)
    assert len(approvals) == 1
    preview = approvals[0]["preview"]
    assert "terminal.run" in preview and "git push origin main" in preview
    assert "tools.terminal_run" not in preview and "print(" not in preview, \
        "владельца спрашивают про вызов, а не про блок кода"

    rows = {r["call_id"]: r for r in await _rows(env, tool_calls_t, task_id=task_id)}
    assert rows["call_1"]["tool"] == FACADE_RUN and rows["call_1"]["status"] == "executed"
    asked = [r for r in rows.values() if r["status"] == "pending_approval"]
    assert len(asked) == 1 and asked[0]["tool"] == "terminal.run"
    assert asked[0]["approval_id"] == approvals[0]["id"]


async def test_approved_call_runs_exactly_once_on_the_engine_issued_approval(env):
    calls: list = []
    seen_approval_ids: list = []
    stack, adapter = await _ask_stack(env, calls, approvals_seen=seen_approval_ids)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    aid = (await _rows(env, approvals_t))[0]["id"]
    r = await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert await _run_task(env, task_id, until=FINISHED) == "completed"

    assert calls == [{"command": "git push origin main"}]
    assert seen_approval_ids == [aid], "инструмент видит одобрение, выданное движком, а не аргумент модели"

    # the history stays valid for the provider: every tool_call id has its tool message,
    # and the model was told the snippet stopped before the call
    final = adapter.seen_messages[-1]
    ids = [tc["id"] for m in final if m.get("tool_calls") for tc in m["tool_calls"]]
    answered = [m["tool_call_id"] for m in final if m.get("role") == "tool"]
    assert sorted(ids) == sorted(answered) and len(ids) == 2
    outer = next(m for m in final if m.get("role") == "tool" and m["tool_call_id"] == "call_1")
    assert "needs_approval" in outer["content"] and "after" not in outer["content"].replace("did NOT", "")
    assert "before" in outer["content"]
    assert "pushed" in next(m for m in final if m.get("role") == "tool"
                            and m["tool_call_id"] != "call_1")["content"]


async def test_a_rejected_call_inside_run_executes_nothing(env):
    calls: list = []
    stack, adapter = await _ask_stack(env, calls)
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    aid = (await _rows(env, approvals_t))[0]["id"]
    r = await env.client.post(f"/api/approvals/{aid}", json={"approve": False, "by": "owner"})
    assert r.status_code == 200
    await _run_task(env, task_id, until=FINISHED)
    assert calls == []
    last = adapter.seen_messages[-1][-1]["content"]
    assert "отклонено" in last


async def test_an_approval_for_one_call_does_not_cover_a_different_call(env):
    """Same tool, other arguments: a new question, not a free pass."""
    calls: list = []
    stack, adapter = await _ask_stack(env, calls, script=[
        _run_call("tools.terminal_run(command='git push origin main')"),
        _run_call("tools.terminal_run(command='git push --force origin main')"),
        ("text", "готово"),
    ])
    task_id = stack["task"]["id"]
    assert await _run_task(env, task_id) == "waiting_approval"
    first = (await _rows(env, approvals_t))[0]
    await env.client.post(f"/api/approvals/{first['id']}", json={"approve": True, "by": "owner"})

    async def second_question():
        rows = await _rows(env, approvals_t)
        return rows if len(rows) == 2 else None
    # the run resumes, executes the approved call, the model asks for another one -> a NEW question
    env.svc.engine.poll_interval = 0.02
    import asyncio
    from .conftest import wait_for
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    try:
        approvals = await wait_for(second_question, timeout=8.0)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)
    assert len(approvals) == 2 and calls == [{"command": "git push origin main"}]
    assert "--force" in approvals[1]["preview"]


# ------------------------------------------------------------------ STOP, caps, data volume

async def test_owner_stop_aborts_the_snippet_between_calls(env):
    seen: list = []
    holder: dict = {}

    async def handler(args, ctx):
        seen.append(args)
        # the owner presses STOP while the snippet is running
        await env.svc.engine.stop(holder["task_id"])
        return ToolResult(content="ok", one_line="echo")
    _install("test.echo", handler=handler)
    adapter = ToolAdapter([
        _run_call("for i in range(5):\n    tools.test_echo(text=str(i))\nprint('finished')"),
        ("text", "готово"),
    ])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    holder["task_id"] = stack["task"]["id"]
    await _enable_code_mode(env, stack["agent"]["id"])
    status = await _run_task(env, stack["task"]["id"], until=FINISHED)
    assert status == "stopped"
    assert len(seen) == 1, f"STOP must abort the snippet before the next call, ran {len(seen)}"


async def test_output_is_capped_and_intermediate_data_stays_in_the_sandbox(env):
    big = "Z" * 60_000

    async def handler(args, ctx):
        return ToolResult(content=big, one_line="big")
    _install("test.echo", handler=handler)
    adapter = ToolAdapter([
        _run_call("r = tools.test_echo(text='x')\nprint(len(r['content']))\n"
                  "for i in range(20000):\n    print('line', i)"),
        ("text", "готово"),
    ])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    await _enable_code_mode(env, stack["agent"]["id"])
    assert await _run_task(env, stack["task"]["id"]) == "completed"
    result = adapter.seen_messages[1][-1]["content"]
    assert "60000" in result
    assert "ZZZZ" not in result, "промежуточный результат инструмента не должен попасть к модели"
    assert len(result) < 9_500
    assert "[output truncated]" in result


async def test_a_broken_snippet_is_data_for_the_model_not_a_crash(env):
    _install("test.echo")
    adapter = ToolAdapter([_run_call("import os"), ("text", "понял")])
    stack = await _stack_with_tools(env, ["test.echo"], adapter=adapter)
    await _enable_code_mode(env, stack["agent"]["id"])
    # like any refused/errored tool call, the run's verdict is not "completed" (honest refusal)
    assert await _run_task(env, stack["task"]["id"], until=FINISHED) in FINISHED
    assert "status: rejected" in adapter.seen_messages[1][-1]["content"]
    assert "Import is not allowed" in adapter.seen_messages[1][-1]["content"]


# ------------------------------------------------------------------ guest / outside the engine

async def test_a_guest_bound_agent_cannot_see_owner_tools_through_search(env, monkeypatch):
    from bcc import gmail_connector as gm

    async def guests(svc):
        return {7}
    monkeypatch.setattr(gm, "_participant_agent_ids", guests)

    async def _h(args, ctx):
        return ToolResult(content="x")
    gmail = ToolSpec(name="plugin:gmail.read", description="read the owner's mail", handler=_h,
                     input_schema={"query": {"type": "string"}}, source="plugin",
                     context_deny=gm.owner_only_denial)
    web = ToolSpec(name="web.search", description="search the web", handler=_h,
                   input_schema={"query": {"type": "string"}}, source="builtin")
    term = ToolSpec(name="terminal.run", description="run a shell command", handler=_h,
                    input_schema={"command": {"type": "string"}}, source="terminal")
    specs = [gmail, web, term]

    guest_ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"id": 7, "permissions": {}})
    shown, _ = await visible_specs(guest_ctx, specs)
    assert [s.name for s in shown] == ["web.search"]
    catalog = await build_catalog(guest_ctx, specs)
    assert "gmail" not in catalog.render_search("read mail", 5)
    assert "terminal" not in catalog.render_search("run a shell command", 5)

    owner_ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"id": 8, "permissions": {}})
    shown_owner, _ = await visible_specs(owner_ctx, specs)
    assert {s.name for s in shown_owner} >= {"web.search", "terminal.run"}


async def test_unreadable_guest_config_hides_everything_outside_the_perimeter(env, monkeypatch):
    from bcc import gmail_connector as gm

    async def broken(svc):
        raise OSError("config unreadable")
    monkeypatch.setattr(gm, "_participant_agent_ids", broken)

    async def _h(args, ctx):
        return ToolResult(content="x")
    term = ToolSpec(name="terminal.run", description="run", handler=_h, source="terminal")
    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"id": 9, "permissions": {}})
    shown, _ = await visible_specs(ctx, [term])
    assert shown == []


async def test_bossman_run_refuses_to_execute_outside_the_engine(env):
    _search, run = facade_specs()
    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={"id": 1})
    result = await execute_tool(run, {"code": "print(1)"}, ctx)
    assert result.error and "только движком" in result.content


def test_facade_tools_are_not_in_the_global_registry():
    """Not grantable by name, not part of the capability manifest."""
    assert REGISTRY.get(FACADE_RUN) is None and REGISTRY.get(FACADE_SEARCH) is None
    names = [s.name for s in facade_specs()]
    assert names == [FACADE_SEARCH, FACADE_RUN]
    # the whole facade costs a few hundred tokens whatever the tool count
    assert sum(len(json.dumps(s.schema(), ensure_ascii=False)) for s in facade_specs()) < 1800
