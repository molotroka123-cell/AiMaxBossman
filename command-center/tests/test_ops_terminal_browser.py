"""Lane ops (2026-09-30): терминал, браузер и жизненный цикл их сессий.

Что проверяется и почему это не «зелёная галочка»:

* P0-гейт браузера: клик по «Оплатить/Купить/Опубликовать/Отправить/Удалить…»
  раньше был AUTO. Тесты идут на НАСТОЯЩЕМ Chromium по локальной странице и
  всегда парами: опасная кнопка — решение владельца, обычная ссылка — по-прежнему
  AUTO (негативный контроль: гейт не превратился в «спрашивать всегда»).
* Пароль агент не вводит: только `fill_secret` (значение из хранилища рантайма).
* Терминал: контейнер песочницы получает имя и убивается `docker kill`; хвост вывода
  переживает вытеснение из памяти; остановка задачи убивает её команды; рестарт
  не оставляет строки `running`.
* Браузер: мёртвая строка `running` не переиспользуется, takeover/resume мёртвой
  сессии — 409, а не 500, скриншоты не копятся файлами, сессии закрываются с задачей.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import random
import shutil
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc.db import tasks as tasks_t, utcnow
from bcc.features import browser as browser_feature
from bcc.features import terminal as terminal_feature
from bcc.features import tools_browser
from bcc.tools import ToolContext, decide_effect
from bcc.v2 import terminal_control
from bcc.v2.browser_control import (BrowserConsequenceApprovalRequired, BrowserManager,
                                    BrowserPolicy, BrowserPolicyDenied, click_consequence,
                                    live_ref_info, selector_consequence)
from bcc.v2.tables import browser_sessions as bs_t, terminal_sessions as term_t
from bcc.v2.terminal_control import TerminalManager, TerminalPolicy, TerminalSession

from .browser_support import chromium_available, reason as browser_reason
from .conftest import make_settings, start_app, wait_for
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

pytestmark_browser = pytest.mark.skipif(not chromium_available(), reason=browser_reason())


@pytest.fixture(autouse=True)
def _allow_private_browser_targets(monkeypatch):
    """F-010: тестовый сайт живёт на 127.0.0.1 — owner-override только для этих тестов."""
    monkeypatch.setenv("BCC_BROWSER_ALLOW_PRIVATE", "1")


# ----------------------------------------------------------------------------------
# Страницы: по одной кнопке на каждый способ «переименовать» опасное действие
# ----------------------------------------------------------------------------------

def _mark(text: str) -> str:
    return f"onclick=\"document.getElementById('out').innerText='{text}'\""


SHOP_PAGE = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Магазин</title></head><body>
<p id="out">ничего не нажато</p>
<a id="about" href="/about.html">О нас</a>
<button id="pay" type="button" {_mark('НАЖАТА-ОПЛАТА')}>Оплатить</button>
<button id="more" type="button" {_mark('НАЖАТО-ЕЩЁ')}>Показать ещё</button>
<button id="buy" type="button" {_mark('НАЖАТО-BUY')}>Buy now</button>
<button id="caps" type="button" {_mark('НАЖАТО-CAPS')}>ОПЛАТИТЬ</button>
<button id="glyph" type="button" {_mark('НАЖАТО-GLYPH')}>Pаy</button>
<button id="spaced" type="button" {_mark('НАЖАТО-SPACED')}>П о к у п к а</button>
<button id="aria" type="button" aria-label="Опубликовать" {_mark('НАЖАТО-ARIA')}>OK</button>
<button id="btn-pay" type="button" {_mark('НАЖАТО-ICON')}>&#128179;</button>
<button id="del" type="button" {_mark('НАЖАТО-DEL')}><span id="delspan">Удалить аккаунт</span></button>
<div id="box"><button id="nested" type="button" {_mark('НАЖАТО-NESTED')}>Send message</button></div>
<form method="post" action="/nowhere" onsubmit="return false">
  <input id="user" name="user" type="text">
  <input id="pw" name="pw" type="password">
  <button id="neutral" type="submit" {_mark('НАЖАТО-FORM')}>Продолжить</button>
</form>
<form method="get" action="/search" onsubmit="return false">
  <input id="q" name="q" type="text">
  <button id="search" type="submit" {_mark('НАЖАТО-ПОИСК')}>Найти</button>
</form>
</body></html>"""

ABOUT_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>О нас</title></head><body><h1>О нас</h1></body></html>"""

# Порядок узлов фиксирован: ref = e<поколение>-<номер среди a,button,input,…>
E2E_PAGE = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Касса</title></head><body>
<p id="out">ничего не нажато</p>
<a id="about" href="/about.html">О нас</a>
<button id="pay" type="button" {_mark('НАЖАТА-ОПЛАТА')}>Оплатить</button>
</body></html>"""


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "shop.html").write_text(SHOP_PAGE, encoding="utf-8")
    (root / "e2e.html").write_text(E2E_PAGE, encoding="utf-8")
    (root / "about.html").write_text(ABOUT_PAGE, encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
async def mgr(tmp_path):
    from bcc.features.browser import _patch_executable
    manager = BrowserManager(tmp_path / "browser")
    _patch_executable(manager)
    yield manager
    try:
        await manager.close()
    except Exception:
        pass


async def _open_shop(mgr, site, page="shop.html") -> int:
    await mgr.start(1, BrowserPolicy.from_dict(None), headless=True)
    await mgr.navigate(1, f"{site}/{page}", actor="agent", approved=True)
    return 1


def _desc(**kw):
    base = dict(tag="button", type="", interactive=True, role="", text="", aria="", title="",
                alt="", value="", name="", id="", testid="", cls="", href="", submit=False,
                method="get", action="")
    base.update(kw)
    return base


# ==================================================================================
# 1. P0: клик с последствиями — словарь (без браузера)
# ==================================================================================

@pytest.mark.parametrize("label", [
    "Оплатить", "ОПЛАТИТЬ", "оплатить заказ", "Купить", "Купить сейчас", "Заказать звонок",
    "Оформить заказ", "Подписаться", "Опубликовать", "Отправить", "Удалить", "Удалить аккаунт",
    "Перевод средств", "Pay", "Pay now", "Buy now", "Purchase", "Checkout", "Place order",
    "Subscribe", "Publish", "Post", "Send message", "Submit", "Delete", "Transfer",
    "Pаy",                       # кириллическая «а» в латинском слове
    "Оплaтить".replace("т", "t"),   # латиница внутри русского слова
    "P a y", "П о к у п к а",         # буквы через пробел
    "Buy​ now",                  # невидимый символ внутри
    "Ｐａｙ",                          # полноширинные буквы
])
def test_dictionary_catches_consequential_labels_in_every_spelling(label):
    hit = click_consequence(_desc(text=label))
    assert hit, f"клик по «{label}» должен требовать решения владельца"
    assert hit["label"] == label[:80]


@pytest.mark.parametrize("desc", [
    _desc(text="", aria="Опубликовать"),
    _desc(text="", title="Buy"),
    _desc(text="", alt="Оплатить", tag="input", type="image"),
    _desc(text="", value="Удалить", tag="input", type="button"),
    _desc(text="", id="btn-pay"),
    _desc(text="", id="buyNow"),
    _desc(text="", testid="checkout-submit"),
    _desc(text="Продолжить", type="submit", submit=True, method="post"),
    _desc(text="Go", type="submit", submit=True, method="get", action="/cart/checkout"),
    _desc(tag="a", text="Изменить", href="https://shop.test/items/5/delete"),
])
def test_dictionary_catches_renamed_and_structural_targets(desc):
    assert click_consequence(desc), desc


@pytest.mark.parametrize("desc", [
    _desc(tag="a", text="О нас", href="/about"),
    _desc(tag="a", text="Тарифы", href="/pricing"),
    _desc(tag="a", text="Contact us", href="/contact"),
    _desc(tag="a", text="Найти", href="/watch.html"),
    _desc(tag="a", text="Войти", href="/login"),          # ссылка на страницу входа — не вход
    _desc(tag="a", text="How to buy a house and order furniture in a new country this year",
          href="/blog/how-to-buy"),                        # заголовок статьи — не команда
    _desc(tag="a", text="Дом", href="https://x.test/blog/how-to-buy-a-house"),
    _desc(text="Показать ещё"),
    _desc(text="OK"),
    _desc(text="Отмена"),
    _desc(text="Postal code"),
    _desc(text="Border"),
    _desc(text="Применить купон"),
    _desc(text="Стерео звук"),
    _desc(text="Найти", type="submit", submit=True, method="get"),   # поиск по GET
])
def test_dictionary_leaves_ordinary_targets_alone(desc):
    """Негативный контроль: словарь не стал «спрашивать обо всём»."""
    assert click_consequence(desc) is None, desc


def test_unknown_target_fails_closed():
    assert click_consequence(None)["category"] == "unknown"


def test_selector_text_is_a_weak_early_warning_not_a_free_pass():
    assert selector_consequence("text=Оплатить")
    assert selector_consequence("button:has-text('Buy')")
    assert selector_consequence("#btn-pay")
    assert selector_consequence("input[type=submit]")
    assert selector_consequence("#about") is None
    assert selector_consequence("a.nav-link") is None


class _SnapPage:
    """Страница-двойник: отдаёт заранее заданные элементы, как отдал бы JS снимка."""
    url = "http://shop.test/"

    def __init__(self, items):
        self.items = items

    async def evaluate(self, js, arg=None):
        return {"text": "t", "total": 1, "interactive": [dict(i) for i in self.items]}

    async def content(self):
        return "<html></html>"

    async def title(self):
        return "t"


class _SnapContext:
    pages: list = []


async def test_snapshot_classification_survives_odd_and_missing_descriptions(tmp_path):
    """Описание узла не собралось (null) — цель «неизвестна» и помечена; у секретного поля и
    у страницы-двойника без описаний классифицировать нечего и падать тоже нельзя."""
    from bcc.v2.browser_control import BrowserRuntimeSession
    items = [
        {"ref": "e1-0", "tag": "button", "text": "Оплатить", "desc": _desc(text="Оплатить")},
        {"ref": "e1-1", "tag": "a", "text": "О нас", "desc": _desc(tag="a", text="О нас", href="/about")},
        {"ref": "e1-2", "tag": "button", "text": "?", "desc": None},
        {"ref": "e1-3", "tag": "input", "type": "password", "secret": True, "desc": None},
        {"ref": "e1-4", "tag": "div", "text": "без описания"},
    ]
    mgr = BrowserManager(tmp_path / "browser")
    mgr._sessions[1] = BrowserRuntimeSession(id=1, policy=BrowserPolicy.from_dict(None),
                                             context=_SnapContext(), page=_SnapPage(items))
    snap = await mgr.snapshot(1, actor="agent", approved=True)
    by_ref = {el["ref"]: el for el in snap["interactive"]}
    assert by_ref["e1-0"]["consequence"] == "payment"
    assert "consequence" not in by_ref["e1-1"]
    assert by_ref["e1-2"]["consequence"] == "unknown"
    assert "consequence" not in by_ref["e1-3"] and "consequence" not in by_ref["e1-4"]
    assert all("desc" not in el for el in snap["interactive"])
    refs = mgr._sessions[1].refs
    assert refs["e1-0"]["consequence"]["category"] == "payment" and refs["e1-3"]["secret"] is True
    assert refs["e1-1"]["consequence"] is None


# ==================================================================================
# 2. P0: клик на настоящем Chromium — граница BrowserManager.click
# ==================================================================================

@pytestmark_browser
@pytest.mark.parametrize("selector,mark", [
    ("#pay", "НАЖАТА-ОПЛАТА"), ("#buy", "НАЖАТО-BUY"), ("#caps", "НАЖАТО-CAPS"),
    ("#glyph", "НАЖАТО-GLYPH"), ("#spaced", "НАЖАТО-SPACED"), ("#aria", "НАЖАТО-ARIA"),
    ("#btn-pay", "НАЖАТО-ICON"), ("#del", "НАЖАТО-DEL"), ("#delspan", "НАЖАТО-DEL"),
    ("#nested", "НАЖАТО-NESTED"), ("#neutral", "НАЖАТО-FORM"),
])
async def test_consequential_click_is_refused_until_the_owner_decides(mgr, site, selector, mark):
    sid = await _open_shop(mgr, site)
    with pytest.raises(BrowserConsequenceApprovalRequired) as caught:
        await mgr.click(sid, selector, actor="agent", approved=True, consequence_approved=False)
    assert caught.value.consequence["why"]
    assert mark not in (await mgr.snapshot(sid, actor="agent", approved=True))["text"], \
        "кнопка нажата без решения владельца"
    # по умолчанию (approved=False: HTTP, Jev) тоже отказ
    with pytest.raises(BrowserConsequenceApprovalRequired):
        await mgr.click(sid, selector, actor="agent")
    # решение владельца есть — клик проходит
    await mgr.click(sid, selector, actor="agent", approved=True, consequence_approved=True)
    assert mark in (await mgr.snapshot(sid, actor="agent", approved=True))["text"]


@pytestmark_browser
async def test_ordinary_clicks_stay_auto_negative_control(mgr, site):
    """Пара к тесту выше: ссылка, обычная кнопка и поиск по GET проходят без вопроса."""
    sid = await _open_shop(mgr, site)
    await mgr.click(sid, "#more", actor="agent", approved=True, consequence_approved=False)
    assert "НАЖАТО-ЕЩЁ" in (await mgr.snapshot(sid, actor="agent", approved=True))["text"]
    await mgr.click(sid, "#search", actor="agent", approved=True, consequence_approved=False)
    assert "НАЖАТО-ПОИСК" in (await mgr.snapshot(sid, actor="agent", approved=True))["text"]
    await mgr.click(sid, "#about", actor="agent", approved=True, consequence_approved=False)
    await mgr._sessions[sid].page.wait_for_url("**/about.html")


@pytestmark_browser
async def test_owner_at_the_wheel_is_not_asked(mgr, site):
    sid = await _open_shop(mgr, site)
    await mgr.click(sid, "#pay", actor="human")
    assert "НАЖАТА-ОПЛАТА" in (await mgr.snapshot(sid, actor="human"))["text"]


@pytestmark_browser
async def test_submit_policy_deny_still_wins_over_the_owner_approval(mgr, site):
    """Политика `submit: deny` у сессии — не вопрос, а запрет; плюс обычный клик жив."""
    await mgr.start(1, BrowserPolicy.from_dict({"rules": {"submit": "deny"}}), headless=True)
    await mgr.navigate(1, f"{site}/shop.html", actor="agent", approved=True)
    with pytest.raises(BrowserPolicyDenied):
        await mgr.click(1, "#pay", actor="agent", approved=True, consequence_approved=True)
    await mgr.click(1, "#more", actor="agent", approved=True, consequence_approved=True)


@pytestmark_browser
async def test_snapshot_marks_consequential_refs_and_the_hook_asks_for_them(mgr, site):
    sid = await _open_shop(mgr, site)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    by_id = {el["name"] or el["text"]: el for el in snap["interactive"]}
    pay = next(el for el in snap["interactive"] if el["text"] == "Оплатить")
    more = next(el for el in snap["interactive"] if el["text"] == "Показать ещё")
    about = next(el for el in snap["interactive"] if el["text"] == "О нас")
    assert pay["consequence"] == "payment"
    assert "consequence" not in more and "consequence" not in about, by_id
    assert "desc" not in pay, "сырое описание цели не должно доходить до модели"
    assert live_ref_info(pay["ref"])["consequence"]["category"] == "payment"

    granted = {"permissions": {"browser.read": True, "browser.control": True}}
    click = next(s for s in tools_browser.SPECS if s.name == "browser.click")
    effect, why = decide_effect(click, {"ref": pay["ref"]}, granted)
    assert effect == "ask" and "Оплатить" in why
    assert decide_effect(click, {"ref": more["ref"]}, granted)[0] == "auto"
    assert decide_effect(click, {"ref": about["ref"]}, granted)[0] == "auto"
    # селектор: слабое раннее предупреждение, обычный остаётся AUTO
    assert decide_effect(click, {"selector": "text=Оплатить"}, granted)[0] == "ask"
    assert decide_effect(click, {"selector": "#about"}, granted)[0] == "auto"
    # прежний контракт: хук не ослабляет и не ломает вызов без цели
    assert decide_effect(click, {}, granted)[0] == "auto"


@pytestmark_browser
async def test_policy_rules_cannot_lower_the_click_floor(mgr, site):
    sid = await _open_shop(mgr, site)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    pay = next(el for el in snap["interactive"] if el["text"] == "Оплатить")
    click = next(s for s in tools_browser.SPECS if s.name == "browser.click")
    granted = {"permissions": {"browser.control": True}}
    auto_all = [{"tool": "*", "resource": "*", "effect": "auto"}]
    assert decide_effect(click, {"ref": pay["ref"]}, granted, auto_all)[0] == "ask"


@pytestmark_browser
async def test_stale_snapshot_does_not_open_a_hole(mgr, site):
    """Новый снимок заменяет разметку: ref прошлого поколения уже ничего не значит."""
    sid = await _open_shop(mgr, site)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    pay = next(el for el in snap["interactive"] if el["text"] == "Оплатить")
    await mgr.snapshot(sid, actor="agent", approved=True)
    assert live_ref_info(pay["ref"]) is None


# ==================================================================================
# 3. Пароль: агент не вводит
# ==================================================================================

VAULT_VALUE = "Vault-Only-7d1c-OPS"   # ci-secret-scan: allow (тестовая канарейка)


@pytestmark_browser
async def test_agent_cannot_type_into_a_password_field(mgr, site):
    sid = await _open_shop(mgr, site)
    with pytest.raises(BrowserPolicyDenied):
        await mgr.type_text(sid, "#pw", "из-головы", actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="human")
    field = next(el for el in snap["interactive"] if el["name"] == "pw")
    assert field["filled"] is False, "значение попало в поле несмотря на отказ"
    # Негативный контроль: обычное поле работает как раньше.
    await mgr.type_text(sid, "#user", "тимур", actor="agent", approved=True)
    user = next(el for el in (await mgr.snapshot(sid, actor="human"))["interactive"]
                if el["name"] == "user")
    assert user["text"] == "тимур"


@pytestmark_browser
async def test_vault_fill_secret_and_the_owner_can_still_fill_the_password(mgr, site):
    sid = await _open_shop(mgr, site)
    await mgr.fill_secret(sid, "#pw", secret=VAULT_VALUE, actor="agent", approved=True)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    assert VAULT_VALUE not in json.dumps(snap, ensure_ascii=False)
    assert next(el for el in snap["interactive"] if el["name"] == "pw")["filled"] is True
    await mgr.type_text(sid, "#pw", "владелец-сам", actor="human")      # не агент — можно


@pytestmark_browser
async def test_hook_denies_typing_into_secret_refs_and_password_selectors(mgr, site):
    sid = await _open_shop(mgr, site)
    snap = await mgr.snapshot(sid, actor="agent", approved=True)
    pw = next(el for el in snap["interactive"] if el["name"] == "pw")
    user = next(el for el in snap["interactive"] if el["name"] == "user")
    type_tool = next(s for s in tools_browser.SPECS if s.name == "browser.type")
    granted = {"permissions": {"browser.control": True}}
    assert decide_effect(type_tool, {"ref": pw["ref"], "text": "x"}, granted)[0] == "deny"
    assert decide_effect(type_tool, {"selector": "input[type=password]", "text": "x"},
                         granted)[0] == "deny"
    assert decide_effect(type_tool, {"ref": user["ref"], "text": "x"}, granted)[0] == "auto"
    assert decide_effect(type_tool, {"selector": "#user", "text": "x"}, granted)[0] == "auto"


# ==================================================================================
# 4. P0 через движок и HTTP: вопрос владельцу, а не молчаливый клик
# ==================================================================================

async def _grant_browser(env, stack):
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"browser.read": True, "browser.control": True}})


@pytestmark_browser
async def test_model_click_on_pay_waits_for_the_owner_then_runs(env, site):
    """Было: клик по «Оплатить» исполнялся сразу и задача шла дальше."""
    url = f"{site}/e2e.html"
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_click", {"ref": "e1-1"}),
        ("tool", "browser_read_dom", {}),
        ("text", "оплата нажата"),
    ])
    stack = await _stack_with_tools(
        env, ["browser.open", "browser.read_dom", "browser.click", "browser.submit"],
        adapter=adapter, max_steps=8)
    await _grant_browser(env, stack)

    assert await _run_task(env, stack["task"]["id"], timeout=90) == "waiting_approval"
    pending = (await env.client.get("/api/approvals?status=pending")).json()
    assert len(pending) == 1 and "browser.click" in pending[0]["preview"]
    assert "Оплатить" in pending[0]["preview"], "владелец должен видеть, на что нажимают"
    # ничего не нажато, пока владелец молчит
    assert env.svc.browser._sessions, "сессия должна ждать решения"
    page = next(iter(env.svc.browser._sessions.values())).page
    assert "НАЖАТА-ОПЛАТА" not in await page.inner_text("body")

    await env.client.post(f"/api/approvals/{pending[0]['id']}", json={"approve": True, "by": "owner"})
    assert await _run_task(env, stack["task"]["id"], timeout=90, until=FINISHED) == "completed"
    assert "НАЖАТА-ОПЛАТА" in adapter.seen_messages[3][-1]["content"]

    # Сессия закрылась вместе с задачей (а не висела до остановки процесса).
    async def closed():
        async with env.svc.db.session() as s:
            row = (await s.execute(sa.select(bs_t.c.status))).first()
        return row and row[0] == "stopped"
    await wait_for(closed, timeout=15)
    assert not env.svc.browser._sessions


@pytestmark_browser
async def test_model_click_on_a_plain_link_is_not_asked_negative_control(env, site):
    url = f"{site}/e2e.html"
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_click", {"ref": "e1-0"}),
        ("text", "открыл страницу «О нас»"),
    ])
    stack = await _stack_with_tools(env, ["browser.open", "browser.click"],
                                    adapter=adapter, max_steps=6)
    await _grant_browser(env, stack)
    assert await _run_task(env, stack["task"]["id"], timeout=90, until=FINISHED) == "completed"
    assert (await env.client.get("/api/approvals")).json() == []
    assert "О нас" in adapter.seen_messages[2][-1]["content"]


@pytestmark_browser
async def test_selector_click_the_hook_cannot_see_is_stopped_at_the_boundary(env, site):
    """Хук по селектору `#pay` промолчит (слов нет), но граница смотрит на живой элемент."""
    url = f"{site}/e2e.html"
    adapter = ToolAdapter([
        ("tool", "browser_open", {"url": url}),
        ("tool", "browser_click", {"selector": "body > button"}),
        ("text", "попробовал"),
    ])
    stack = await _stack_with_tools(env, ["browser.open", "browser.read_dom", "browser.click"],
                                    adapter=adapter, max_steps=6)
    await _grant_browser(env, stack)
    await _run_task(env, stack["task"]["id"], timeout=90, until=FINISHED)
    reply = adapter.seen_messages[2][-1]["content"]
    assert "подтверждени" in reply and "browser.submit" in reply
    page = next(iter(env.svc.browser._sessions.values()), None)
    # сессия может быть уже закрыта вместе с задачей; если жива — кнопка не нажата
    if page is not None:
        assert "НАЖАТА-ОПЛАТА" not in await page.page.inner_text("body")


@pytestmark_browser
async def test_http_act_click_asks_then_runs_with_the_approval(env, site):
    created = await env.client.post("/api/browser/sessions", json={})
    if created.status_code == 503:
        pytest.skip("Playwright недоступен в этом окружении")
    sid = created.json()["session_id"]
    try:
        nav = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                    json={"action": "navigate", "url": f"{site}/e2e.html"})
        assert nav.status_code == 200, nav.text
        first = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                      json={"action": "click", "selector": "#pay"})
        assert first.status_code == 202, first.text
        body = first.json()
        inner = body.get("error") or body.get("detail") or body
        assert "Оплатить" in json.dumps(inner, ensure_ascii=False)
        aid = inner["approval_id"]
        await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner"})
        again = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                      json={"action": "click", "selector": "#pay",
                                            "approval_id": aid})
        assert again.status_code == 200, again.text
        # негативный контроль: ссылка — без вопроса
        link = await env.client.post(f"/api/browser/sessions/{sid}/act",
                                     json={"action": "click", "selector": "#about"})
        assert link.status_code == 200, link.text
    finally:
        await env.client.post(f"/api/browser/sessions/{sid}/stop")


# ==================================================================================
# 5. Браузер: жизненный цикл сессий
# ==================================================================================

class _FakeBrowser:
    """Двойник менеджера: помнит, какие сессии «живы», и что ему велели закрыть."""

    def __init__(self, live=()):
        self.live = set(live)
        self.started: list[int] = []
        self.stopped: list[int] = []
        self._png = b"\x89PNG\r\n\x1a\n" + b"ops"

    def is_live(self, sid):
        return sid in self.live

    async def start(self, sid, policy, *, headless=True):
        self.live.add(sid)
        self.started.append(sid)

    async def stop(self, sid):
        self.live.discard(sid)
        self.stopped.append(sid)

    async def screenshot(self, sid, **kw):
        return self._png


async def _new_row(env, *, task_id=None, status="running") -> int:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.insert(bs_t).values(task_id=task_id, status=status,
                                                     created_at=utcnow(), updated_at=utcnow()))
        await s.commit()
    return int(res.inserted_primary_key[0])


async def _row_status(env, sid) -> str:
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(bs_t.c.status).where(bs_t.c.id == sid))).scalar_one()


async def test_session_for_replaces_a_dead_running_row_instead_of_reusing_it(env, monkeypatch):
    stack = await make_stack(env.client)
    task = {"id": stack["task"]["id"]}
    dead = await _new_row(env, task_id=task["id"])
    fake = _FakeBrowser()
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)
    ctx = ToolContext(svc=env.svc, task=task, run_id=1, agent={"id": stack["agent"]["id"]})

    sid = await tools_browser._session_for(ctx, {})

    assert sid != dead and sid in fake.live, "вместо мёртвой строки должна открыться новая сессия"
    assert await _row_status(env, dead) == "lost"
    assert await _row_status(env, sid) == "running"
    # негативный контроль: живая сессия переиспользуется, а не плодится
    assert await tools_browser._session_for(ctx, {}) == sid
    assert fake.started == [sid]


async def test_failed_browser_start_does_not_leave_a_created_row(env, monkeypatch):
    stack = await make_stack(env.client)
    task = {"id": stack["task"]["id"]}

    class _Boom(_FakeBrowser):
        async def start(self, sid, policy, *, headless=True):
            raise RuntimeError("нет Chromium")

    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: _Boom())
    ctx = ToolContext(svc=env.svc, task=task, run_id=1, agent={"id": stack["agent"]["id"]})
    with pytest.raises(RuntimeError):
        await tools_browser._session_for(ctx, {})
    async with env.svc.db.session() as s:
        statuses = [r[0] for r in (await s.execute(sa.select(bs_t.c.status))).fetchall()]
    assert statuses == ["failed"]


async def test_restart_reconciles_running_rows_to_lost(tmp_path):
    """Настоящий повторный запуск сервисов на той же БД: setup() фич закрывает хвосты."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with svc.db.session() as s:
        for status in ("running", "created", "stopped", "failed"):
            await s.execute(sa.insert(bs_t).values(status=status))
        for sid, status in (("aaaaaaaaaaaa", "running"), ("bbbbbbbbbbbb", "finished"),
                            ("cccccccccccc", "killed")):
            await s.execute(sa.insert(term_t).values(
                id=sid, mode="project_host", cwd="x", command="sleep 100", status=status, pid=1))
        await s.commit()
    await svc.stop()

    app2, svc2 = await start_app(settings, start_workers=False)
    try:
        async with svc2.db.session() as s:
            browsers = [r[0] for r in (await s.execute(
                sa.select(bs_t.c.status).order_by(bs_t.c.id))).fetchall()]
            terminals = dict((await s.execute(sa.select(term_t.c.id, term_t.c.status))).fetchall())
        assert browsers == ["lost", "lost", "stopped", "failed"]
        assert terminals == {"aaaaaaaaaaaa": "lost", "bbbbbbbbbbbb": "finished",
                             "cccccccccccc": "killed"}
    finally:
        await svc2.stop()


async def test_takeover_and_resume_on_a_dead_session_answer_409_not_500(env):
    sid = await _new_row(env)                     # строка есть, рантайм-сессии в процессе нет
    for action in ("takeover", "resume"):
        resp = await env.client.post(f"/api/browser/sessions/{sid}/{action}")
        assert resp.status_code == 409, (action, resp.status_code, resp.text)
        assert "сессия браузера не запущена" in resp.text
    assert await _row_status(env, sid) == "lost", "строка продолжала врать, что сессия идёт"


async def test_screenshot_endpoint_returns_bytes_and_writes_no_files(env, monkeypatch):
    fake = _FakeBrowser(live={7})
    monkeypatch.setattr(browser_feature, "_mgr", lambda svc: fake)
    folder = env.settings.data_dir / "browser"
    for _ in range(12):
        resp = await env.client.get("/api/browser/sessions/7/screenshot")
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
        assert resp.content == fake._png
    leftovers = list(folder.glob("shot-*.png")) if folder.is_dir() else []
    assert leftovers == [], f"опрос кадра оставил {len(leftovers)} файлов на диске"


async def test_agent_screenshot_tool_keeps_a_bounded_ring_of_files(env, monkeypatch):
    fake = _FakeBrowser(live={7})
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)

    async def fixed(ctx, args):
        return 7
    monkeypatch.setattr(tools_browser, "_session_for", fixed)
    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=1, agent={})
    paths = set()
    for _ in range(tools_browser.SHOT_RING * 3):
        res = await tools_browser._screenshot({}, ctx)
        assert not res.error, res.content
        paths.add(res.data["path"])
    files = list(tools_browser.shots_dir(env.svc).glob("shot-*.png"))
    assert len(files) <= tools_browser.SHOT_RING, f"{len(files)} файлов вместо кольца"
    assert len(paths) <= tools_browser.SHOT_RING
    # закрытие сессии убирает кадры
    assert tools_browser.drop_session_shots(env.svc, 7) == len(files)
    assert not list(tools_browser.shots_dir(env.svc).glob("shot-*.png"))


async def test_browser_sessions_close_when_their_task_ends(env, monkeypatch):
    stack = await make_stack(env.client)
    agent_id = stack["agent"]["id"]
    tid1 = stack["task"]["id"]
    tid2 = (await env.client.post("/api/tasks", json={
        "title": "второй", "prompt": "x", "agent_id": agent_id, "run_now": False})).json()["task"]["id"]
    s1, s2, s_done = (await _new_row(env, task_id=tid1), await _new_row(env, task_id=tid2),
                      await _new_row(env, task_id=tid1, status="stopped"))
    fake = _FakeBrowser(live={s1, s2})
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)

    await env.svc.bus.emit("task.completed", task_id=tid1)

    async def closed():
        return fake.stopped == [s1] or None
    await wait_for(closed, timeout=10)
    # the handler stops the browser first and records "stopped" right after: wait for the row too, not only the
    # browser call (CI py3.11 on 94327840 read "running" in between)
    async def row_stopped():
        return await _row_status(env, s1) == "stopped" or None
    await wait_for(row_stopped, timeout=10)
    assert await _row_status(env, s1) == "stopped"
    assert await _row_status(env, s2) == "running", "чужая задача не должна терять браузер"
    assert await _row_status(env, s_done) == "stopped"
    # failed и stopped закрывают так же
    for kind in ("task.failed", "task.stopped"):
        fake.live.add(s2)
        await env.svc.bus.emit(kind, task_id=tid2)
        await wait_for(lambda: _stopped_twice(fake, s2), timeout=10)
        async with env.svc.db.session() as s:
            await s.execute(sa.update(bs_t).where(bs_t.c.id == s2).values(status="running"))
            await s.commit()
        fake.stopped.clear()


async def _stopped_twice(fake, sid):
    return sid in fake.stopped or None


async def test_sweep_closes_live_sessions_of_tasks_that_already_ended(env, monkeypatch):
    """Страховка, если событие потеряно: по состоянию задачи в БД."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    sid = await _new_row(env, task_id=tid)
    other = await _new_row(env, task_id=None)
    fake = _FakeBrowser(live={sid, other})
    monkeypatch.setattr(tools_browser, "_mgr", lambda svc: fake)
    assert await tools_browser.sweep_ended_tasks(env.svc) == []        # задача ещё идёт
    async with env.svc.db.session() as s:
        await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(status="completed"))
        await s.commit()
    assert await tools_browser.sweep_ended_tasks(env.svc) == [sid]
    assert await _row_status(env, sid) == "stopped" and fake.live == {other}


# ==================================================================================
# 6. Терминал
# ==================================================================================

class _FakeProc:
    _pids = itertools.count(41000)

    def __init__(self, argv):
        self.argv = [str(a) for a in argv]
        self.pid = next(self._pids)
        self.returncode = None
        self.stdin = None
        self.stdout = asyncio.StreamReader()
        self._done = asyncio.Event()

    def finish(self, code):
        if self.returncode is None:
            self.returncode = code
            self.stdout.feed_eof()
            self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    async def communicate(self):
        await self.wait()
        return b"", None

    def kill(self):
        self.finish(137)


class _FakeSpawner:
    """Заменяет create_subprocess_exec: ничего не запускает и записывает argv."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.procs: list[_FakeProc] = []

    async def __call__(self, *argv, **kw):
        proc = _FakeProc(argv)
        self.calls.append(proc.argv)
        self.procs.append(proc)
        name = proc.argv[0]
        if name == "docker" and proc.argv[1] == "kill":
            # `docker kill <имя>`: контейнер умирает, а с ним и клиент `docker run`
            for other in self.procs:
                if "--name" in other.argv and proc.argv[2] == other.argv[other.argv.index("--name") + 1]:
                    other.finish(137)
            proc.finish(0)
        elif name == "taskkill":
            for other in self.procs:
                if str(other.pid) == proc.argv[2]:
                    other.finish(1)
            proc.finish(0)
        return proc

    def killpg(self, pid, sig):
        for proc in self.procs:
            if proc.pid == pid:
                proc.finish(137)


async def test_sandbox_container_is_named_and_docker_kill_stops_it(tmp_path, monkeypatch):
    spawner = _FakeSpawner()
    monkeypatch.setattr(terminal_control.asyncio, "create_subprocess_exec", spawner)
    monkeypatch.setattr(terminal_control.os, "killpg", spawner.killpg, raising=False)
    mgr = TerminalManager()
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="sandbox")

    session = await mgr.start("sleep 100", tmp_path, policy, approved=True)
    run = spawner.calls[0]
    assert run[:2] == ["docker", "run"]
    assert "--name" in run, "контейнер без имени нечем убить"
    assert run[run.index("--name") + 1] == f"bcc-{session.id}"
    assert run.index("--name") < run.index("--network")

    await mgr.kill(session.id)
    assert ["docker", "kill", f"bcc-{session.id}"] in spawner.calls, spawner.calls
    assert session.finished and session.proc.returncode is not None


async def test_host_sessions_do_not_touch_docker_on_kill(tmp_path, monkeypatch):
    """Негативный контроль: host-сессия не зовёт `docker kill` и не получает имени."""
    spawner = _FakeSpawner()
    monkeypatch.setattr(terminal_control.asyncio, "create_subprocess_exec", spawner)
    monkeypatch.setattr(terminal_control.asyncio, "create_subprocess_shell", spawner)
    monkeypatch.setattr(terminal_control.os, "killpg", spawner.killpg, raising=False)
    mgr = TerminalManager()
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")
    session = await mgr.start("sleep 100", tmp_path, policy, approved=True)
    assert session.container is None
    await mgr.kill(session.id)
    assert not any(call[:2] == ["docker", "kill"] for call in spawner.calls)
    assert session.finished


@pytest.mark.skipif(sys.platform != "win32" or shutil.which("sh") is None,
                    reason="гонка fork'ов Git-sh с taskkill существует только на Windows с Git-sh")
async def test_kill_right_after_start_leaves_no_orphan_shell(tmp_path):
    """Корень осиротевших `sh.exe` (и WinError 32 на папке проекта) после STOP.

    `taskkill /T` обходит дерево по снимку; login-профиль Git-sh на старте делает
    десятки fork'ов, и ребёнок, родившийся после снимка, жил дальше с открытой
    рабочей папкой. Измерение 2026-09-30 на прежнем коде: 8 из 60 остановок с
    случайной задержкой до 0.35 с оставляли процесс. Job Object убивает дерево целиком."""
    import psutil

    tag = f"BCCORPHAN{os.getpid()}"
    mgr = TerminalManager()
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")
    rnd = random.Random(7)

    async def start_and_stop(delay: float) -> None:
        session = await mgr.start(f'python -c "import time; time.sleep(40)" # {tag}',
                                  tmp_path, policy, approved=True)
        await asyncio.sleep(delay)
        await mgr.kill(session.id)

    def leftovers():
        found = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if tag in " ".join(proc.info["cmdline"] or []):
                    found.append(proc)
            except (psutil.Error, OSError):
                pass
        return found

    try:
        for _round in range(6):
            await asyncio.gather(*(start_and_stop(rnd.uniform(0.0, 0.3)) for _ in range(8)))
        deadline = time.monotonic() + 8
        left = leftovers()
        while left and time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            left = leftovers()
        assert not left, f"после STOP остались процессы: {[(p.pid, p.name()) for p in left]}"
    finally:
        for proc in leftovers():
            with contextlib.suppress(psutil.Error):
                proc.kill()


@pytest.mark.skipif(sys.platform != "win32",
                    reason="CREATE_SUSPENDED и Job Object существуют только на Windows")
@pytest.mark.parametrize("psutil_available", [True, False])
async def test_windows_session_is_born_suspended_bound_to_a_job_and_then_runs(
        tmp_path, monkeypatch, psutil_available):
    """Процесс рождается приостановленным, попадает в Job Object и возобновляется; при
    недоступном psutil возобновление идёт через ntdll. Команда при этом обязана выполниться."""
    if not psutil_available:
        monkeypatch.setitem(sys.modules, "psutil", None)          # import psutil → ImportError
    mgr = TerminalManager()
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")

    live = await mgr.start(_sleeper(tmp_path), tmp_path, policy, approved=True)
    assert live.job, "процесс не попал в Job Object"
    await mgr.kill(live.id)
    assert live.finished and live.job is None, "ручка задания не освобождена после остановки"

    done = await mgr.start("echo ops-resume-marker", tmp_path, policy, approved=True)
    deadline = time.monotonic() + 30
    while not done.finished and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert done.finished and done.exit_code == 0, (done.exit_code, done.output)
    assert any("ops-resume-marker" in line for line in done.output)
    assert done.job is None, "ручка задания осталась после нормального завершения"


def _sleeper(tmp_path: Path) -> str:
    (tmp_path / "sleeper.py").write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    return "python sleeper.py"


async def _host_session(env, tmp_path: Path, *, owner: str | None):
    policy = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")
    session = await terminal_feature._mgr(env.svc).start(
        _sleeper(tmp_path), tmp_path, policy, approved=True, owner=owner)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(term_t).values(
            id=session.id, mode="project_host", cwd=str(tmp_path), command="sleep", status="running",
            pid=session.proc.pid, started_at=utcnow()))
        await s.commit()
    return session


async def test_stopping_a_task_kills_only_that_tasks_terminal_commands(env, tmp_path):
    mine = await _host_session(env, tmp_path, owner="77")
    other_task = await _host_session(env, tmp_path, owner="78")
    by_owner_less = await _host_session(env, tmp_path, owner=None)        # запущена владельцем из UI
    try:
        await env.svc.bus.emit("task.stopped", task_id=77)
        await wait_for(lambda: _finished(mine), timeout=20)
        assert mine.finished
        assert not other_task.finished and not by_owner_less.finished, \
            "остановка одной задачи не должна трогать чужие команды"
        async def statuses():
            async with env.svc.db.session() as s:
                rows = dict((await s.execute(sa.select(term_t.c.id, term_t.c.status))).fetchall())
            return rows if rows[mine.id] == "killed" else None       # строку закрывают после kill
        rows = await wait_for(statuses, timeout=10)
        assert rows[other_task.id] == "running" and rows[by_owner_less.id] == "running"
    finally:
        await terminal_feature._mgr(env.svc).kill_all()


async def _finished(session):
    return session.finished or None


async def test_sweep_kills_commands_of_tasks_stopped_in_the_database(env, tmp_path):
    """Если событие task.stopped потеряно, тик добивает команды по состоянию задачи."""
    stack = await make_stack(env.client)
    tid = stack["task"]["id"]
    session = await _host_session(env, tmp_path, owner=str(tid))
    try:
        assert await terminal_feature.sweep_stopped_tasks(env.svc) == []   # задача идёт
        async with env.svc.db.session() as s:
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == tid).values(status="stopped"))
            await s.commit()
        assert await terminal_feature.sweep_stopped_tasks(env.svc) == [session.id]
        assert session.finished
    finally:
        await terminal_feature._mgr(env.svc).kill_all()


async def test_backend_shutdown_kills_live_terminal_sessions(tmp_path):
    """Процесс владельца не должен пережить backend: хук завершения фичи."""
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    work = tmp_path / "work"
    work.mkdir()
    policy = TerminalPolicy(allowed_roots=[work], mode="project_host")
    session = await terminal_feature._mgr(svc).start(_sleeper(work), work, policy, approved=True)
    async with svc.db.session() as s:
        await s.execute(sa.insert(term_t).values(
            id=session.id, mode="project_host", cwd=str(work), command="sleep", status="running",
            pid=session.proc.pid, started_at=utcnow()))
        await s.commit()
    assert not session.finished
    await svc.stop()
    assert session.finished and session.proc.returncode is not None, \
        "процесс пережил остановку сервисов"
    # строка в БД закрыта тем же хуком (проверяем новым запуском на той же базе)
    app2, svc2 = await start_app(settings, start_workers=False)
    try:
        async with svc2.db.session() as s:
            status = (await s.execute(sa.select(term_t.c.status).where(
                term_t.c.id == session.id))).scalar_one()
        assert status == "killed"
    finally:
        await svc2.stop()


async def test_reconcile_does_not_touch_a_live_session(env, tmp_path):
    """Негативный контроль к рестарту: живая сессия процесса остаётся `running`."""
    live = await _host_session(env, tmp_path, owner=None)
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(term_t).values(
            id="dddddddddddd", mode="project_host", cwd="x", command="old", status="running", pid=1))
        await s.commit()
    try:
        assert await terminal_feature.reconcile_sessions(env.svc) == 1
        async with env.svc.db.session() as s:
            rows = dict((await s.execute(sa.select(term_t.c.id, term_t.c.status))).fetchall())
        assert rows[live.id] == "running" and rows["dddddddddddd"] == "lost"
    finally:
        await terminal_feature._mgr(env.svc).kill_all()


async def test_finished_output_is_readable_after_it_leaves_memory(env):
    """Раньше: вытеснена из памяти (или рестарт) — вывод потерян навсегда."""
    root = str(env.settings.data_dir)
    body = {"command": "echo ops-tail-marker", "mode": "project_host", "cwd": root}
    first = await env.client.post("/api/terminal/run", json=body)
    assert first.status_code == 202, first.text
    aid = int((first.json().get("error") or first.json().get("detail") or first.json())["approval_id"])
    await env.client.post(f"/api/approvals/{aid}", json={"approve": True, "by": "тест"})
    run = await env.client.post("/api/terminal/run", json={**body, "approval_id": aid})
    assert run.status_code == 200, run.text
    sid = run.json()["session_id"]
    mgr = env.svc.terminal

    async def done():
        return mgr.sessions[sid].finished or None
    await wait_for(done, timeout=30)

    async def row_done():
        async with env.svc.db.session() as s:
            st = (await s.execute(sa.select(term_t.c.status).where(term_t.c.id == sid))).scalar_one()
        return st == "finished" or None
    await wait_for(row_done, timeout=10)           # итог в БД без единого GET статуса

    mgr.sessions.pop(sid)                          # как после вытеснения RETAIN_FINISHED
    gone = await env.client.get(f"/api/terminal/sessions/{sid}")
    assert gone.status_code == 404                 # «нет в памяти» — прежний контракт
    stored = await env.client.get(f"/api/terminal/sessions/{sid}/log")
    assert stored.status_code == 200, stored.text
    data = stored.json()
    assert data["finished"] is True and data["exit_code"] == 0 and data["from_log"] is True
    assert any("ops-tail-marker" in line for line in data["output_tail"])
    # негативный контроль: чужой/несуществующий id — честный 404, и путь не выходит из каталога
    assert (await env.client.get("/api/terminal/sessions/000000000000/log")).status_code == 404
    assert (await env.client.get("/api/terminal/sessions/..%2F..%2Fbcc/log")).status_code in (404, 422)


def test_log_is_capped_and_keeps_the_tail(tmp_path):
    mgr = TerminalManager(log_dir=tmp_path / "logs")

    class _P:
        pid = 1
        returncode = 0

    lines = [f"строка-{i:06d} " + "x" * 200 for i in range(3000)]
    session = TerminalSession("0123456789ab", tmp_path, "c", "project_host", _P(), output=lines)
    mgr._persist_log(session)
    path = mgr.log_path("0123456789ab")
    assert path is not None and path.is_file()
    assert path.stat().st_size <= terminal_control.LOG_CAP_BYTES + 100
    tail = mgr.read_log("0123456789ab")
    assert tail and tail[-1] == lines[-1], "хвост вывода потерян"
    assert mgr.log_path("../../etc/passwd") is None and mgr.log_path("short") is None


def test_log_files_are_pruned(tmp_path):
    mgr = TerminalManager(log_dir=tmp_path / "logs")
    folder = tmp_path / "logs"
    folder.mkdir()
    for i in range(12):
        (folder / f"{i:012x}.log").write_text("x", encoding="utf-8")
        os.utime(folder / f"{i:012x}.log", (1000 + i, 1000 + i))
    mgr._prune_logs(folder, keep=5)
    left = sorted(p.name for p in folder.glob("*.log"))
    assert left == [f"{i:012x}.log" for i in range(7, 12)], "должны остаться самые свежие"


@pytest.mark.parametrize("which,returncode,out,available,default", [
    (None, 0, b"", False, "project_host"),                     # docker не установлен
    ("docker", 1, b"", False, "project_host"),                 # CLI есть, демон молчит
    ("docker", 0, b"27.3.1\n", True, "sandbox"),               # всё работает
])
async def test_capabilities_choose_the_default_mode_from_a_real_docker_probe(
        env, monkeypatch, which, returncode, out, available, default):
    class _Probe:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self):
            return out, b""

    async def fake_exec(*argv, **kw):
        return _Probe()

    monkeypatch.setattr(terminal_feature.shutil, "which", lambda name: which)
    monkeypatch.setattr(terminal_feature.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(terminal_feature, "_docker_cache", None)
    resp = await env.client.get("/api/terminal/capabilities")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["docker"]["available"] is available
    assert data["default_mode"] == default
    terminal_feature._docker_cache = None


# ==================================================================================
# 7. UI: честные кнопки
# ==================================================================================

UI = Path(__file__).resolve().parent.parent / "ui" / "pages"


def test_mobile_page_offers_no_takeover_for_dead_browser_sessions():
    src = (UI / "mobile.js").read_text(encoding="utf-8")
    body = src[src.index("function browserBody"):src.index("function opencodeBody")]
    assert "s.live !== false" in body, "мобильная страница не смотрит на live"
    assert "!alive" in body and "'takeover'" in body
    assert "console.log" not in src


def test_terminal_page_picks_its_default_mode_from_the_docker_probe():
    src = (UI / "terminal.js").read_text(encoding="utf-8")
    assert "/api/terminal/capabilities" in src and "modeChosen" in src
    assert "/log`" in src                                # вывод ушедшей из памяти сессии
    assert "console.log" not in src
