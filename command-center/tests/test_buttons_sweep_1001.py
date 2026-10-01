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
