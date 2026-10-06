"""RC19 R10: a launch the desktop operator verified satisfies "open the app".

Computer Use acceptance (scenario a) finished "Open Notepad" as
no_verified_action although computer.act launch reported ПРОВЕРЕНО: the
action contract only counted the apps family. Only a verified app-lifecycle
computer call may count; typing, unverified or failed calls never do.
"""
import sqlalchemy as sa

from bcc.db import tool_calls
from bcc.features import action_contract as ac

from .helpers import make_stack

APPS = next(c for c in ac.CAPABILITIES if c.name == "APPS_ACTION")


def _row(action, preview, *, tool="computer.act", source="computer"):
    return {"tool": tool, "source": source, "args": {"action": action, "target": "notepad"},
            "result_preview": preview}


def test_filter_accepts_only_verified_lifecycle_computer_calls():
    ok = "launch «notepad» выполнено. Результат: ПРОВЕРЕНО. окно найдено"
    assert ac._is_verified_app_call(_row("launch", ok))
    assert ac._is_verified_app_call(_row("focus_window", ok.replace("launch", "focus_window")))
    assert not ac._is_verified_app_call(_row("launch", "launch «notepad» выполнено. Результат: НЕ ПРОВЕРЕНО."))
    assert not ac._is_verified_app_call(_row("type", "type «x» выполнено. Результат: ПРОВЕРЕНО."))
    assert not ac._is_verified_app_call(_row("launch", ok, tool="computer.observe"))
    assert ac._is_verified_app_call({"tool": "apps.start", "source": "apps", "args": {}})


def test_granted_tools_for_app_tasks_are_unchanged():
    assert APPS.attach == ("apps.start", "apps.stop")
    assert APPS.pattern.search("открой блокнот")


async def _insert(env, stack, rows):
    task_id = stack["task"]["id"]
    async with env.svc.db.session() as s:
        from bcc.db import task_runs
        run_id = (await s.execute(sa.select(task_runs.c.id).where(
            task_runs.c.task_id == task_id).limit(1))).scalar()
        if run_id is None:
            res = await s.execute(sa.insert(task_runs).values(task_id=task_id, status="running"))
            run_id = int(res.inserted_primary_key[0])
        for i, (tool, source, args, status, preview) in enumerate(rows):
            await s.execute(sa.insert(tool_calls).values(
                task_id=task_id, run_id=run_id, call_id=f"r10-{i}", tool=tool, source=source,
                args=args, effect="ask", status=status, result_preview=preview))
        await s.commit()
    return run_id


async def test_verified_computer_launch_satisfies_the_contract(env):
    stack = await make_stack(env.client)
    run_id = await _insert(env, stack, [
        ("computer.act", "computer", {"action": "launch", "target": "notepad"}, "executed",
         "launch «notepad» выполнено. Результат: ПРОВЕРЕНО. новое окно notepad.exe")])
    assert await ac._has_family_tool_call(env.svc, run_id, APPS.tool_sources, APPS.call_filter)


async def test_unverified_or_typing_computer_calls_do_not(env):
    stack = await make_stack(env.client)
    run_id = await _insert(env, stack, [
        ("computer.act", "computer", {"action": "launch", "target": "notepad"}, "executed",
         "launch «notepad» выполнено. Результат: НЕ ПРОВЕРЕНО."),
        ("computer.act", "computer", {"action": "type", "text": "x"}, "executed",
         "type «x» выполнено. Результат: ПРОВЕРЕНО."),
        ("computer.act", "computer", {"action": "launch", "target": "notepad"}, "error",
         "действие не выполнено: отказ")])
    assert not await ac._has_family_tool_call(env.svc, run_id, APPS.tool_sources, APPS.call_filter)
