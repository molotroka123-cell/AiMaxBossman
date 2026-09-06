"""Аудит 07 (UX-контракт Command Center), строки A7-03…A7-11.

Каждая проверка — в настоящем Chromium против живого сервера: находки аудита
про то, ЧТО ВИДИТ ВЛАДЕЛЕЦ, а не про форму функции. Недоступную ручку
имитируем перехватом маршрута (page.route → abort) — это тот же обрыв, что и
в репродукции аудита, только воспроизводимый.
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from .browser_support import chromium_available, reason as browser_reason
from .test_ux2_thinking_pane import _launch, _login, live  # noqa: F401

pytestmark = [pytest.mark.timeout(180),
              pytest.mark.skipif(not chromium_available(), reason=browser_reason())]


def _pending_approval(srv, *, kind: str = "terminal", preview: str = "выполнить terminal.run rm -rf") -> int:
    """Ожидающее решение прямо в БД живого сервера."""
    from bcc.db import approvals as approvals_t, utcnow

    async def go():
        async with srv.svc.db.session() as s:
            aid = int((await s.execute(sa.insert(approvals_t).values(
                kind=kind, preview=preview, status="pending", created_at=utcnow()))).inserted_primary_key[0])
            await s.commit()
        return aid
    return asyncio.run_coroutine_threadsafe(go(), srv.loop).result(10)


def _kill_approvals(page) -> None:
    """Ручка решений не отвечает — ровно то состояние, где «пусто» становится ложью."""
    page.route("**/api/approvals*", lambda route: route.abort())


# ---------------------------------------------------------------- A7-03


def test_overview_reports_approvals_outage_instead_of_calm_zero(live):
    from playwright.sync_api import sync_playwright

    _pending_approval(live)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _kill_approvals(page)          # до входа: оболочка спрашивает решения уже на старте
            _login(page, live)
            page.goto(live.url + "/#/overview")
            page.wait_for_selector("text=Needs You", timeout=15000)
            card = page.locator("section.panel", has=page.locator("h2", has_text="Needs You"))
            text = card.inner_text()
            assert "ничего не ждёт решения" not in text, text
            assert "не загрузилось" in text, text
            assert errors == []
        finally:
            browser.close()


def test_overview_still_reports_a_true_empty_queue(live):
    """Негативный контроль: успешный пустой ответ обязан остаться «пусто»."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/overview")
            page.wait_for_selector("text=Needs You", timeout=15000)
            card = page.locator("section.panel", has=page.locator("h2", has_text="Needs You"))
            text = card.inner_text()
            assert "ничего не ждёт решения" in text, text
            assert "не загрузилось" not in text, text
        finally:
            browser.close()


def test_mobile_console_reports_approvals_outage_instead_of_calm_zero(live):
    from playwright.sync_api import sync_playwright

    _pending_approval(live)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _kill_approvals(page)          # до входа: оболочка спрашивает решения уже на старте
            _login(page, live)
            page.goto(live.url + "/#/command")
            page.wait_for_selector("text=Needs You", timeout=15000)
            text = page.locator("#view").inner_text()
            assert "Ничего не ждёт решения" not in text, text
            assert "не загрузилось" in text, text
            assert errors == []
        finally:
            browser.close()


# ---------------------------------------------------------------- A7-04


def _revoke_session_in_place(page) -> None:
    """Сессию отозвали (logout в другой вкладке): cookie больше не принимается."""
    page.evaluate("""async () => {
      await fetch('/api/logout', {method: 'POST',
        headers: {'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''}});
    }""")


def test_revoked_session_reaches_the_login_screen_and_does_not_count_down_forever(live):
    """A7-04 как контракт, а не как регрессия: код не менялся, проверка сторожит.

    Замер: api.py закрывает WS кодом 4401 ДО accept, поэтому Starlette
    отвергает рукопожатие на уровне HTTP и в браузер приходит 1006 без кода —
    ветка `ev.code === 4401` на клиенте была бы мёртвой. Вечного отсчёта при
    этом нет: следующий обычный запрос получает 401 и уводит на вход. Потолок —
    30-секундный такт верхней строки (app.js: loadTopStats), поэтому ждём 35 с.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_selector("#conn-dot.dot-ok", timeout=15000)
            page.wait_for_load_state("networkidle")
            _revoke_session_in_place(page)
            page.evaluate("() => window.__bxConn.bus.reconnectNow()")
            page.wait_for_selector("#login:not([hidden])", timeout=35000)
            assert page.is_hidden("#shell")
        finally:
            browser.close()


def test_server_outage_still_retries_and_does_not_log_the_owner_out(live):
    """Негативный контроль: обрыв без 4401 остаётся обрывом, а не выходом."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.wait_for_selector("#conn-dot.dot-ok", timeout=15000)
            live.stop()
            page.wait_for_selector("#conn-dot:not(.dot-ok)", timeout=15000)
            page.wait_for_timeout(2500)
            assert page.is_hidden("#login")
            assert page.evaluate("() => window.__bxConn.bus.stopped") is False
        finally:
            browser.close()


# ---------------------------------------------------------------- A7-06

PNG_1PX = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
           "AAAABJRU5ErkJggg==")


def _real_image_model(srv, *, alias: str = "sdxl-turbo") -> None:
    """Модель с image-капами: у владельца появляется НЕ-заглушечный выбор."""
    from bcc.db import models as models_t, providers as providers_t

    async def go():
        async with srv.svc.db.session() as s:
            pid = int((await s.execute(sa.insert(providers_t).values(
                name="local-sd", kind="openai_compat", base_url="http://127.0.0.1:9"))).inserted_primary_key[0])
            await s.execute(sa.insert(models_t).values(
                provider_id=pid, name="SDXL Turbo", alias=alias, kind="local",
                caps={"image_generation": True}, status="online"))
            await s.commit()
    asyncio.run_coroutine_threadsafe(go(), srv.loop).result(10)


def _failed_job(srv, *, model_alias: str, error: str) -> int:
    """Упавший job генерации с причиной — ровно то, что пишет _fail_job."""
    from bcc.db import utcnow
    from bcc.v2.images_tables import image_jobs as jobs_t

    async def go():
        async with srv.svc.db.session() as s:
            jid = int((await s.execute(sa.insert(jobs_t).values(
                kind="generate", status="failed", prompt="город на закате",
                model_alias=model_alias, aspect_ratio="16:9", width=1280, height=720,
                error=error, created_at=utcnow(), updated_at=utcnow(),
                finished_at=utcnow()))).inserted_primary_key[0])
            await s.commit()
        return jid
    return asyncio.run_coroutine_threadsafe(go(), srv.loop).result(10)


def _open_images(page, srv):
    _login(page, srv)
    page.goto(srv.url + "/#/images")
    page.wait_for_selector("section.images-composer", timeout=15000)


def test_composer_marks_the_mock_image_model_as_a_stub(live):
    from playwright.sync_api import sync_playwright

    _real_image_model(live)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open_images(page, live)
            options = page.locator("section.images-composer select").first.locator("option").all_inner_texts()
            mock = [o for o in options if "Mock" in o]
            real = [o for o in options if "SDXL" in o]
            assert mock and "заглушка" in mock[0], options
            # Негативный контроль: настоящая модель заглушкой не называется.
            assert real and "заглушка" not in real[0], options
            assert errors == []
        finally:
            browser.close()


def test_failed_generation_shows_its_reason_and_hides_a_doomed_retry(live):
    from playwright.sync_api import sync_playwright

    _real_image_model(live)
    _failed_job(live, model_alias="sdxl-turbo",
                error="реальный image provider для «sdxl-turbo» ещё не подключён")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open_images(page, live)
            page.click("text=Генерации")
            row = page.locator(".images-job-row").first
            row.wait_for(timeout=10000)
            assert "ещё не подключён" in row.inner_text(), row.inner_text()
            assert row.locator("button", has_text="Повторить").count() == 0, row.inner_text()
            assert errors == []
        finally:
            browser.close()


def test_failed_mock_generation_still_offers_retry(live):
    """Позитивный контроль: исполнимая модель сохраняет «Повторить»."""
    from playwright.sync_api import sync_playwright

    _failed_job(live, model_alias="mock-image", error="диск был занят")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            _open_images(page, live)
            page.click("text=Генерации")
            row = page.locator(".images-job-row").first
            row.wait_for(timeout=10000)
            assert "диск был занят" in row.inner_text(), row.inner_text()
            assert row.locator("button", has_text="Повторить").count() == 1, row.inner_text()
        finally:
            browser.close()


def test_variation_of_an_import_keeps_the_chosen_model_not_a_mock_stamp(live):
    from playwright.sync_api import sync_playwright

    _real_image_model(live)
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _open_images(page, live)
            page.evaluate("""async data => {
              const headers = {'Content-Type':'application/json',
                               'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''};
              const res = await fetch('/api/images/assets/import', {method:'POST', headers,
                body: JSON.stringify({filename:'shot.png', data_base64:data, title:'Мой файл'})});
              if (!res.ok) throw new Error('import failed: ' + res.status);
            }""", PNG_1PX)
            page.reload()
            page.wait_for_selector("section.images-composer", timeout=15000)
            page.select_option("section.images-composer select >> nth=0", "sdxl-turbo")
            page.click("button:has-text('Вариация')")
            page.wait_for_selector(".images-job-row", timeout=10000)
            job = page.evaluate("""async () => {
              const r = await (await fetch('/api/images/jobs?limit=1')).json();
              return r.items[0];
            }""")
            assert job["model_alias"] == "sdxl-turbo", job
            assert errors == []
        finally:
            browser.close()


# ---------------------------------------------------------------- A7-11


def test_operator_channel_does_not_call_an_outage_a_calm_silence(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _kill_approvals(page)
            _login(page, live)
            page.goto(live.url + "/#/mission_console")
            page.wait_for_selector(".mc-feed .mc-blank", timeout=15000)
            text = page.locator(".mc-feed").inner_text()
            assert "Это не сбой" not in text, text
            assert "не ответил" in text, text
            assert errors == []
        finally:
            browser.close()


def test_operator_channel_still_calls_a_true_silence_a_silence(live):
    """Негативный контроль: живая ручка и пустая работа — по-прежнему «канал молчит»."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/mission_console")
            page.wait_for_selector(".mc-feed .mc-blank", timeout=15000)
            text = page.locator(".mc-feed").inner_text()
            assert "Канал молчит" in text, text
            assert "Это не сбой" in text, text
        finally:
            browser.close()


# ---------------------------------------------------------------- A7-10


def _decide_elsewhere(page, approval_id: int, approve: bool) -> None:
    """Решение, принятое в другой вкладке, пока эта держит карточку открытой."""
    page.evaluate("""async ([id, approve]) => {
      const res = await fetch(`/api/approvals/${id}`, {method: 'POST',
        headers: {'Content-Type':'application/json',
                  'X-BCC-CSRF': localStorage.getItem('bcc.csrf') || ''},
        body: JSON.stringify({approve, by: 'другая вкладка'})});
      if (!res.ok) throw new Error('decide failed: ' + res.status);
    }""", [approval_id, approve])


def test_console_does_not_confirm_a_decision_that_was_already_taken(live):
    from playwright.sync_api import sync_playwright

    aid = _pending_approval(live, kind="terminal", preview="выполнить terminal.run ls")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.goto(live.url + "/#/mission_console")
            page.wait_for_selector('[data-card="approval"]', timeout=15000)
            _decide_elsewhere(page, aid, approve=False)
            page.click('[data-card="approval"] button:has-text("Подтвердить")')
            page.wait_for_selector(".toast", timeout=10000)
            text = page.locator(".toast").first.inner_text()
            assert "Разрешено" not in text, text
            assert "уже" in text, text
            row = page.evaluate("""async id => (await (await fetch(`/api/approvals?status=`)).json())
                                     .find(a => a.id === id)""", aid)
            assert row["status"] == "rejected", row
            assert errors == []
        finally:
            browser.close()


def test_console_confirms_a_decision_it_actually_made(live):
    """Позитивный контроль: обычное подтверждение по-прежнему тостится успехом."""
    from playwright.sync_api import sync_playwright

    aid = _pending_approval(live, kind="terminal", preview="выполнить terminal.run ls")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            _login(page, live)
            page.goto(live.url + "/#/mission_console")
            page.wait_for_selector('[data-card="approval"]', timeout=15000)
            page.click('[data-card="approval"] button:has-text("Подтвердить")')
            page.wait_for_selector(".toast", timeout=10000)
            assert "Разрешено" in page.locator(".toast").first.inner_text()
            row = page.evaluate("""async id => (await (await fetch(`/api/approvals?status=`)).json())
                                     .find(a => a.id === id)""", aid)
            assert row["status"] == "approved", row
        finally:
            browser.close()


def test_mobile_console_does_not_confirm_a_decision_that_was_already_taken(live):
    from playwright.sync_api import sync_playwright

    aid = _pending_approval(live, kind="terminal", preview="выполнить terminal.run ls")
    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _login(page, live)
            page.goto(live.url + "/#/command")
            page.wait_for_selector(".cmd-approval button:has-text('Разрешить')", timeout=15000)
            _decide_elsewhere(page, aid, approve=False)
            page.click(".cmd-approval button:has-text('Разрешить')")
            page.wait_for_selector(".toast", timeout=10000)
            text = page.locator(".toast").first.inner_text()
            assert "Подтверждено" not in text, text
            assert "уже" in text, text
            assert errors == []
        finally:
            browser.close()


# ---------------------------------------------------------------- A7-08

# Флот включается тремя env-флагами V3, поэтому ответ ручки подменяем на месте:
# строка проверяет контракт «Пульта», а не способ поднять организацию.
FLEET_ON = {
    "owner_view": {"rows": [], "rule": "правило владельца"},
    "queue": {"queued": 0},
    "treasury": {"burn_rate_usd_per_h": 0, "fable": {"status": "OK", "remaining_usd": 10}},
    "latency": {},
    "fleet": {"enabled": True, "nodes": [{"id": "node-1"}], "queue_depth": 3,
              "remote_transport_production_ready": False, "node_auth_production_ready": False},
}
FLEET_OFF = {**FLEET_ON, "fleet": {"enabled": False, "nodes": [], "queue_depth": 0,
                                   "remote_transport_production_ready": False,
                                   "node_auth_production_ready": False}}


def _stub_control_plane(page, body) -> None:
    import json
    page.route("**/api/control-plane",
               lambda route: route.fulfill(status=200, content_type="application/json",
                                           body=json.dumps(body)))


def _facts_text(page, srv, body):
    _stub_control_plane(page, body)
    _login(page, srv)
    page.goto(srv.url + "/#/control")
    page.wait_for_selector("text=Состояние системы", timeout=15000)
    return page.locator("#view").inner_text()


def test_fleet_row_is_marked_experimental_while_transport_is_not_production(live):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            text = _facts_text(page, live, FLEET_ON)
            assert "узлов 1 · очередь 3" in text, text
            assert "эксперимент" in text, text
            assert "НЕТ (не production)" in text, text
            assert errors == []
        finally:
            browser.close()


def test_disabled_fleet_row_carries_no_experiment_badge(live):
    """Негативный контроль: выключенный флот не обрастает пометками."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch(pw)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            text = _facts_text(page, live, FLEET_OFF)
            assert "выключен" in text, text
            assert "эксперимент" not in text, text
        finally:
            browser.close()
