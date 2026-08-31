"""V2.1 фазы B и C — терминал и браузер как настоящие инструменты модели.

Терминал: реальные процессы (project_host — subprocess; sandbox требует docker).
Браузер: реальный Chromium + локальная страница-фикстура на http.server.
"""
import asyncio
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import settings_kv, tool_calls as tool_calls_t
from bcc.features.tools_terminal import extra_ask_reason, hard_deny_reason
from bcc.tools import REGISTRY, decide_effect

from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools
from .browser_support import chromium_available, reason as browser_reason

FIXTURE_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>BOSSMAN тестовая форма</title></head><body>
<h1>Форма заявки</h1>
<input id="name" name="name" placeholder="Имя">
<button id="go" onclick="document.getElementById('out').textContent =
  'Принято: ' + document.getElementById('name').value">Отправить</button>
<p id="out">пока пусто</p>
</body></html>"""


@pytest.fixture
def fixture_site(tmp_path):
    """Локальный сайт: file:// не проходит domain_allowed (пустой hostname)."""
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(FIXTURE_HTML, encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
    server.server_close()


async def _allow_root(env, path: Path) -> None:
    enc = env.svc.vault.encrypt(json.dumps([str(path)]))
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "terminal.roots"))
        await s.execute(sa.insert(settings_kv).values(key="terminal.roots", value_enc=enc))
        await s.commit()


# ---------------------------------------------------------------- терминал

def test_terminal_policy_classification():
    """Классификация команд из §3 мастер-промпта — на самих регэкспах."""
    assert hard_deny_reason("git push --force origin main")
    assert hard_deny_reason("mkfs.ext4 /dev/sda1")
    assert hard_deny_reason("cat ~/.ssh/id_rsa")
    assert hard_deny_reason("cat wallet.dat")
    assert not hard_deny_reason("git push origin main")

    assert extra_ask_reason("git push origin main")
    assert extra_ask_reason("npm install left-pad")
    assert extra_ask_reason("pip install requests")
    assert extra_ask_reason("docker compose up -d")
    assert extra_ask_reason("sudo systemctl restart nginx")
    assert not extra_ask_reason("pytest -q")
    assert not extra_ask_reason("git status")


async def test_terminal_effect_hook_cannot_be_loosened_by_permission(env):
    """Право terminal.run не делает git push автоматическим.
    (env нужен: инструменты регистрируются в setup() фичи при старте сервисов.)"""
    spec = REGISTRY.get("terminal.run")
    assert spec is not None, "инструмент terminal.run не зарегистрирован"
    granted = {"permissions": {"terminal.run": True}}
    assert decide_effect(spec, {"command": "pytest -q"}, granted)[0] == "auto"
    assert decide_effect(spec, {"command": "git push origin main"}, granted)[0] == "ask"
    assert decide_effect(spec, {"command": "npm install x"}, granted)[0] == "ask"
    assert decide_effect(spec, {"command": "git push --force"}, granted)[0] == "deny"
    assert decide_effect(spec, {"command": "pytest", "network": True}, granted)[0] == "ask"


async def test_model_runs_real_command_and_reads_output(env, tmp_path):
    """Модель вызывает terminal.run → реальный процесс → вывод возвращается модели."""
    work = tmp_path / "proj"
    work.mkdir()
    (work / "hello.txt").write_text("это файл проекта\n", encoding="utf-8")
    await _allow_root(env, work)

    read_command = "type hello.txt" if os.name == "nt" else "cat hello.txt"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": read_command, "mode": "project_host",
                                  "cwd": str(work)}),
        ("text", "прочитал файл проекта"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    # project_host is deliberately an ASK boundary even for a read command.
    assert await _run_task(env, stack["task"]["id"], timeout=15) == "waiting_approval"
    approval = (await env.client.get("/api/approvals")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": True, "by": "test"})
    assert await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED) == "completed"
    tool_msg = adapter.seen_messages[1][-1]["content"]
    assert "это файл проекта" in tool_msg
    assert "exit_code=0" in tool_msg
    # вывод внешнего мира помечен как данные
    assert tool_msg.startswith("Ниже — внешние данные")


async def test_model_edits_code_after_owner_approval(env, tmp_path):
    """A host-side code edit remains blocked until its recorded owner approval."""
    work = tmp_path / "repo"
    work.mkdir()
    (work / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (work / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n", encoding="utf-8")
    await _allow_root(env, work)

    # правка меняет и длину файла: иначе pytest подхватит старый .pyc
    # (инвалидция по mtime+size, а оба запуска попадают в одну секунду)
    # Invoke a fixture script by filename.  The old POSIX heredoc could never
    # exercise Windows cmd.exe; this form is portable and does not rely on
    # platform-specific nested-quote parsing.
    (work / "edit_calc.py").write_text(
        "import pathlib, shutil\n"
        "pathlib.Path('calc.py').write_text('def add(a, b):\\n    # sum, not difference\\n    return a + b\\n')\n"
        "shutil.rmtree('__pycache__', ignore_errors=True)\n", encoding="utf-8")
    fix = "python edit_calc.py"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": fix, "mode": "project_host", "cwd": str(work)}),
        ("text", "изменение выполнено после подтверждения владельца"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter, max_steps=8)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    # The edit is not executed merely because the agent has terminal.run.
    assert await _run_task(env, stack["task"]["id"], timeout=30) == "waiting_approval"
    assert "return a - b" in (work / "calc.py").read_text(encoding="utf-8")
    approval = (await env.client.get("/api/approvals?status=pending")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}", json={"approve": True, "by": "test"})
    assert await _run_task(env, stack["task"]["id"], timeout=30, until=FINISHED) == "completed"
    assert "exit_code=0" in adapter.seen_messages[1][-1]["content"]
    assert (work / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")

    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t))).fetchall()
    assert len([r for r in rows if dict(r._mapping)["tool"] == "terminal.run"]) == 1
    assert all(dict(r._mapping)["status"] == "executed" for r in rows)


async def test_terminal_refuses_cwd_outside_roots(env, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    await _allow_root(env, allowed)

    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "ls", "mode": "project_host", "cwd": str(outside)}),
        ("text", "каталог недоступен"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    # project_host is always an ASK boundary, even when the eventual executor
    # will reject the cwd.  Approval never converts an out-of-roots cwd into
    # execution: the adapter returns the refusal after the approved resume.
    assert await _run_task(env, stack["task"]["id"], timeout=15) == "waiting_approval"
    approval = (await env.client.get("/api/approvals?status=pending")).json()[0]
    await env.client.post(f"/api/approvals/{approval['id']}",
                          json={"approve": True, "by": "test"})
    assert await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED) == "completed"
    tool_msg = adapter.seen_messages[1][-1]["content"]
    assert "вне разрешённых корней" in tool_msg
    assert "вне разрешённых корней" in adapter.seen_messages[1][-1]["content"]


async def test_destructive_command_is_denied_not_asked(env, tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "git push --force origin main",
                                  "mode": "project_host", "cwd": str(work)}),
        ("text", "понял, так нельзя"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    # DENY, а не waiting_approval: такое не одобряется в принципе
    assert await _run_task(env, stack["task"]["id"], timeout=15) == "completed"
    assert (await env.client.get("/api/approvals")).json() == []
    assert "запрещено политикой" in adapter.seen_messages[1][-1]["content"]


async def test_git_push_asks_even_with_permission(env, tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    await _allow_root(env, work)
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "git push origin main", "mode": "project_host",
                                  "cwd": str(work)}),
        ("text", "запушил"),
    ])
    stack = await _stack_with_tools(env, ["terminal.run"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"terminal.run": True}})

    assert await _run_task(env, stack["task"]["id"], timeout=15) == "waiting_approval"
    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and "git push" in appr[0]["preview"]


# ---------------------------------------------------------------- браузер

pytestmark_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())


@pytestmark_browser
async def test_model_drives_browser_end_to_end(env, fixture_site):
    """§4: LLM → read_dom → type → click → read_dom → финальный ответ."""
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": fixture_site}),
        ("tool", "browser_type", {"selector": "#name", "text": "Тимур"}),
        ("tool", "browser_click", {"selector": "#go"}),
        ("tool", "browser_read_dom", {}),
        ("text", "форма приняла имя Тимур"),
    ])
    stack = await _stack_with_tools(
        env, ["browser.open", "browser.read_dom", "browser.type", "browser.click"],
        adapter=adapter, max_steps=8)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True, "browser.control": True}})

    assert await _run_task(env, stack["task"]["id"], timeout=90) == "completed"

    first_dom = adapter.seen_messages[1][-1]["content"]
    assert "Форма заявки" in first_dom
    assert "[0] <input" in first_dom or "name=name" in first_dom
    final_dom = adapter.seen_messages[4][-1]["content"]
    assert "Принято: Тимур" in final_dom          # страница реально изменилась

    async with env.svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(tool_calls_t))).fetchall()]
    assert [r["tool"] for r in rows] == ["browser.open", "browser.type", "browser.click",
                                         "browser.read_dom"]
    assert all(r["status"] == "executed" and r["source"] == "browser" for r in rows)


@pytestmark_browser
async def test_human_takeover_blocks_agent_then_resume_works(env, fixture_site):
    """Take Over: клик агента отклонён; после Resume агент снова работает."""
    adapter = ToolAdapter([("tool", "browser_open", {"url": fixture_site}),
                           ("text", "открыл")])
    stack = await _stack_with_tools(env, ["browser.open", "browser.read_dom", "browser.click"],
                                    adapter=adapter, max_steps=4)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True, "browser.control": True}})
    assert await _run_task(env, stack["task"]["id"], timeout=90) == "completed"

    async with env.svc.db.session() as s:
        from bcc.v2.tables import browser_sessions as bs_t
        sid = int((await s.execute(sa.select(bs_t.c.id).order_by(bs_t.c.id.desc()))).first()[0])

    mgr = env.svc.browser
    await mgr.takeover(sid)
    from bcc.v2.browser_control import BrowserTakeoverActive
    with pytest.raises(BrowserTakeoverActive):
        await mgr.click(sid, "#go", actor="agent", approved=True)

    # человек всё ещё может действовать
    await mgr.click(sid, "#go", actor="human", approved=True)
    await mgr.resume(sid)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert "Принято" in snap["text"]              # после Resume DOM перечитывается
    await mgr.stop(sid)


@pytestmark_browser
async def test_browser_payment_is_never_allowed(env):
    """DENY навсегда: платёж/кошелёк не одобряются даже человеком."""
    from bcc.features.tools_browser import NEVER
    assert {"payment", "wallet", "bank_transfer", "purchase"} <= NEVER
    # таких инструментов нет в реестре в принципе
    assert not [n for n in REGISTRY.names()
                if any(k in n for k in ("payment", "wallet", "bank_transfer"))]


async def test_sensitive_browser_actions_ask_even_with_permission(env):
    """login/submit остаются ASK при выданном browser.control."""
    granted = {"permissions": {"browser.read": True, "browser.control": True}}
    for name in ("browser.submit", "browser.login"):
        spec = REGISTRY.get(name)
        assert spec is not None, f"{name} не зарегистрирован"
        assert decide_effect(spec, {"selector": "#go"}, granted)[0] == "ask"
    for name in ("browser.open", "browser.read_dom", "browser.click", "browser.type"):
        spec = REGISTRY.get(name)
        assert decide_effect(spec, {"url": "http://x/", "selector": "#a"}, granted)[0] == "auto"
