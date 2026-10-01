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
