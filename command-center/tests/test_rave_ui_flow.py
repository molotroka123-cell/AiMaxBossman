"""Agentic Rave page in a real Chromium against a live server (lane rave-apps): start form with repo/allow/test,
connectors + CLI version, events timeline, the real Apply button (WAIT_APPROVAL -> owner approves in the UI ->
apply with approval_id), the confirmed global STOP, and the account-pool card (off by default, opt-in through an
approval). The agents are the mock connector; the CLIs are the stub in tests/rave_stub_cli.py (no network)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytest.importorskip("bossman.apprentice.proc_tree", reason="bossman-core not importable")
pytestmark = [pytest.mark.timeout(240), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

STUB = Path(__file__).with_name("rave_stub_cli.py")
NETWORK_NOISE = re.compile(r"net::ERR_|Failed to load resource|the server responded with a status of (404|501|503)", re.I)


def test_rave_page_runs_applies_with_approval_stops_and_shows_the_pool(live, monkeypatch, tmp_path):  # noqa: F811
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("BOSSMAN_RAVE_CLAUDE_CMD", json.dumps([sys.executable, str(STUB), "claude"]))
    monkeypatch.setenv("BOSSMAN_RAVE_CODEX_CMD", json.dumps([sys.executable, str(STUB), "codex"]))
    monkeypatch.setenv("STUB_LOGIN", "subscription")
    monkeypatch.setenv("BOSSMAN_RAVE_PROFILES_DIR", str(tmp_path / "profiles"))
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and not NETWORK_NOISE.search(m.text) else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.goto(f"{live.url}/#/rave", wait_until="domcontentloaded")
        page.wait_for_function("document.getElementById('page-title').textContent === 'Agentic Rave'", timeout=15000)

        # --- connectors: login, plan and the CLI version are visible
        page.get_by_text("Подключения агентов").wait_for(timeout=15000)
        view = page.locator("#view")
        view.get_by_text("CLI 2.1.284").first.wait_for(timeout=15000)
        assert "план max" in view.inner_text()

        # --- the start form has repo / allow / test fields and refuses an empty task without a request
        view.get_by_placeholder("путь к git-проекту").wait_for()
        view.get_by_placeholder("пути, где local-агенту можно писать").wait_for()
        view.get_by_placeholder("команда проверки").wait_for()
        view.get_by_role("button", name="Запустить").click()
        page.get_by_text("Сначала введите задачу").wait_for(timeout=5000)

        # --- a rave: two mock agents on the same file -> conflict; one of them is applied
        view.get_by_placeholder("Задача для всех агентов").fill("write the readme")
        view.get_by_placeholder("mock:a,local:qwen").fill("mock:a?steps=1&delay=0&file=README.md,mock:b?steps=1&delay=0&file=README.md")
        view.get_by_role("button", name="Запустить").click()
        page.get_by_text(re.compile(r"Рейв rv-[a-z0-9]{8} запущен")).first.wait_for(timeout=15000)
        row_a = view.locator("tbody tr", has_text="mock (scripted")
        view.get_by_text("Конфликты (1)").wait_for(timeout=30000)          # both finished, conflict shown
        assert row_a.count() >= 2

        # events timeline is there and has readable labels
        view.get_by_text("Журнал событий").wait_for()
        assert "агент закончил" in view.inner_text() and "конфликт файлов" in view.inner_text()

        # full answer view
        view.get_by_role("button", name="Ответ").first.click()
        page.locator("#modal-root .modal").get_by_text("Ответ агента").wait_for(timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-root .modal", state="detached", timeout=5000)

        # --- Apply: 202 WAIT_APPROVAL -> the owner approves in the window -> applied
        rid = live.svc.rave.list()[0]["id"]
        rec = live.svc.rave.load(rid)
        readme = Path(rec["repo"]) / "README.md"
        assert "by a" not in readme.read_text(encoding="utf-8")
        agent_row = lambda name: view.locator("tbody tr").filter(has=page.locator("b", has_text=re.compile(f"^{name}$")))  # noqa: E731
        agent_row("a").get_by_role("button", name="Применить").click()
        modal = page.locator("#modal-root .modal")
        modal.get_by_text("Применить результат агента a в проект?").wait_for(timeout=10000)
        assert "rave apply:" in modal.inner_text()
        assert "by a" not in readme.read_text(encoding="utf-8")           # nothing written before the decision
        modal.get_by_role("button", name="Одобрить и применить").click()
        page.get_by_text(re.compile("Применено в проект")).first.wait_for(timeout=15000)
        assert "by a" in readme.read_text(encoding="utf-8")
        view.get_by_text(re.compile(r"применён \(разрешение #\d+\)")).wait_for(timeout=15000)

        # the second agent touches the same file: the refusal is shown in Russian and nothing is written
        agent_row("b").get_by_role("button", name="Применить").click()
        page.get_by_text("уже не совпадают с базой рейва").first.wait_for(timeout=15000)
        assert "by b" not in readme.read_text(encoding="utf-8")

        # --- the confirmed global STOP
        view.get_by_role("button", name="STOP всех рейвов").click()
        page.get_by_text("STOP: активных рейвов не было").first.wait_for(timeout=10000)

        # --- account pool: off by default; the add-account button opens a modal that Esc closes
        view.get_by_text("Пул аккаунтов").first.wait_for()
        assert "выключен" in view.inner_text()
        view.get_by_role("button", name="Добавить аккаунт").click()
        page.locator("#modal-root .modal").get_by_text("Добавить аккаунт в пул").wait_for(timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_selector("#modal-root .modal", state="detached", timeout=5000)
        view.get_by_role("button", name="Добавить аккаунт").click()
        modal = page.locator("#modal-root .modal")
        modal.get_by_placeholder("Имя для себя").fill("Claude основной")
        modal.get_by_role("button", name="Добавить", exact=True).click()
        page.locator("#modal-root .modal").get_by_text("Аккаунт Claude основной добавлен").wait_for(timeout=10000)
        page.keyboard.press("Escape")
        view.get_by_text("Claude основной").first.wait_for(timeout=10000)
        view.get_by_role("button", name="Включить пул").click()
        modal = page.locator("#modal-root .modal")
        modal.get_by_text("Включить пул аккаунтов?").wait_for(timeout=10000)
        assert "условиям использования" in modal.inner_text()
        modal.get_by_role("button", name="Одобрить и включить").click()
        view.get_by_text("Выключить пул").wait_for(timeout=15000)
        assert live.svc.rave.pool.load()["enabled"] is True
        browser.close()
    assert errors == [], errors
