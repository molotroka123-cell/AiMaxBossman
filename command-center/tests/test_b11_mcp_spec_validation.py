"""B11 #5 — манифест MCP-сервера (`MCPServerSpec.validate`) проверяется целиком.

Дефект: validate() смотрел только transport и наличие command/url. Принимались
пустое имя («   »), имя с управляющими символами, url `file:///…`/`ftp://`,
url с логином:паролем в userinfo (секрет оседал в БД открытым текстом — для
секретов есть шаблон `{secret:NAME}`), timeout 0/-5/10 000 с и env-ключи вида
«PATH=evil». POST /api/mcp/servers сохранял такие строки, а рантайм затем
отдавал file:// в транспорт или ждал вечность. Ошибки — по-русски.
"""
from __future__ import annotations

import sys

import pytest

from bcc.v2.mcp_hub import MCPServerSpec
from bcc.web_access.string_mcp import ENDPOINT_TEMPLATE


def _http(url: str, **kw) -> MCPServerSpec:
    return MCPServerSpec(id="srv", name="srv", transport="http", url=url, **kw)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.org/mcp", "javascript:alert(1)",
    "https://user:pa55@example.org/mcp", "http:///nohost", "https://exa mple.org/mcp",
])
def test_bad_http_urls_are_refused_in_russian(url):
    errs = _http(url).validate()
    assert errs, url
    assert any("адрес" in e.lower() for e in errs), errs


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9/mcp", "https://mcp.example.org/v1/mcp", ENDPOINT_TEMPLATE])
def test_good_http_urls_still_accepted(url):
    assert _http(url).validate() == []


@pytest.mark.parametrize("name", ["", "   ", "bad\x00name", "x" * 200])
def test_bad_names_refused(name):
    spec = MCPServerSpec(id=name, name=name, transport="stdio", command=[sys.executable, "s.py"])
    errs = spec.validate()
    assert any("имя" in e.lower() for e in errs), errs


@pytest.mark.parametrize("timeout", [0, -5, 10_000])
def test_timeout_out_of_range_refused(timeout):
    errs = _http("https://mcp.example.org/mcp", timeout_seconds=timeout).validate()
    assert any("таймаут" in e.lower() for e in errs), errs


def test_bad_env_key_refused():
    spec = MCPServerSpec(id="s", name="s", transport="stdio", command=[sys.executable, "s.py"],
                         env_keys=["GOOD_KEY", "PATH=evil"])
    errs = spec.validate()
    assert any("переменн" in e.lower() for e in errs), errs


def test_legacy_messages_kept():
    assert MCPServerSpec("s", "s", "stdio").validate() == ["stdio MCP requires command"]


async def test_post_server_with_file_url_is_422_and_not_stored(env):
    r = await env.client.post("/api/mcp/servers", json={
        "name": "local-file", "transport": "http", "url": "file:///etc/passwd"})
    assert r.status_code == 422, r.text
    assert "адрес" in r.text.lower() or "\\u0430\\u0434\\u0440\\u0435\\u0441" in r.text
    rows = (await env.client.get("/api/mcp/servers")).json()
    assert not any(row["name"] == "local-file" for row in rows)


async def test_post_server_with_blank_name_is_422(env):
    r = await env.client.post("/api/mcp/servers", json={
        "name": "   ", "transport": "stdio", "command": [sys.executable, "srv.py"]})
    assert r.status_code == 422, r.text
