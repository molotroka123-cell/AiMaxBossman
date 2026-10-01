"""Обход «каждой кнопки» 01.10.2026 (docs/owner/runs/BUTTONS_SWEEP_20261001.md): регрессии найденных дефектов.

Каждый тест красный на коде 9251414d и зелёный после правки. Сырой английский текст бэкенда и путь к файлу
на диске в уведомлении владельцу — дефект, а не «подробность».
"""
from __future__ import annotations

import re

import pytest

from .helpers import make_stack  # noqa: F401  (поднимает общие фикстуры)

LATIN_PHRASE = re.compile(r"[A-Za-z][a-z']{2,}(?:\s+[A-Za-z][a-z'-]{1,}){2,}")
WINDOWS_PATH = re.compile(r"[A-Za-z]:\|SKILL\.md")

SKILL = """---
name: buttons-sweep
description: Проверка кнопок
---
# Проверка
1. нажать
"""


@pytest.fixture(autouse=True)
def _skills_in_tmp(env, tmp_path):
    from bcc.v2.skill_library import SkillLibrary, default_skill_roots
    workspace = tmp_path / "skills-workspace"
    (workspace / ".agents" / "skills").mkdir(parents=True)
    env.svc.skills = SkillLibrary(default_skill_roots(workspace), workspace / ".agents" / "skills")


def _message(resp) -> str:
    body = resp.json()
    err = body.get("error", body)
    detail = err.get("message") if isinstance(err, dict) else err
    if isinstance(detail, dict):
        detail = detail.get("message")
    return str(detail)


@pytest.mark.parametrize("route,payload", [
    ("/api/skills", {"id": "Мой навык", "content": SKILL}),
    ("/api/skills/import", {"id": "Мой навык", "content": SKILL}),
])
async def test_bad_skill_name_is_explained_in_russian(env, route, payload):
    resp = await env.client.post(route, json=payload)
    assert resp.status_code == 409
    msg = _message(resp)
    assert not LATIN_PHRASE.search(msg), f"сырой английский текст бэкенда в сообщении: {msg!r}"
    assert "латин" in msg


async def test_duplicate_skill_does_not_show_a_file_path(env):
    assert (await env.client.post("/api/skills", json={"id": "buttons-sweep", "content": SKILL})).status_code == 200
    for route, payload in (("/api/skills", {"id": "buttons-sweep", "content": SKILL}),
                           ("/api/skills/buttons-sweep/clone", {"new_id": "buttons-sweep"}),
                           ("/api/skills/import", {"id": "buttons-sweep", "content": SKILL})):
        resp = await env.client.post(route, json=payload)
        assert resp.status_code == 409, route
        msg = _message(resp)
        assert not WINDOWS_PATH.search(msg), f"путь к файлу в уведомлении: {msg!r}"
        assert "уже существует" in msg


# ------------------------------------------------------------------ «Студия»: сообщения для человека

import base64


async def test_studio_reference_import_refusals_are_russian(env):
    bad_type = await env.client.post("/api/studio/references", json={
        "filename": "заметка.txt", "data_base64": base64.b64encode(b"x").decode()})
    assert bad_type.status_code == 422
    msg = _message(bad_type)
    assert not LATIN_PHRASE.search(msg), msg
    assert "PNG" in msg
    empty = await env.client.post("/api/studio/references", json={"filename": "a.png", "data_base64": ""})
    assert empty.status_code == 422
    assert not LATIN_PHRASE.search(_message(empty)), _message(empty)


async def test_studio_reframe_without_reference_is_russian(env):
    resp = await env.client.post("/api/studio/jobs", json={"model": "local:reframe", "prompt": "x"})
    assert resp.status_code == 422
    msg = _message(resp)
    assert not LATIN_PHRASE.search(msg), f"сырой английский текст бэкенда: {msg!r}"
    assert "рефрейм" in msg.lower()


async def test_studio_missing_things_are_russian(env):
    for path in ("/api/studio/runs/99999", "/api/studio/packages/nope"):
        resp = await env.client.get(path)
        assert resp.status_code in (404, 409, 422), (path, resp.status_code)
        assert not LATIN_PHRASE.search(_message(resp)), (path, _message(resp))


# ------------------------------------------------------------------ новый чат: кнопка голосового ввода

async def test_chat_voice_button_reason_is_russian(env, monkeypatch, tmp_path):
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(tmp_path / "нет-такой-модели"))
    opts = (await env.client.get("/api/chat/options")).json()
    reason = (opts.get("speech") or {}).get("reason") or ""
    assert reason, opts.get("speech")
    assert not LATIN_PHRASE.search(reason), f"подсказка кнопки микрофона на английском: {reason!r}"


# ------------------------------------------------------------------ «Нет связи»: причина словами, а не классом исключения

import httpx

TECH_NET = re.compile(r"ConnectError|All connection attempts failed|ReadTimeout|HTTPError|Traceback")


def _dead_network(monkeypatch):
    """Каждый исходящий запрос провайдера кончается ConnectError с английским текстом httpx."""
    def refuse(request):
        raise httpx.ConnectError("All connection attempts failed", request=request)

    def dead_client(url, *, timeout=10, transport=None, **kw):
        return httpx.AsyncClient(timeout=timeout, transport=httpx.MockTransport(refuse))
    import bcc.providers
    import bcc.v2.openrouter_ext
    monkeypatch.setattr(bcc.providers, "http_client", dead_client)
    monkeypatch.setattr(bcc.v2.openrouter_ext, "http_client", dead_client)


async def test_openrouter_connect_without_network_is_human(env, monkeypatch):
    _dead_network(monkeypatch)
    resp = await env.client.post("/api/openrouter/connect", json={"api_key": "sk-or-v1-" + "0" * 40})
    text = resp.text
    assert not TECH_NET.search(text), f"сырой текст сетевой ошибки в ответе: {text[:300]}"
    assert "нет связи" in text.lower()


async def test_free_provider_connect_without_network_does_not_blame_the_key(env, monkeypatch):
    _dead_network(monkeypatch)
    resp = await env.client.post("/api/free-providers/google_ai_studio/connect", json={"api_key": "AIza" + "0" * 35})
    assert resp.status_code == 502
    text = resp.text
    assert not TECH_NET.search(text), text[:300]
    assert "не принял ключ" not in text, "сеть недоступна, про ключ ничего не известно"
    assert "Ключ не проверен" in text


# ------------------------------------------------------------------ новый чат: код проверки не показывается как есть

import os
import shutil
import subprocess
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "ui"


def test_chat_verdict_codes_are_words_in_node():
    node = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, "--experimental-detect-module", "--no-warnings", "--test",
                             str(UI / "tests" / "buttons_sweep_chat.test.mjs")],
                            capture_output=True, text=True, timeout=180, cwd=str(UI.parent))
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    assert re.search(r"^# fail 0$", result.stdout, re.M), result.stdout[-1500:]


def test_chat_footer_and_panel_use_the_verdict_label():
    render = (UI / "chat" / "render.js").read_text(encoding="utf-8")
    panel = (UI / "chat" / "panel.js").read_text(encoding="utf-8")
    assert "Проверка: ${evalRow.verdict}" not in render, "в подписи ответа сырой код NOT_APPLICABLE/PASS/FAIL"
    assert "verdictLabel(evalRow.verdict)" in render
    assert "verdictLabel(e.verdict)" in panel and "e.verdict || '—'" not in panel


# ------------------------------------------------------------------ новый чат: «Память» без подключённого хранилища

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, live  # noqa: F401


@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_chat_memory_popover_does_not_request_stats_of_an_unconfigured_store(live):  # noqa: F811
    """Кнопка «Память» при неподключённом хранилище не должна ходить за статистикой: сервер на это честно
    отвечает 503, и каждое открытие окна оставляло в консоли ошибку (и ответ 5xx в сети)."""
    from playwright.sync_api import sync_playwright

    bad: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.status >= 500 else None)
            page.on("console", lambda m: bad.append(f"console: {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: bad.append(f"pageerror: {e}"))
            page.goto(live.url + "/chat.html", wait_until="domcontentloaded")
            page.fill("#chat-login-token", live.svc.auth.token)
            page.click("#chat-login-submit")
            page.wait_for_selector("#chat-app:not([hidden])", timeout=15000)
            page.click("#chip-memory")
            page.wait_for_selector("text=Хранилище памяти не подключено", timeout=8000)
            page.wait_for_timeout(500)
        finally:
            browser.close()
    assert bad == [], bad


# ------------------------------------------------------------------ «Цели»: переключатель условия показывает выбор

@pytest.mark.skipif(not chromium_available(), reason=browser_reason())
def test_segmented_switch_shows_the_pressed_choice(live):  # noqa: F811
    """«должно быть false» в мастере цели раньше менял только скрытый черновик: кнопка не меняла вид
    (обход кнопок видел «мёртвую» кнопку), пока шаг не перерисуют."""
    from playwright.sync_api import sync_playwright

    from .test_ux2_thinking_pane import _login

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(f"{live.url}/#/objectives", wait_until="domcontentloaded")
            page.wait_for_selector("#view button:has-text('Новая цель')", timeout=15000)
            page.locator("#view button", has_text="Новая цель").first.click()
            page.wait_for_selector("#modal-root .bx-seg", timeout=8000)
            yes = page.locator("#modal-root .bx-seg button", has_text="должно быть true")
            no = page.locator("#modal-root .bx-seg button", has_text="должно быть false")
            assert "is-on" in (yes.get_attribute("class") or "")
            no.click()
            assert "is-on" in (no.get_attribute("class") or ""), "нажатый вариант не выделился"
            assert "is-on" not in (yes.get_attribute("class") or ""), "прежний вариант остался выделенным"
            assert no.get_attribute("aria-pressed") == "true" and yes.get_attribute("aria-pressed") == "false"
        finally:
            browser.close()


# ------------------------------------------------------------------ общие отказы: проверка полей и запрет платного облака

async def test_validation_errors_are_russian_not_pydantic_english(env):
    bad_int = await env.client.get("/api/web-designer/projects/тест")
    assert bad_int.status_code == 422
    msg = _message(bad_int)
    assert "Input should" not in msg and not LATIN_PHRASE.search(msg), msg
    assert "целое число" in msg
    missing = await env.client.post("/api/providers", json={})
    assert missing.status_code == 422
    msg = _message(missing)
    assert "Field required" not in msg and "обязательно" in msg, msg


def test_cloud_refusals_are_russian(monkeypatch):
    import bcc.provider_governance as gov
    from bcc.provider_governance import free_only_refusal, unknown_price_message

    monkeypatch.setattr(gov, "free_only_policy_active", lambda: True)

    model = {"name": "vendor/x", "alias": "x", "kind": "cloud"}
    texts = [unknown_price_message(model), unknown_price_message(model, "stale"), unknown_price_message(model, "absent"),
             free_only_refusal({"kind": "openai_compat", "base_url": "https://api.cloud-provider.example/v1"},
                               {**model, "price_in": 1.0, "price_out": 2.0}),
             free_only_refusal({"kind": "openai_compat", "base_url": "https://api.cloud-provider.example/v1"}, model)]
    for text in texts:
        assert text, "отказ не должен быть пустым"
        assert re.search(r"[А-Яа-я]{4,}", text), text
        assert not LATIN_PHRASE.search(text.replace("vendor/x", "")), f"английская фраза в отказе: {text!r}"


def test_resource_plan_explanations_are_russian():
    """Строки плана памяти попадают на экран «Задача» (кнопка «Открыть задачу»): по-английски им там не место."""
    from bcc.v2.resource_brain import Reservation, ResourceSnapshot, plan_memory

    idle = Reservation(owner="idle-model", memory_mb=30_000, idle=True) if "idle" in Reservation.__dataclass_fields__ else None
    snap = ResourceSnapshot(total_memory_mb=100_000, used_system_mb=10_000, reserve_floor_mb=4_000,
                            reservations=[idle] if idle else [])
    lines = []
    for request, policy in ((100, "balanced"), (60_000, "balanced"), (95_000, "balanced"), (95_000, "low_power")):
        lines += plan_memory(snap, request, policy=policy).explanation
    assert lines
    for line in lines:
        assert re.search(r"[А-Яа-я]{3,}", line), line
        assert not LATIN_PHRASE.search(line), f"английская строка в плане: {line!r}"
