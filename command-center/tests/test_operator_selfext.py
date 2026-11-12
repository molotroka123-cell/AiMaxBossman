"""PC operator self-extension: skill.save/find/read through the ONE Skill Library, tool.create/run_custom through
terminal.run's path and policy (owner 11.11 night «по пути создавая tools and skills»)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa

from bcc.db import agents as agents_t, approvals as approvals_t
from bcc.features import pc_operator as po
from bcc.features import tools_selfext as se
from bcc.tools import REGISTRY, ToolContext, allowed_tools_for, decide_effect
from bcc.v2.skill_library import SkillLibrary

from .helpers import make_stack


@pytest.fixture
def iso(env, tmp_path, monkeypatch):
    """Skills and custom tools in this test's tmp dir, never the repo / %LOCALAPPDATA%."""
    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(env.svc, "skills", SkillLibrary([root], root), raising=False)
    monkeypatch.setenv(se.TOOLS_DIR_ENV, str(tmp_path / "operator_tools"))
    return root


async def _operator(env) -> tuple[dict, dict]:
    stack = await make_stack(env.client)
    await env.client.put("/api/operator", json={"total_control": True})
    async with env.svc.db.session() as s:
        row = dict((await s.execute(sa.select(agents_t).where(agents_t.c.role == po.ROLE))).first()._mapping)
        tools = list(row["tools"]) + ["terminal.run"]      # live agent 82 holds terminal.run like this
        await s.execute(sa.update(agents_t).where(agents_t.c.id == row["id"]).values(tools=tools))
        await s.commit()
    return {**row, "tools": tools}, stack


def _ctx(env, agent: dict, task: dict) -> ToolContext:
    return ToolContext(svc=env.svc, task=task, run_id=1, agent=agent)


async def _call(name: str, args: dict, ctx: ToolContext):
    from bcc.tools import bind_arguments, execute_tool
    spec = REGISTRY.get(name)
    assert spec is not None, name
    bound = await bind_arguments(spec, args)
    assert bound is None, bound
    return await execute_tool(spec, args, ctx)


async def test_operator_gets_selfext_tools_and_rule_others_do_not(env, iso):
    agent, stack = await _operator(env)
    assert set(po.SELFEXT_TOOLS) <= set(agent["tools"])
    assert po.SELFEXT_MARK in agent["system_prompt"]
    await env.client.get("/api/operator")
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.id == agent["id"]))).first()._mapping
    assert row["system_prompt"].count(po.SELFEXT_MARK) == 1          # idempotent
    other = dict(stack["agent"])
    granted = {t.name for t in REGISTRY.resolve(allowed_tools_for({"meta": {}}, other))}
    assert not set(po.SELFEXT_TOOLS) & granted


async def test_skill_save_find_read_round_trip_and_versioning(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    args = {"id": "disk-free-space", "title": "Свободное место на дисках",
            "when_to_use": "спрашивают сколько места свободно на дисках",
            "steps": "1. terminal.run `wmic logicaldisk get caption,freespace,size`\n2. перевести байты в ГБ",
            "required_tools": ["terminal.run"]}
    r = await _call("skill.save", dict(args), ctx)
    assert not r.error, r.content
    assert r.data["version"] == "1" and (iso / "disk-free-space" / "SKILL.md").is_file()
    found = await _call("skill.find", {"query": "сколько свободного места на диске"}, ctx)
    assert found.data["skills"][0]["id"] == "disk-free-space" and found.data["skills"][0]["own"] is True
    read = await _call("skill.read", {"id": "disk-free-space"}, ctx)
    assert "wmic logicaldisk" in read.content and read.data["required_tools"] == ["terminal.run"]
    r2 = await _call("skill.save", {**args, "steps": args["steps"] + "\n3. ответить таблицей"}, ctx)
    assert not r2.error and r2.data["version"] == "2"
    assert "таблицей" in (await _call("skill.read", {"id": "disk-free-space"}, ctx)).content


async def test_skill_save_refuses_foreign_skill_traversal_and_widening_goes_to_approvals(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    (iso / "shipped").mkdir()
    (iso / "shipped" / "SKILL.md").write_text("---\nname: shipped\ndescription: from repo\n---\nbody\n", encoding="utf-8")
    base = {"title": "t", "when_to_use": "w", "steps": "s"}
    assert (await _call("skill.save", {**base, "id": "shipped"}, ctx)).error
    for bad in ("../evil", "..", "a/b", "C:\\x", "Ab"):
        res = await _call("skill.save", {**base, "id": bad}, ctx)
        assert res.error, bad
    assert not any(p.name == "evil" for p in iso.parent.iterdir())
    res = await _call("skill.save", {**base, "id": "needs-mail", "required_tools": ["email.send", "terminal.run"]}, ctx)
    assert res.error and res.data["added_tools"] == ["email.send"]
    assert not (iso / "needs-mail").exists()
    async with env.svc.db.session() as s:
        appr = (await s.execute(sa.select(approvals_t).where(approvals_t.c.id == res.data["approval_id"]))).first()
    assert appr._mapping["kind"] == "skill_permissions" and appr._mapping["status"] == "pending"


async def test_tool_create_then_run_custom_executes_through_terminal(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    code = "import sys\nprint('hello-' + '-'.join(sys.argv[1:]))\n"
    made = await _call("tool.create", {"name": "say_hello", "description": "печатает привет", "language": "python",
                                        "code": code, "args_schema": {"who": {"type": "string"}}}, ctx)
    assert not made.error, made.content
    listed = await _call("tool.list_custom", {}, ctx)
    assert listed.data["tools"][0]["name"] == "say_hello"
    spec = REGISTRY.get("tool.run_custom")
    assert decide_effect(spec, {"name": "say_hello", "args": {"who": "owner"}}, agent)[0] == "auto"
    out = await _call("tool.run_custom", {"name": "say_hello", "args": {"who": "owner"}}, ctx)
    assert not out.error, out.content
    assert "exit_code=0" in out.content and "hello---who-owner" in out.content
    # a new version keeps the old one and changes the bound digest
    made2 = await _call("tool.create", {"name": "say_hello", "description": "v2", "language": "python",
                                         "code": "print('v2')\n"}, ctx)
    assert made2.data["version"] == 2 and (se.tools_root() / "say_hello" / "v1.py").is_file()
    from bcc.tools import execute_tool
    stale = await execute_tool(spec, {"name": "say_hello", "_code_sha256": "0" * 64}, ctx)
    assert stale.error and "изменился" in stale.content          # approved bytes == executed bytes


@pytest.mark.parametrize("code", [
    "import os\nos.system('rm -rf /')\n",
    "import subprocess\nsubprocess.run(['format', 'D:'])\n",
    "Format-Volume -DriveLetter D\n",
    "import smtplib\n",
    "import requests\nrequests.patch('http://127.0.0.1:8801/api/agents/82', json={})\n",
    "print(open('C:/Users/asd/.ssh/id_rsa').read())\n",
])
async def test_destructive_or_privileged_code_is_denied_at_create_and_at_run(env, iso, code):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    lang = "powershell" if code.startswith("Format-") else "python"
    made = await _call("tool.create", {"name": "bad_one", "description": "x", "language": lang, "code": code}, ctx)
    assert made.error and not (se.tools_root() / "bad_one").exists()
    # planted on disk behind the tool's back: the run is still denied by decide_effect and by the handler
    d = se.tools_root() / "bad_one"
    d.mkdir(parents=True)
    (d / se.LANGS[lang]).write_text(code, encoding="utf-8")
    (d / "tool.json").write_text('{"name": "bad_one", "language": "%s", "version": 1}' % lang, encoding="utf-8")
    spec = REGISTRY.get("tool.run_custom")
    assert decide_effect(spec, {"name": "bad_one"}, agent)[0] == "deny"
    from bcc.tools import execute_tool
    assert (await execute_tool(spec, {"name": "bad_one"}, ctx)).error


async def test_harmless_format_words_and_package_install_semantics(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    ok = await _call("tool.create", {"name": "fmt_ok", "description": "x", "language": "python",
                                      "code": "print('{} GB'.format(5))\n"}, ctx)
    assert not ok.error, ok.content
    ps = await _call("tool.create", {"name": "drives", "description": "x", "language": "powershell",
                                      "code": "Get-PSDrive -PSProvider FileSystem | Format-Table Name,Free\n"}, ctx)
    assert not ps.error, ps.content
    pip = await _call("tool.create", {"name": "installer", "description": "x", "language": "python",
                                       "code": "import os\nos.system('pip install requests')\n"}, ctx)
    assert not pip.error
    spec = REGISTRY.get("tool.run_custom")
    effect, reason = decide_effect(spec, {"name": "installer"}, agent)
    assert effect == "ask" and "установка пакетов" in reason        # «Полный доступ» does not lift it


async def test_tool_names_with_traversal_are_rejected(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    spec = REGISTRY.get("tool.run_custom")
    for bad in ("../x", "..\\x", "a/b", "C:", "X", "", "a" * 60, "name.py"):
        res = await _call("tool.create", {"name": bad, "description": "x", "language": "python",
                                          "code": "print(1)\n"}, ctx)
        assert res.error, bad
        assert decide_effect(spec, {"name": bad}, agent)[0] == "deny", bad
    assert not (iso.parent / "x").exists()


async def test_run_custom_is_not_auto_for_agents_without_operator_total_control(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    await _call("tool.create", {"name": "probe", "description": "x", "language": "python", "code": "print(1)\n"}, ctx)
    spec = REGISTRY.get("tool.run_custom")
    worker = {"role": "worker", "permissions": {k: True for k in po.TOTAL_CONTROL}}
    assert decide_effect(spec, {"name": "probe"}, worker)[0] == "ask"
    assert decide_effect(spec, {"name": "probe"}, {"role": "worker", "permissions": {}})[0] == "ask"
    create = REGISTRY.get("tool.create")
    assert decide_effect(create, {"name": "probe"}, {"role": "worker", "permissions": {}})[0] == "ask"


async def test_skill_save_accepts_api_names_but_unheld_tools_still_need_approval(env, iso):
    """Live 12.11 task 2992: `terminal_run` (API name) was parked as a widening. Negative control: unknown/unheld still are."""
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    base = {"title": "t", "when_to_use": "w", "steps": "s"}
    ok = await _call("skill.save", {**base, "id": "api-names", "required_tools": ["terminal_run", "skill_find"]}, ctx)
    assert not ok.error, ok.content
    assert ok.data["required_tools"] == ["skill.find", "terminal.run"]
    for unheld in (["no_such_tool"], ["computer.observe"]):
        bad = await _call("skill.save", {**base, "id": "api-unheld", "required_tools": unheld}, ctx)
        assert bad.error and bad.data["approval_id"], unheld
    assert not (iso / "api-unheld").exists()


async def test_operator_task_gets_its_own_matching_skill_attached_others_do_not(env, iso):
    agent, stack = await _operator(env)
    ctx = _ctx(env, agent, stack["task"])
    await _call("skill.save", {"id": "disk-free-space", "title": "Свободное место на дисках",
                               "when_to_use": "свободное место на дисках", "steps": "1. Get-PSDrive -PSProvider FileSystem"},
                ctx)
    await _call("tool.create", {"name": "disk_free", "description": "свободное место на дисках в ГБ",
                                "language": "python", "code": "print(1)\n"}, ctx)
    hint = await se.operator_task_hint(env.svc, agent, "Сколько свободного места на дисках?")
    assert "disk-free-space" in hint and "Get-PSDrive" in hint and "disk_free" in hint
    # negative controls: unrelated task gets no skill; a non-operator agent gets nothing at all
    other = await se.operator_task_hint(env.svc, agent, "Открой блокнот")
    assert "disk-free-space" not in other and "skill.save" in other
    assert await se.operator_task_hint(env.svc, {**agent, "role": "worker"}, "свободное место на дисках") == ""
    # a foreign (repo) skill is never pushed into the operator's task
    (iso / "shipped-disk").mkdir()
    (iso / "shipped-disk" / "SKILL.md").write_text("---\nname: shipped-disk\ndescription: свободное место дисках\n---\nx\n",
                                                   encoding="utf-8")
    assert "shipped-disk" not in await se.operator_task_hint(env.svc, agent, "свободное место на дисках")
