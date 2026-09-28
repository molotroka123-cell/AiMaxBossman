"""RC 1.9 soak (workstream F): a completed task showed a red «Ошибка» above its result.

Repro: an agent whose model is unreachable gets a task; the engine retries, falls back to
another healthy model and completes. The run keeps the earlier error text, and the task
card rendered it as a red «Ошибка» section right above «Результат» — the owner reads a
successful task as failed. The text stays (it explains the fallback); for a completed task
it is titled «Сбои до ответа» and not painted as a failure. Failed tasks are unchanged.
"""
from __future__ import annotations

import pytest

from bcc.db import utcnow

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_home_attention import _insert_task
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _card(page, title: str) -> dict:
    page.locator("#view .task-head", has_text=title).first.click()
    page.wait_for_function("() => { const b = document.querySelector('#view .task.open .task-body'); "
                           "return b && !b.innerText.includes('Загрузка деталей') }", timeout=10000)
    return page.evaluate("""() => {
        const open = document.querySelector('#view .task.open');
        const titles = [...open.querySelectorAll('.section-title')].map(e => e.innerText.trim().toLowerCase());
        const blocks = [...open.querySelectorAll('pre.block')].map(e => ({ text: e.innerText, color: e.style.color }));
        return { titles, blocks };
    }""")


def test_completed_task_with_fallback_error_is_not_shown_as_failed(live):  # noqa: F811
    from playwright.sync_api import sync_playwright

    _insert_task(live, title="Ответ через запасную модель", status="completed",
                 run={"error": "нет связи с http://127.0.0.1:8879: ConnectError", "result": "Париж",
                      "model_alias": "soak-fast", "finished_at": utcnow()})
    _insert_task(live, title="Настоящий провал", status="failed",
                 run={"error": "провайдер недоступен", "finished_at": utcnow()})
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        _login(page, live)
        page.wait_for_selector("#view[data-rendered]", timeout=20000)
        page.goto(live.url + "/#/tasks")
        page.wait_for_selector("#view[data-rendered='tasks']", timeout=20000)
        done = _card(page, "Ответ через запасную модель")
        page.locator("#view .task-head", has_text="Ответ через запасную модель").first.click()   # collapse
        failed = _card(page, "Настоящий провал")
        browser.close()

    assert "ошибка" not in done["titles"], done
    assert "сбои до ответа" in done["titles"] and "результат" in done["titles"]
    err_block = next(b for b in done["blocks"] if "нет связи" in b["text"])
    assert "--err" not in err_block["color"]
    # a failed task still says «Ошибка» in the error colour
    assert "ошибка" in failed["titles"]
    assert any("--err" in b["color"] for b in failed["blocks"] if "провайдер недоступен" in b["text"])
