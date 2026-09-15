"""Real BCC browser + real File Commander child + independently read files.

No service/model mocks; only the post-mutation history read is interrupted.
A second BCC process uses the same data directory; managed child identity
and exact file history must survive that restart.
"""
from pathlib import Path
import os
import re
import json
import sqlite3
import time

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import chromium_available, reason as browser_reason
from .test_editors_user_acceptance import EditorServer, login
from .test_ux2_thinking_pane import _launch

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _record_margin(step: str, milliseconds: float) -> None:
    """Запас времени, а не только вердикт.

    Зелёный на 4,8 с при бюджете 5 с и зелёный на 0,2 с говорят о продукте
    разное, а в журнале выглядят одинаково. BL-048 остался открытым ровно
    потому, что этого числа не было ни в одном успешном прогоне. Пишется
    рядом с уликами, которые задание и так выгружает; сбой записи не может
    уронить проверку продукта.
    """
    directory = os.environ.get("BOSSMAN_EDITOR_EVIDENCE_DIR")
    if not directory:
        return
    try:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        with (path / "owner-history-margins.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"step": step, "ms": round(milliseconds, 1)}) + "\n")
    except OSError:
        pass


def _refresh_launcher(page):
    header = page.locator(".bx-appview-head").element_handle()
    assert header is not None
    try:
        page.locator("#refresh-btn").click()
        # Wait for the actual rerender, not just response headers while the
        # old document is still visible and could satisfy an early assertion.
        page.wait_for_function("header => !header.isConnected", arg=header)
    finally:
        header.dispose()


def test_apps_files_owner_browser_restart_and_persistence(tmp_path, monkeypatch):
    workspace = tmp_path / "owner-workspace"
    workspace.mkdir()
    source = workspace / "owner report.pdf"
    source.write_bytes(b"File Commander owner acceptance; real file bytes")
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
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                login(page, server)
                page.goto(server.url + "/#/apps?open=file-commander-mini")
                page.get_by_role("button", name="Разрешить запуск приложений", exact=True).click()
                start = page.get_by_role("button", name="Запустить", exact=True)
                expect(start).to_be_enabled()
                start.click()
                frame = page.frame_locator('iframe[title="File Commander Mini"]')
                expect(frame.locator("#status")).to_contain_text("1 files", timeout=30000)
                frame.locator("#organize").click()
                expect(frame.locator("#moves tr")).to_have_count(1)
                assert source.exists(), "preview mutated source"
                # A confirmed move must remain recoverable in the displayed
                # history even if the following read is interrupted by restart.
                history_url = "**/view/api/files/batches"
                page.route(history_url, lambda route: route.abort("connectionreset"))
                frame.locator("#apply").click()
                expect(frame.locator("#status")).to_contain_text("APPLIED")
                database = server.data / "app-data/file-commander-mini/app.db"
                with sqlite3.connect(database) as connection:
                    batch = json.loads(connection.execute(
                        "SELECT value FROM kv WHERE namespace='batches'"
                    ).fetchone()[0])
                assert batch["status"] == "APPLIED"
                expect(frame.locator("#history")).to_contain_text("APPLIED")
                expect(frame.locator("#history")).to_contain_text(batch["batch_id"])
                page.unroute(history_url)
                target = workspace / "Documents/PDF/owner report.pdf"
                assert target.read_bytes() == b"File Commander owner acceptance; real file bytes"
                assert not source.exists()
                # Restart BCC with the File Commander process still running.
                server.restart()
                page.reload()
                frame = page.frame_locator('iframe[title="File Commander Mini"]')
                durable_history = server.url + "/api/apps/file-commander-mini/view/api/files/batches"
                # Красный отказ этой строки до сих пор не различал «окно ожидания
                # моргнуло» и «персистентность потеряна», а это разные дефекты, и
                # второй тяжелее. Поэтому на отказе сначала спрашивается
                # авторитетный сервер, и сообщение само говорит, что произошло.
                # Порог не поднят: 5000 мс закрыли бы оба случая одинаково.
                started = time.monotonic()
                try:
                    expect(frame.locator("#history")).to_contain_text("APPLIED")
                except AssertionError as displayed:
                    try:
                        answer = context.request.get(durable_history)
                        served = f"status={answer.status} body={answer.text()[:2000]}"
                    except Exception as unreachable:  # диагностика, не проверка
                        served = f"сервер не ответил: {unreachable!r}"
                    raise AssertionError(
                        "после перезапуска история APPLIED не показана в интерфейсе; "
                        f"авторитетный ответ перезапущенного сервера: {served}"
                    ) from displayed
                _record_margin("history_after_restart", (time.monotonic() - started) * 1000)
                # Read through the restarted BCC independently of the freshly
                # loaded iframe, distinguishing durable history from DOM state.
                response = context.request.get(durable_history)
                assert response.status == 200, response.text()
                assert any(item["batch_id"] == batch["batch_id"] and item["status"] == "APPLIED"
                           for item in response.json()["batches"])
                page.route(history_url, lambda route: route.abort("connectionreset"))
                frame.get_by_role("button", name="Restore original names", exact=True).click()
                expect(frame.locator("#status")).to_contain_text("ROLLED_BACK")
                expect(frame.locator("#history")).to_contain_text("ROLLED_BACK")
                expect(frame.get_by_role("button", name="Restore original names", exact=True)).to_have_count(0)
                page.unroute(history_url)
                assert source.read_bytes() == b"File Commander owner acceptance; real file bytes"
                assert not target.exists()
                frame.locator("#root").fill(str(workspace.parent))
                # The shell follows this same render path on ws.open/reconnect.
                # An ordinary refresh must not unload the active app iframe.
                _refresh_launcher(page)
                expect(frame.locator("#root")).to_have_value(str(workspace.parent))
                frame.locator("#scan").click()
                expect(frame.locator("#status")).to_contain_text("outside FILE_COMMANDER_ROOTS")
                _refresh_launcher(page)
                expect(frame.locator("#status")).to_contain_text("outside FILE_COMMANDER_ROOTS")
                assert source.exists()
                page.get_by_role("button", name="Остановить", exact=True).click()
                expect(page.get_by_role("button", name="Запустить", exact=True)).to_be_enabled()
                page.goto(server.url + "/#/apps")
                page.get_by_role("button", name="Запретить запуск приложений", exact=True).click()
                expect(page.get_by_role("button", name="Разрешить запуск приложений", exact=True)).to_be_visible()
                server.restart()
                page.goto(server.url + "/#/apps?open=file-commander-mini")
                expect(page.get_by_role("button", name="Разрешить запуск приложений", exact=True)).to_be_visible()
                # Disabled policy: the app view's Start control is disabled and
                # its title says why (the UI swaps the launch title for the
                # reason). No control anywhere still offers a launch that the
                # server would refuse.
                expect(page.get_by_title("Управление приложениями выключено",
                                         exact=True)).to_be_disabled()
                expect(page.get_by_title("Запустить File Commander Mini на этой машине",
                                         exact=True)).to_have_count(0)
                assert not errors, errors
            finally:
                browser.close()
    finally:
        # Own test app only, through the authenticated launcher; never kill a
        # process found just because it occupies a port.
        if server.process is not None and server.process.poll() is None:
            with httpx.Client(trust_env=False, timeout=10, headers={"X-BCC-Token": (server.data / "token").read_text().strip()}) as client:
                client.put(server.url + "/api/apps/control/policy", json={"enabled": True})
                client.post(server.url + "/api/apps/file-commander-mini/stop")
        server.stop()
