"""Live 23.10: a cloud-model agent (GLM 5.3 Flash via OpenRouter) scoped by the owner to
code.search/code.expand/code.status with permissions {"filesystem.read": true} got a code-review task
whose prompt named repo files.

1. action_contract classified the review as TERMINAL_FILE_ACTION/CODE_ACTION, attached terminal.run/
   kill/stdin to that agent via the routing grant, and failed the read-only review with
   action_contract/no_verified_action.
2. code.search returned 0 results 24 times: with code.roots unset the default root was terminal.roots[0]
   (the Python stdlib dir on the owner PC), never the Bossman repo.

The invariant SIDE_EFFECT_REQUIRED && no verified side effect -> fail stays for genuine action requests.
"""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import action_contract as ac
from bcc.features import tools_code
from bcc.tools import to_api_name

from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

CLOUD_TOOLS = ["code.search", "code.expand", "code.status"]
CLOUD_PERMS = {"filesystem.read": True}
TERMINAL_API = {to_api_name(n) for n in ("terminal.run", "terminal.kill", "terminal.stdin")}

REVIEW_PROMPTS = [
    # the live shape: «сделай … файлов» + «напиши список» + «баги»
    "Сделай код-ревью файлов command-center/bcc/features/chat_vision.py и "
    "command-center/bcc/features/tools_photo_edit.py. Найди баги и напиши список с номерами строк.",
    "Проверь код в tools/lora_dataset.py: найди ошибки, напиши, что исправить.",
    "Проведи ревью bcc/features/chat_vision.py и напиши отчёт о найденных багах.",
    "Do a code review of bcc/features/chat_vision.py and write a report of the bugs with file:line.",
    "Run an audit of tools/lora_dataset.py and write the findings.",
]


@pytest.mark.parametrize("prompt", REVIEW_PROMPTS)
def test_review_only_prompt_requires_no_side_effect(prompt):
    assert ac.classify_all(prompt) == [], [c.name for c in ac.classify_all(prompt)]


@pytest.mark.parametrize("prompt,expected", [
    ("Сделай ревью bcc/x.py и исправь баг", "CODE_ACTION"),
    ("Review bcc/x.py and fix the bugs", "CODE_ACTION"),
    ("Review x.py and write tests in tests/test_x.py", "CODE_ACTION"),
    ("Проведи ревью и создай файл report.md с выводами", "TERMINAL_FILE_ACTION"),
    ("Сделай ревью кода и закоммить", "GITHUB_ACTION"),
    ("Проверь файл x.py и измени функцию foo", "CODE_ACTION"),
    # unchanged non-review classifications
    ("Создай файл hello.txt", "TERMINAL_FILE_ACTION"),
    ("Исправь баг в bcc/features/chat_vision.py", "CODE_ACTION"),
])
def test_review_with_genuine_action_still_classifies(prompt, expected):
    assert expected in [c.name for c in ac.classify_all(prompt)]


async def test_naming_a_read_tool_is_not_a_terminal_request(env):
    # env registers the code.* specs; «файлы … через» used to match TERMINAL_FILE_ACTION
    assert ac.classify_all("Найди файлы с chat_vision через code.search и опиши проблемы.") == []
    caps = [c.name for c in ac.classify_all("Используй code.search и создай файл out.txt")]
    assert caps == ["TERMINAL_FILE_ACTION"]


async def _cloud_stack(env, prompt, script, *, tools=CLOUD_TOOLS, perms=CLOUD_PERMS):
    adapter = ToolAdapter(script)
    stack = await _stack_with_tools(env, tools, adapter=adapter, prompt=prompt, max_steps=4)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"permissions": perms})
    return adapter, stack


def _seen(adapter) -> set[str]:
    out: set[str] = set()
    for tools in adapter.seen_tools:
        for t in tools or []:
            out.add((t.get("function") or t).get("name"))
    return out


async def _task_meta(env, task_id) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(dbm.tasks.c.id == task_id))).first()
    return dict(row._mapping["meta"] or {})


async def test_cloud_review_completes_without_terminal_tools(env):
    adapter, stack = await _cloud_stack(env, REVIEW_PROMPTS[0], [
        ("text", "Ревью: chat_vision.py:42 — возможен None; tools_photo_edit.py:10 — нет проверки пути."),
    ])
    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert status == "completed"
    assert not (TERMINAL_API & _seen(adapter)), sorted(_seen(adapter))


async def test_scoped_agent_is_not_extended_with_ungranted_terminal(env):
    """A genuine action request to the same scoped agent: terminal.* is NOT handed over (the owner never
    granted terminal.run), and the task still fails — no verified side effect, the invariant holds."""
    adapter, stack = await _cloud_stack(env, "Создай файл hello.txt через терминал",
                                        [("text", "Готово, файл создан.")])
    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert status == "failed"
    assert not (TERMINAL_API & _seen(adapter)), sorted(_seen(adapter))
    meta = await _task_meta(env, stack["task"]["id"])
    assert "terminal.run" in (meta.get("action_contract") or {}).get("withheld_tools", [])
    assert "terminal.run" not in (meta.get("allowed_tools") or [])


async def test_granted_permission_still_gets_the_terminal_family(env):
    """Control: the same scoped agent WITH terminal.run granted is extended as before."""
    adapter, stack = await _cloud_stack(env, "Создай файл hello.txt через терминал",
                                        [("text", "Готово.")],
                                        perms={"filesystem.read": True, "terminal.run": True})
    await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert to_api_name("terminal.run") in _seen(adapter)


async def test_agent_without_tool_list_keeps_routing_grant(env):
    """Control: an agent with no declared tools still gets the routing grant (every call goes through ASK)."""
    adapter, stack = await _cloud_stack(env, "Создай файл hello.txt через терминал",
                                        [("text", "Готово.")], tools=[], perms={})
    await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    assert to_api_name("terminal.run") in _seen(adapter)


# ------------------------------------------------------------------ code.search default root

async def _set(env, key, value) -> None:
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(dbm.settings_kv).where(dbm.settings_kv.c.key == key))
        if value is not None:
            await s.execute(sa.insert(dbm.settings_kv).values(
                key=key, value_enc=env.svc.vault.encrypt(json.dumps(value))))
        await s.commit()


async def test_default_code_root_is_the_repo_not_terminal_roots_first(env, tmp_path):
    stdlib_like = tmp_path / "Python312" / "Lib"
    stdlib_like.mkdir(parents=True)
    await _set(env, tools_code.CODE_ROOTS_KEY, None)
    await _set(env, tools_code.TERMINAL_ROOTS_KEY, [str(stdlib_like)])

    assert await tools_code.resolve_root(env.svc, None) == tools_code._repo_root()
    # the terminal root stays reachable explicitly; nothing outside is opened
    assert await tools_code.resolve_root(env.svc, str(stdlib_like)) == stdlib_like.resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PermissionError, match="вне разрешённых корней"):
        await tools_code.resolve_root(env.svc, str(outside))
    # private paths stay blocked even inside the repo root
    with pytest.raises(PermissionError, match="T3"):
        await tools_code.resolve_root(env.svc, str(tools_code._repo_root() / "tdata"))
    # allowed_roots (scope for coding_tasks/cli/rave) is unchanged
    assert await tools_code.allowed_roots(env.svc) == [stdlib_like.resolve()]


async def test_explicit_code_roots_are_used_exactly(env, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    await _set(env, tools_code.CODE_ROOTS_KEY, [str(proj)])
    assert await tools_code.code_roots(env.svc) == [proj.resolve()]
    assert await tools_code.resolve_root(env.svc, None) == proj.resolve()
    with pytest.raises(PermissionError, match="вне разрешённых корней"):
        await tools_code.resolve_root(env.svc, str(tools_code._repo_root()))


async def test_code_search_default_root_hits_the_repo(env, tmp_path, monkeypatch):
    """code.search without `root` on a terminal.roots-first-is-stdlib setup finds repo code."""
    repo = tmp_path / "bossman"
    (repo / "command-center").mkdir(parents=True)
    (repo / "command-center" / "marker_mod.py").write_text(
        "def unique_bossman_marker_symbol():\n    return 1\n", encoding="utf-8")
    stdlib_like = tmp_path / "Lib"
    stdlib_like.mkdir()
    (stdlib_like / "os_like.py").write_text("def path_join(): pass\n", encoding="utf-8")
    monkeypatch.setattr(tools_code, "_repo_root", lambda: repo.resolve())
    await _set(env, tools_code.CODE_ROOTS_KEY, None)
    await _set(env, tools_code.TERMINAL_ROOTS_KEY, [str(stdlib_like)])

    from bcc.tools import ToolContext
    ctx = ToolContext(svc=env.svc, task={}, run_id=0, agent={})
    res = await tools_code.tool_code_search({"query": "unique bossman marker symbol"}, ctx)
    assert not res.error, res.content
    assert res.data["root"] == str(repo.resolve())
    assert res.data["items"] >= 1
    assert any("marker_mod.py" in s for s in res.data["sources"])
