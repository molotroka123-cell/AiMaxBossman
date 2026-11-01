"""Owner 31.10: «глм да тоже имеет доступ но внутри определённого проекта максимум» + «ну и другие облачные агенты
соответственно».

A CLOUD agent (chat-only included) in a project chat runs as a project sandbox: exactly code.read / code.write /
code.patch, confined to that project folder. Never the terminal, never another folder, never _inbox, never a routing
grant. Synthetic data only (tmp data dir + tmp projects root from conftest).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc import tools as tools_mod
from bcc.features import chat_projects as cp
from bcc.features import tools_code
from bcc.tools import ToolContext

from .test_chat_projects import _local_stack, _project, _root, _task

CLOUD = "https://api.example-cloud.test/v1"


async def _ctx(env, agent: dict, folder: Path, *, sandbox: bool) -> ToolContext:
    task = {"id": 1, "workspace_path": str(folder), "meta": {"project_sandbox": True} if sandbox else {}}
    return ToolContext(svc=env.svc, task=task, run_id=1, agent=agent, step=0, workspace=str(folder), call_id="c1")


async def test_cloud_agent_in_a_project_gets_the_folder_and_the_sandbox_line(env):
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    assert cloud["permissions"].get("cloud_pc_access") is False
    p = await _project(env, "Glm Proj")
    thread = (await env.client.post("/api/chat/threads", json={"project": p["slug"]})).json()
    prompt, ws = await cp.bind_chat_turn(env.svc, thread["id"], cloud["id"], "создай файл")
    assert ws == Path(p["path"]).resolve()
    assert "ТОЛЬКО этой папки проекта" in prompt and "Терминала и других папок ПК нет" in prompt
    assert await cp.sandbox_for(env.svc, cloud["id"], ws) is True


async def test_cloud_agent_without_a_project_gets_nothing(env):
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    inbox_thread = (await env.client.post("/api/chat/threads", json={})).json()
    assert await cp.bind_chat_turn(env.svc, inbox_thread["id"], cloud["id"], "текст") == ("текст", None)
    assert await cp.sandbox_for(env.svc, cloud["id"], _root() / cp.INBOX) is False


async def test_local_agent_is_never_a_sandbox(env):
    local = (await _local_stack(env.client, tools=["code.write"]))["agent"]
    p = await _project(env, "Local Proj")
    assert await cp.sandbox_for(env.svc, local["id"], Path(p["path"])) is False


def test_sandbox_task_sees_exactly_the_three_file_tools():
    task = {"workspace_path": "C:/p", "meta": {"project_sandbox": True}}
    rich = {"tools": ["terminal.run", "browser.open", "code.write"], "permissions": {"cloud_pc_access": True}}
    chat_only = {"tools": [], "permissions": {"cloud_pc_access": False}}
    assert tools_mod.allowed_tools_for(task, rich) == list(tools_mod.PROJECT_SANDBOX_TOOLS)
    assert tools_mod.allowed_tools_for(task, chat_only) == list(tools_mod.PROJECT_SANDBOX_TOOLS)
    # negative: no flag / no folder / a non-True flag -> the old rules (chat-only = nothing)
    assert tools_mod.allowed_tools_for({"workspace_path": "C:/p", "meta": {}}, chat_only) == []
    assert tools_mod.allowed_tools_for({"meta": {"project_sandbox": True}}, chat_only) == []
    assert tools_mod.allowed_tools_for({"workspace_path": "C:/p", "meta": {"project_sandbox": "true"}}, chat_only) == []


def test_sandbox_effect_grant_is_per_run_and_only_for_the_sandbox():
    agent = {"permissions": {"cloud_pc_access": False}}
    sand = tools_mod.effect_agent({"workspace_path": "C:/p", "meta": {"project_sandbox": True}}, agent)
    assert sand["permissions"]["terminal.run"] is True and "terminal.run" not in agent["permissions"]
    assert tools_mod.effect_agent({"meta": {}}, agent) is agent
    assert tools_mod.sandbox_schemas_only([{"function": {"name": tools_mod.to_api_name("code.patch")}}])
    assert not tools_mod.sandbox_schemas_only([{"function": {"name": tools_mod.to_api_name("terminal.run")}}])


async def test_sandbox_root_is_this_project_folder_only(env):
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    p, other = await _project(env, "Mine"), await _project(env, "Other")
    folder = Path(p["path"]).resolve()
    ctx = await _ctx(env, cloud, folder, sandbox=True)
    assert await cp.workspace_grant(ctx) == folder
    assert await tools_code.resolve_root(env.svc, None, str(folder), ctx) == folder
    for bad in (str(Path(other["path"]).resolve()), str(_root()), str(env.settings.data_dir), str(folder.parent)):
        with pytest.raises(PermissionError, match="песочница проекта"):
            await tools_code.resolve_root(env.svc, bad, str(folder), ctx)
    # negative: the same cloud agent WITHOUT the sandbox flag gets no grant at all
    assert await cp.workspace_grant(await _ctx(env, cloud, folder, sandbox=False)) is None
    # negative: a sandbox flag pointing at _inbox or an unregistered folder grants nothing
    inbox = _root() / cp.INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    assert await cp.workspace_grant(await _ctx(env, cloud, inbox, sandbox=True)) is None
    stray = _root() / "stray"
    stray.mkdir()
    assert await cp.workspace_grant(await _ctx(env, cloud, stray, sandbox=True)) is None
    with pytest.raises(PermissionError, match="песочница проекта"):
        await tools_code.resolve_root(env.svc, None, str(stray), await _ctx(env, cloud, stray, sandbox=True))


async def test_sandbox_write_lands_in_the_project_and_escape_is_refused(env):
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    p = await _project(env, "Write")
    folder = Path(p["path"]).resolve()
    ctx = await _ctx(env, cloud, folder, sandbox=True)
    ok = await tools_code.tool_code_write({"path": "src/app.py", "content": "print(1)\n"}, ctx)
    assert not ok.error, ok.content
    assert (folder / "src" / "app.py").read_text(encoding="utf-8") == "print(1)\n"
    bad = await tools_code.tool_code_write({"path": "../escape.py", "content": "x"}, ctx)
    assert bad.error and not (folder.parent / "escape.py").exists()
    bad = await tools_code.tool_code_write({"path": "x.py", "content": "x", "root": str(env.settings.data_dir)}, ctx)
    assert bad.error and not (Path(env.settings.data_dir) / "x.py").exists()


async def test_set_task_workspace_marks_only_cloud_project_tasks(env):
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    local = (await _local_stack(env.client, tools=["code.write"], name="loc"))["agent"]
    p = await _project(env, "Mark")
    folder = Path(p["path"]).resolve()
    ids = []
    for agent in (cloud, local):
        t = (await env.client.post("/api/tasks", json={"prompt": "x", "title": "x", "agent_id": agent["id"],
                                                       "run_now": False})).json()["task"]
        await cp.set_task_workspace(env.svc, t["id"], folder)
        ids.append(t["id"])
    assert (await _task(env, ids[0]))["meta"].get("project_sandbox") is True
    assert not ((await _task(env, ids[1]))["meta"] or {}).get("project_sandbox")


async def test_cloud_agent_outside_a_project_gets_no_tools_at_all(env):
    """Live cloud matrix 01.11: Kimi/DeepSeek (cloud_pc_access True) asked the owner to approve a terminal in a chat
    WITHOUT a project. Outside a project a cloud agent gets no tools; a local agent is untouched (negative control)."""
    rich = (await _local_stack(env.client, tools=["terminal.run", "code.search"], base_url=CLOUD, name="kimi"))["agent"]
    local = (await _local_stack(env.client, tools=["terminal.run"], name="loc"))["agent"]
    ids = {}
    for name, agent in (("cloud", rich), ("local", local)):
        t = (await env.client.post("/api/tasks", json={"prompt": "x", "title": "x", "agent_id": agent["id"],
                                                       "run_now": False})).json()["task"]
        ids[name] = (t["id"], await cp.restrict_cloud_without_project(env.svc, t["id"], agent["id"], None))
    assert ids["cloud"][1] is True and ids["local"][1] is False
    cloud_task = await _task(env, ids["cloud"][0])
    assert cloud_task["meta"]["allowed_tools"] == []
    assert tools_mod.allowed_tools_for(cloud_task, {"tools": ["terminal.run"], "permissions": {"cloud_pc_access": True}}) == []
    # in a project the sandbox rule applies, not this one
    p = await _project(env, "With project")
    assert await cp.restrict_cloud_without_project(env.svc, ids["cloud"][0], rich["id"], Path(p["path"])) is False


async def test_a_relative_root_in_the_sandbox_is_the_project_folder(env):
    """Live cloud matrix 01.11 (task 959): GLM passed root="." and the sandbox refused - «.» was the backend's cwd."""
    cloud = (await _local_stack(env.client, base_url=CLOUD, name="glm"))["agent"]
    p = await _project(env, "Rel")
    folder = Path(p["path"]).resolve()
    ctx = await _ctx(env, cloud, folder, sandbox=True)
    ok = await tools_code.tool_code_write({"path": "a.txt", "content": "x", "root": "."}, ctx)
    assert not ok.error, ok.content
    assert (folder / "a.txt").read_text(encoding="utf-8") == "x"
    (folder / "sub").mkdir()
    assert await tools_code.resolve_root(env.svc, "sub", str(folder), ctx) == folder / "sub"
    for bad in ("..", "../..", "sub/../.."):                       # negatives: relative escapes stay refused
        with pytest.raises(PermissionError, match="песочница проекта"):
            await tools_code.resolve_root(env.svc, bad, str(folder), ctx)
