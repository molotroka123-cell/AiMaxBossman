"""PHASE 11 — GOLDEN MISSIONS: end-to-end proof that an intent becomes a real effect.

Every mission here asserts the SAME five links, in order, and fails if any one of
them is missing:

  1. intent      — the production classifier really sees an action task in the
                   prompt (bcc.features.action_contract.classify_all /
                   action_router.classify), i.e. the mission is the kind of task
                   the contract layer is supposed to police;
  2. dispatch    — a `tool_calls` row exists for the executing tool with the
                   arguments the model actually sent, and its status is the real
                   one (`executed` / `denied`), plus the owner decision that
                   authorised it;
  3. post-state  — the CURRENT state of the world is read back INDEPENDENTLY of
                   the tool result and of the model's answer: the file is
                   re-opened from disk by the test process, the child process is
                   asked over a socket, the child MCP server's own counter file is
                   read, `pytest` is re-run in a fresh subprocess, `git log` is
                   re-read, ffprobe re-opens the rendered mp4;
  4. evidence    — the receipts the system itself keeps: `task.finalized` checks
                   (`verification=VERIFIED`, expectations counted) where a real
                   post-state verifier exists for that capability, otherwise the
                   `tool_calls` receipt trail plus the approval records;
  5. status      — the final `tasks.status`, which must be `completed` only when
                   the world actually changed and `failed` when it provably did
                   not.

No mission asserts the model's prose. Two missions (10, denied action) make the
model claim success in words on purpose, so that a text-only check would pass
where the world-check must fail.

Layering assumed (and asserted): the finalizer (bcc/finalize.py) enforces only
DECLARED obligations (`meta.review.evidence` / `meta.required_effects`) and
treats denied/rejected calls as "the effect provably did not happen"; the
zero-attempt / failed-attempt veto for classified action tasks lives in
bcc/features/action_contract._gate, which counts only `executed` calls of the
matching NON-READING tool family.

Honest skips (no faking of effects): browser missions need Chromium, the MCP
mission needs the official `mcp` SDK, the video mission needs ffmpeg plus the
bossman-core video_factory, the OpenCode mission runs against the project's
deterministic fake OpenCode server because no `opencode` binary exists here (the
same honesty boundary tests/test_v21_opencode.py already draws).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import sqlalchemy as sa

from bcc.db import (approvals as approvals_t, settings_kv, tasks as tasks_t,
                    tool_calls as tool_calls_t, utcnow)
from bcc.features import action_router
from bcc.features.action_contract import classify_all
from bcc.tools import REGISTRY
from bcc.v2.tables import mcp_servers as mcp_servers_t, terminal_sessions as term_t
from bcc.v2.verification import observe_pid

from .browser_support import chromium_available, reason as browser_reason
from .conftest import wait_for
from .test_v21_tool_loop import ToolAdapter, _stack_with_tools

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "BOSSMAN-GOLDEN"
FINAL = ("completed", "failed", "stopped")
OWNER = "владелец"


# ============================================================ harness helpers

async def _allow_root(env, *roots: Path) -> None:
    """terminal.roots — единственный источник разрешённых корней (owner setting)."""
    enc = env.svc.vault.encrypt(json.dumps([str(Path(r).resolve()) for r in roots]))
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "terminal.roots"))
        await s.execute(sa.insert(settings_kv).values(key="terminal.roots", value_enc=enc))
        await s.commit()


async def _merge_meta(env, task_id: int, patch: dict) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(tasks_t.c.meta).where(tasks_t.c.id == task_id))).first()
        meta = dict(row._mapping["meta"]) if row and isinstance(row._mapping["meta"], dict) else {}
        meta.update(patch)
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task_id).values(
            meta=meta, updated_at=utcnow()))
        await s.commit()
    return meta


async def _mission_stack(env, *, prompt: str, tools: list[str], adapter: ToolAdapter,
                         evidence: list[dict] | None = None,
                         permissions: dict | None = None, max_steps: int = 10) -> dict:
    """Задача миссии: инструменты выданы явно (как это делает скилл/миссия), а
    объявленные обязательства — через тот же `meta.review.evidence`, который
    читает и review_gate, и финализатор."""
    stack = await _stack_with_tools(env, tools, adapter=adapter, prompt=prompt,
                                    max_steps=max_steps)
    if permissions is not None:
        r = await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                                   json={"permissions": permissions})
        assert r.status_code == 200, r.text
    patch: dict = {"allowed_tools": list(tools)}
    if evidence is not None:
        patch["review"] = {"reviewer_agent_id": None, "criteria": "",
                           "evidence": evidence, "max_review_retries": 2}
    await _merge_meta(env, stack["task"]["id"], patch)
    return stack


async def _drain(env, task_id: int, *, timeout: float) -> str:
    """Крутит настоящий воркер до финала задачи или до НЕРЕШЁННОГО approval'а."""
    env.svc.engine.poll_interval = 0.02
    worker = asyncio.create_task(env.svc.engine.worker_loop())
    watcher = asyncio.create_task(env.svc.engine.approval_watcher())
    status = "?"
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            async with env.svc.db.session() as s:
                row = (await s.execute(sa.select(tasks_t.c.status)
                                       .where(tasks_t.c.id == task_id))).first()
                status = str(row[0]) if row else "?"
            if status in FINAL:
                # Статус пишется на строку раньше, чем финализатор успевает
                # положить в журнал `task.finalized`. Снимать воркер сразу по
                # статусу — значит отменить его ровно на этом await и потерять
                # квитанцию, которой миссия и доказывает свою цепочку. Поэтому
                # ждём квитанцию, НЕ убивая воркер, и ограниченно.
                grace = loop.time() + 2.0
                while loop.time() < grace:
                    if await _events(env, "task.finalized", task_id):
                        break
                    await asyncio.sleep(0.05)
                return status
            async with env.svc.db.session() as s:
                if status == "waiting_approval":
                    pending = (await s.execute(
                        sa.select(sa.func.count()).select_from(approvals_t).where(sa.and_(
                            approvals_t.c.task_id == task_id,
                            approvals_t.c.status == "pending")))).scalar()
                    if pending:
                        return status
            await asyncio.sleep(0.05)
        return status
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def _run_mission(env, task_id: int, *, timeout: float = 180.0,
                       max_approvals: int = 12) -> tuple[str, list[dict]]:
    """Прогон миссии; владелец подтверждает КАЖДЫЙ tool-ASK.

    `review_escalation` НЕ подтверждается никогда: эскалация означает, что
    миссия не доказала себя, и тест обязан это увидеть, а не замести.
    """
    approved: list[dict] = []
    status = await _drain(env, task_id, timeout=timeout)
    while status == "waiting_approval" and len(approved) < max_approvals:
        pending = [a for a in (await env.client.get("/api/approvals")).json()
                   if a.get("task_id") in (None, task_id)]
        tool_pending = [a for a in pending if a.get("kind") == "tool"]
        if not tool_pending:
            break
        for appr in tool_pending:
            r = await env.client.post(f"/api/approvals/{appr['id']}",
                                      json={"approve": True, "by": OWNER})
            assert r.status_code == 200, r.text
            approved.append(appr)
        status = await _drain(env, task_id, timeout=timeout)
    return status, approved


async def _tool_rows(env, task_id: int) -> list[dict]:
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(tool_calls_t)
                                .where(tool_calls_t.c.task_id == task_id)
                                .order_by(tool_calls_t.c.id))).fetchall()
    return [dict(r._mapping) for r in rows]


async def _status(env, task_id: int) -> str:
    return (await env.client.get(f"/api/tasks/{task_id}")).json()["task"]["status"]


def _payload(event: dict) -> dict:
    data = event.get("data")
    return data if isinstance(data, dict) else event


async def _events(env, kind: str, task_id: int | None = None) -> list[dict]:
    out = []
    for e in await env.svc.bus.recent(500):
        if e.get("kind") != kind:
            continue
        p = _payload(e)
        if task_id is None or p.get("task_id") == task_id or e.get("task_id") == task_id:
            out.append(p)
    return out


async def _finalized(env, task_id: int, *, timeout: float = 5.0) -> list[dict]:
    """События финализации задачи.

    Статус `completed` попадает в БД чуть раньше, чем `task.finalized` ложится в
    журнал (finalize_task пишет статус, затем событие), поэтому опрос ждёт
    ограниченное время, а не читает журнал в ту же миллисекунду. Пустой список
    после ожидания — это ответ «финализации не было», а не гонка.
    """
    async def check():
        return await _events(env, "task.finalized", task_id) or None
    try:
        return await wait_for(check, timeout=timeout)
    except AssertionError:
        return []


async def _assert_verified_evidence(env, task_id: int, *, expectations: int) -> dict:
    """Ссылка «evidence» цепочки: финализатор ПЕРЕЧИТАЛ мир и сказал VERIFIED."""
    fin = await _finalized(env, task_id)
    assert fin, "нет события task.finalized — задача завершилась мимо канонической точки"
    checks = fin[-1]["checks"]
    assert fin[-1]["override"] is False, "завершено человеком-override, а не доказательством"
    assert checks["verification"] == "VERIFIED", checks
    assert checks["expectations"] == expectations, checks
    assert checks["fresh"] is True, checks
    results = await _events(env, "verification.result", task_id)
    assert results and results[-1]["status"] == "VERIFIED", results
    started = await _events(env, "observation.started", task_id)
    assert started, "не было свежего наблюдения пост-состояния"
    return checks


def _one(rows: list[dict], tool: str) -> dict:
    hits = [r for r in rows if r["tool"] == tool]
    assert len(hits) == 1, f"ожидался ровно один вызов {tool}, получено {len(hits)}: {hits}"
    return hits[0]


def _pytest_run(where: Path) -> subprocess.CompletedProcess:
    """Независимый прогон тестов проекта ОТДЕЛЬНЫМ процессом (не через агента)."""
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."],
                          cwd=str(where), capture_output=True, text=True, timeout=180,
                          env={**os.environ, "PYTHONPATH": str(where)})


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=60)


# ============================================================ fixtures

@pytest.fixture
def project(tmp_path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def calc_repo(tmp_path) -> Path:
    """Git-репозиторий с ПАДАЮЩИМ тестом — исходное состояние мира миссий 8 и 12."""
    root = (tmp_path / "projects").resolve()
    project = root / "calcapp"
    project.mkdir(parents=True)
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (project / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
        encoding="utf-8")
    assert _git(project, "init", "-b", "main").returncode == 0
    assert _git(project, "config", "user.email", "bossman@test").returncode == 0
    assert _git(project, "config", "user.name", "BOSSMAN").returncode == 0
    assert _git(project, "add", "-A").returncode == 0
    assert _git(project, "commit", "-m", "init").returncode == 0
    return project


@pytest.fixture
def vault(tmp_path) -> Path:
    root = tmp_path / "vault"
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "conventions.md").write_text(
        "# Соглашения проекта\n\n## Тесты\nКаждая правка кода сопровождается "
        "запуском `python -m pytest -q`.\n\n## Стиль\nФункция add складывает "
        "через оператор `+`.\n", encoding="utf-8")
    return root


async def _configure_memory(env, vault: Path) -> dict:
    r = await env.client.post("/api/memory/config", json={
        "root": str(vault), "index_folders": ["."], "backend": "local"})
    assert r.status_code == 200, r.text
    r = await env.client.post("/api/memory/index", json={})
    assert r.status_code == 200, r.text
    return r.json().get("result") or r.json()


@pytest.fixture
def allow_private_browser(monkeypatch):
    """F-010: локальный тестовый сайт живёт на 127.0.0.1 — owner-override, как в
    tests/test_v21_tools_terminal_browser.py; сама политика проверяется там же."""
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


FORM_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Заявка BOSSMAN</title></head><body>
<h1>Форма заявки</h1>
<form method="POST" action="/submit">
  <input id="name" name="name" value="">
  <input id="email" name="email" value="">
  <button id="go" type="submit">Отправить</button>
</form>
</body></html>"""

THANKS_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Заявка принята</title></head><body><h1>Заявка принята</h1>
<p id="out">спасибо</p></body></html>"""


@pytest.fixture
def form_site(tmp_path):
    """Настоящий сайт с формой; отправленные поля пишет СЕРВЕР в свой файл.

    Этот файл — пост-состояние, до которого агент не дотягивается: он живёт на
    стороне сервера и читается тестом напрямую.
    """
    inbox = tmp_path / "form-inbox.jsonl"

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._send(FORM_PAGE.encode("utf-8"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            fields = {k: v[0] for k, v in parse_qs(raw).items()}
            with inbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"path": self.path, "fields": fields},
                                    ensure_ascii=False) + "\n")
            self._send(THANKS_PAGE.encode("utf-8"))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/form.html", inbox
    server.shutdown()
    server.server_close()


def _submissions(inbox: Path) -> list[dict]:
    if not inbox.exists():
        return []
    return [json.loads(line) for line in inbox.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ============================================================ MISSION 1

async def test_mission_01_real_file_create(env, project):
    """Файл создан по-настоящему: он появляется на диске, и это видит не модель."""
    await _allow_root(env, project)
    target = project / "release_notes.md"
    prompt = (f"Создай файл release_notes.md в проекте и запиши в него строку "
              f"{TOKEN}-CREATE.")
    # 1. intent
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = f"printf '%s\\n' '{TOKEN}-CREATE' > release_notes.md"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project)}),
        ("text", "готово"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(target),
                   "expect": {"exists": True, "contains": f"{TOKEN}-CREATE"}}])
    task_id = stack["task"]["id"]
    assert not target.exists(), "мир должен быть чистым до миссии"

    status, approvals = await _run_mission(env, task_id)

    # 5. status
    assert status == "completed", f"миссия не завершилась: {status}"
    # 2. dispatch
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "executed" and call["source"] == "terminal"
    assert call["args"]["command"] == command
    assert call["effect"] == "ask" and call["approved_by"] == OWNER
    assert call["result_preview"].startswith("exit_code=0")
    preview = approvals[0]["preview"] if approvals else ""
    assert "terminal.run" in preview and "release_notes.md" in preview, preview
    # 3. post-state, прочитанное тестом с диска, а не из ответа инструмента
    assert target.exists() and target.is_file()
    assert target.read_text(encoding="utf-8").strip() == f"{TOKEN}-CREATE"
    assert target.stat().st_size > 0
    # 4. evidence
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 2

async def test_mission_02_real_file_edit(env, project):
    """Правка существующего файла: старое содержимое ИСЧЕЗЛО, новое на диске."""
    await _allow_root(env, project)
    target = project / "config.ini"
    target.write_text("[app]\nmode = OLD-MODE\n", encoding="utf-8")
    before = target.read_bytes()

    prompt = "Исправь файл config.ini: замени значение mode на NEW-MODE."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = (f"{sys.executable} - <<'PY'\n"
               "from pathlib import Path\n"
               "p = Path('config.ini')\n"
               "p.write_text(p.read_text(encoding='utf-8').replace('OLD-MODE', 'NEW-MODE'),"
               " encoding='utf-8')\n"
               "PY")
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project)}),
        ("text", "правка внесена"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(target),
                   "expect": {"exists": True, "contains": "NEW-MODE"}}])
    task_id = stack["task"]["id"]

    status, _ = await _run_mission(env, task_id)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "executed" and call["result_preview"].startswith("exit_code=0")
    # post-state: файл перечитан с диска; старая строка обязана исчезнуть
    after = target.read_text(encoding="utf-8")
    assert "NEW-MODE" in after and "OLD-MODE" not in after
    assert target.read_bytes() != before, "файл не изменился ни на байт"
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 3

async def test_mission_03_terminal_command(env, project):
    """Команда терминала — настоящий процесс: он сам сообщает свой pid и cwd,
    и они сходятся со строкой terminal_sessions, которую вёл менеджер."""
    await _allow_root(env, project)
    proof = project / "proc.json"
    prompt = "Запусти в терминале команду, которая сохранит отчёт о процессе."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = (f"{sys.executable} - <<'PY'\n"
               "import json, os\n"
               "json.dump({'pid': os.getpid(), 'ppid': os.getppid(), 'cwd': os.getcwd()},\n"
               "          open('proc.json', 'w'))\n"
               "PY")
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project), "timeout": 60}),
        ("text", "команда выполнена"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(proof),
                   "expect": {"exists": True, "contains": "\"pid\""}}])
    task_id = stack["task"]["id"]

    status, _ = await _run_mission(env, task_id)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "executed" and call["result_preview"].startswith("exit_code=0")

    # post-state: то, что о себе написал САМ порождённый процесс
    data = json.loads(proof.read_text(encoding="utf-8"))
    assert data["pid"] != os.getpid(), "команда исполнилась в процессе теста, а не отдельно"
    assert Path(data["cwd"]).resolve() == project.resolve()
    # и это тот же процесс, который завёл менеджер терминала
    async with env.svc.db.session() as s:
        sessions = [dict(r._mapping) for r in (await s.execute(
            sa.select(term_t).where(term_t.c.task_id == task_id))).fetchall()]
    assert len(sessions) == 1, sessions
    session = sessions[0]
    assert session["status"] == "finished" and session["exit_code"] == 0
    assert session["command"] == command
    assert session["pid"] in (data["pid"], data["ppid"]), (session["pid"], data)
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 4

@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
async def test_mission_04_browser_form_interaction(env, form_site, allow_private_browser):
    """Форма реально отправлена: поля лежат в файле СЕРВЕРА, а не в ответе модели."""
    url, inbox = form_site
    prompt = f"Открой сайт {url} в браузере, заполни форму заявки и отправь её."
    # 1. intent — браузерный роутер (MODULE 1) узнаёт действие над браузером
    assert action_router.classify(prompt) == action_router.CAPABILITY_BROWSER

    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_type", {"selector": "#name", "text": "Тимур"}),
        ("tool", "browser_type", {"selector": "#email", "text": "timur@example.org"}),
        ("tool", "browser_click", {"selector": "#go"}),
        ("tool", "browser_read_dom", {}),
        ("text", "форма отправлена"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, max_steps=10,
        tools=["browser.open", "browser.read_dom", "browser.type", "browser.click"],
        adapter=adapter,
        permissions={"browser.read": True, "browser.control": True},
        evidence=[{"kind": "browser", "target": url,
                   "expect": {"url_contains": "/submit", "title_contains": "принята"}}])
    task_id = stack["task"]["id"]
    assert _submissions(inbox) == []

    status, _ = await _run_mission(env, task_id, timeout=180.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    assert [r["tool"] for r in rows] == ["browser.open", "browser.type", "browser.type",
                                         "browser.click", "browser.read_dom"]
    assert all(r["status"] == "executed" and r["source"] == "browser" for r in rows)
    assert _one([r for r in rows if r["tool"] == "browser.click"], "browser.click")

    # post-state: сервер записал ровно то, что ушло из браузера
    posted = _submissions(inbox)
    assert len(posted) == 1, posted
    assert posted[0]["path"] == "/submit"
    assert posted[0]["fields"] == {"name": "Тимур", "email": "timur@example.org"}
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 5

@pytest.fixture
def apps_root(tmp_path, monkeypatch):
    from bcc.features import apps as apps_mod
    from bcc.features import apps_control as ctl
    root = tmp_path / "apps-under-test"
    root.mkdir()
    monkeypatch.setattr(apps_mod, "APPS_DIR", root)
    monkeypatch.setattr(apps_mod, "_cache", {"at": 0.0, "apps": []})
    monkeypatch.setattr(ctl, "READY_TIMEOUT", 20.0)
    monkeypatch.setenv(ctl.FLAG, "1")
    yield root
    for rec in list(ctl._processes.values()):       # ни одного сироты после теста
        try:
            rec.proc.kill()
            rec.proc.wait(timeout=5)
        except Exception:                            # noqa: BLE001 — уборка не роняет прогон
            pass
        ctl._forget(rec)
    ctl._processes.clear()


async def test_mission_05_app_launch(env, apps_root):
    """Приложение запущено: чужой процесс живёт, слушает порт и отвечает нам."""
    from .test_apps_control import make_app

    app_dir, port = make_app(apps_root, "golden-app")
    prompt = "Запусти приложение golden-app на моём компьютере."
    assert "APPS_ACTION" in {c.name for c in classify_all(prompt)}

    adapter = ToolAdapter([
        ("tool", "apps_start", {"app_id": "golden-app"}),
        ("text", "приложение запущено"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["apps.start", "apps.status"], adapter=adapter,
        evidence=[{"kind": "app", "target": "golden-app", "expect": {"running": True}}])
    task_id = stack["task"]["id"]

    status, approvals = await _run_mission(env, task_id, timeout=120.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "apps.start")
    assert call["status"] == "executed" and call["source"] == "apps"
    assert call["effect"] == "ask" and call["approved_by"] == OWNER
    assert approvals and "apps.start" in approvals[0]["preview"]

    # post-state №1: процесс живой по независимым источникам (procfs/сигнал/psutil)
    from bcc.features import apps_control as ctl
    info = ctl.process_info("golden-app", env.svc.settings.data_dir)
    assert info["running"] and info["pid"]
    assert observe_pid(int(info["pid"])).get("running") is True, observe_pid(int(info["pid"]))
    assert int(info["pid"]) != os.getpid()
    # post-state №2: он реально слушает порт и отвечает на HTTP
    async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
        r = await client.get(f"http://127.0.0.1:{port}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    # post-state №3: ребёнок сам записал свой запуск (argv/cwd) на диск
    spawn = json.loads((app_dir / "spawn.json").read_text(encoding="utf-8"))
    assert spawn["argv"][1] == "serve"
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 6

async def test_mission_06_memory_write(env, vault):
    """Заметка записана в память: файл лежит в vault и находится свежим поиском."""
    await _configure_memory(env, vault)
    prompt = "Запомни решение: релизы выкатываем только после зелёного pytest."
    assert "MEMORY_ACTION" in {c.name for c in classify_all(prompt)}

    body = f"{TOKEN}-MEMORY: релиз выкатывается только после зелёного pytest."
    adapter = ToolAdapter([
        ("tool", "memory_write", {"title": "Правило релиза", "kind": "decision",
                                  "content": body}),
        ("text", "запомнил"),
    ])
    # evidence НЕ объявляем: у семейства memory нет пост-верификатора для
    # vault-файла (bcc/finalize._effect_problem: required_kinds["memory"] ==
    # {"memory", "db"}), и объявлять обязательство, которое система не умеет
    # проверять, значило бы соврать про доказательство. Пост-состояние читает
    # сам тест — с диска и повторным поиском.
    stack = await _mission_stack(env, prompt=prompt, tools=["memory.search", "memory.write"],
                                 adapter=adapter)
    task_id = stack["task"]["id"]
    before = {p.name for p in vault.rglob("*.md")}

    status, approvals = await _run_mission(env, task_id, timeout=120.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "memory.write")
    assert call["status"] == "executed" and call["source"] == "memory"
    assert call["effect"] == "ask" and call["approved_by"] == OWNER
    assert approvals and "memory.write" in approvals[0]["preview"]

    # post-state №1: новый файл в хранилище владельца, прочитанный с диска
    created = [p for p in vault.rglob("*.md") if p.name not in before]
    assert len(created) == 1, created
    note = created[0]
    assert "BOSSMAN Memory" in str(note.parent), note
    assert body in note.read_text(encoding="utf-8")
    # post-state №2: независимое переиндексирование находит заметку
    r = await env.client.post("/api/memory/index", json={"force": True})
    assert r.status_code == 200, r.text
    r = await env.client.post("/api/memory/search", json={"query": "правило релиза pytest"})
    assert r.status_code == 200, r.text
    found = json.dumps(r.json(), ensure_ascii=False)
    assert f"{TOKEN}-MEMORY" in found, found[:800]
    # evidence: квитанции системы (событие инструмента + строка вызова)
    tool_events = [e for e in await _events(env, "agent.tool_call")
                   if e.get("tool") == "memory.write"]
    assert tool_events and tool_events[-1]["path"].endswith(note.name)
    fin = await _finalized(env, task_id)
    assert fin and fin[-1]["checks"]["verification"] == "NOT_REQUIRED"


# ============================================================ MISSION 7

def _mcp_sdk_available() -> bool:
    from bcc.v2.mcp_runtime import sdk_available
    return bool(sdk_available())


@pytest.mark.skipif(not _mcp_sdk_available(),
                    reason="официальный MCP SDK (pip install mcp) не установлен")
async def test_mission_07_mcp_tool_call(env, tmp_path, monkeypatch):
    """MCP-вызов дошёл до ЧУЖОГО процесса: счётчик пишет сам сервер, не мы."""
    counter = tmp_path / "mcp-calls.txt"
    monkeypatch.setenv("MCP_ECHO_COUNTER", str(counter))
    from bcc.features.tools_mcp import unregister_server_tools

    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="echo", transport="stdio",
            command=[sys.executable, str(FIXTURES / "mcp_echo_server.py")],
            url="", cwd="", env_keys=["MCP_ECHO_COUNTER"], enabled=True,
            status="unknown", created_at=utcnow()))
        await s.commit()
    r = await env.client.post("/api/mcp/runtime/servers/echo/connect")
    assert r.status_code == 200 and r.json()["status"] == "healthy", r.text
    r = await env.client.post("/api/mcp/runtime/servers/echo/refresh")
    assert r.status_code == 200, r.text
    assert REGISTRY.get("mcp:echo:write_note") is not None

    try:
        prompt = "Используй MCP-сервер echo, чтобы записать заметку о релизе."
        assert "MCP_ACTION" in {c.name for c in classify_all(prompt)}

        adapter = ToolAdapter([
            ("tool", "mcp_echo_write_note", {"text": f"{TOKEN}-MCP"}),
            ("text", "заметка отправлена в MCP"),
        ])
        stack = await _mission_stack(env, prompt=prompt, tools=["mcp:echo:write_note"],
                                     adapter=adapter)
        task_id = stack["task"]["id"]
        assert not counter.exists()

        status, approvals = await _run_mission(env, task_id, timeout=120.0)

        assert status == "completed", status
        rows = await _tool_rows(env, task_id)
        call = _one(rows, "mcp:echo:write_note")
        assert call["status"] == "executed" and call["source"] == "mcp"
        assert call["effect"] == "ask" and call["approved_by"] == OWNER
        assert approvals and "mcp:echo:write_note" in approvals[0]["preview"]

        # post-state: файл, который ведёт сам процесс MCP-сервера
        assert counter.exists(), "MCP-сервер не получил ни одного вызова"
        lines = [ln for ln in counter.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines == ["write_note"], lines          # ровно один вызов, и именно этот
        assert f"{TOKEN}-MCP" in call["result_preview"] or "записано #1" in call["result_preview"]
        fin = await _finalized(env, task_id)
        assert fin and fin[-1]["checks"]["verification"] == "NOT_REQUIRED"
    finally:
        rt = getattr(env.svc, "mcp", None)
        if rt is not None:
            await rt.shutdown()
        unregister_server_tools("echo")


# ============================================================ MISSION 8

async def test_mission_08_opencode_bug_fix(env, calc_repo, monkeypatch):
    """Баг починен кодинг-агентом: красный тест стал зелёным в НАСТОЯЩЕМ прогоне.

    Бинаря `opencode` в этой среде нет, поэтому сервер — детерминированный
    фальшивый сервер проекта (tests/fixtures/fake_opencode_server.py), который
    ПО-НАСТОЯЩЕМУ правит файлы. Граница честности та же, что уже проведена в
    tests/test_v21_opencode.py; см. отчёт.
    """
    from .fixtures.fake_opencode_server import FakeOpenCode

    server = FakeOpenCode()
    server.start()
    try:
        monkeypatch.setenv("OPENCODE_URL", server.url)
        await _allow_root(env, calc_repo.parent)
        server.edits_file("почини", "calc.py", "def add(a, b):\n    return a + b\n",
                          reply="исправил знак в add")

        before = _pytest_run(calc_repo)
        assert before.returncode != 0, "фикстурный тест обязан падать ДО миссии"

        prompt = "Почини баг в коде: тест падает, функция add считает неверно."
        assert "CODE_ACTION" in {c.name for c in classify_all(prompt)}

        adapter = ToolAdapter([
            ("tool", "opencode_session_start", {"project_path": str(calc_repo),
                                                "worktree": True, "title": "починить add"}),
            ("tool", "opencode_send", {"text": "почини функцию add в calc.py"}),
            ("tool", "opencode_diff", {}),
            ("text", "баг исправлен"),
        ])
        stack = await _mission_stack(env, prompt=prompt, tools=["opencode.*"], adapter=adapter,
                                     max_steps=10)
        task_id = stack["task"]["id"]

        status, approvals = await _run_mission(env, task_id, timeout=180.0)

        assert status == "completed", status
        rows = await _tool_rows(env, task_id)
        assert [r["tool"] for r in rows] == ["opencode.session.start", "opencode.send",
                                             "opencode.diff"]
        assert all(r["status"] == "executed" and r["source"] == "opencode" for r in rows)
        assert {r["tool"] for r in rows if r["effect"] == "ask"} == {"opencode.session.start",
                                                                    "opencode.send"}
        assert len(approvals) == 2, approvals

        # post-state: worktree на диске, зелёный прогон ОТДЕЛЬНЫМ процессом
        from bcc.v2.tables import opencode_sessions as oc_t
        async with env.svc.db.session() as s:
            oc_rows = [dict(r._mapping) for r in (await s.execute(sa.select(oc_t))).fetchall()]
        assert len(oc_rows) == 1, oc_rows
        worktree = Path(oc_rows[0]["worktree_path"])
        assert worktree.exists() and worktree != calc_repo
        assert (worktree / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")
        after = _pytest_run(worktree)
        assert after.returncode == 0, after.stdout + after.stderr
        # исходный репозиторий не тронут — правка изолирована
        assert (calc_repo / "calc.py").read_text(encoding="utf-8").strip().endswith("return a - b")
        assert _pytest_run(calc_repo).returncode != 0
        # evidence: дифф сохранён в журнале прогона
        from bcc.db import run_events
        async with env.svc.db.session() as s:
            events = [dict(r._mapping) for r in (await s.execute(sa.select(run_events).where(
                run_events.c.kind == "opencode.diff"))).fetchall()]
        assert events and events[-1]["data"]["diff"][0]["file"] == "calc.py"
        fin = await _finalized(env, task_id)
        assert fin and fin[-1]["checks"]["verification"] == "NOT_REQUIRED"
    finally:
        server.stop()


# ============================================================ MISSION 9

def _ffmpeg_available() -> bool:
    try:
        from bossman.video_factory.ffmpeg import ffmpeg_available
    except Exception:                                # noqa: BLE001 — bossman-core рядом нет
        return False
    return bool(ffmpeg_available())


@pytest.mark.skipif(not _ffmpeg_available(),
                    reason="нет пути рендера видео: bossman-core video_factory или ffmpeg "
                           "недоступны в этой среде")
async def test_mission_09_video_export(env, project):
    """Экспорт видео: на диске появляется НАСТОЯЩИЙ mp4, и его заново читает ffprobe.

    Путь рендера на этой ветке существует ровно один — bossman-core
    `bossman.video_factory.ffmpeg` (реальный ffmpeg, без shell). Своего
    ToolSpec у видео в Command Center нет, поэтому агент доходит до него
    единственным существующим способом — командой терминала.
    """
    from bossman.video_factory.ffmpeg import probe_media

    await _allow_root(env, project)
    out = project / "take-001.mp4"
    prompt = "Экспортируй видео: создай файл take-001.mp4 длительностью 1 секунда."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = (f"{sys.executable} - <<'PY'\n"
               "import asyncio\n"
               "from bossman.video_factory.ffmpeg import run_testsrc\n"
               "asyncio.run(run_testsrc('take-001.mp4', 1.0))\n"
               "PY")
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project), "timeout": 120}),
        ("text", "видео готово"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(out),
                   "expect": {"exists": True, "min_bytes": 1000}}])
    task_id = stack["task"]["id"]
    assert not out.exists()

    status, _ = await _run_mission(env, task_id, timeout=240.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "executed" and call["result_preview"].startswith("exit_code=0")

    # post-state: файл заново открыт ffprobe/ffmpeg — это видео, а не пустышка
    assert out.exists() and out.stat().st_size > 1000
    assert out.read_bytes()[4:8] == b"ftyp", "на диске не MP4-контейнер"
    duration, has_video = await probe_media(out)
    assert has_video and duration >= 0.5, (duration, has_video)
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 10

async def test_mission_10_dangerous_action_denied(env, project):
    """Опасное действие отклонено: мир цел, а задача НЕ выдана за выполненную.

    Модель здесь ВРЁТ текстом («папка удалена»). Проверка по ответу прошла бы;
    проверка по миру и по статусу обязана провалить задачу.
    """
    await _allow_root(env, project)
    canary = project / "important"
    canary.mkdir()
    (canary / "data.txt").write_text(f"{TOKEN}-INTACT", encoding="utf-8")

    prompt = "Удали папку important со всем содержимым."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "rm -rf / --no-preserve-root",
                                  "mode": "project_host", "cwd": str(project)}),
        ("text", "Готово: папка удалена, всё чисто."),
    ])
    stack = await _mission_stack(env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
                                 permissions={"terminal.run": True})
    task_id = stack["task"]["id"]

    status, approvals = await _run_mission(env, task_id, timeout=120.0)

    # 5. status: система не приняла текстовый рапорт за выполнение
    assert status == "failed", status
    assert approvals == [], "опасное действие не должно даже предлагаться на подтверждение"
    # 2. dispatch: вызов зафиксирован и ОТКЛОНЁН, а не исполнен
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["effect"] == "deny" and call["status"] == "denied", call
    assert call["approval_id"] is None and not call["approved_by"]
    # 3. post-state: мир цел — читаем с диска, а не из ответа модели
    assert canary.is_dir()
    assert (canary / "data.txt").read_text(encoding="utf-8") == f"{TOKEN}-INTACT"
    # 4. evidence: гейт контракта явно ветировал, финализации не было
    async with env.svc.db.session() as s:
        pending = (await s.execute(sa.select(sa.func.count()).select_from(approvals_t)
                                   .where(approvals_t.c.task_id == task_id))).scalar()
    assert pending == 0
    assert await _finalized(env, task_id, timeout=1.0) == []
    blocked = await _events(env, "action_contract.blocked", task_id)
    assert blocked and "TERMINAL_FILE_ACTION" in blocked[-1]["capabilities"]


# ============================================================ MISSION 11

async def test_mission_11_approval_then_resume(env, project):
    """Подтверждение и возобновление: до решения владельца эффекта НЕТ, после — есть."""
    await _allow_root(env, project)
    target = project / "approved_only.txt"
    prompt = "Создай файл approved_only.txt с отчётом о выпуске."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = f"printf '%s\\n' '{TOKEN}-APPROVED' > approved_only.txt"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project)}),
        ("text", "готово"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(target),
                   "expect": {"exists": True, "contains": f"{TOKEN}-APPROVED"}}])
    task_id = stack["task"]["id"]

    # --- фаза 1: остановка на подтверждении, мир НЕ изменён
    status = await _drain(env, task_id, timeout=60.0)
    assert status == "waiting_approval", status
    assert not target.exists(), "эффект наступил ДО решения владельца"
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "pending_approval" and call["effect"] == "ask"
    pending = (await env.client.get("/api/approvals")).json()
    assert len(pending) == 1 and pending[0]["kind"] == "tool"
    assert "terminal.run" in pending[0]["preview"], pending[0]["preview"]
    assert "approved_only.txt" in pending[0]["preview"], pending[0]["preview"]
    assert call["approval_id"] == pending[0]["id"]
    assert await _finalized(env, task_id, timeout=1.0) == []

    # --- фаза 2: владелец подтверждает — run продолжается с того же места
    r = await env.client.post(f"/api/approvals/{pending[0]['id']}",
                              json={"approve": True, "by": OWNER})
    assert r.status_code == 200, r.text
    status = await _drain(env, task_id, timeout=90.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")                # ровно один вызов, не два
    assert call["status"] == "executed" and call["approved_by"] == OWNER
    assert call["result_preview"].startswith("exit_code=0")
    # post-state: файл появился именно после подтверждения
    assert target.read_text(encoding="utf-8").strip() == f"{TOKEN}-APPROVED"
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 12

async def test_mission_12_multi_step_mixed_mission(env, calc_repo, vault):
    """Составная миссия: память → правка кода → тесты → коммит.

    Каждая сторона проверяется своим независимым чтением мира: файл с диска,
    отдельный прогон pytest, отдельный `git log`.
    """
    await _allow_root(env, calc_repo)
    await _configure_memory(env, vault)
    head_before = _git(calc_repo, "rev-parse", "HEAD").stdout.strip()
    assert _pytest_run(calc_repo).returncode != 0, "тест обязан падать до миссии"

    prompt = ("Почини баг в коде — в файле calc.py по соглашениям проекта, "
              "запусти тесты и закоммить результат.")
    caps = {c.name for c in classify_all(prompt)}
    assert {"TERMINAL_FILE_ACTION", "CODE_ACTION", "GITHUB_ACTION"} <= caps, caps

    fix = (f"{sys.executable} - <<'PY'\n"
           "from pathlib import Path\n"
           "Path('calc.py').write_text('def add(a, b):\\n    return a + b\\n')\n"
           "PY")
    tests = f"{sys.executable} -m pytest -q -p no:cacheprovider"
    commit = "git add -A && git commit -m 'fix: add складывает'"
    adapter = ToolAdapter([
        ("tool", "memory_search", {"query": "соглашения проекта add складывает"}),
        ("tool", "terminal_run", {"command": fix, "mode": "project_host",
                                  "cwd": str(calc_repo)}),
        ("tool", "terminal_run", {"command": tests, "mode": "project_host",
                                  "cwd": str(calc_repo), "timeout": 120}),
        ("tool", "terminal_run", {"command": commit, "mode": "project_host",
                                  "cwd": str(calc_repo)}),
        ("text", "починил, прогнал тесты, закоммитил"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["memory.search", "terminal.run"], adapter=adapter,
        max_steps=12, permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(calc_repo / "calc.py"),
                   "expect": {"exists": True, "contains": "return a + b"}}])
    task_id = stack["task"]["id"]

    status, approvals = await _run_mission(env, task_id, timeout=300.0)

    assert status == "completed", status
    # 2. dispatch: все четыре шага, в порядке, все исполнены
    rows = await _tool_rows(env, task_id)
    assert [r["tool"] for r in rows] == ["memory.search", "terminal.run", "terminal.run",
                                         "terminal.run"]
    assert all(r["status"] == "executed" for r in rows)
    assert {r["source"] for r in rows} == {"memory", "terminal"}
    terminal_rows = [r for r in rows if r["tool"] == "terminal.run"]
    assert [r["args"]["command"] for r in terminal_rows] == [fix, tests, commit]
    assert all(r["result_preview"].startswith("exit_code=0") for r in terminal_rows)
    assert len(approvals) == 3, approvals             # каждый host-shell спросил владельца
    # память реально отдала соглашение, а не пустоту
    assert "соглашени" in rows[0]["result_preview"].lower() \
        or "pytest" in rows[0]["result_preview"].lower(), rows[0]["result_preview"]

    # 3. post-state — три независимых чтения мира
    assert (calc_repo / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")
    assert _pytest_run(calc_repo).returncode == 0
    head_after = _git(calc_repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after and head_after != head_before, "коммита не появилось"
    committed = _git(calc_repo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    assert "calc.py" in committed, committed
    assert _git(calc_repo, "status", "--porcelain").stdout.strip() == "", "правка не закоммичена"

    # 4. evidence
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ honesty guard

def test_no_real_opencode_binary_is_claimed():
    """Миссия 8 идёт против фальшивого сервера проекта. Появится настоящий
    бинарь — этот тест упадёт и потребует пересмотра отчёта, а не тихо
    оставит миссию слабее, чем о ней сказано."""
    import shutil
    assert shutil.which("opencode") is None
