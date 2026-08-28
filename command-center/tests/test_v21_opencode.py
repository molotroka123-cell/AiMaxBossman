"""V2.1 фаза F — OpenCode как доказанный runtime-путь.

ЧЕСТНАЯ ОБЛАСТЬ ПРОВЕРКИ. Бинаря `opencode` в этой среде нет
(`which opencode` пусто), поэтому настоящий host-E2E здесь НЕ выполняется —
он вынесен в отдельный тест со `skipif` и явной причиной. Всё остальное
проверяется против детерминированного фальшивого сервера, который реализует
контракт эндпоинтов из `packages/sdk/openapi.json` исходников OpenCode
(см. tests/fixtures/fake_opencode_server.py).

Что доказывается сквозным прогоном через настоящий tool-loop движка:
  1. создан отдельный git worktree под задачу;
  2. сессия OpenCode заведена и связана с task_id/run_id в БД;
  3. задание отправлено;
  4. дифф собран и сохранён;
  5. красный тест в worktree стал зелёным, а исходный репозиторий не тронут;
  6. abort останавливает вторую, длинную сессию;
  7. сессия и дифф переживают перезапуск процесса;
  8. неодобренный путь получает отказ, а не подтверждение;
  9. health честно отвечает unavailable, когда сервера нет.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import run_events, settings_kv
from bcc.tools import REGISTRY, decide_effect
from bcc.v2.tables import opencode_sessions as oc_t

from .conftest import client_for, start_app
from .fixtures.fake_opencode_server import FakeOpenCode
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

BROKEN = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST_FILE = "test_calc.py"
TEST_SRC = ("from calc import add\n\n\n"
            "def test_add():\n    assert add(2, 2) == 4\n")

# Оператор разрешил этому агенту OpenCode явным правилом — единственный
# легальный способ снять ASK (модель сама этого сделать не может).
AUTO_RULES = {"permissions": {"tool_rules": [
    {"tool": "opencode.*", "resource": "*", "effect": "auto",
     "reason": "оператор разрешил кодинг-агента для этой задачи"}]}}


# ------------------------------------------------------------------ фикстуры

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=bossman@test",
                           "-c", "user.name=BOSSMAN", *args],
                          cwd=str(cwd), capture_output=True, text=True, timeout=60)


@pytest.fixture
def repo(tmp_path) -> Path:
    """Крошечный git-репозиторий с ПАДАЮЩИМ тестом."""
    root = (tmp_path / "projects").resolve()
    project = root / "calcapp"
    project.mkdir(parents=True)
    (project / "calc.py").write_text(BROKEN, encoding="utf-8")
    (project / TEST_FILE).write_text(TEST_SRC, encoding="utf-8")
    assert _git(project, "init", "-b", "main").returncode == 0
    assert _git(project, "add", "-A").returncode == 0
    assert _git(project, "commit", "-m", "init").returncode == 0
    return project


def run_tests(where: Path) -> subprocess.CompletedProcess:
    """Прогон тестов фикстурного репозитория отдельным процессом."""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "."],
        cwd=str(where), capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(where)})


@pytest.fixture
def fake():
    server = FakeOpenCode()
    server.start()
    try:
        yield server
    finally:
        server.stop()


async def set_roots(env, roots: list[Path]) -> None:
    enc = env.svc.vault.encrypt(json.dumps([str(Path(r).resolve()) for r in roots]))
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(settings_kv).where(settings_kv.c.key == "opencode.roots"))
        await s.execute(sa.insert(settings_kv).values(key="opencode.roots", value_enc=enc))
        await s.commit()


async def oc_rows(svc) -> list[dict]:
    async with svc.db.session() as s:
        rows = (await s.execute(sa.select(oc_t).order_by(oc_t.c.id))).fetchall()
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------- реестр

async def test_tools_registered_with_canonical_contract(env):
    """Инструменты живут в общем реестре с source=opencode и нужными эффектами."""
    names = [n for n in REGISTRY.names() if n.startswith("opencode.")]
    assert names == ["opencode.abort", "opencode.diff", "opencode.send",
                     "opencode.session.start", "opencode.status"]
    for name in names:
        assert REGISTRY.get(name).source == "opencode"

    agent = {"permissions": {}}
    for name in ("opencode.session.start", "opencode.send"):
        effect, _ = decide_effect(REGISTRY.get(name), {}, agent)
        assert effect == "ask", f"{name} обязан спрашивать человека"
    for name in ("opencode.status", "opencode.diff"):
        effect, _ = decide_effect(REGISTRY.get(name), {}, agent)
        assert effect == "auto"

    # даже выданное право terminal.run НЕ делает запуск кодинг-агента автоматом
    powerful = {"permissions": {"terminal.run": True}}
    effect, reason = decide_effect(REGISTRY.get("opencode.session.start"), {}, powerful)
    assert effect == "ask" and "автономного" in reason

    # схемы уходят модели без точек в именах
    api = {REGISTRY.get(n).api_name for n in names}
    assert "opencode_session_start" in api and "opencode_send" in api


# -------------------------------------------------------------------- E2E

async def test_e2e_failing_test_fixed_through_bossman_tool_loop(env, repo, fake,
                                                                monkeypatch):
    """Главный тест лейна: красный тест → OpenCode → дифф → зелёный тест."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])
    fake.edits_file("почини", "calc.py", FIXED, reply="исправил знак в add")

    before = run_tests(repo)
    assert before.returncode != 0, "фикстурный тест обязан падать ДО работы агента"

    adapter = ToolAdapter([
        ("tool", "opencode_session_start",
         {"project_path": str(repo), "worktree": True, "title": "починить add"}),
        ("tool", "opencode_send", {"text": "почини функцию add в calc.py"}),
        ("tool", "opencode_diff", {}),
        ("text", "OpenCode починил calc.py, дифф собран"),
    ])
    stack = await _stack_with_tools(env, ["opencode.*"], max_steps=8, adapter=adapter,
                                    prompt="почини падающий тест")
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json=AUTO_RULES)

    status = await _run_task(env, stack["task"]["id"], timeout=30, until=FINISHED)
    assert status == "completed", f"адаптер вызвал {adapter.calls} раз"

    # 1. worktree создан и лежит внутри одобренных корней
    rows = await oc_rows(env.svc)
    assert len(rows) == 1
    row = rows[0]
    worktree = Path(row["worktree_path"])
    assert worktree != repo and worktree.exists()
    assert worktree.parent == repo.parent
    assert (worktree / ".git").exists()

    # 2. сессия OpenCode заведена и связана с BOSSMAN
    assert row["session_id"].startswith("ses_")
    assert row["task_id"] == stack["task"]["id"] and row["run_id"] is not None
    assert row["project_path"] == str(repo)
    # OpenCode получил РОВНО одобренный каталог
    created = [q for m, p, q in fake.requests if m == "POST" and p == "/session"]
    assert created and created[0]["directory"] == [str(worktree)]

    # 3. задание дошло до сервера
    assert any(p.endswith("/message") for m, p, _ in fake.requests if m == "POST")

    # 4. дифф собран и отдан модели
    diff_msg = adapter.seen_messages[3][-1]
    assert diff_msg["role"] == "tool" and "calc.py" in diff_msg["content"]
    assert "+    return a + b" in diff_msg["content"]

    # 5. тесты в worktree теперь зелёные, исходный репозиторий не тронут
    after = run_tests(worktree)
    assert after.returncode == 0, after.stdout + after.stderr
    assert (repo / "calc.py").read_text() == BROKEN
    assert run_tests(repo).returncode != 0

    # дифф сохранён в журнале run'а
    async with env.svc.db.session() as s:
        events = (await s.execute(sa.select(run_events)
                                  .where(run_events.c.kind == "opencode.diff"))).fetchall()
    assert len(events) == 1
    saved = dict(events[0]._mapping)["data"]
    assert saved["summary"]["files"] == 1 and saved["diff"][0]["file"] == "calc.py"


# ------------------------------------------------------------------- права

async def test_session_start_asks_human_by_default(env, repo, fake, monkeypatch):
    """Без явного правила оператора запуск кодинг-агента уходит в approval."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])

    adapter = ToolAdapter([
        ("tool", "opencode_session_start", {"project_path": str(repo)}),
        ("text", "готово")])
    stack = await _stack_with_tools(env, ["opencode.*"], max_steps=6, adapter=adapter)

    status = await _run_task(env, stack["task"]["id"], timeout=20)
    assert status == "waiting_approval"

    appr = (await env.client.get("/api/approvals")).json()
    assert len(appr) == 1 and appr[0]["kind"] == "tool"
    assert "opencode.session.start" in appr[0]["preview"]
    # сессия НЕ создана, пока человек не решил
    assert not [p for m, p, _ in fake.requests if m == "POST" and p == "/session"]
    assert await oc_rows(env.svc) == []


async def test_unapproved_path_is_refused_not_asked(env, repo, fake, monkeypatch, tmp_path):
    """Путь вне одобренных корней — отказ данными, OpenCode его не видит."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    adapter = ToolAdapter([
        ("tool", "opencode_session_start", {"project_path": str(outside)}),
        ("text", "понял, путь не одобрен")])
    stack = await _stack_with_tools(env, ["opencode.*"], max_steps=6, adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json=AUTO_RULES)

    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "completed"
    refusal = adapter.seen_messages[1][-1]
    assert refusal["role"] == "tool" and "вне одобренных корней" in refusal["content"]
    assert not [p for m, p, _ in fake.requests if m == "POST" and p == "/session"]

    # тот же отказ у оператора в HTTP — 403, а не «спросим человека»
    r = await env.client.post("/api/opencode/sessions",
                              json={"project_path": str(outside)})
    assert r.status_code == 403
    assert "вне одобренных корней" in json.dumps(r.json(), ensure_ascii=False)


# -------------------------------------------------------------------- abort

async def test_abort_stops_long_running_session(env, repo, fake, monkeypatch):
    """Вторая, длинная сессия (prompt_async) останавливается по abort."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])

    started = (await env.client.post("/api/opencode/sessions",
                                     json={"project_path": str(repo),
                                           "title": "долгая"})).json()
    sid = started["session_id"]

    sent = await env.client.post(f"/api/opencode/sessions/{sid}/send",
                                 json={"text": "перепиши весь проект", "wait": False})
    assert sent.status_code == 200 and sent.json()["queued"] is True

    busy = (await env.client.get(f"/api/opencode/sessions/{sid}/status")).json()
    assert busy["state"]["type"] == "busy" and busy["db_status"] == "running"

    aborted = await env.client.post(f"/api/opencode/sessions/{sid}/abort")
    assert aborted.status_code == 200 and aborted.json()["aborted"] is True
    assert fake.sessions[sid].aborted is True

    idle = (await env.client.get(f"/api/opencode/sessions/{sid}/status")).json()
    assert idle["state"]["type"] == "idle" and idle["db_status"] == "aborted"


async def test_fork_and_children_are_mapped(env, repo, fake, monkeypatch):
    """Fork даёт дочернюю сессию, тоже привязанную к тем же task/run."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])
    parent = (await env.client.post("/api/opencode/sessions",
                                    json={"project_path": str(repo),
                                          "task_id": None})).json()
    sid = parent["session_id"]
    child = (await env.client.post(f"/api/opencode/sessions/{sid}/fork", json={})).json()
    assert child["session_id"].startswith("ses_") and child["parent_id"] == sid

    kids = (await env.client.get(f"/api/opencode/sessions/{sid}/children")).json()
    assert [k["id"] for k in kids["children"]] == [child["session_id"]]
    assert {r["session_id"] for r in await oc_rows(env.svc)} == {sid, child["session_id"]}


# ------------------------------------------------------------------ рестарт

async def test_session_and_diff_survive_restart(env, repo, fake, monkeypatch, tmp_path):
    """Маппинг и снимок диффа переживают перезапуск процесса (только БД)."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])
    fake.edits_file("почини", "calc.py", FIXED)

    adapter = ToolAdapter([
        ("tool", "opencode_session_start", {"project_path": str(repo)}),
        ("tool", "opencode_send", {"text": "почини add"}),
        ("tool", "opencode_diff", {}),
        ("text", "готово")])
    stack = await _stack_with_tools(env, ["opencode.*"], max_steps=8, adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json=AUTO_RULES)
    assert await _run_task(env, stack["task"]["id"], timeout=30,
                           until=FINISHED) == "completed"
    before = (await oc_rows(env.svc))[0]

    # «перезапуск»: новый процесс приложения на той же базе
    app2, svc2 = await start_app(env.settings, start_workers=False)
    try:
        async with client_for(app2, svc2) as c2:
            rows = (await c2.get("/api/opencode/sessions")).json()
            assert [r["session_id"] for r in rows] == [before["session_id"]]
            assert rows[0]["worktree_path"] == before["worktree_path"]

            # сервер OpenCode «умер» вместе с процессом — отдаём сохранённый снимок
            monkeypatch.setenv("OPENCODE_URL", "http://127.0.0.1:1")
            saved = (await c2.get(
                f"/api/opencode/sessions/{before['session_id']}/diff")).json()
            assert saved["source"] == "snapshot"
            assert saved["diff"][0]["file"] == "calc.py"
            assert saved["summary"]["files"] == 1
    finally:
        await svc2.stop()


# ------------------------------------------------------------------- health

async def test_health_online_against_fake_server(env, fake, monkeypatch):
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    body = (await env.client.get("/api/opencode/health")).json()
    assert body["status"] == "online" and body["probe"] == "/api/health"


async def test_health_unavailable_is_honest_not_500(env, monkeypatch):
    """Сервера нет → 200 + honest unavailable с подсказкой, а не ошибка и не ложь."""
    monkeypatch.setenv("OPENCODE_URL", "http://127.0.0.1:1")
    r = await env.client.get("/api/opencode/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["base_url"] == "http://127.0.0.1:1"
    assert "opencode serve" in body["hint"]


async def test_tool_reports_unavailable_without_inventing_work(env, repo, monkeypatch):
    """Инструмент не выдумывает результат, когда сервера нет."""
    monkeypatch.setenv("OPENCODE_URL", "http://127.0.0.1:1")
    await set_roots(env, [repo.parent])
    adapter = ToolAdapter([
        ("tool", "opencode_session_start", {"project_path": str(repo)}),
        ("text", "OpenCode недоступен, задачу не выполнить")])
    stack = await _stack_with_tools(env, ["opencode.*"], max_steps=6, adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json=AUTO_RULES)

    assert await _run_task(env, stack["task"]["id"], timeout=20,
                           until=FINISHED) == "completed"
    msg = adapter.seen_messages[1][-1]
    assert msg["role"] == "tool" and "OpenCode недоступен" in msg["content"]
    assert await oc_rows(env.svc) == []


# --------------------------------------------------------- настоящий бинарь

@pytest.mark.skipif(
    shutil.which("opencode") is None,
    reason="бинаря `opencode` нет в этой среде — настоящий host-E2E не выполнялся; "
           "проверено только против фальшивого сервера (см. docs/v2_1_agent_notes/"
           "lane-f-opencode.md)")
async def test_real_opencode_host_smoke(env, repo, monkeypatch):
    """Host-smoke на НАСТОЯЩЕМ `opencode serve`. Пропускается без бинаря."""
    proc = subprocess.Popen(["opencode", "serve", "--port", "0"],
                            cwd=str(repo), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        url = ""
        for _ in range(200):
            line = proc.stdout.readline() if proc.stdout else ""
            if "http://" in line:
                url = "http://" + line.split("http://", 1)[1].strip().split()[0]
                break
            await asyncio.sleep(0.05)
        assert url, "opencode serve не сообщил адрес"
        monkeypatch.setenv("OPENCODE_URL", url)
        await set_roots(env, [repo.parent])

        health = (await env.client.get("/api/opencode/health")).json()
        assert health["status"] == "online"
        started = (await env.client.post("/api/opencode/sessions",
                                         json={"project_path": str(repo)})).json()
        assert started["session_id"].startswith("ses")
        assert (await env.client.get(
            f"/api/opencode/sessions/{started['session_id']}/status")).status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=20)
