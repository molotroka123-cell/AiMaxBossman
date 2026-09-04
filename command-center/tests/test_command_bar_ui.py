"""Командная строка в настоящем Chromium: намерение показывается, без
подтверждения не выполняется, начатая задача переживает уход со страницы.

Серверную половину проверяет test_command_bar.py. Здесь проверяется ровно то,
что владелец делает руками: печатает команду в поле, читает, ЧТО будет сделано,
и убеждается, что необратимое действие не случается само.

Панель монтируется из теста (`import('/commandbar.js')`), потому что строку
подключения в app.js добавляет ведущий: тест проверяет модуль, а не чужой файл.
"""
from __future__ import annotations

import httpx
import pytest

from bcc.features import command_bar as cb

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

MOUNT = ("async () => { const m = await import('/commandbar.js');"
         " window.__bxCmd = m.mountCommandBar(); }")

# Разбор и запуск мимо панели: нужен, чтобы отдельно доказать, что отказ живёт
# на СЕРВЕРЕ, а не только в виде неактивной кнопки.
RUN_WITHOUT_CONFIRM = """
async (text) => {
  const headers = {'Content-Type': 'application/json',
                   'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''};
  const parsed = await (await fetch('/api/command-bar/parse',
    {method: 'POST', headers, body: JSON.stringify({text})})).json();
  const res = await fetch('/api/command-bar/run',
    {method: 'POST', headers, body: JSON.stringify({intent_id: parsed.intent_id})});
  return res.status;
}
"""


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Флаг выключен по умолчанию — включаем до старта живого сервера."""
    monkeypatch.setenv(cb.FLAG, "1")


def _real_errors(errors: list[str]) -> list[str]:
    """Ошибки консоли за вычетом тех, что тест вызвал намеренно."""
    return [e for e in errors if "412" not in e]


def _client(srv) -> httpx.Client:
    """Прямой клиент к живому серверу мимо прокси из окружения (см. loopback_get)."""
    return httpx.Client(trust_env=False, timeout=10.0, base_url=srv.url,
                        headers={"X-BCC-Token": srv.svc.auth.token})


def _page(pw, live_srv, errors: list[str]):
    browser = _launch(pw)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _login(page, live_srv)
    page.evaluate(MOUNT)
    page.wait_for_selector("#cmdbar-input", timeout=15000)
    page.wait_for_function(
        "() => (document.querySelector('#cmdbar-note')||{}).textContent"
        "?.includes('Возможностей')", timeout=15000)
    return browser, page


def test_intent_is_shown_and_nothing_runs_without_confirmation(live):  # noqa: F811
    """Поле принимает команду, показывает намерение и не выполняет его само."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with _client(live) as api:
        agent_id = api.post("/api/agents", json={"name": "жертва"}).json()["id"]

        with sync_playwright() as pw:
            browser, page = _page(pw, live, errors)

            page.fill("#cmdbar-input", f"agents.delete {agent_id}")
            page.click("#cmdbar-parse")
            page.wait_for_selector("#cmdbar-summary", timeout=15000)

            summary = page.inner_text("#cmdbar-summary")
            capability = page.inner_text("#cmdbar-capability")
            assert "НЕОБРАТИМО" in summary, summary
            assert "DELETE /api/agents/{agent_id}" in capability, capability
            assert str(agent_id) in page.inner_text("#cmdbar-args"), \
                "цель действия обязана быть видна"

            # Кнопка не работает до отметки, и «нажать всё равно» ничего не даёт:
            # отключённая кнопка события клика не порождает.
            assert page.get_attribute("#cmdbar-run", "disabled") is not None
            page.evaluate("() => document.querySelector('#cmdbar-run').click()")
            page.wait_for_timeout(700)

            assert api.get("/api/agents").json(), "агент обязан остаться на месте"
            assert api.get("/api/command-bar/tasks").json()["tasks"] == [], \
                "без подтверждения не заводится даже фоновая задача"

            # Тот же отказ на сервере: неактивная кнопка — удобство, а не защита.
            assert page.evaluate(RUN_WITHOUT_CONFIRM, f"agents.delete {agent_id}") == 412

            # А с подтверждением — выполняется: проверка не должна быть
            # «всегда отказывает», иначе она ничего не доказывает.
            page.check("#cmdbar-confirm")
            assert page.get_attribute("#cmdbar-run", "disabled") is None
            page.click("#cmdbar-run")
            page.wait_for_selector('.bx-cmd-task[data-state="done"]', timeout=20000)
            browser.close()

        assert api.get("/api/agents").json() == [], "подтверждённое действие обязано случиться"
    # 412 браузер печатает в консоль сам, а этот отказ тест провоцирует нарочно
    # (проверка RUN_WITHOUT_CONFIRM выше) — он доказательство, а не дефект.
    assert not _real_errors(errors), f"ошибки консоли: {errors}"


def test_a_started_task_is_still_there_after_leaving_the_page(live):  # noqa: F811
    """Задача живёт на сервере: перезагрузка страницы её не теряет."""
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser, page = _page(pw, live, errors)

        page.fill("#cmdbar-input", "система")
        page.click("#cmdbar-parse")
        page.wait_for_selector("#cmdbar-summary", timeout=15000)
        assert "Обратимо" in page.inner_text("#cmdbar-reversible")
        assert page.query_selector("#cmdbar-confirm") is None, "обратимое не требует отметки"

        page.click("#cmdbar-run")
        row = page.wait_for_selector(".bx-cmd-task", timeout=20000)
        task_id = row.get_attribute("data-id")
        assert task_id

        page.reload(wait_until="domcontentloaded")          # ушли со страницы и вернулись
        page.wait_for_selector("#shell:not([hidden])", timeout=15000)
        page.evaluate(MOUNT)
        page.wait_for_selector(f'.bx-cmd-task[data-id="{task_id}"]', timeout=20000)
        state = page.get_attribute(f'.bx-cmd-task[data-id="{task_id}"]', "data-state")
        assert state in ("running", "done"), state
        browser.close()

    assert not _real_errors(errors), f"ошибки консоли: {errors}"
