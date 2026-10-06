"""web_research: проверка доступа к вебу 2026-09-30 — два дефекта, найденных живой пробой.

D4. «who is Alan Turing», «what is entropy», «что такое энтропия» — ровно те
вопросы, ради которых `pick_backend` выбирает Википедию по ключевым словам, и
ровно те, на которые OpenSearch (поиск по ПРЕФИКСУ названия) отвечал
`empty_result`. Живой замер на этой машине: 4 из 4 таких вопросов пусты, а
«Alan Turing» даёт 10 статей. Вред тихий: модель слышит «ничего не найдено» и
сообщает владельцу, что статьи нет.

D5. Текст «ИСКАТЬ НЕГДЕ» обещал третий путь — ключ Brave, — хотя хранилища ключей
поисковых API в сборке нет (`api.KEY_STORE_NOTE`). Владелец искал несуществующее
поле. Это известная запись аудита A11a-04 от 2026-09-06.

D9. «Alan Turing» в Википедии — 1,3 МБ HTML, «Москва» — 2,1 МБ, «Python» — 1,0 МБ.
Потолок чтения страницы 400 КБ превращал `web.open` в отказ «response exceeds
max_bytes» на КАЖДОЙ длинной статье — то есть на основном бесключевом источнике.
Сквозной прогон локальной модели (qwen3-coder:30b) это и показал: открыть w1 из
Википедии не удалось ни разу. Теперь страница читается до потолка и помечается
«ИСТОЧНИК ОБРЕЗАН»; API и robots.txt по-прежнему отвергаются целиком.

D6. Fetch исследовательского стола Jeff (Telegram-компаньон) отсекал только
ЛИТЕРАЛЬНЫЕ адреса: `localtest.me` и `127.0.0.1.nip.io` проходили фильтр и
возвращали содержимое loopback-службы (замер 2026-09-30). Адрес страницы выбирает
третье лицо (выдача поиска), поэтому клиент «интернета» обязан ходить только на
публичные адреса. Правка в транспорте (`adapters.Models.remote`), потому что
`bcc/pit/**` — чужая полоса.

D8. Stack Overflow — бесключевой источник для вопросов об ошибках (ключевые слова
error/exception/ошибка), но его ВЫДАЧА была голым списком заголовков, а сами
страницы сайт нам не отдаёт (HTTP 403 уже на robots.txt, любой User-Agent). Модель
получала названия без единого слова содержимого и открыть их не могла (сквозной
прогон 2026-09-30). `search/excerpts` отдаёт выдержки; разметка в них не нужна.

D12. `sources.ensure_source` возвращал записанную когда-то карточку встроенного
backend'а как есть: правка его объявления в коде (путь API, парсер) не доходила
ни до одного экземпляра, где источник уже использовался. Живая проба: после
перехода Stack Overflow на `search/excerpts` экземпляр продолжал ходить по
старому пути и отдавал пустые описания.

D11. Адрес внутренней сети в `web.open {"url": ...}` доходил до владельца как вопрос
«одобрить?» (`ask`) и только потом отказывал на чтении. Заведомо отказной адрес
теперь `deny` сразу: одобрение владельца не тратится на то, что не исполнится.

D10. `web.open` извлекал ровно столько знаков, сколько собирался ПОКАЗАТЬ (3000),
и именно этот кусок становился текстом, по которому ищут `web.find` и `web.cite`.
У англоязычной Википедии первые 3000 знаков текста — меню сайта: тело статьи в
извлечение не попадало, `web.find "Bletchley"` молчал, цитировать было нечего.

Сети нет: транспорт OSIRIS подменён стендом с `live = False`, а для D9 подменён
сокет (настоящие `WebFetchAdapter` и `safe_get`).
"""
from __future__ import annotations

import dataclasses
import json
import socket
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from bcc import plugin_security as psec
from bcc.features import osiris
from bcc.features.web_research import config, ledger, net, render, sources
from bcc.plugin_security import PluginSecurityError
from bcc.tools import REGISTRY, ToolContext, execute_tool

from .conftest import client_for, make_settings, start_app

EN_API = "https://en.wikipedia.org/w/api.php"
EN_ROBOTS = "https://en.wikipedia.org/robots.txt"
RU_API = "https://ru.wikipedia.org/w/api.php"
RU_ROBOTS = "https://ru.wikipedia.org/robots.txt"
ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"


class StubAdapter:
    live = False

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, str, dict[str, str]]] = {}
        self.calls: list[str] = []

    def route(self, marker: str, body: str, *, content_type: str = "application/json") -> None:
        self.routes[marker] = (200, body, {"content-type": content_type})

    async def fetch(self, url: str, *, headers=None, timeout: float = 15.0):
        self.calls.append(url)
        for marker, (status, body, head) in self.routes.items():
            if marker in url:
                return osiris.FetchResult(status=status, body=body, url=url, headers=head)
        return osiris.FetchResult(status=404, body="", url=url,
                                  headers={"content-type": "text/plain"})

    def api_calls(self) -> list[str]:
        return [u for u in self.calls if "/w/api.php" in u]


@pytest.fixture(autouse=True)
def _isolate_process_tables():
    tools_before = set(REGISTRY.names())
    parsers_before = dict(osiris.PARSERS)
    net.robots_cache_clear()
    yield
    for name in set(REGISTRY.names()) - tools_before:
        REGISTRY.unregister(name)
    osiris.PARSERS.clear()
    osiris.PARSERS.update(parsers_before)
    net.robots_cache_clear()


@asynccontextmanager
async def _stand(tmp_path, monkeypatch):
    assert config.env_errors() == ()
    monkeypatch.setenv(config.FLAG, "1")
    monkeypatch.setenv(config.OSIRIS_FLAG, "1")
    monkeypatch.setattr(config, "SEARXNG_URL", "")
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    adapter = StubAdapter()
    adapter.route(EN_ROBOTS, ROBOTS_ALLOW, content_type="text/plain")
    adapter.route(RU_ROBOTS, ROBOTS_ALLOW, content_type="text/plain")
    adapter.route(EN_API, json.dumps(["x", ["Alan Turing"], ["d"],
                                      ["https://en.wikipedia.org/wiki/Alan_Turing"]]))
    adapter.route(RU_API, json.dumps(["x", ["Энтропия"], ["d"],
                                      ["https://ru.wikipedia.org/wiki/Энтропия"]]))
    osiris.store(svc).adapter = adapter
    try:
        async with client_for(app, svc) as client:
            yield svc, adapter, client
    finally:
        await svc.stop()


def _ctx(svc, run_id: int = 11) -> ToolContext:
    return ToolContext(svc=svc, task={"id": 1, "meta": {}}, run_id=run_id,
                       agent={"id": 1, "name": "аналитик"}, step=1)


def _backend(source_id: str) -> sources.Backend:
    bk = sources.backend_by_id(source_id)
    assert bk is not None
    return bk


# ------------------------------------------------------------------- D4


async def test_who_is_question_reaches_wikipedia_as_the_article_title(tmp_path, monkeypatch):
    """Вред: вопрос-определение уходит в префиксный поиск целиком и возвращает
    пустоту. Проверяется на ОТПРАВЛЕННОМ адресе, а не на вспомогательной
    функции: тест должен падать, если вызов не подключён."""
    async with _stand(tmp_path, monkeypatch) as (svc, adapter, _client):
        result = await execute_tool(REGISTRY.get("web.search"),
                                    {"query": "who is Alan Turing"}, _ctx(svc))
        assert result.error is False, result.content
        assert result.data["backend"] == "wikipedia-opensearch-en"
        assert result.data["refs"] == ["w1"], "обвязка не снята: поиск вернул пустоту"
        sent = adapter.api_calls()
        assert len(sent) == 1
        assert "search=Alan%20Turing" in sent[0]
        assert "who" not in sent[0].lower()


async def test_russian_definition_question_is_stripped_too(tmp_path, monkeypatch):
    async with _stand(tmp_path, monkeypatch) as (svc, adapter, _client):
        result = await execute_tool(REGISTRY.get("web.search"),
                                    {"query": "что такое энтропия?"}, _ctx(svc))
        assert result.error is False, result.content
        assert result.data["backend"] == "wikipedia-opensearch-ru"
        sent = adapter.api_calls()
        assert len(sent) == 1
        assert "search=%D1%8D%D0%BD%D1%82%D1%80%D0%BE%D0%BF%D0%B8%D1%8F" in sent[0]   # «энтропия»


async def test_owner_api_search_sends_the_same_stripped_text(tmp_path, monkeypatch):
    """Ручка владельца и инструмент модели — один конвейер; расхождение между
    ними было бы ровно тем «владелец проверил одно, модель делает другое»."""
    async with _stand(tmp_path, monkeypatch) as (_svc, adapter, client):
        reply = await client.post("/api/web/search",
                                  json={"query": "what is Alan Turing", "fresh": True})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body["code"] == "ok" and body["shown"] == 1
        assert body["subject"] == "Alan Turing"
        assert "search=Alan%20Turing" in adapter.api_calls()[0]


@pytest.mark.parametrize("backend_id, asked, sent", [
    ("wikipedia-opensearch-en", "who is Alan Turing", "Alan Turing"),
    ("wikipedia-opensearch-en", "What is entropy?", "entropy"),
    ("wikipedia-opensearch-en", "tell me about the Roman Empire", "Roman Empire"),
    ("wikipedia-opensearch-ru", "что такое энтропия", "энтропия"),
    ("wikipedia-opensearch-ru", "кто такой Алан Тьюринг?", "Алан Тьюринг"),
    ("wikipedia-opensearch-ru", "расскажи про Луну", "Луну"),
])
def test_lead_in_is_removed_only_from_prefix_search_backends(backend_id, asked, sent):
    assert sources.backend_subject(_backend(backend_id), asked) == sent


@pytest.mark.parametrize("backend_id, asked", [
    # Негативный контроль 1: слова названия не снимаются. «История России» — статья.
    ("wikipedia-opensearch-ru", "история России"),
    ("wikipedia-opensearch-en", "history of Rome"),
    # Негативный контроль 2: обвязка была ВСЕМ запросом — искать другое нельзя.
    ("wikipedia-opensearch-en", "who is"),
    ("wikipedia-opensearch-ru", "что такое"),
    # Негативный контроль 3: обычный запрос без обвязки не меняется.
    ("wikipedia-opensearch-en", "Alan Turing"),
    # Негативный контроль 4: не-OpenSearch источники текст запроса не правят.
    ("stackexchange", "what is this error in asyncio"),
    ("hn-algolia", "who is hiring"),
    ("pypi", "what is requests"),
])
def test_lead_in_stays_where_it_is_part_of_the_question(backend_id, asked):
    assert sources.backend_subject(_backend(backend_id), asked) == asked


# ------------------------------------------------------------------- D5


def test_zero_config_text_does_not_promise_a_brave_key_that_has_nowhere_to_go(monkeypatch):
    """Вред: совет, которому нельзя последовать. Хранилища ключей поисковых API
    нет (`api.KEY_STORE_NOTE`), `brave-search` навсегда «ключ не задан»."""
    monkeypatch.setenv(config.FLAG, "1")
    keyless = [{"id": "pypi", "ready": True, "keyless": True, "general_web": False}]
    for backends in (keyless, []):
        ready = config.readiness(backends=backends, osiris_on=True)
        text = ready["text"]
        assert "либо добавить ключ Brave" not in text, ready["code"]
        assert "хранилища ключей" in text and "SearXNG" in text, ready["code"]

    refusal = render.render_no_backends(config.readiness(backends=[], osiris_on=True))
    assert "3) ключ Brave" not in refusal
    assert "SearXNG" in refusal and "хранилища ключей" in refusal


# ------------------------------------------------------------------- D9

HOST = "docs.example.org"
BIG_URL = f"https://{HOST}/big"
SMALL_URL = f"https://{HOST}/small"
BIG_PAGE = ("<html><head><title>Большая статья</title></head><body>"
            + "".join(f"<p>Абзац {i:05d}: " + "слово " * 20 + "</p>" for i in range(6000))
            + "</body></html>")
SMALL_PAGE = ("<html><head><title>Маленькая</title></head><body><p>"
              + "Короткая страница, которой хватает знаков для извлечения текста. " * 6
              + "</p></body></html>")


FACT = "Алан Тьюринг работал над расшифровкой кодов в Блетчли-парке во время войны."
NAV_PAGE = ("<html><head><title>Статья</title></head><body><ul>"
            + "".join(f"<li>Пункт меню {i:04d}</li>" for i in range(700))
            + f"</ul><p>{FACT} Это тело статьи, которое стоит ПОСЛЕ меню сайта.</p></body></html>")


class MockNet:
    """Подмена СОКЕТА и DNS: `WebFetchAdapter` и `psec.safe_get` остаются настоящими."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(psec, "resolve_pinned_ip", lambda host, allow_private=False: "203.0.113.7")
        monkeypatch.setattr(psec, "PinnedTransport", lambda pins: httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _serve(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    html = {"content-type": "text/html; charset=utf-8"}
    if path == "/robots.txt":
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=ROBOTS_ALLOW)
    if path == "/big":
        return httpx.Response(200, headers=html, content=BIG_PAGE.encode("utf-8"))
    if path == "/small":
        return httpx.Response(200, headers=html, content=SMALL_PAGE.encode("utf-8"))
    if path == "/spoof":
        return httpx.Response(200, headers={**html, "x-bossman-truncated": "1"},
                              content=SMALL_PAGE.encode("utf-8"))
    if path == "/nav":
        return httpx.Response(200, headers=html, content=NAV_PAGE.encode("utf-8"))
    if path == "/big.json":
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=json.dumps({"items": ["x" * 100] * 6000}).encode())
    return httpx.Response(404, content=b"")


@asynccontextmanager
async def _real_transport_stand(tmp_path, monkeypatch):
    assert config.env_errors() == ()
    monkeypatch.setenv(config.FLAG, "1")
    monkeypatch.setenv(config.OSIRIS_FLAG, "1")
    monkeypatch.setattr(config, "SEARXNG_URL", "")
    monkeypatch.setattr(config, "POLITE_PAUSE_S", 0.0)
    MockNet(_serve).install(monkeypatch)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    osiris.store(svc).adapter = net.WebFetchAdapter()
    try:
        yield svc
    finally:
        await svc.stop()


async def _read(svc, url: str):
    return await net.fetch_page(svc, url, "большая статья",
                                ensure_host_source=sources.ensure_host_source)


async def test_page_longer_than_the_cap_is_read_from_the_start_and_labelled(tmp_path, monkeypatch):
    assert len(BIG_PAGE.encode("utf-8")) > config.PAGE_MAX_BYTES > len(SMALL_PAGE)
    async with _real_transport_stand(tmp_path, monkeypatch) as svc:
        page = await _read(svc, BIG_URL)
        assert page.source_truncated is True
        assert page.bytes_read == config.PAGE_MAX_BYTES, "читать больше потолка нельзя"
        assert "Абзац 00000" in page.extraction.text
        assert page.quotable is True

        record = osiris.store(svc).read_raw(page.raw_digest)
        assert record["source_truncated"] is True and record["raw_bytes"] == config.PAGE_MAX_BYTES
        obs = [o for o in osiris.store(svc).observations("большая статья")
               if o.get("attribute") == "page.text"]
        assert obs and obs[0]["value"]["source_truncated"] is True


async def test_web_open_shows_the_beginning_of_a_long_article_instead_of_a_refusal(tmp_path, monkeypatch):
    async with _real_transport_stand(tmp_path, monkeypatch) as svc:
        led = ledger.Ledger.load(svc, 21)
        token = led.mint(BIG_URL, kind="search", subject="большая статья", origin="wikipedia-opensearch-ru",
                         origin_host=HOST, trusted_hosts=(HOST,))
        assert token == "w1"
        led.save()
        result = await execute_tool(REGISTRY.get("web.open"), {"ref": "w1"}, _ctx(svc, 21))
        assert result.error is False, result.content
        assert "ОТКАЗ" not in result.content and "exceeds" not in result.content
        assert "ИСТОЧНИК ОБРЕЗАН" in result.content
        assert "Абзац 00000" in result.content


async def test_small_page_is_not_labelled_and_a_spoofed_header_is_ignored(tmp_path, monkeypatch):
    """Негативные контроли: метка ставится только за реальное обрезание, а
    заголовок, присланный самим сервером, её не включает."""
    async with _real_transport_stand(tmp_path, monkeypatch) as svc:
        small = await _read(svc, SMALL_URL)
        assert small.source_truncated is False
        spoof = await _read(svc, f"https://{HOST}/spoof")
        assert spoof.source_truncated is False, "метку обрезания не вправе ставить сервер"


async def test_api_and_non_page_reads_are_still_refused_when_oversize(tmp_path, monkeypatch):
    """Обрезанный JSON — мусор, а не ответ: отказ целиком остался там, где он нужен."""
    async with _real_transport_stand(tmp_path, monkeypatch):
        adapter = net.WebFetchAdapter()
        with pytest.raises(PluginSecurityError, match="max_bytes"):
            await adapter.fetch_bytes(f"https://{HOST}/big.json", max_bytes=1000)
        with pytest.raises(PluginSecurityError, match="max_bytes"):
            await adapter.fetch(f"https://{HOST}/big.json")


# ------------------------------------------------------------------- D6


class _CountingHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        body = b"<html><body>LOCAL-SECRET-CANARY</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def loopback_service():
    _CountingHandler.hits = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CountingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _fake_dns(monkeypatch, table: dict[str, str]):
    real = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], 0))]
        return real(host, *args, **kwargs)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


async def test_companion_internet_client_never_reaches_loopback_by_name(tmp_path, monkeypatch, loopback_service):
    from bcc.telegram_companion.adapters import Models, text_request
    from bcc.telegram_companion.config import CompanionError, Person, Settings

    _fake_dns(monkeypatch, {"localtest.example": "127.0.0.1", "lan.example": "10.0.0.5",
                            "meta.example": "169.254.169.254"})
    models = Models(Settings((Person(1, 1, "owner", 10),)), tmp_path)
    try:
        for url in (f"http://localtest.example:{loopback_service}/",      # имя → loopback
                    f"http://lan.example:{loopback_service}/",           # имя → LAN
                    "http://meta.example/latest/meta-data/",             # имя → metadata
                    f"http://127.0.0.1:{loopback_service}/",             # литерал
                    "http://169.254.169.254/latest/meta-data/"):
            with pytest.raises(CompanionError) as caught:
                await text_request(models.remote, url, timeout=5)
            assert "NETWORK_UNAVAILABLE" in str(caught.value), url
        assert _CountingHandler.hits == 0, "loopback-служба получила запрос"
    finally:
        await models.close()


async def test_companion_egress_guard_lets_public_names_through(monkeypatch):
    """Парный контроль: законное проходит, плохое отвергается. С прокси имя
    резолвит прокси — там проверяются только литералы, и это названо, а не
    спрятано."""
    from bcc.telegram_companion.adapters import _egress_guard

    _fake_dns(monkeypatch, {"good.example": "93.184.216.34", "sneaky.example": "127.0.0.1",
                            "mixed.example": "93.184.216.34"})
    hook = _egress_guard(resolve=True)
    await hook(httpx.Request("GET", "https://good.example/page"))
    with pytest.raises(httpx.ConnectError):
        await hook(httpx.Request("GET", "https://sneaky.example/page"))

    via_proxy = _egress_guard(resolve=False)
    await via_proxy(httpx.Request("GET", "https://sneaky.example/page"))          # имя — забота прокси
    with pytest.raises(httpx.ConnectError):
        await via_proxy(httpx.Request("GET", "http://127.0.0.1:8801/api/tasks"))   # литерал — всегда нет


# ------------------------------------------------------------------- D8


def test_stackexchange_serp_carries_excerpts_without_markup_and_without_duplicates():
    bk = _backend("stackexchange")
    assert "/search/excerpts" in bk.decl["path_template"]
    assert "403" in bk.honest_capability, "модель должна знать, почему страницы не открываются"

    payload = {"items": [
        {"item_type": "question", "question_id": 111, "answer_count": 1,
         "title": "How to use &quot;asyncio&quot; timeout",
         "excerpt": 'I try <span class="highlight">asyncio</span>.timeout &amp; wait_for'},
        {"item_type": "answer", "question_id": 111, "title": "How to use &quot;asyncio&quot; timeout",
         "excerpt": "Use <span>async with</span> timeout()"},
        {"item_type": "question", "question_id": 222, "title": "Other question", "excerpt": "plain text"},
        # Негативный контроль: значение вне узкого алфавита в адрес не собирается.
        {"item_type": "question", "question_id": "1 2", "title": "bad id", "excerpt": "x"},
    ]}
    parsed = sources.parse_serp(bk, payload)
    assert parsed["outcome"] == "ok"
    urls = [h["url"] for h in parsed["hits"]]
    assert urls == ["https://stackoverflow.com/questions/111", "https://stackoverflow.com/questions/222"]
    first = parsed["hits"][0]
    assert first["snippet"] == "I try asyncio.timeout & wait_for"
    assert first["title"] == 'How to use "asyncio" timeout'
    assert "<" not in first["snippet"] and "<" not in first["title"]
    assert parsed["dropped"] == 2                      # дубль ответа и негодный id
    assert all(h["trusted"] for h in parsed["hits"])


# ------------------------------------------------------------------- D10


async def test_text_after_a_long_site_menu_is_reachable_by_open_find_and_cite(tmp_path, monkeypatch):
    nav_chars = len(NAV_PAGE.split("</ul>")[0])
    assert nav_chars > config.PAGE_CHARS_DEFAULT, "меню должно быть длиннее показываемого куска"
    async with _real_transport_stand(tmp_path, monkeypatch) as svc:
        url = f"https://{HOST}/nav"
        led = ledger.Ledger.load(svc, 31)
        assert led.mint(url, kind="search", subject="статья", origin="wikipedia-opensearch-ru",
                        origin_host=HOST, trusted_hosts=(HOST,)) == "w1"
        led.save()
        ctx = _ctx(svc, 31)

        opened = await execute_tool(REGISTRY.get("web.open"), {"ref": "w1", "query": "Блетчли"}, ctx)
        assert opened.error is False, opened.content
        assert "Блетчли" in opened.content
        assert "нет ни одного совпадения" not in opened.content

        found = await execute_tool(REGISTRY.get("web.find"), {"ref": "w1", "query": "расшифровкой"}, ctx)
        assert found.error is False and found.data["matches"] >= 1, found.content

        cited = await execute_tool(REGISTRY.get("web.cite"),
                                   {"ref": "w1", "quote": FACT, "claim": "чем занимался Тьюринг"}, ctx)
        assert cited.error is False, cited.content
        assert "ССЫЛАЙСЯ" in cited.content or "цитата" in cited.content.lower()


# ------------------------------------------------------------------- D11


@pytest.mark.parametrize("url", [
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1:8801/api/tasks",
    "https://localhost/admin",
    "https://10.0.0.5/",
    "https://metadata.google.internal/computeMetadata/v1/",
])
def test_internal_addresses_are_denied_before_the_owner_is_asked(url):
    from bcc.features.web_research import tools
    verdict = tools.open_effect({"url": url})
    assert verdict is not None and verdict[0] == "deny", (url, verdict)
    assert "внутреннюю сеть" in verdict[1]


@pytest.mark.parametrize("url", [
    "https://docs.python.org/3/library/asyncio.html",
    "https://en.wikipedia.org/wiki/Alan_Turing",
    "https://example.com/",
])
def test_legitimate_public_address_is_still_shown_to_the_owner(url):
    """Парный контроль: законный адрес по-прежнему `ask`, а не `deny` и не `auto`."""
    from bcc.features.web_research import tools
    verdict = tools.open_effect({"url": url})
    assert verdict is not None and verdict[0] == "ask", (url, verdict)


# ------------------------------------------------------------------- D12


async def test_persisted_builtin_source_follows_the_code_declaration(tmp_path, monkeypatch):
    async with _stand(tmp_path, monkeypatch) as (svc, _adapter, _client):
        st = osiris.store(svc)
        bk = _backend("stackexchange")
        stale = dataclasses.replace(
            osiris.normalize_source(dict(bk.decl)),
            path_template="/2.3/search/advanced?order=desc&q={subject}",
            live_status="ok", live_checked_at="2026-09-01T00:00:00+00:00")
        st.save_source(stale)

        got = sources.ensure_source(st, bk.decl)
        assert "/search/excerpts" in got.path_template, "карточка на диске затёрла объявление в коде"
        assert (got.live_status, got.live_checked_at) == ("ok", "2026-09-01T00:00:00+00:00"),             "живое состояние добыто сетью и не должно теряться при обновлении объявления"
        assert "/search/excerpts" in st.sources()["stackexchange"].path_template, "обновление не записано"


async def test_unchanged_or_owner_declared_source_is_left_alone(tmp_path, monkeypatch):
    """Негативные контроли: совпадающее объявление не переписывается (иначе каждое
    чтение трогало бы диск), а источник владельца не подменяется кодом."""
    async with _stand(tmp_path, monkeypatch) as (svc, _adapter, _client):
        st = osiris.store(svc)
        writes: list[str] = []
        real_save = st.save_source
        monkeypatch.setattr(st, "save_source", lambda src: (writes.append(src.id), real_save(src))[1])

        bk = _backend("pypi")
        first = sources.ensure_source(st, bk.decl)
        again = sources.ensure_source(st, bk.decl)
        assert writes == ["pypi"], "второй вызов с тем же объявлением переписал карточку"
        assert again.path_template == first.path_template

        own = dict(sources._api_decl(source_id="my-own-api", base_url="https://example.org",   # noqa: SLF001
                                     path_template="/a/{subject}", license_="MIT", honest="свой", rate=10))
        own["parser"] = "web.serp_json"
        kept = sources.ensure_source(st, own)
        own["path_template"] = "/b/{subject}"
        assert sources.ensure_source(st, own).path_template == kept.path_template == "/a/{subject}"
