"""Потерянный ответ после состоявшегося перемещения — и повтор владельцем.

Самый опасный для данных случай — не «сервер внятно отказал», а «неизвестно,
случилось ли». Запрос дошёл, файлы уже переехали, ответ до браузера не добрался:
владелец видит ошибку и жмёт ещё раз. Если повтор двинет файлы второй раз или
запишет вторую партию, откатывать будет нечего и нечем.

Здесь это воспроизводится по-настоящему, а не вежливым HTTP-кодом: запрос
ВЫПОЛНЯЕТСЯ через `route.fetch()`, после чего обрывается доставка ответа. Такой
обрыв неотличим для страницы от настоящего разрыва сети, но эффект на диске уже
произошёл. Файлы настоящие, база настоящая, дочерний процесс настоящий.
"""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from .browser_support import chromium_available, reason as browser_reason, required
from .test_ux2_thinking_pane import _launch
from .test_editors_user_acceptance import EditorServer, login

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available() and not required(), reason=browser_reason())]

CONTENT = b"owner document; exactly these bytes must survive"


def _batches(server) -> list[dict]:
    database = server.data / "app-data/file-commander-mini/app.db"
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT value FROM kv WHERE namespace='batches'").fetchall()
    return [json.loads(row[0]) for row in rows]


def _open_file_commander(page, server):
    page.goto(server.url + "/#/apps?open=file-commander-mini")
    page.get_by_role("button", name="Разрешить запуск приложений", exact=True).click()
    start = page.get_by_role("button", name="Запустить", exact=True)
    from playwright.sync_api import expect

    expect(start).to_be_enabled()
    start.click()
    frame = page.frame_locator('iframe[title="File Commander Mini"]')
    expect(frame.locator("#status")).to_contain_text("1 files", timeout=30000)
    return frame


def test_a_lost_response_after_a_real_move_does_not_move_anything_twice(tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    workspace = tmp_path / "owner-workspace"
    workspace.mkdir()
    source = workspace / "owner report.pdf"
    source.write_bytes(CONTENT)
    monkeypatch.setenv("FILE_COMMANDER_ROOTS", str(workspace))
    monkeypatch.delenv("BOSSMAN_APPS_CONTROL_ENABLED", raising=False)
    monkeypatch.delenv("BOSSMAN_APPS_CONTROL_LOCK", raising=False)
    server = EditorServer(tmp_path / "bcc")
    try:
        server.start()
        with sync_playwright() as pw:
            browser = _launch(pw)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                login(page, server)
                frame = _open_file_commander(page, server)
                frame.locator("#organize").click()
                expect(frame.locator("#moves tr")).to_have_count(1)

                # Ответ теряется ровно один раз, и ТОЛЬКО ответ: сам запрос
                # выполняется, поэтому эффект на диске происходит.
                swallowed: list[str] = []

                def lose_the_reply(route):
                    if swallowed:
                        route.continue_()
                        return
                    route.fetch()          # сервер ДЕЛАЕТ работу
                    swallowed.append(route.request.url)
                    route.abort("connectionreset")   # ответ до страницы не доходит

                page.route("**/view/api/files/apply", lose_the_reply)
                frame.locator("#apply").click()
                # Владелец видит отказ — и он честный: страница действительно
                # не знает, что случилось.
                expect(frame.locator("#status.error")).to_be_visible(timeout=30000)
                assert swallowed, "запрос не был выполнен — сценарий не воспроизведён"

                # А на диске эффект УЖЕ есть. Это и есть неизвестный исход.
                target = workspace / "Documents/PDF/owner report.pdf"
                assert target.read_bytes() == CONTENT, "перемещение не состоялось — не тот сценарий"
                assert not source.exists()
                assert len(_batches(server)) == 1, _batches(server)

                # Слепой повтор невозможен по построению: обработчик снимает
                # план и обновляет историю, прежде чем пробросить ошибку. Это
                # и есть верный ответ на неизвестный исход — не дать нажать
                # ещё раз, а показать, что на самом деле произошло.
                expect(frame.locator("#preview")).to_be_hidden()
                expect(frame.locator("#history")).to_contain_text("APPLIED", timeout=30000)
                expect(frame.locator("#history")).to_contain_text(_batches(server)[0]["batch_id"])

                # Законный путь владельца: пересмотреть папку заново. Продукт
                # обязан сказать, что двигать больше нечего, а не двинуть ещё раз.
                frame.locator("#organize").click()
                expect(frame.locator("#status")).to_contain_text("No moves are needed", timeout=30000)
                expect(frame.locator("#moves tr")).to_have_count(0)

                after = _batches(server)
                assert len(after) == 1, f"повтор создал вторую партию: {after}"
                assert after[0]["status"] == "APPLIED"
                assert target.read_bytes() == CONTENT
                assert not source.exists()
                assert not (workspace / "Documents/PDF/Documents").exists(), (
                    "файл переехал второй раз, вложившись в собственную папку")
                assert sorted(p.name for p in (workspace / "Documents/PDF").iterdir()) == \
                    ["owner report.pdf"]
                assert not errors, errors
            finally:
                browser.close()
    finally:
        if server.process is not None and server.process.poll() is None:
            with httpx.Client(trust_env=False, timeout=10,
                              headers={"X-BCC-Token": (server.data / "token").read_text().strip()}) as client:
                client.put(server.url + "/api/apps/control/policy", json={"enabled": True})
                client.post(server.url + "/api/apps/file-commander-mini/stop")
        server.stop()
