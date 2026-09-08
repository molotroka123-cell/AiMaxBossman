"""Искать ответ на поломку — да. Искать, как обойти контроль доступа — нет.

Владелец попросил, чтобы при ошибках с браузером модель сразу смотрела в веб.
Разница между «интерфейс перерисовали, как теперь найти кнопку» и «как пройти
капчу» — это вся суть модуля: первое чинят, второе не обходят.
"""
from __future__ import annotations

import pytest

from bcc.features.browser_help import (FORBIDDEN_INTENT, LOOKUP_WORTHY,
                                       OWNER_ONLY, BrowserTrouble, build_query,
                                       plan_lookup)
from bcc.features.web_research import tools as web_tools


def trouble(kind: str, **kwargs) -> BrowserTrouble:
    kwargs.setdefault("provider", "higgsfield")
    return BrowserTrouble(kind=kind, **kwargs)


# ------------------------------------------------------------ ищем

@pytest.mark.parametrize("kind", sorted(LOOKUP_WORTHY))
def test_our_own_breakages_are_worth_looking_up(kind):
    plan = plan_lookup(trouble(kind, target_name="Generate"))
    assert plan.search, plan.refusal
    assert plan.query
    assert plan.advisory_only


def test_the_query_says_what_broke_and_where():
    plan = plan_lookup(trouble("ui_changed", target_name="Generate"))
    assert "higgsfield" in plan.query
    assert "selector" in plan.query
    assert "Generate" in plan.query


def test_a_library_error_carries_the_library_name():
    plan = plan_lookup(trouble("runtime_error", library="playwright"))
    assert "playwright" in plan.query


# ------------------------------------------------------------ не ищем

@pytest.mark.parametrize("kind", sorted(OWNER_ONLY))
def test_access_controls_are_never_looked_up(kind):
    """Капча, вход, второй фактор, лимит, план — это владелец, а не поисковик."""
    plan = plan_lookup(trouble(kind))
    assert not plan.search
    assert plan.refusal
    assert plan.owner_action_required
    assert plan.query == "", "у отказа не бывает запроса"


def test_an_unknown_breakage_is_not_searched_at_random():
    plan = plan_lookup(trouble("something_new"))
    assert not plan.search and "неизвестен" in plan.refusal


def test_a_button_label_cannot_smuggle_a_forbidden_intent():
    """Имя цели приходит СО СТРАНИЦЫ провайдера и может содержать что угодно."""
    for label in ("Solve the captcha", "bypass verification", "anti-bot check"):
        plan = plan_lookup(trouble("ui_changed", target_name=label))
        assert not plan.search, label
        assert plan.owner_action_required


def test_every_forbidden_intent_pattern_actually_catches_something():
    """Список запретов, который ничего не ловит, — это украшение."""
    samples = {"обход контроля": "how to bypass the check",
               "капча": "recaptcha appears",
               "антидетект": "antidetect browser",
               "ограничение частоты": "rate limit reached",
               "второй фактор": "enter the otp",
               "учётные данные": "login credentials"}
    for pattern, what in FORBIDDEN_INTENT:
        assert any(pattern.search(text) for text in samples.values()), what


# --------------------------------------------------- ничего лишнего наружу

def test_the_page_itself_never_reaches_the_query():
    plan = plan_lookup(BrowserTrouble(
        kind="ui_changed", provider="higgsfield",
        target_name="<div id='x'>secret</div> https://acct.example/u/12345"))
    if plan.search:
        assert "<" not in plan.query and ">" not in plan.query
        assert "https://" not in plan.query
        assert "12345" not in plan.query


def test_a_long_label_is_cut_not_carried():
    plan = plan_lookup(trouble("ui_changed", target_name=" ".join(["word"] * 40)))
    if plan.search:
        assert plan.query.count("word") <= 6


def test_the_query_passes_the_same_gate_as_any_other_search():
    """Второго набора правил для «нашего» запроса не существует."""
    for kind in LOOKUP_WORTHY:
        plan = plan_lookup(trouble(kind, target_name="Generate"))
        if plan.search:
            assert web_tools.guard_query(plan.query) is None, plan.query


def test_a_query_the_gate_refuses_is_not_sent_trimmed():
    """Урезанный запрос и поиск ломает, и событие прячет."""
    plan = plan_lookup(BrowserTrouble(kind="ui_changed", provider="x" * 200))
    assert plan.search is False or web_tools.guard_query(plan.query) is None


# ------------------------------------------------------------ полномочия

def test_a_finding_is_data_and_says_so():
    plan = plan_lookup(trouble("ui_changed", target_name="Generate"))
    body = plan.to_dict()
    assert body["advisory_only"] is True
    assert "не разрешение" in body["note"]


def test_the_planner_performs_no_search_itself():
    """Модуль решает, ЧТО искать. Ищет существующая машинерия, со своим
    журналом, своими одобрениями и своими границами."""
    import inspect

    from bcc.features import browser_help
    source = inspect.getsource(browser_help)
    for forbidden in ("httpx.get", "httpx.post", "requests.get", "urlopen"):
        assert forbidden not in source, forbidden


async def test_the_route_shows_the_query_before_it_goes_anywhere(tmp_path):
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    app = create_app(Settings(data_dir=tmp_path / "d",
                              database_url=f"sqlite+aiosqlite:///{tmp_path/'d'/'b.db'}",
                              ui_dir=tmp_path / "no-ui"),
                     announce_token=False, start_workers=False)
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t",
                                     headers={HEADER: svc.auth.token}) as c:
            ok = await c.post("/api/browser-help/plan",
                              json={"kind": "ui_changed", "provider": "higgsfield",
                                    "target_name": "Generate"})
            assert ok.status_code == 200 and ok.json()["search"] is True

            refused = await c.post("/api/browser-help/plan",
                                   json={"kind": "human_challenge"})
            assert refused.status_code == 200
            assert refused.json()["search"] is False
            assert refused.json()["owner_action_required"] is True

            bad = await c.post("/api/browser-help/plan", json={})
            assert bad.status_code == 422

            policy = await c.get("/api/browser-help/policy")
            assert policy.status_code == 200
            assert "human_challenge" in policy.json()["owner_only"]
    finally:
        await svc.stop()


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"
