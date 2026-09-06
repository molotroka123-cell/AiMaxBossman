"""GOLDEN MISSIONS — the canonical missions tests/test_golden_missions.py did not carry.

The twelve missions in test_golden_missions.py cover file create/edit, terminal,
browser, app launch, memory write, MCP, coding repair, video export, denied
dangerous action, approval+resume and a mixed multi-step mission. Five canonical
missions had no home at all, and every one of them is a mission about the system
being HONEST when something goes wrong:

  13  MISSING EXECUTOR      — the capability's runtime is not on this machine.
  14  TOOL FAILURE + RECOVERY — a real non-zero attempt, then a real success.
  15  PRIVATE LOCAL-ONLY    — a private task must never reach a cloud provider,
                              not on the primary route, not on the fallback.
  16  MULTI-APP TASK        — browser + terminal + memory in one task, each side
                              read back from a different, independent world.
  17  RESTART RECOVERY      — the process dies mid-mission; a fresh engine takes
                              over and the effect happens exactly once.

They obey the same five links as the missions next door (intent → dispatch →
post-state → evidence → status) and the same rule: the world is read back by the
test process, never taken from the model's prose or the tool's own answer.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest
import sqlalchemy as sa

from bcc.db import (agents as agents_t, fetch_one, task_runs as runs_t,
                    tasks as tasks_t, tool_calls as tool_calls_t, utcnow)
from bcc.engine import TaskEngine
from bcc.features import action_router
from bcc.features.action_contract import classify_all
from bcc.v2.tables import mcp_servers as mcp_servers_t

from .browser_support import chromium_available, reason as browser_reason
from .test_golden_missions import (FIXTURES, OWNER, TOKEN, _allow_root, _assert_verified_evidence,
                                   _configure_memory, _drain, _events, _finalized, _merge_meta,
                                   _mission_stack, _one, _run_mission, _status, _submissions,
                                   _tool_rows, allow_private_browser, form_site, project, vault)
from .test_v21_tool_loop import ToolAdapter

# Fixtures re-exported by the import above (pytest needs them in this module's
# namespace); naming them here keeps linters from calling them unused.
__all__ = ["project", "vault", "form_site", "allow_private_browser"]


def _docker_daemon_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ============================================================ MISSION 13
# MISSING EXECUTOR — the runtime the capability needs is not installed here.

async def test_mission_13_missing_mcp_executor_never_completes(env, tmp_path):
    """MCP-сервер, которого нет на машине: подключение честно отказывает,
    инструмент не появляется в реестре, и задача НЕ завершается успехом.

    Модель при этом отвечает текстом «готово» — проверка по тексту прошла бы.
    Проверка по миру обязана провалить: чужой процесс не создан, его файла нет.
    """
    counter = tmp_path / "mcp-calls.txt"
    missing = tmp_path / "no-such-mcp-server-binary"
    assert not missing.exists()

    async with env.svc.db.session() as s:
        await s.execute(sa.insert(mcp_servers_t).values(
            name="ghost", transport="stdio", command=[str(missing)], url="", cwd="",
            env_keys=[], enabled=True, status="unknown", created_at=utcnow()))
        await s.commit()

    # 1. intent — контракт видит MCP-действие
    prompt = "Используй MCP-сервер ghost, чтобы записать заметку о релизе."
    assert "MCP_ACTION" in {c.name for c in classify_all(prompt)}

    # исполнителя нет: connect честно отказывает, а не рапортует healthy
    r = await env.client.post("/api/mcp/runtime/servers/ghost/connect")
    assert r.status_code != 200 or r.json().get("status") != "healthy", r.text
    from bcc.tools import REGISTRY
    assert REGISTRY.get("mcp:ghost:write_note") is None, "инструмент несуществующего сервера"

    adapter = ToolAdapter([
        ("tool", "mcp_ghost_write_note", {"text": f"{TOKEN}-GHOST"}),
        ("text", "Готово: заметка записана через MCP."),
    ])
    stack = await _mission_stack(env, prompt=prompt, tools=["mcp:ghost:write_note"],
                                 adapter=adapter)
    task_id = stack["task"]["id"]

    status, approvals = await _run_mission(env, task_id, timeout=120.0)

    # 5. status — текстовый рапорт не стал выполнением
    assert status == "failed", status
    assert approvals == [], "у отсутствующего исполнителя нечего подтверждать"
    # 2. dispatch — вызов зафиксирован как отказ, а не как исполнение
    rows = await _tool_rows(env, task_id)
    assert rows, "вызов вообще не записан — цепочка обрывается на диспатче"
    assert all(r["status"] == "denied" for r in rows), rows
    # 3. post-state — чужого процесса не было: он не создал ни одного следа
    assert not counter.exists()
    async with env.svc.db.session() as s:
        srv = (await s.execute(sa.select(mcp_servers_t.c.status)
                               .where(mcp_servers_t.c.name == "ghost"))).scalar()
    assert srv != "healthy", srv
    # 4. evidence — финализации не было
    assert await _finalized(env, task_id, timeout=1.0) == []


async def _escalation_cannot_be_waived(env, task_id: int, *, timeout: float = 180.0) -> dict:
    """Общий финал для миссий «эффекта не было»: система останавливается на
    решении человека, а решение человека НЕ делает несостоявшийся эффект
    состоявшимся. Владелец подтверждает эскалацию — задача всё равно не
    completed, и в журнале лежит отказ финализатора."""
    status = await _drain(env, task_id, timeout=timeout)
    assert status in ("failed", "waiting_approval"), status
    if status == "failed":
        return {"status": status}
    pending = [a for a in (await env.client.get("/api/approvals")).json()
               if a.get("task_id") == task_id]
    assert pending and all(a["kind"] != "tool" for a in pending), pending
    for appr in pending:
        r = await env.client.post(f"/api/approvals/{appr['id']}",
                                  json={"approve": True, "by": OWNER})
        assert r.status_code == 200, r.text
    # решение человека по review_escalation исполняет свип фичи-ревьюера
    # (bcc/features/review_gate._tick), а не воркер: зовём его явно, иначе
    # проверялось бы «человек нажал, и ничего не произошло».
    from bcc.features import review_gate
    await review_gate._tick(env.svc)
    after = await _drain(env, task_id, timeout=30.0)
    assert after != "completed", (after, pending)
    refused = await _events(env, "task.finalize_refused", task_id)
    assert refused, "человек «подтвердил», а отказа финализатора в журнале нет"
    assert await _finalized(env, task_id, timeout=1.0) == []
    return {"status": after, "refused": refused}


async def test_mission_13b_missing_sandbox_runtime_is_honest_not_faked(env, project):
    """Второй вид отсутствующего исполнителя: песочница терминала.

    Демон docker в этой среде не поднят, поэтому `mode=sandbox` физически не
    может исполниться. Инструмент обязан честно вернуть провал, файл на диске
    обязан отсутствовать, задача — не стать completed, и — главное — подпись
    владельца под эскалацией НЕ имеет права её завершить: отсутствующий эффект
    не становится состоявшимся оттого, что человек нажал «да».
    """
    if _docker_daemon_up():
        pytest.skip("демон docker доступен — эта миссия проверяет именно его ОТСУТСТВИЕ")
    await _allow_root(env, project)
    target = project / "sandboxed.txt"

    prompt = "Создай файл sandboxed.txt в песочнице."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}
    command = f"printf '%s\\n' '{TOKEN}-SANDBOX' > sandboxed.txt"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "sandbox",
                                  "cwd": str(project), "timeout": 60}),
        ("text", "Готово: файл создан в песочнице."),
    ])
    stack = await _mission_stack(env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
                                 max_steps=8, permissions={"terminal.run": True})
    task_id = stack["task"]["id"]

    await _escalation_cannot_be_waived(env, task_id)

    rows = await _tool_rows(env, task_id)
    calls = [r for r in rows if r["tool"] == "terminal.run"]
    assert calls, rows
    for call in calls:
        preview = str(call["result_preview"] or "")
        assert not preview.startswith("exit_code=0"), preview
    assert "docker" in str(calls[-1]["result_preview"] or "").lower(), calls[-1]["result_preview"]
    # 3. post-state: файла нет — читаем диск, а не ответ модели
    assert not target.exists()


# ============================================================ MISSION 14
# TOOL FAILURE + RECOVERY — a real failed attempt, then a real success.

async def test_mission_14_tool_failure_then_recovery_completes(env, project):
    """Первая попытка ПРОВАЛИЛАСЬ по-настоящему (ненулевой код, файла нет),
    вторая — та же команда — прошла. Итог: completed, и мир это подтверждает.

    Команда одна и та же в обоих вызовах, поэтому это именно повтор действия, а
    не «другая, удачная работа», которой замазали неудачную.
    """
    await _allow_root(env, project)
    target = project / "flaky.txt"
    flag = project / "attempt.flag"
    # первый прогон падает (код 7) и НИЧЕГО не пишет; второй — пишет файл
    command = ("if [ ! -f attempt.flag ]; then : > attempt.flag; exit 7; fi; "
               f"printf '%s\\n' '{TOKEN}-RECOVERED' > flaky.txt")

    prompt = "Создай файл flaky.txt с отчётом; если сорвётся — повтори."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    args = {"command": command, "mode": "project_host", "cwd": str(project), "timeout": 60}
    adapter = ToolAdapter([
        ("tool", "terminal_run", dict(args)),
        ("tool", "terminal_run", dict(args)),
        ("text", "со второй попытки получилось"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter, max_steps=8,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(target),
                   "expect": {"exists": True, "contains": f"{TOKEN}-RECOVERED"}}])
    task_id = stack["task"]["id"]

    status, approvals = await _run_mission(env, task_id, timeout=180.0)

    # 5. status
    assert status == "completed", status
    # 2. dispatch: ровно две попытки, первая провалена, вторая исполнена
    rows = [r for r in await _tool_rows(env, task_id) if r["tool"] == "terminal.run"]
    assert len(rows) == 2, rows
    assert rows[0]["result_preview"].startswith("exit_code=7"), rows[0]["result_preview"]
    assert rows[1]["result_preview"].startswith("exit_code=0"), rows[1]["result_preview"]
    assert all(r["approved_by"] == OWNER for r in rows), rows
    assert len(approvals) == 2, "каждая host-попытка спрашивает владельца отдельно"
    # 3. post-state: файл на диске — и он появился именно со второй попытки
    assert flag.exists(), "первая попытка обязана была реально состояться"
    assert target.read_text(encoding="utf-8").strip() == f"{TOKEN}-RECOVERED"
    # 4. evidence: финализатор перечитал мир
    await _assert_verified_evidence(env, task_id, expectations=1)


async def test_mission_14b_failed_effect_never_completes_on_its_own(env, project):
    """Контрольная половина той же миссии: без восстановления провал не имеет
    права стать успехом — ни сам по себе, ни подписью владельца."""
    await _allow_root(env, project)
    target = project / "never.txt"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": "exit 9", "mode": "project_host",
                                  "cwd": str(project), "timeout": 60}),
        ("text", "Готово: файл never.txt создан."),
    ])
    stack = await _mission_stack(
        env, prompt="Создай файл never.txt.", tools=["terminal.run"], adapter=adapter,
        max_steps=8, permissions={"terminal.run": True})
    task_id = stack["task"]["id"]

    # владелец подтверждает host-shell, но НЕ эскалацию: сначала доводим
    # попытку до конца, потом проверяем, что её провал неотменяем
    status = await _drain(env, task_id, timeout=120.0)
    while status == "waiting_approval":
        pending = [a for a in (await env.client.get("/api/approvals")).json()
                   if a.get("task_id") == task_id and a["kind"] == "tool"]
        if not pending:
            break
        for appr in pending:
            await env.client.post(f"/api/approvals/{appr['id']}",
                                  json={"approve": True, "by": OWNER})
        status = await _drain(env, task_id, timeout=120.0)

    await _escalation_cannot_be_waived(env, task_id, timeout=30.0)

    assert not target.exists()
    rows = [r for r in await _tool_rows(env, task_id) if r["tool"] == "terminal.run"]
    assert rows and all(str(r["result_preview"] or "").startswith("exit_code=9") for r in rows), rows


# ============================================================ MISSION 15
# PRIVATE LOCAL-ONLY — a private task must never egress to a cloud provider.

class _Recorder(BaseHTTPRequestHandler):
    hits: list[str] = []

    def _reply(self):
        _Recorder.hits.append(self.path)
        body = json.dumps({
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude",
            "content": [{"type": "text", "text": "ОБЛАКО ОТВЕТИЛО"}],
            "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 3},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _reply

    def log_message(self, *a):
        pass


@pytest.fixture
def cloud_endpoint():
    """«Облако», которое ведёт свой журнал попаданий. Ни один приватный запрос
    не имеет права появиться в этом журнале."""
    _Recorder.hits = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", _Recorder.hits
    server.shutdown()
    server.server_close()


async def _cloud_stack(env, base_url: str, *, prompt: str, privacy: str) -> dict:
    """Настоящие адаптеры (без FakeAdapter): цепочка обязана упереться в
    границу приватности внутри провайдера, а не в подменённый объект."""
    prov = (await env.client.post("/api/providers", json={
        "name": f"облако-{privacy}", "kind": "anthropic", "base_url": base_url,
        "api_key": "sk-ant-test"})).json()
    assert "id" in prov, prov
    primary = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": "claude-haiku-4-5",
        "alias": f"cloud-primary-{privacy}"})).json()
    fallback = (await env.client.post("/api/models", json={
        "provider_id": prov["id"], "name": "claude-haiku-4-5",
        "alias": f"cloud-fallback-{privacy}"})).json()
    agent = (await env.client.post("/api/agents", json={
        "name": f"агент-{privacy}", "system_prompt": "коротко", "model_id": primary["id"],
        "fallback_model_id": fallback["id"], "max_steps": 2})).json()
    task = (await env.client.post("/api/tasks", json={
        "title": "приватная", "prompt": prompt, "agent_id": agent["id"],
        "run_now": True, "max_retries": 0})).json()["task"]
    await _merge_meta(env, task["id"], {"privacy": privacy})
    return {"provider": prov, "agent": agent, "task": task}


async def test_mission_15_private_task_never_reaches_the_cloud(env, cloud_endpoint):
    """PRIVATE-задача: ни основной маршрут, ни fallback не уходят в облако.

    Пост-состояние читается НЕ из статуса задачи, а из журнала самого «облака»:
    сервер записывает каждый пришедший к нему запрос. Пусто — значит egress'а не
    было. Положительный контроль в конце доказывает, что журнал вообще работает.
    """
    base_url, hits = cloud_endpoint
    stack = await _cloud_stack(env, base_url, prompt="Проанализируй мои личные записи.",
                               privacy="private")
    task_id = stack["task"]["id"]

    status = await _drain(env, task_id, timeout=60.0)

    # 5. status: приватная задача не выдана за выполненную
    assert status != "completed", status
    # 3. post-state: «облако» не получило НИ ОДНОГО запроса — ни от основной
    # модели, ни от fallback'а (он тоже облачный, и он тоже не сработал)
    assert hits == [], hits
    assert await _finalized(env, task_id, timeout=1.0) == []
    # результат модели не подменён облачным ответом
    async with env.svc.db.session() as s:
        run = (await s.execute(sa.select(runs_t).where(runs_t.c.task_id == task_id)
                               .order_by(runs_t.c.id.desc()).limit(1))).first()
    text = json.dumps(dict(run._mapping), ensure_ascii=False, default=str) if run else ""
    assert "ОБЛАКО ОТВЕТИЛО" not in text, text[:500]

    # положительный контроль: тот же стек БЕЗ приватности доходит до «облака»,
    # значит выше был заблокирован именно egress, а не сломанный сервер
    public = await _cloud_stack(env, base_url, prompt="Публичная задача.", privacy="public")
    await _drain(env, public["task"]["id"], timeout=60.0)
    assert hits, "контрольный публичный запрос не дошёл — журнал «облака» ничего не доказывает"


def test_mission_15b_privacy_boundary_is_enforced_in_the_provider_itself():
    """Граница приватности живёт в провайдере, а не в вызывающем коде: любой
    путь к платному облаку под приватным контекстом обязан падать."""
    from bossman_shared.privacy import assert_provider_egress, execution_privacy

    with execution_privacy("private"):
        for kind in ("anthropic", "openai", "openrouter", "gemini"):
            with pytest.raises(PermissionError):
                assert_provider_egress(kind, "https://api.example.com/v1/messages")
        # локальный исполнитель остаётся разрешён — приватность не означает «ничего»
        assert_provider_egress("openai_compat", "http://127.0.0.1:8080/v1")
        # вложенный «публичный» контекст НЕ понижает приватность
        with execution_privacy("public"):
            with pytest.raises(PermissionError):
                assert_provider_egress("anthropic", "https://api.anthropic.com")


# ============================================================ MISSION 16
# MULTI-APP TASK — browser + terminal + memory in one mission.

@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
async def test_mission_16_multi_app_mission(env, form_site, project, vault,
                                            allow_private_browser):
    """Одна задача — три разных мира, и каждый читается независимо:

      * браузер   → поля лежат в файле СЕРВЕРА (агент до него не дотягивается);
      * терминал  → отчёт лежит на диске и перечитан тестом;
      * память    → заметка найдена повторным поиском по хранилищу владельца.
    """
    url, inbox = form_site
    await _allow_root(env, project)
    await _configure_memory(env, vault)
    report = project / "mission16.txt"

    prompt = (f"Открой сайт {url}, отправь заявку, затем сохрани отчёт "
              f"в файл mission16.txt и запомни результат.")
    caps = {c.name for c in classify_all(prompt)}
    assert {"TERMINAL_FILE_ACTION", "MEMORY_ACTION"} <= caps, caps
    assert action_router.classify(prompt) == action_router.CAPABILITY_BROWSER

    body = f"{TOKEN}-MULTI: заявка отправлена и отчёт сохранён."
    command = f"printf '%s\\n' '{TOKEN}-MULTI' > mission16.txt"
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_type", {"selector": "#name", "text": "Тимур"}),
        ("tool", "browser_click", {"selector": "#go"}),
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project), "timeout": 60}),
        ("tool", "memory_write", {"title": "Итог миссии 16", "kind": "decision",
                                  "content": body}),
        ("text", "всё сделано"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, max_steps=14, permissions={"terminal.run": True},
        tools=["browser.open", "browser.type", "browser.click", "terminal.run",
               "memory.write", "memory.search"],
        adapter=adapter,
        evidence=[{"kind": "file", "target": str(report),
                   "expect": {"exists": True, "contains": f"{TOKEN}-MULTI"}}])
    task_id = stack["task"]["id"]
    before = {p.name for p in vault.rglob("*.md")}

    status, approvals = await _run_mission(env, task_id, timeout=300.0)

    assert status == "completed", status
    rows = await _tool_rows(env, task_id)
    assert [r["tool"] for r in rows] == ["browser.open", "browser.type", "browser.click",
                                         "terminal.run", "memory.write"], rows
    assert all(r["status"] == "executed" for r in rows), rows
    assert {r["source"] for r in rows} == {"browser", "terminal", "memory"}

    # post-state №1 — файл сервера, до которого агент не дотягивается
    posted = _submissions(inbox)
    assert len(posted) == 1 and posted[0]["fields"]["name"] == "Тимур", posted
    # post-state №2 — файл на диске, перечитанный тестом
    assert report.read_text(encoding="utf-8").strip() == f"{TOKEN}-MULTI"
    # post-state №3 — заметка в хранилище владельца + независимый повторный поиск
    created = [p for p in vault.rglob("*.md") if p.name not in before]
    assert len(created) == 1, created
    assert body in created[0].read_text(encoding="utf-8")
    assert (await env.client.post("/api/memory/index", json={"force": True})).status_code == 200
    found = json.dumps((await env.client.post(
        "/api/memory/search", json={"query": "итог миссии 16"})).json(), ensure_ascii=False)
    assert f"{TOKEN}-MULTI" in found, found[:600]
    # evidence
    await _assert_verified_evidence(env, task_id, expectations=1)


# ============================================================ MISSION 17
# RESTART RECOVERY — the process dies mid-mission; the effect happens once.

async def _drain_with(engine, env, task_id: int, *, timeout: float) -> str:
    """Тот же цикл, что и `_drain`, но ДРУГИМ движком — «после перезапуска»."""
    engine.poll_interval = 0.02
    worker = asyncio.create_task(engine.worker_loop())
    watcher = asyncio.create_task(engine.approval_watcher())
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            status = await _status(env, task_id)
            if status in ("completed", "failed", "stopped"):
                grace = loop.time() + 2.0
                while loop.time() < grace:
                    if await _events(env, "task.finalized", task_id):
                        break
                    await asyncio.sleep(0.05)
                return status
            await asyncio.sleep(0.05)
        return await _status(env, task_id)
    finally:
        worker.cancel()
        watcher.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


async def test_mission_17_restart_recovery_executes_the_effect_exactly_once(env, project):
    """Перезапуск посреди миссии: эффект наступает РОВНО один раз.

    Ход: задача встаёт на подтверждение (мир ещё чист) → процесс «умирает»
    (движок, который её вёл, выброшен, аренда прогона протухла) → владелец
    подтверждает → НОВЫЙ движок восстанавливает прогон и доводит его. Файл на
    диске обязан появиться один раз и с ожидаемым содержимым, а строка вызова —
    остаться единственной.
    """
    await _allow_root(env, project)
    target = project / "after_restart.txt"
    prompt = "Создай файл after_restart.txt с отчётом о выпуске."
    assert "TERMINAL_FILE_ACTION" in {c.name for c in classify_all(prompt)}

    command = f"printf '%s\\n' '{TOKEN}-RESTART' >> after_restart.txt"
    adapter = ToolAdapter([
        ("tool", "terminal_run", {"command": command, "mode": "project_host",
                                  "cwd": str(project), "timeout": 60}),
        ("text", "готово"),
    ])
    stack = await _mission_stack(
        env, prompt=prompt, tools=["terminal.run"], adapter=adapter,
        permissions={"terminal.run": True},
        evidence=[{"kind": "file", "target": str(target),
                   "expect": {"exists": True, "contains": f"{TOKEN}-RESTART"}}])
    task_id = stack["task"]["id"]

    # --- фаза 1: остановка на подтверждении, мир НЕ изменён
    status = await _drain(env, task_id, timeout=60.0)
    assert status == "waiting_approval", status
    assert not target.exists(), "эффект наступил до решения владельца"
    pending = (await env.client.get("/api/approvals")).json()
    assert len(pending) == 1 and pending[0]["kind"] == "tool"

    # --- фаза 2: ПЕРЕЗАПУСК. Старый движок больше не участвует; новый видит
    # только то, что лежит в БД, — checkpoint прогона.
    async with env.svc.db.session() as s:
        run_id = (await s.execute(sa.select(runs_t.c.id).where(runs_t.c.task_id == task_id)
                                  .order_by(runs_t.c.id.desc()).limit(1))).scalar()
        row = await fetch_one(s, runs_t, int(run_id))
    assert (row.get("checkpoint") or {}).get("pending_tool_call"), row.get("checkpoint")

    fresh = TaskEngine(env.svc.db, env.svc.bus, env.svc.registry,
                       lease_seconds=5, heartbeat_seconds=1)
    fresh.services = env.svc
    env.svc.engine = fresh                       # всё, что дальше, ведёт новый движок

    r = await env.client.post(f"/api/approvals/{pending[0]['id']}",
                              json={"approve": True, "by": OWNER})
    assert r.status_code == 200, r.text

    status = await _drain_with(fresh, env, task_id, timeout=120.0)

    # 5. status
    assert status == "completed", status
    # 2. dispatch: ровно ОДНА строка вызова — рестарт не создал второй
    rows = await _tool_rows(env, task_id)
    call = _one(rows, "terminal.run")
    assert call["status"] == "executed" and call["approved_by"] == OWNER
    assert call["result_preview"].startswith("exit_code=0")
    assert call["run_id"] == run_id, "прогон продолжен, а не начат заново"
    # 3. post-state: команда дописывающая (>>), поэтому дубль был бы виден
    text = target.read_text(encoding="utf-8")
    assert text.strip() == f"{TOKEN}-RESTART", f"эффект продублирован: {text!r}"
    assert text.count(f"{TOKEN}-RESTART") == 1, text
    # 4. evidence
    await _assert_verified_evidence(env, task_id, expectations=1)
