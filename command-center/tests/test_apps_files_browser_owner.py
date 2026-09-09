"""Real BCC browser + real File Commander child + independently read files.

No service/model mocks. A second BCC process uses the same data directory;
managed child identity and exact file history must survive that restart.
"""
from pathlib import Path
import re

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from .browser_support import chromium_available, reason as browser_reason
from .test_editors_user_acceptance import EditorServer, login
from .test_ux2_thinking_pane import _launch

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


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
                frame.locator("#apply").click()
                expect(frame.locator("#status")).to_contain_text("APPLIED")
                target = workspace / "Documents/PDF/owner report.pdf"
                assert target.read_bytes() == b"File Commander owner acceptance; real file bytes"
                assert not source.exists()
                # Restart BCC with the File Commander process still running.
                server.restart()
                page.goto(server.url + "/#/apps?open=file-commander-mini")
                frame = page.frame_locator('iframe[title="File Commander Mini"]')
                expect(frame.locator("#history")).to_contain_text("APPLIED")
                frame.get_by_role("button", name="Restore original names", exact=True).click()
                expect(frame.locator("#status")).to_contain_text("ROLLED_BACK")
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
                # Disabled policy renders the catalogue with one Start per
                # app. Assert the same File Commander control, unambiguously.
                expect(page.get_by_title("Запустить File Commander Mini на этой машине",
                                         exact=True)).to_be_disabled()
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
