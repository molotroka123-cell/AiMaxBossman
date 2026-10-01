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
