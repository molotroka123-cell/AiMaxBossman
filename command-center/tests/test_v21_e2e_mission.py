"""V2.1 §20 — главный сквозной тест: автономная миссия с 10+ вызовами инструментов.

Что здесь НАСТОЯЩЕЕ:
  * модель — отдельный HTTP-процесс, отвечающий боевым OpenAI-форматом
    (tool_calls разбирает реальный bcc/providers.py, а не подменённый адаптер);
  * терминал — реальные процессы в разрешённом корне;
  * память — реальный индекс по реальному Obsidian-хранилищу;
  * MCP — реальный сервер на официальном SDK в отдельном процессе;
  * браузер — настоящий Chromium по локальной странице;
  * approvals, Reviewer Gate, tool_calls, checkpoints — боевые.

Что подменено: только «сообразительность» модели — следующий шаг выбирается по
детерминированному сценарию. Это честная граница: без GPU-модели в этой среде
рассуждение не проверить, а вот исполнение — проверяется целиком.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import (approvals as approvals_t, missions as missions_t, settings_kv,
                    tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from bcc.v2.tables import mcp_servers as mcp_servers_t
from .browser_support import chromium_available, reason as browser_reason

FIXTURES = Path(__file__).parent / "fixtures"

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Калькулятор</title></head><body>
<h1>Проверка калькулятора</h1>
<p id="result">2 + 2 = 4</p>
</body></html>"""

CONVENTION = """# Соглашения проекта

## Тесты
Каждая правка кода сопровождается запуском `python -m pytest -q` в корне проекта.

## Стиль
Функции складывают через оператор `+`. Вычитание в `add` — известная ошибка,
её уже допускали раньше: см. журнал инцидентов.
"""


# ---------------------------------------------------------------- окружение

@pytest.fixture
def project(tmp_path):
    """Мини-проект с падающим тестом."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n", encoding="utf-8")
    return root


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "conventions.md").write_text(CONVENTION, encoding="utf-8")
    return root


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(PAGE, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()
    server.server_close()


@pytest.fixture
def scripted_llm(tmp_path):
    """Поднимает «модель» отдельным процессом; отдаёт (base_url, script_path, log)."""
    script = tmp_path / "script.json"
    script.write_text("[]", encoding="utf-8")
    log = tmp_path / "llm.log"
    env = {**os.environ, "SCRIPT_FILE": str(script), "SCRIPT_LOG": str(log),
           "SCRIPT_PORT": "0"}
    proc = subprocess.Popen([sys.executable, str(FIXTURES / "scripted_llm_server.py")],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    port = None
    deadline = time.time() + 15
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line.startswith("SCRIPTED_LLM_PORT="):
            port = int(line.strip().split("=")[1])
            break
    assert port, "сценарная модель не поднялась"
    yield f"http://127.0.0.1:{port}/v1", script, log
    proc.terminate()
    proc.wait(timeout=10)


async def _make_stack(env, base_url: str, *, tools: list[str], max_steps: int = 24):
    provider = (await env.client.post("/api/providers", json={
        "name": "сценарная", "kind": "openai_compat", "base_url": base_url,
        "api_key": "sk-test"})).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"], "name": "scripted-coder", "alias": "scripted-coder",
        "kind": "local"})).json()
    agent = (await env.client.post("/api/agents", json={
        "name": "Инженер", "role": "coder",
        "system_prompt": "Ты инженер. Пользуйся инструментами, не выдумывай результаты.",
        "model_id": model["id"], "max_steps": max_steps, "tools": tools,
        # filesystem.write НАМЕРЕННО не выдан: запись в хранилище знаний должна
        # спросить человека — это и есть единственное подтверждение в сценарии
        "permissions": {"terminal.run": True, "browser.read": True,
                        "browser.control": True}})).json()
    return {"provider": provider, "model": model, "agent": agent}


async def _configure_memory(env, vault: Path):
    r = await env.client.post("/api/memory/config", json={
        "root": str(vault), "index_folders": ["."], "backend": "local"})
    assert r.status_code == 200, r.text
    r = await env.client.post("/api/memory/index", json={})
    assert r.status_code == 200, r.text
    return r.json().get("result") or r.json()


async def _configure_terminal(env, root: Path):
    enc = env.svc.vault.encrypt(json.dumps([str(root)]))
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "terminal.roots"))
        await s.execute(sa.insert(settings_kv).values(key="terminal.roots", value_enc=enc))
        await s.commit()


async def _configure_mcp(env, counter: Path):
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo", transport="stdio",
            command=[sys.executable, str(FIXTURES / "mcp_echo_server.py")],
            url="", cwd="", env_keys=["MCP_ECHO_COUNTER"], enabled=True,
            status="unknown", created_at=utcnow()))
        await s.commit()
    r = await env.client.post("/api/mcp/runtime/servers/echo/connect")
    assert r.status_code == 200, r.text
    # политику ставим ДО discovery: ToolSpec берёт её в момент регистрации
    await env.client.post("/api/mcp/policy", json={"canonical": "mcp:echo:echo",
                                                   "policy": "auto"})
    r = await env.client.post("/api/mcp/runtime/servers/echo/refresh")
    assert r.status_code == 200, r.text


async def _drain(env, task_id: int, *, timeout: float, stop_on_approval: bool = True):
    """Крутит воркер до финала задачи.

    Останов на waiting_approval — только если РЕАЛЬНО есть непринятое решение:
    иначе, пока одобренный вызов возвращается в очередь, статус ещё
    waiting_approval, и наивная проверка вернулась бы зря.
    """
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    status = "?"
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            async with env.svc.db.session() as s:
                row = (await s.execute(sa.select(tasks_t.c.status)
                                       .where(tasks_t.c.id == task_id))).first()
                status = str(row[0]) if row else "?"
                if status in ("completed", "failed", "stopped"):
                    return status
                if stop_on_approval and status == "waiting_approval":
                    pending = (await s.execute(
                        sa.select(sa.func.count()).select_from(approvals_t)
                        .where(approvals_t.c.status == "pending"))).scalar()
                    if pending:
                        return status
            await asyncio.sleep(0.05)
        return status
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


# ---------------------------------------------------------------- сам тест

@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
async def test_autonomous_mission_with_ten_plus_tool_calls(
        env, project, vault, site, scripted_llm, tmp_path, monkeypatch):
    """§20: одна миссия — память, терминал, MCP, браузер, ревью, отчёт."""
    base_url, script_path, llm_log = scripted_llm
    counter = tmp_path / "mcp_calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))

    await _configure_terminal(env, project)
    indexed = await _configure_memory(env, vault)
    assert indexed.get("added", 0) >= 1, indexed
    await _configure_mcp(env, counter)

    fix = ("python - <<'PY'\n"
           "import pathlib, shutil\n"
           "p = pathlib.Path('calc.py')\n"
           "p.write_text('def add(a, b):\\n    # по соглашению проекта — сложение\\n"
           "    return a + b\\n')\n"
           "shutil.rmtree('__pycache__', ignore_errors=True)\n"
           "PY")

    # Сценарий «рассуждения». Каждый шаг — настоящий вызов настоящего инструмента.
    script = [
        {"tool": "memory_search", "arguments": {"query": "соглашения проекта тесты"}},
        {"tool": "terminal_run", "arguments": {"command": "cat calc.py",
                                               "mode": "project_host", "cwd": str(project)}},
        {"tool": "terminal_run", "arguments": {"command": "python -m pytest -q",
                                               "mode": "project_host", "cwd": str(project),
                                               "timeout": 60}},
        {"tool": "mcp_echo_echo", "arguments": {"text": "план: заменить минус на плюс"}},
        {"tool": "terminal_run", "arguments": {"command": fix, "mode": "project_host",
                                               "cwd": str(project)}},
        {"tool": "terminal_run", "arguments": {"command": "python -m pytest -q",
                                               "mode": "project_host", "cwd": str(project),
                                               "timeout": 60}},
        {"tool": "browser_open", "arguments": {"url": site}},
        {"tool": "browser_read_dom", "arguments": {}},
        {"tool": "terminal_run", "arguments": {"command": "git diff --stat || true",
                                               "mode": "project_host", "cwd": str(project)}},
        {"tool": "memory_write", "arguments": {"title": "Починка add", "kind": "lesson",
                                               "content": "add складывал через минус; "
                                                          "исправлено, тест зелёный."}},
        {"text": "Готово. Соглашение прочитано из памяти, тест был красным (add возвращал "
                 "a - b), исправлено на a + b, повторный прогон зелёный, страница "
                 "показывает 2 + 2 = 4, урок записан в память."},
    ]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    stack = await _make_stack(env, base_url, tools=[
        "memory.search", "memory.write", "terminal.run",
        "browser.open", "browser.read_dom", "mcp:echo:echo"])

    mission = (await env.client.post("/api/missions", json={
        "title": "Починить проект и отчитаться",
        "goal": "Выполнить 1 задачу: изучить проект, починить тест, проверить в браузере",
        "max_workers": 1, "duration_minutes": 30})).json()
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.mission_id == mission["id"]).values(
            agent_id=stack["agent"]["id"], kind="coding",
            prompt="Изучи проект, сверься с соглашениями в памяти, почини падающий тест, "
                   "проверь страницу в браузере и запиши урок в память."))
        await s.commit()
        task_id = int((await s.execute(sa.select(tasks_t.c.id)
                                       .where(tasks_t.c.mission_id == mission["id"]))).first()[0])

    await env.client.post(f"/api/missions/{mission['id']}/start")

    # --- одно намеренное подтверждение человека: memory.write требует ASK
    status = await _drain(env, task_id, timeout=180)
    approvals_seen = 0
    while status == "waiting_approval":
        approvals_seen += 1
        appr = (await env.client.get("/api/approvals")).json()
        async with env.svc.db.session() as s:
            pending = [dict(r._mapping) for r in (await s.execute(
                sa.select(tool_calls_t).where(
                    tool_calls_t.c.status == "pending_approval"))).fetchall()]

        assert appr, f"задача ждёт подтверждения, но approval не создан; " \
                     f"незакрытые вызовы: {pending}"
        await env.client.post(f"/api/approvals/{appr[0]['id']}",
                              json={"approve": True, "by": "оператор"})
        status = await _drain(env, task_id, timeout=180)

    assert status == "completed", f"миссия не завершилась: {status}"
    assert approvals_seen == 1, "ожидалось ровно одно подтверждение (memory.write)"

    # --- проверяем РЕЗУЛЬТАТ, а не рапорт
    assert (project / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")

    async with env.svc.db.session() as s:
        rows = [dict(r._mapping) for r in
                (await s.execute(sa.select(tool_calls_t).order_by(tool_calls_t.c.id))).fetchall()]

    assert len(rows) >= 10, f"нужно 10+ вызовов инструментов, получено {len(rows)}"
    used = {r["tool"] for r in rows}
    assert {"memory.search", "terminal.run", "browser.open", "browser.read_dom",
            "mcp:echo:echo", "memory.write"} <= used, used
    assert all(r["status"] in ("executed", "error") for r in rows), \
        [r for r in rows if r["status"] not in ("executed", "error")]
    # разные источники инструментов реально задействованы
    assert {"memory", "terminal", "browser", "mcp"} <= {r["source"] for r in rows}

    # MCP-вызов дошёл до серверного процесса (счётчик пишет сам сервер)
    assert counter.exists() and "echo" in counter.read_text(encoding="utf-8")

    # модели каждый раз предлагались только выданные инструменты
    offered = [json.loads(line)["tools_offered"] for line in
               llm_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert offered, "модель не получила ни одного запроса"
    for names in offered:
        assert set(names) == {"memory_search", "memory_write", "terminal_run",
                              "browser_open", "browser_read_dom", "mcp_echo_echo"}, names

    # память реально нашла соглашение, а не пустоту
    mem_call = next(r for r in rows if r["tool"] == "memory.search")
    assert "соглашени" in mem_call["result_preview"].lower() \
        or "pytest" in mem_call["result_preview"].lower(), mem_call["result_preview"]

    # миссия закрыта, прогресс 100%. Тик миссий — фоновая петля фичи, а в тестах
    # фоновые тики не запущены (start_workers=False), поэтому дёргаем явно.
    from bcc.features.missions import _tick as mission_tick
    await mission_tick(env.svc)
    async with env.svc.db.session() as s:
        m = dict((await s.execute(sa.select(missions_t)
                                  .where(missions_t.c.id == mission["id"]))).first()._mapping)
    assert m["status"] == "completed" and (m["progress"] or 0) >= 1.0


@pytest.mark.skipif(shutil.which("opencode") is not None,
                    reason="есть настоящий opencode — отдельный host-smoke")
def test_real_host_smoke_is_not_claimed():
    """Честность §20: реальный host-smoke на opencode здесь не выполнялся."""
    assert shutil.which("opencode") is None
