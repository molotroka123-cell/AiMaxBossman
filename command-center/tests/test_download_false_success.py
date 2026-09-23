"""Owner run 2026-09-23 (P1 DOWNLOAD-FALSE-SUCCESS).

«Скачай PDF-файл https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf»
— без слов «браузер»/«открой». Ни action_router (его тема — сайт/браузер, а
окно «глагол…тема» обрывалось на точке внутри `www.`), ни action_contract
(глагола «скачай» не было ни в одном семействе) задачу не распознали: модель
не получила ни одного инструмента, ответила «у меня нет инструментов», а
задача завершилась completed — владелец просил ЭФФЕКТ (файл на диске), а не
текст.

Все проверки идут через настоящий движок (claim/execute, хуки before_run и
gate_completion, finalize), а не через подмену классификатора.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc import db as dbm
from bcc.features.action_contract import classify_all

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task

LIVE_PROMPT = ("Скачай PDF-файл "
               "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf")
NO_TOOLS_ANSWER = ("У меня нет инструментов для скачивания файлов. Откройте ссылку и "
                   "сохраните PDF самостоятельно.")


def names(prompt: str) -> list[str]:
    return [cap.name for cap in classify_all(prompt)]


# ------------------------------------------------------------- CLASSIFY

@pytest.mark.parametrize("prompt", [
    LIVE_PROMPT,
    "Скачай файл по ссылке https://example.org/files/report.pdf",
    "Загрузи PDF по ссылке https://www.w3.org/dummy.pdf",
    "Сохрани файл по ссылке https://site.example/doc.pdf",
    "Сохрани PDF https://example.org/a.pdf в загрузки",
    "download the pdf from https://example.org/a.pdf",
    "Download https://www.w3.org/WAI/dummy.pdf please",
    "save this file https://x.io/f.bin",
    "https://example.org/a.pdf — скачай его",
])
def test_a_download_of_a_url_is_a_download_contract(prompt):
    assert "DOWNLOAD_ACTION" in names(prompt), names(prompt)


def test_save_file_by_url_is_a_download_not_a_terminal_file_obligation():
    # «сохрани файл» + URL — это скачивание; без поглощения клаузы задача
    # требовала бы ЕЩЁ и terminal.run, и честное скачивание не завершалось бы.
    assert names("save this file https://x.io/f.bin") == ["DOWNLOAD_ACTION"]
    assert names("Сохрани файл по ссылке https://site.example/doc.pdf") == ["DOWNLOAD_ACTION"]


def test_a_separate_file_request_next_to_a_download_is_kept():
    assert set(names("Скачай PDF https://example.org/a.pdf и создай файл notes.txt с итогом")) \
        == {"DOWNLOAD_ACTION", "TERMINAL_FILE_ACTION"}


@pytest.mark.parametrize("prompt", [
    "как скачать pdf?",
    "Как скачать PDF-файл https://example.org/a.pdf?",
    "How do I download the pdf from https://example.org/a.pdf?",
    "не скачивай ничего, просто объясни",
    "Не скачивай https://example.org/a.pdf, просто объясни, что это за формат",
    "Don't download https://example.org/a.pdf, just explain what PDF is",
    "Привет! Как дела?",
    "Что такое PDF?",
    "Explain what https://example.org is",
    "Открой на моём компьютере в браузере YouTube и включи Never Gonna Give You Up",
])
def test_questions_prohibitions_and_chat_are_not_download_contracts(prompt):
    assert "DOWNLOAD_ACTION" not in names(prompt), names(prompt)


# ------------------------------------------------------ engine (real hooks)

async def _meta(env, task_id: int) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(
            dbm.tasks.c.id == task_id))).first()
    return dict(row._mapping["meta"] or {})


async def test_live_prompt_text_only_answer_is_not_completed(env):
    """Точный живой сценарий: агент без инструментов (дефолт), модель отвечает
    «у меня нет инструментов». Задача обязана НЕ стать completed, а
    инструмент скачивания обязан быть выдан этому run'у."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(NO_TOOLS_ANSWER)
    stack = await make_stack(env.client, prompt=LIVE_PROMPT, max_steps=4)
    assert not (stack["agent"].get("tools") or [])

    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "failed"
    meta = await _meta(env, stack["task"]["id"])
    assert "browser.download" in meta.get("allowed_tools", []), meta
    assert "DOWNLOAD_ACTION" in (meta.get("action_contract") or {}).get("capabilities", []), meta


async def test_preset_tools_without_download_still_cannot_complete(env):
    """Инвариант независим от маршрутизации: если скилл/владелец заранее задал
    allowed_tools без browser.download (роутер тогда ничего не прикрепляет),
    текстовый ответ всё равно не превращается в completed."""
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Готово, файл скачан.")
    stack = await make_stack(env.client, prompt=LIVE_PROMPT, max_steps=4)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(dbm.tasks).where(dbm.tasks.c.id == stack["task"]["id"]).values(
            meta={"allowed_tools": ["terminal.run"]}))
        await s.commit()

    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "failed"
    assert (await _meta(env, stack["task"]["id"]))["allowed_tools"] == ["terminal.run"]


async def test_errored_download_call_is_not_success(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("Файл скачан.")
    stack = await make_stack(env.client, prompt=LIVE_PROMPT, max_steps=4)
    run_id = await env.svc.engine.claim()
    assert run_id is not None
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(dbm.tool_calls).values(
            run_id=run_id, task_id=stack["task"]["id"], tool="browser.download",
            source="browser", status="error"))
        await s.commit()
    await env.svc.engine.execute(run_id)
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "failed"


@pytest.mark.parametrize("prompt", ["Как скачать PDF-файл?", "не скачивай ничего, просто объясни"])
async def test_a_question_about_downloading_completes_without_tools(env, prompt):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter(
        "Откройте ссылку в браузере и нажмите «Сохранить».")
    stack = await make_stack(env.client, prompt=prompt, max_steps=4)
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED)
    assert status == "completed"
    meta = await _meta(env, stack["task"]["id"])
    assert "allowed_tools" not in meta, meta


# --------------------------------------- real Chromium: the download happens

from .browser_support import chromium_available, reason  # noqa: E402
from .test_browser_download_b4 import PDF, site  # noqa: E402,F401 — фикстура локального сайта


@pytest.mark.skipif(not chromium_available(), reason=reason())
async def test_download_request_gets_the_download_tool_and_a_real_file(env, site):  # noqa: F811
    """Положительная сторона: агент БЕЗ инструментов, формулировка владельца без
    слова «браузер». Роутер выдаёт browser.download, после подтверждения
    владельца файл реально на диске, задача completed."""
    url = f"{site}/attach.pdf"
    adapter = ToolAdapter([("tool", "browser_download", {"url": url}), ("text", "файл скачан")])
    env.svc.registry.adapter_factory = lambda m, p: adapter
    stack = await make_stack(env.client, prompt=f"Скачай PDF-файл {url}", max_steps=6)
    assert not (stack["agent"].get("tools") or [])
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True, "browser.control": True}})
    try:
        assert await _run_task(env, stack["task"]["id"], timeout=90) == "waiting_approval"
        offered = {t["function"]["name"] if "function" in t else t.get("name")
                   for t in (adapter.seen_tools[0] or [])}
        assert "browser_download" in offered, offered
        appr = [a for a in (await env.client.get("/api/approvals")).json()
                if "browser.download" in a["preview"]]
        assert len(appr) == 1, appr
        await env.client.post(f"/api/approvals/{appr[0]['id']}", json={"approve": True, "by": "тест"})
        assert await _run_task(env, stack["task"]["id"], until=FINISHED, timeout=90) == "completed"
        files = list((env.svc.browser.data_dir / "downloads").rglob("*.pdf"))
        assert len(files) == 1 and files[0].read_bytes() == PDF
        assert hashlib.sha256(files[0].read_bytes()).hexdigest() == hashlib.sha256(PDF).hexdigest()
    finally:
        await env.svc.browser.close()
