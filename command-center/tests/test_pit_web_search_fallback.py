"""Jeff web search: a dead SearXNG must fall back to keyless DuckDuckGo, and `doctor` must say which path is really alive.

Before: `web_results` raised when SearXNG was configured but down (no fallback), and the doctor accepted ANY 2xx from DuckDuckGo
(its bot check answers 202, which the real search rejects) so a path that cannot serve a query was reported alive.
Offline: httpx.MockTransport only; the live DuckDuckGo path cannot be proven from CI and is shown by `bcc.pit.cli doctor` on the owner's PC.
"""
from __future__ import annotations

import httpx
import pytest

from bcc.telegram_companion.adapters import Models, probe_search_paths
from bcc.telegram_companion.config import CompanionError, Person, Settings

DDG_OK = """<html><body>
<div class="result"><a class="result__a" href="https://example.org/a">Пример А</a>
<a class="result__snippet">Описание А</a></div>
<div class="result"><a class="result__a" href="https://example.org/b">Пример Б</a>
<a class="result__snippet">Описание Б</a></div></body></html>"""
SEARX_OK = {"results": [{"title": "Из SearXNG", "url": "https://searx.example/x", "content": "найдено"}]}


def _settings(search_url="http://127.0.0.1:8888"):
    return Settings((Person(1, 1, "owner", 10),), search_url=search_url)


def _transport(*, searx="ok", ddg="ok"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "127.0.0.1":
            if searx == "down":
                raise httpx.ConnectError("refused", request=request)
            if searx == "bad":
                return httpx.Response(200, json={"oops": 1})
            return httpx.Response(200, json=SEARX_OK)
        if request.url.host.endswith("duckduckgo.com"):
            if ddg == "bot_check":
                return httpx.Response(202, text="<html>captcha</html>")
            if ddg == "down":
                raise httpx.ConnectError("no route", request=request)
            return httpx.Response(200, text=DDG_OK)
        raise AssertionError(request.url)
    return httpx.MockTransport(handler), calls


async def test_a_live_searxng_is_used_and_ddg_is_not_touched(tmp_path):
    transport, calls = _transport()
    models = Models(_settings(), tmp_path, transport=transport)
    try:
        rows = await models.web_results("привет")
    finally:
        await models.close()
    assert rows and rows[0]["url"] == "https://searx.example/x"
    assert models.search_path == "searxng" and not any(h.endswith("duckduckgo.com") for h in calls)


@pytest.mark.parametrize("searx", ["down", "bad"])
async def test_a_dead_searxng_falls_back_to_keyless_ddg(tmp_path, searx):
    transport, _calls = _transport(searx=searx)
    models = Models(_settings(), tmp_path, transport=transport)
    try:
        rows = await models.web_results("привет")
    finally:
        await models.close()
    assert [r["url"] for r in rows] == ["https://example.org/a", "https://example.org/b"]
    assert models.search_path == "keyless_ddg" and models.search_note.startswith("searxng_down:")


async def test_when_both_paths_are_dead_the_error_is_honest_not_empty(tmp_path):
    transport, _calls = _transport(searx="down", ddg="down")
    models = Models(_settings(), tmp_path, transport=transport)
    try:
        with pytest.raises(CompanionError):
            await models.web_results("привет")
    finally:
        await models.close()


async def test_doctor_reports_the_alive_path_and_rejects_a_bot_check():
    searx_down, _ = _transport(searx="down")
    report = await probe_search_paths(_settings(), transport=searx_down)
    assert report["active"] == "KEYLESS_FALLBACK"
    assert report["searxng"]["state"] == "DOWN" and report["keyless_ddg"]["state"] == "OK"

    alive, _ = _transport()
    assert (await probe_search_paths(_settings(), transport=alive))["active"] == "SEARXNG"

    no_searx, _ = _transport()
    assert (await probe_search_paths(_settings(search_url=""), transport=no_searx))["active"] == "KEYLESS_FALLBACK"

    # bad cases: DDG answers its bot check (202) -> NOT alive (the old probe accepted any 2xx); both down -> NONE
    bot, _ = _transport(searx="down", ddg="bot_check")
    report = await probe_search_paths(_settings(), transport=bot)
    assert report["active"] == "NONE" and report["keyless_ddg"]["state"] == "DOWN"
    dead, _ = _transport(searx="down", ddg="down")
    assert (await probe_search_paths(_settings(), transport=dead))["active"] == "NONE"
