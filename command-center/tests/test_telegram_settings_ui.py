"""Telegram section in the real Settings page (Chromium): fill, save, reload.

Model catalogs and Telegram are mocked in-process; no network, no real bot,
no companion process. Skips honestly when no Chromium is available.
"""
from __future__ import annotations

import httpx
import pytest

from bcc.features import telegram_settings as ts

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

OSS = "http://127.0.0.1:8083/v1"
FAST = "http://127.0.0.1:8082/v1"
MAIN = "http://127.0.0.1:8081/v1"
IDS = {OSS: r"C:\m\openai_gpt-oss-120b-MXFP4_MOE-00001-of-00002.gguf",
       MAIN: r"C:\m\Qwen3.8-27B-UD-Q5_K_M.gguf", FAST: r"C:\m\Qwen3.6-35B-A3B-UD-Q5_K_M.gguf"}
TOKEN = "123456" + "789:" + "Ui" * 18          # fixture shape only


@pytest.fixture
def mocked(tmp_path, monkeypatch):
    path = tmp_path / "tg" / "config.json"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_CONFIG", str(path))

    def catalog(request):
        for base, model in IDS.items():
            if str(request.url).startswith(base):
                return httpx.Response(200, json={"data": [{"id": model}]})
        raise AssertionError("unexpected " + str(request.url))
    monkeypatch.setattr(ts, "MODELS_TRANSPORT", httpx.MockTransport(catalog))
    monkeypatch.setattr(ts, "TELEGRAM_TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": True, "result": {"is_bot": True, "username": "ui_fixture_bot"}
                                            if r.url.path.endswith("getMe") else {"url": ""}})))
    monkeypatch.setattr(ts, "_PROC", {"proc": None, "started": None, "log": None})
    return path


def test_owner_fills_and_saves_telegram_settings(live, mocked):  # noqa: F811
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        _login(page, live)
        page.goto(live.url + "/#/settings")
        form = page.wait_for_selector("[data-testid=telegram-settings]", timeout=15000)
        assert form is not None
        # Dropdowns fill themselves from the (mocked) live /v1/models; defaults: best=8083, fastest=8082.
        page.wait_for_function("() => document.querySelector('[name=tg-best]').options.length >= 3", timeout=15000)
        assert "8083" in page.eval_on_selector("[name=tg-best]", "s => s.options[s.selectedIndex].text")
        assert "8082" in page.eval_on_selector("[name=tg-fastest]", "s => s.options[s.selectedIndex].text")
        assert page.is_disabled("[name=tg-delegation]")

        page.fill("[name=tg-token]", TOKEN)
        page.fill("[name=tg-owner]", "11111")
        page.fill("[name=tg-guests]", "22222")
        page.click("text=Сохранить")
        page.wait_for_function("() => document.querySelector('[name=tg-token]').value === ''", timeout=15000)

        page.click("text=Проверить бота")
        page.wait_for_selector("text=ui_fixture_bot", timeout=15000)

        page.reload()
        page.wait_for_selector("[data-testid=telegram-settings]", timeout=15000)
        placeholder = page.get_attribute("[name=tg-token]", "placeholder")
        assert TOKEN not in page.content() and TOKEN[-4:] in placeholder
        assert page.input_value("[name=tg-owner]") == "11111"
        browser.close()

    assert mocked.is_file() and TOKEN not in mocked.read_text(encoding="utf-8")
    assert not errors, errors
