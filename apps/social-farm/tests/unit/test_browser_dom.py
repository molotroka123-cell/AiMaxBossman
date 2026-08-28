"""Порт к странице и пул воркеров: то, что проверяется без браузера.

Сверка обеих половин порта живёт в `test_browser_real_chromium.py` — она
требует настоящего Chromium. Здесь остаётся то, что от браузера не зависит:
поведение фикстуры на границах и правила пула.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from browser_kit import PASSWORD, feed_page, login_page, ready_session, session
from social_farm.browser import (BrowserConfig, BrowserWorkerPool, DomError,
                                 FixtureDom, FixtureElement, FixturePage,
                                 playwright_available)
from social_farm.browser.dom import is_secret_field


# ------------------------------------------------------------------ признак секрета

@pytest.mark.parametrize("field,expected", [
    (dict(tag="input", type_="password"), True),
    (dict(tag="input", type_="hidden"), True),
    (dict(tag="input", type_="text", autocomplete="current-password"), True),
    (dict(tag="input", type_="text", autocomplete="new-password"), True),
    (dict(tag="input", type_="text", autocomplete="one-time-code"), True),
    (dict(tag="input", type_="text", name="csrf_token"), True),
    (dict(tag="input", type_="text", element_id="otp-1"), True),
    (dict(tag="input", type_="tel", name="mfa_code"), True),
    (dict(tag="input", type_="text", name="caption"), False),
    (dict(tag="input", type_="checkbox", name="agree"), False),
    (dict(tag="textarea", type_="", name="caption"), False),
    (dict(tag="div", type_="", name="password"), False),
])
def test_the_secret_predicate_covers_the_ways_a_secret_hides(field, expected):
    assert is_secret_field(**field) is expected


# ------------------------------------------------------------------ фикстура

async def test_a_reference_from_a_previous_generation_is_refused():
    """Ссылка, выданная прошлым снимком, недействительна: страница могла
    перерисоваться между тем, что видел вызывающий, и моментом действия."""
    dom = FixtureDom(feed_page())
    first = await dom.elements(50)
    await dom.elements(50)                       # новое поколение
    with pytest.raises(DomError):
        await dom.click(first[0]["ref"])
    assert dom.clicks == []


async def test_an_unknown_search_strategy_is_refused():
    dom = FixtureDom(feed_page())
    with pytest.raises(DomError):
        await dom.find("coordinates", "120,240")


async def test_the_fixture_refuses_an_xpath_it_does_not_understand():
    """Узкое подмножество — и честный отказ на остальном, а не тихое «не нашли».

    Тихий пустой ответ выглядел бы как «цели нет» и уводил бы в поломку
    интерфейса вместо ошибки в пакете селекторов.
    """
    dom = FixtureDom(feed_page())
    with pytest.raises(DomError):
        await dom.find("xpath", "//div[contains(@class,'x')]/following::button[1]")


async def test_the_fixture_understands_the_narrow_css_it_promises():
    page = FixturePage(elements=[
        FixtureElement(tag="button", text="Поделиться",
                       attributes={"id": "share", "class": "primary wide"})])
    dom = FixtureDom(page)
    assert await dom.find("css", "#share")
    assert await dom.find("css", ".primary")
    assert await dom.find("css", "button")
    assert await dom.find("css", "[id=share]")
    assert not await dom.find("css", "#other")


async def test_filling_a_field_changes_the_page_not_only_the_log():
    dom = FixtureDom(login_page())
    found = await dom.find("label", "Имя пользователя")
    await dom.fill(found[0]["ref"], "nashe_ateljie")
    again = await dom.find("label", "Имя пользователя")
    assert again[0]["text"] == "nashe_ateljie"
    assert dom.fills == [(found[0]["ref"], "nashe_ateljie")]


async def test_a_secret_field_reports_filled_but_not_its_value():
    dom = FixtureDom(login_page())
    found = await dom.find("label", "Пароль")
    assert found[0]["secret"] is True
    assert found[0]["filled"] is True
    assert found[0]["text"] == ""
    assert PASSWORD not in repr(found)


async def test_navigation_is_recorded():
    dom = FixtureDom(feed_page())
    await dom.navigate("https://fixture.local/create/")
    assert await dom.current_url() == "https://fixture.local/create/"
    assert dom.navigations == ["https://fixture.local/create/"]


async def test_snapshot_generation_matches_the_references_it_hands_out():
    """Иначе вызывающий сверял бы ссылку с чужим номером поколения."""
    dom, sess = ready_session()
    await sess.start()
    snapshot = await sess.snapshot()
    assert all(e["ref"].startswith(f"sf-{snapshot.generation}-")
               for e in snapshot.elements)


def test_playwright_availability_is_reported_honestly():
    assert playwright_available() is True or playwright_available() is False


# ------------------------------------------------------------------ пул воркеров

def test_every_account_gets_its_own_worker(tmp_path: Path):
    """Общего воркера нет: у процесса ровно один аккаунт."""
    pool = BrowserWorkerPool(config=BrowserConfig(context_root=tmp_path))
    try:
        first = pool.worker_for("acc-A")
        again = pool.worker_for("acc-A")
        other = pool.worker_for("acc-B")
        assert first is again
        assert other is not first
        assert pool.call("acc-A", "ping", timeout=60).payload["account_id"] == "acc-A"
        assert pool.call("acc-B", "ping", timeout=60).payload["account_id"] == "acc-B"
    finally:
        pool.shutdown()
    assert not pool.workers


def test_a_worker_is_never_handed_out_without_an_account():
    pool = BrowserWorkerPool()
    with pytest.raises(ValueError):
        pool.worker_for("   ")
