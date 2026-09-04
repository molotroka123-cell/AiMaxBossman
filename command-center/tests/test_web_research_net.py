"""web_research: транспорт и конвейер чтения — что уходит наружу и в каком порядке.

Здесь проверяется не «страница читается», а вред, который случается ТИХО и
выглядит как успех. Каждый тест ловит один такой вред:

  * **сутки висящего соединения.** У `psec.safe_get` общего дедлайна нет: его
    `timeout` перезапускается на КАЖДОМ чтении, поэтому сервер, отдающий по
    байту раз в полсекунды, держит воркер владельца бесконечно и ни одна
    проверка при этом не срабатывает — обмен формально идёт;
  * **сжатый ответ.** `Accept-Encoding: identity` — просьба, а не гарантия
    (C2). Тело, распакованное чужой библиотекой, попадает в память до всякого
    потолка, а `psec.safe_get` копирует `Content-Encoding` в ответ, подставляя
    туда УЖЕ распакованные байты;
  * **два разных имени хоста (C1).** «Хост, который проверили» и «хост, к
    которому подключились» обязаны быть одной строкой. Завершающая точка и
    IDN — ровно те две формы, на которых они расходятся, а расхождение здесь
    означает, что рассуждение «мы сходили туда, куда разрешили» повисает;
  * **редирект за пределы сайта.** Он выглядит как обычное чтение и приносит
    содержимое чужого хоста с паспортом запрошенного;
  * **fail-closed на robots (C3).** Недоступный `robots.txt` обязан быть
    отказом, а не «раз не запрещено, значит можно», и тянуться он обязан тем же
    транспортом с теми же `allowed_hosts` — иначе для него, самого частого
    запроса конвейера, анти-rebinding выключен полностью;
  * **лимит на всех вместо лимита на каждого.** Общий счётчик означал бы, что
    десять чтений одного сайта закрывают доступ ко всем остальным, а лимит,
    ключуемый не по хосту, не защищает ни один сайт от нас;
  * **кэш, выданный за свежее чтение (E2).** «Сейчас» и «41 минуту назад» — это
    разница между фактом и воспоминанием, и без возраста в выводе она исчезает;
  * **перезапись доказательства (D1).** Сырьё, адресованное по URL, означает,
    что перепроверка уничтожает ровно ту улику, которую проверяет;
  * **стенд, отмытый в след как настоящая сеть (D5).** Зелёный тест не имеет
    права означать «источник работает»;
  * **сломанный движок поиска, выданный за пустой интернет (E1).** SearXNG при
    капче в апстриме отвечает HTTP 200 и пустым списком; «поиск не состоялся» и
    «ничего не найдено» — разные утверждения, и первое не имеет права выглядеть
    вторым.

Сети наружу в файле нет ни одной. Подмена делается на двух разных швах, и это
не дублирование:

  * `StubAdapter` подменяет ТРАНСПОРТ (`store(svc).adapter`) — им проверяется
    конвейер: порядок проверок, кэш, лимиты, запись сырья;
  * `MockNet` подменяет СОКЕТ И DNS (`psec.resolve_pinned_ip`,
    `psec.PinnedTransport`), оставляя настоящими `WebFetchAdapter`, `safe_get`,
    `validate_url` и разбор редиректов. Иначе тесты про дедлайн, `allowed_hosts`
    и кросс-сайтовый редирект проверяли бы стенд, а не транспорт.

ОДИН ТЕСТ В ФАЙЛЕ ПАДАЕТ НАМЕРЕННО —
`test_svoy_searxng_oprashivaetsya_privatnoy_dveryu`. Это не флаки и не
незаконченная работа: приватная дверь `net.searxng_fetch` не вызывается
ниоткуда, поэтому при заданном `BOSSMAN_WEB_SEARXNG_URL` готовность обещает
владельцу работающий общий веб-поиск, а каждый поиск отвечает «источник
недоступен». Подогнать тест под это поведение значило бы закрепить обещание,
которого код не выполняет; правка — за ведущим, и она в `tools.py`/`sources.py`,
а не здесь.

Чего этот файл НЕ проверяет (у этого есть свои файлы): выключенный флаг,
извлечение текста, реестр ссылок и эффекты инструментов, рендер как таковой,
хук завершения и ручки владельца.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

import httpx
import pytest

from bcc import plugin_security as psec
from bcc.features import osiris
from bcc.features.web_research import config, ledger, net, sources
from bcc.plugin_security import PluginSecurityError
from bcc.tools import REGISTRY, ToolContext, execute_tool

from .conftest import make_settings, start_app

DOCS_HOST = "docs.example.org"
DOCS_ROBOTS = f"https://{DOCS_HOST}/robots.txt"
DOCS_PAGE = f"https://{DOCS_HOST}/guide/api"
OTHER_HOST = "wiki.example.net"
OTHER_ROBOTS = f"https://{OTHER_HOST}/robots.txt"
OTHER_PAGE = f"https://{OTHER_HOST}/article"

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"

SUBJECT = "как читаются страницы"

# Текста заведомо больше порога MIN_PAGE_TEXT_CHARS: тест про Content-Type не
# должен уметь пройти по причине «на странице мало текста».
PAGE_HTML = (
    "<html><head><title>Руководство</title></head><body>"
    "<p>Страница читается конвейером по одному заранее заданному порядку: сначала "
    "проверяется адрес, потом разрешение robots, потом кэш и лимит, и только "
    "после этого происходит обращение к сети.</p>"
    "<p>Порядок важен не сам по себе: каждая перестановка означает, что отказ "
    "случится позже траты, а часть трат уже необратима — байты ушли с машины "
    "владельца и вернуть их нельзя.</p>"
    "</body></html>")

PAGE_HTML_CHANGED = (
    "<html><head><title>Руководство, версия вторая</title></head><body>"
    "<p>Текст страницы заменён целиком, и это ровно тот случай, ради которого "
    "сырьё адресуется по содержимому: старое чтение обязано остаться на диске "
    "вместе с подписью, на которую ссылается уже выданная цитата.</p>"
    "<p>Новое чтение создаёт новую запись рядом со старой, а не вместо неё.</p>"
    "</body></html>")


# --------------------------------------------------------------------- стенд


class StubAdapter:
    """Транспорт без сети, считающий каждое обращение.

    `live = False` — не косметика, а условие честности: право пометить источник
    проверенным живьём принадлежит атрибуту `live`, и тест, утверждающий
    «наружу не ходили», обязан ставить именно стенд.

    Реализован только осирисовский `fetch`: `fetch_bytes` стенд иметь не обязан
    (протокол его не требует), и конвейер обязан работать с таким стендом,
    иначе «сеть в тестах подменяется» перестало бы быть правдой.
    """

    live = False

    def __init__(self) -> None:
        self.routes: list[tuple[str, int, str, dict[str, str]]] = []
        self.calls: list[str] = []

    def route(self, marker: str, body: str, *, status: int = 200,
              headers: dict[str, str] | None = None) -> None:
        """Ответ на любой адрес, содержащий `marker`. Порядок объявления значим:
        отвечает первый подошедший, поэтому частное объявляется раньше общего."""
        head = {"content-type": "text/html; charset=utf-8", **(headers or {})}
        self.routes.append((marker, status, body, head))

    async def fetch(self, url: str, *, headers: dict[str, str] | None = None,
                    timeout: float = 15.0) -> osiris.FetchResult:
        self.calls.append(url)
        for marker, status, body, head in self.routes:
            if marker in url:
                return osiris.FetchResult(status=status, body=body, url=url, headers=head)
        return osiris.FetchResult(status=404, body="", url=url,
                                  headers={"content-type": "text/plain"})

    def page_calls(self) -> list[str]:
        """Обращения ЗА СОДЕРЖИМЫМ: `robots.txt` — это выяснение, есть ли
        разрешение читать, и в счёт «ходили наружу за страницей» он не идёт."""
        return [u for u in self.calls if not u.endswith("/robots.txt")]


class LiveStubAdapter(StubAdapter):
    """Тот же стенд, но объявивший себя настоящей сетью.

    Нужен ровно для одного: показать, что проверка «стенд не помечает источник
    живым» действительно смотрит на `live`, а не проходит сама собой на любом
    адаптере. Без этой пары утверждение о `live = False` ничего не стоило бы.
    """

    live = True


class MockNet:
    """Подмена СОКЕТА и DNS, а не транспорта.

    `WebFetchAdapter`, `psec.safe_get`, `validate_url`, `allowed_hosts` и разбор
    редиректов остаются настоящими — подменяются только резолв имени и то, что
    отдаёт соединение. Иначе тест про общий дедлайн или про кросс-сайтовый
    редирект проверял бы поведение собственной заглушки.
    """

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(psec, "resolve_pinned_ip",
                            lambda host, allow_private=False: "203.0.113.7")
        monkeypatch.setattr(psec, "PinnedTransport",
                            lambda pins: httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def hosts(self) -> list[str]:
        """Хосты В ТОЙ ФОРМЕ, в которой их увидит транспорт: `raw_host` — это
        байты имени, уходящие в SNI и в словарь пинов, а не отображаемая форма."""
        return [r.url.raw_host.decode("ascii", "replace") for r in self.requests]


class Env:
    def __init__(self, svc, settings, adapter: StubAdapter) -> None:
        self.svc = svc
        self.settings = settings
        self.adapter = adapter

    @property
    def store(self) -> osiris.OsirisStore:
        return osiris.store(self.svc)

    def ctx(self, *, run_id: int = 5, step: int = 1) -> ToolContext:
        return ToolContext(svc=self.svc, task={"id": 1, "meta": {}}, run_id=run_id,
                           agent={"id": 1, "name": "аналитик"}, step=step)

    def raw_files(self) -> list[str]:
        directory = self.store.raw_dir
        return sorted(p.stem for p in directory.glob("*.json")) if directory.is_dir() else []

    def page_observations(self, subject: str = SUBJECT) -> list[dict]:
        return [o for o in self.store.observations(subject)
                if o.get("attribute") == "page.text"]

    def host_source(self, host: str = DOCS_HOST):
        return self.store.sources().get(config.host_source_id(host))


@pytest.fixture(autouse=True)
def _isolate_process_tables():
    """Реестр инструментов, таблица парсеров, память вердиктов robots и память
    пауз вежливости — глобальные на процесс. Не убрав их за собой, файл ломал бы
    соседние тесты (или, хуже, проходил бы за их счёт)."""
    tools_before = set(REGISTRY.names())
    parsers_before = dict(osiris.PARSERS)
    net.robots_cache_clear()
    net._LAST_HIT.clear()                                        # noqa: SLF001
    yield
    for name in set(REGISTRY.names()) - tools_before:
        REGISTRY.unregister(name)
    osiris.PARSERS.clear()
    osiris.PARSERS.update(parsers_before)
    net.robots_cache_clear()
    net._LAST_HIT.clear()                                        # noqa: SLF001


@asynccontextmanager
async def _stand(tmp_path, monkeypatch):
    """Оба флага включены, транспорт подменён стендом, общего веб-поиска нет.

    Флаги ставятся ДО старта: `setup()` зовётся из `Services.start()`. Адаптер
    подменяется ПОСЛЕ старта, потому что `setup()` сам ставит боевой
    `WebFetchAdapter` — без подмены первый же тест пошёл бы в настоящую сеть.

    Пауза вежливости обнуляется намеренно и только здесь: это задержка перед
    чужим сервером, а не свойство безопасности, и её секунда на каждое чтение
    превратила бы набор в минуты ожидания. Всё, что она защищает, —
    посторонний сайт от нашей спешки, а не владельца от нас.
    """
    assert config.env_errors() == (), (
        "в окружении разработчика испорчены переменные BOSSMAN_WEB_*: "
        "стенд обязан стоять на настройке по умолчанию")
    monkeypatch.setenv(config.FLAG, "1")
    monkeypatch.setenv(config.OSIRIS_FLAG, "1")
    monkeypatch.setattr(config, "SEARXNG_URL", "")
    monkeypatch.setattr(config, "POLITE_PAUSE_S", 0.0)
    settings = make_settings(tmp_path)
    _app, svc = await start_app(settings, start_workers=False)
    adapter = StubAdapter()
    adapter.route(DOCS_ROBOTS, ROBOTS_ALLOW, headers={"content-type": "text/plain"})
    adapter.route(OTHER_ROBOTS, ROBOTS_ALLOW, headers={"content-type": "text/plain"})
    adapter.route("/robots.txt", ROBOTS_ALLOW, headers={"content-type": "text/plain"})
    osiris.store(svc).adapter = adapter
    try:
        yield Env(svc, settings, adapter)
    finally:
        await svc.stop()


@pytest.fixture
async def env(tmp_path, monkeypatch):
    async with _stand(tmp_path, monkeypatch) as stand:
        yield stand


async def read_page(env: Env, url: str = DOCS_PAGE, *, subject: str = SUBJECT,
                    force: bool = False):
    """Конвейер чтения целиком, с настоящим созданием источника-на-хост."""
    return await net.fetch_page(env.svc, url, subject,
                                ensure_host_source=sources.ensure_host_source,
                                force=force)


def spec_of(name: str):
    spec = REGISTRY.get(name)
    assert spec is not None, f"{name} не зарегистрирован: фича не установилась"
    return spec


# ------------------------------------------------------------- общий дедлайн


async def test_obshchiy_dedlayn_obryvaet_medlennyy_otvet(env, monkeypatch):
    """Вред: slowloris держит воркер владельца бесконечно, и ни одна проверка
    не срабатывает — обмен формально идёт.

    `timeout` у `psec.safe_get` — per-read, и он ПЕРЕЗАПУСКАЕТСЯ на каждом
    чтении. Сервер, отдающий по куску раз в полсекунды при per-read таймауте в
    десять секунд, законен с точки зрения каждой отдельной проверки и незаконен
    в целом. Останавливает его только общий дедлайн вокруг всего обмена.

    Поэтому здесь подменён сокет, а не транспорт: заглушка, «изображающая
    таймаут», доказывала бы лишь то, что тест умеет бросать исключение. Куски
    приходят быстрее per-read таймаута, значит оборвать обмен может ровно одно —
    `asyncio.timeout` вокруг `safe_get`.
    """
    chunks_sent = 0

    async def drip():
        nonlocal chunks_sent
        while True:
            chunks_sent += 1
            yield "<p>ещё немного текста</p>".encode("utf-8")
            await asyncio.sleep(0.05)

    mock = MockNet(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=drip()))
    mock.install(monkeypatch)

    adapter = net.WebFetchAdapter()
    started = time.monotonic()
    with pytest.raises(osiris.SourceUnavailableError) as caught:
        # Страховка самого теста: без дедлайна в модуле этот вызов не вернётся
        # НИКОГДА, и набор тестов повис бы вместо того, чтобы назвать причину.
        # `TimeoutError` отсюда — это не «сработало», а «не сработало у них».
        async with asyncio.timeout(5.0):
            await adapter.fetch_bytes(DOCS_PAGE, timeout=0.4)
    spent = time.monotonic() - started

    assert "дедлайн" in str(caught.value), "причина обязана называть дедлайн, а не сеть вообще"
    assert spent < 5.0, f"обмен оборван за {spent:.1f} с: общий дедлайн не действует"
    assert chunks_sent > 1, (
        "сервер отдал один кусок и замолчал — это уже ловит per-read таймаут; "
        "тест обязан проверять именно капающий ответ")

    # Контроль: тот же дедлайн НЕ мешает быстрому ответу. Без него «оборвалось»
    # означало бы «транспорт не работает вовсе», и тест прошёл бы на сломанном.
    fast = MockNet(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8"}, content=PAGE_HTML))
    fast.install(monkeypatch)
    ok = await net.WebFetchAdapter().fetch_bytes(DOCS_PAGE, timeout=0.4)
    assert ok.status == 200 and b"<p>" in ok.content


# ------------------------------------------------------ Content-Encoding (C2)


async def test_szhatyy_otvet_otvergaetsya_transportom(env, monkeypatch):
    """Вред: `Accept-Encoding: identity` — просьба, а не гарантия (C2).

    Враждебный сервер сжимает ответ независимо от заголовка. Дальше два разных
    несчастья: чужая библиотека распаковывает кусок ЦЕЛИКОМ до всякого потолка,
    а `psec.safe_get` копирует `Content-Encoding` в ответ, положив туда уже
    распакованное тело, — то есть любое последующее рассуждение о теле
    опирается на заведомо неверный заголовок.

    Проверяется, что тело не идёт в дело: отказ типизирован кодом
    `content_encoding`, а не растворён в общем «источник недоступен».
    """
    # Байты действительно сжаты: объявить `gzip` и прислать открытый текст
    # значило бы проверять обработку битого ответа, а вред здесь другой —
    # ответ ПРАВИЛЬНО сжат, чужая библиотека распакует его молча и целиком.
    mock = MockNet(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8",
                      "content-encoding": "gzip"},
        content=gzip.compress(PAGE_HTML.encode("utf-8"))))
    mock.install(monkeypatch)

    with pytest.raises(net.PageRefused) as caught:
        await net.WebFetchAdapter().fetch_bytes(DOCS_PAGE, timeout=5.0)
    assert caught.value.code == "content_encoding"

    # Второй случай — тот, в котором наша проверка ЕДИНСТВЕННАЯ: httpx знает
    # gzip и deflate и распакует их сам, но незнакомое ему имя кодировки он
    # пропускает как есть. Тогда сжатые байты дошли бы до извлекателя текста
    # под видом страницы, и отказать некому, кроме нас.
    passthrough = MockNet(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8",
                      "content-encoding": "compress"},
        content=PAGE_HTML))
    passthrough.install(monkeypatch)
    with pytest.raises(net.PageRefused) as second:
        await net.WebFetchAdapter().fetch_bytes(DOCS_PAGE, timeout=5.0)
    assert second.value.code == "content_encoding"
    assert "compress" in str(second.value), "владельцу обязано быть названо объявленное сжатие"

    # `identity` и пустое значение — это НЕ отказ: иначе проверка запрещала бы
    # обычный ответ, и «работает» означало бы «ничего не читается».
    plain = MockNet(lambda request: httpx.Response(
        200, headers={"content-type": "text/html; charset=utf-8",
                      "content-encoding": "identity"},
        content=PAGE_HTML))
    plain.install(monkeypatch)
    assert (await net.WebFetchAdapter().fetch_bytes(DOCS_PAGE, timeout=5.0)).status == 200


async def test_szhatyy_otvet_otvergaetsya_i_konveyerom_do_zapisi(env):
    """Вред: проверка, живущая ТОЛЬКО в адаптере, снимается вместе с адаптером.

    Адаптер — подменяемый шов. Если `Content-Encoding` смотрит только он, то
    любой другой транспорт (стенд, чужая реализация протокола, будущая правка)
    проносит сжатое тело мимо проверки. Поэтому здесь стенд отвечает сжатым
    ответом, а отказ обязан прийти от КОНВЕЙЕРА.

    И главное — «до чтения тела»: тело в ответе полноценное, из него получилась
    бы нормальная страница. Значит ни файла сырья, ни наблюдения на диске
    появиться не имеет права.
    """
    env.adapter.route(DOCS_PAGE, PAGE_HTML,
                      headers={"content-encoding": "br"})

    with pytest.raises(net.PageRefused) as caught:
        await read_page(env)

    assert caught.value.code == "content_encoding"
    assert env.raw_files() == [], "сжатое тело не имеет права оказаться на диске"
    assert env.page_observations() == [], "наблюдения о непрочитанной странице быть не может"


# ------------------------------------------------------------- Content-Type


async def test_chuzhoy_content_type_otkaz_do_razbora_tela(env):
    """Вред: `psec.safe_get` на `Content-Type` не смотрит вовсе.

    Без этой проверки PDF, архив или картинка уедут в извлекатель текста, и
    модель получит мусор с полным паспортом прочитанной страницы. Тело здесь —
    настоящий HTML, то есть отказ вызван ИМЕННО заявленным типом, а не тем, что
    разобрать не удалось: иначе тест проходил бы и на коде, который просто не
    умеет читать.
    """
    env.adapter.route(DOCS_PAGE, PAGE_HTML,
                      headers={"content-type": "application/pdf"})

    with pytest.raises(net.PageRefused) as caught:
        await read_page(env)

    assert caught.value.code == "content_type"
    assert "application/pdf" in str(caught.value), "владелец обязан видеть, что именно пришло"
    assert env.raw_files() == [], "тело неразрешённого типа на диск не пишется"
    assert env.page_observations() == []


# ------------------------------------------------------- канонизация адреса (C1)


async def test_zavershayushchaya_tochka_i_idn_ne_uhodyat_v_set(env, monkeypatch):
    """Вред (C1): «хост, который проверили» и «хост, к которому подключились» —
    две разные строки.

    `_PinnedBackend.connect_tcp` ищет имя в словаре пинов, а ключ задаёт httpx:
    он оставляет завершающую точку и разворачивает punycode обратно в юникод.
    Промах ключа сегодня — отказ, вчера был повторный резолв мимо всех проверок.
    Обе беды закрываются с нашей стороны одинаково: наружу уходит ТОЛЬКО форма
    без завершающей точки и с хостом в punycode.

    Поэтому утверждение теста именно такое, а не «адрес с точкой отвергается»:
    отвергать законный `example.org.` было бы отказом владельцу в его же
    адресе. Проверяется то, что действительно защищает, — до сокета не доходит
    ни одна форма имени, отличная от канонической.
    """
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"},
                                  content=ROBOTS_ALLOW)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=PAGE_HTML)

    mock = MockNet(serve)
    mock.install(monkeypatch)
    env.store.adapter = net.WebFetchAdapter()

    page = await read_page(env, "https://Docs.Example.Org./guide/api")
    assert page.host == DOCS_HOST
    assert page.url == DOCS_PAGE, "в паспорт пишется канонический запрошенный адрес"

    idn = await read_page(env, "https://пример.испытание/guide", subject="идн")
    assert idn.host == "xn--e1afmkfd.xn--80akhbyknj4f"

    hosts = mock.hosts()
    assert hosts, "стенд сокета не получил ни одного запроса: тест ничего не проверил"
    for host in hosts:
        assert host.isascii(), f"наружу ушло не-ASCII имя {host!r}: словарь пинов промахнётся"
        assert not host.endswith("."), f"наружу ушла завершающая точка в {host!r}"
        assert host == host.lower(), f"регистр имени не приведён: {host!r}"


async def test_host_ne_privodimyy_k_ascii_otvergaetsya_do_seti(env, monkeypatch):
    """Вред: адрес, чьё имя нельзя привести к ASCII, ушёл бы в сеть в одной
    форме, а проверялся бы в другой.

    Сюда же bidi-символы (B6): ветка одобрения держится на том, что владелец
    видит НАСТОЯЩИЙ адрес, а `\\u202e` переворачивает показ. Оба случая обязаны
    стоить ноль байт, поэтому счётчик обращений проверяется тоже: отказ,
    случившийся после запроса, — это не отказ до сети.
    """
    mock = MockNet(lambda request: httpx.Response(200, content=PAGE_HTML))
    mock.install(monkeypatch)
    env.store.adapter = net.WebFetchAdapter()

    # Символы в трёх адресах ниже НЕВИДИМЫ в исходнике, и это ровно их беда:
    # владелец в предпросмотре видит одно, транспорт получает другое. По
    # порядку: U+202E (переворот показа), U+200B (нулевая ширина внутри имени),
    # процентная последовательность %00 в имени хоста.
    for bad in ("https://docs.example\u202eorg/guide",
                "https://docs.exa\u200bmple.org/guide",
                "https://док%00с.example.org/guide"):
        with pytest.raises(net.PageRefused) as caught:
            await read_page(env, bad)
        assert caught.value.code in ("bad_url", "not_ascii_host"), bad

    # Отдельный случай: `canon_url` этот адрес принимает (http — законная схема
    # для канонизации), и отвергнуть его может только предпроверка. Без него
    # тест проходил бы и на конвейере, у которого предпроверки нет вовсе.
    with pytest.raises(net.PageRefused) as plain:
        await read_page(env, "http://docs.example.org/guide/api")
    assert plain.value.code == "not_https"

    assert mock.requests == [], "негодный адрес не имеет права стоить ни одного обращения"
    assert env.raw_files() == []

    # И та же проверка отдельно, как её зовёт чистый хук предпросмотра: одна
    # функция на «можно ли туда» в одобрении и в исполнении. Разъехавшись, они
    # дали бы владельцу одобрение одного адреса и поход по другому.
    assert net.precheck_target("https://docs.example\u202eorg/guide") is not None
    assert net.precheck_target(DOCS_PAGE) is None


# -------------------------------------------------------------- редиректы


async def test_krossaytovyy_redirekt_ne_vypolnyaetsya(env, monkeypatch):
    """Вред: редирект за пределы сайта выглядит как обычное чтение и приносит
    содержимое чужого хоста с паспортом запрошенного.

    Проверяется парой, а не одним случаем: редирект ВНУТРИ сайта обязан
    выполняться, иначе «защита» — это просто неработающие редиректы, и тест
    прошёл бы на коде, который не умеет их следовать вовсе.

    Сокет подменён, а `safe_get`, `validate_url` и разбор `Location` настоящие:
    именно они решают, куда можно, и проверять надо их.
    """
    evil_host = "collect.evil.example"

    def serve(request: httpx.Request) -> httpx.Response:
        host = request.url.raw_host.decode("ascii")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, headers={"content-type": "text/plain"},
                                  content=ROBOTS_ALLOW)
        if host == DOCS_HOST and request.url.path == "/offsite":
            return httpx.Response(302, headers={"location": f"https://{evil_host}/take"})
        if host == DOCS_HOST and request.url.path == "/onsite":
            return httpx.Response(302, headers={"location": f"https://www.{DOCS_HOST}/guide/api"})
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=PAGE_HTML)

    mock = MockNet(serve)
    mock.install(monkeypatch)
    adapter = net.WebFetchAdapter()

    with pytest.raises(PluginSecurityError) as caught:
        await adapter.fetch_bytes(f"https://{DOCS_HOST}/offsite", timeout=5.0)
    assert "allowlist" in str(caught.value).lower()
    assert all(evil_host not in host for host in mock.hosts()), (
        "к чужому хосту обращались: редирект был выполнен, а не отклонён")

    inside = await adapter.fetch_bytes(f"https://{DOCS_HOST}/onsite", timeout=5.0)
    assert inside.status == 200, "редирект внутри сайта обязан выполняться"
    assert inside.url == f"https://{DOCS_HOST}/onsite", (
        "конечный адрес транспорт не возвращает — в паспорте обязан стоять запрошенный")
    assert f"www.{DOCS_HOST}" in mock.hosts()


# ---------------------------------------------------------------- robots.txt


async def test_nedostupnyy_robots_zapreshchaet_chtenie(env):
    """Вред: «раз не запрещено, значит можно» — это разрешение, выданное чужой
    поломкой.

    Недоступный `robots.txt` (500, обрыв, пустой ответ) обязан быть ОТКАЗОМ, и
    отказ обязан случиться ДО обращения за страницей: иначе байты уже потрачены,
    а запрет только потом «принят к сведению».
    """
    env.adapter.routes.clear()
    env.adapter.route(DOCS_ROBOTS, "", status=500, headers={"content-type": "text/plain"})
    env.adapter.route(DOCS_PAGE, PAGE_HTML)

    with pytest.raises(osiris.RobotsDisallowError) as caught:
        await read_page(env)

    assert "robots" in str(caught.value).lower()
    assert env.adapter.page_calls() == [], (
        "за страницей сходили до того, как выяснили право её читать")
    assert env.raw_files() == []
    assert env.page_observations() == []


async def test_robots_tyanetsya_tem_zhe_transportom_i_temi_zhe_allowed_hosts(env, monkeypatch):
    """Вред (C3): для `robots.txt` анти-rebinding выключен полностью.

    Это самый частый запрос конвейера — он идёт перед КАЖДЫМ чтением нового
    пути, то есть чаще самой страницы. Тянись он мимо `allowed_hosts`, редирект
    с robots уводил бы на произвольный хост, и защита страницы ничего бы не
    значила: до неё дело просто не дошло бы.

    Проверяется на настоящем транспорте: `robots_allows` зовёт `adapter.fetch`,
    а он обязан пройти тем же путём, что и страница, — с тем же набором хостов и
    тем же потолком тела.
    """
    seen: list[dict[str, Any]] = []

    async def recording_safe_get(url, **kw):
        seen.append({"url": url, **kw})
        return httpx.Response(
            200,
            headers={"content-type": "text/plain" if url.endswith("/robots.txt")
                     else "text/html; charset=utf-8"},
            content=ROBOTS_ALLOW if url.endswith("/robots.txt") else PAGE_HTML)

    monkeypatch.setattr(psec, "safe_get", recording_safe_get)
    env.store.adapter = net.WebFetchAdapter()

    await read_page(env)

    robots_calls = [c for c in seen if c["url"] == DOCS_ROBOTS]
    page_calls = [c for c in seen if c["url"] == DOCS_PAGE]
    assert robots_calls, "robots.txt вообще не запрашивался: fail-closed держится на нём"
    assert page_calls, "страница не запрашивалась: тест не дошёл до сравнения"

    expected = net.same_site(DOCS_HOST)
    assert robots_calls[0]["allowed_hosts"] == expected == page_calls[0]["allowed_hosts"], (
        "robots тянется другим набором хостов, чем страница")
    assert robots_calls[0]["max_bytes"] == config.PAGE_MAX_BYTES
    assert robots_calls[0].get("allow_private") in (False, None), (
        "приватная дверь (allow_private) открыта не только для своего SearXNG")


# ------------------------------------------------------------- лимит на хост


async def test_limit_chastoty_dejstvuet_i_on_pohostovyy(env):
    """Вред двусторонний, и обе стороны одинаково плохи.

    Если лимита нет — мы устраиваем чужому сайту маленькую DDoS-атаку и получаем
    бан вместо жалобы. Если лимит ОБЩИЙ — десять чтений одного сайта закрывают
    доступ ко всем остальным, и владелец видит «лимит исчерпан» там, где к
    хосту никто не обращался ни разу.

    Ключ лимита у OSIRIS — `source.id`, а источник здесь создаётся на каждый
    хост, поэтому лимит впервые становится похостовым. Адреса разные:
    повторное чтение того же адреса ушло бы в кэш и лимита не тронуло бы.
    """
    env.adapter.route("/guide/", PAGE_HTML)
    env.adapter.route(OTHER_PAGE, PAGE_HTML)

    for number in range(config.HOST_RATE_PER_MIN):
        page = await read_page(env, f"https://{DOCS_HOST}/guide/{number}")
        assert page.status == 200, f"чтение {number} отказало до исчерпания лимита"

    with pytest.raises(osiris.RateLimitedError) as caught:
        await read_page(env, f"https://{DOCS_HOST}/guide/999")
    assert DOCS_HOST in str(caught.value), "владельцу обязан быть назван хост, а не источник"

    other = await read_page(env, OTHER_PAGE, subject="другой сайт")
    assert other.status == 200, "лимит одного хоста закрыл доступ к другому: он не похостовый"


# ------------------------------------------------------------------- кэш (E2)


async def test_vtoroe_chtenie_iz_arhiva_ne_dergaet_set_i_nazyvaet_vozrast(env):
    """Вред: воспоминание, выданное за факт.

    Попадание в кэш обязано быть видно в выводе. Без слова «из архива» и без
    возраста строка «получено» читается как «сейчас», и модель уверенно
    рассказывает владельцу про страницу, которой, возможно, уже нет.

    Проверяются обе половины: сети действительно не было (счётчик обращений не
    вырос) И это НАПИСАНО в тексте, который увидит модель. Первое без второго —
    молчаливая подмена, второе без первого — надпись без содержания.
    """
    env.adapter.route(DOCS_PAGE, PAGE_HTML)
    led = ledger.Ledger.load(env.svc, 5)
    token = led.mint(DOCS_PAGE, kind="owner", subject=SUBJECT, origin="owner:test")
    assert token, "адрес не отчеканен: тест не дошёл до проверки кэша"
    led.save()

    first = await execute_tool(spec_of("web.open"), {"ref": token}, env.ctx())
    assert first.error is False, first.content
    after_first = len(env.adapter.page_calls())
    assert after_first == 1, "первое чтение обязано сходить в сеть"
    assert "получено: сейчас" in first.content, (
        "свежее чтение обязано называть себя свежим, иначе разницы с архивом нет")

    second = await execute_tool(spec_of("web.open"), {"ref": token}, env.ctx())
    assert second.error is False, second.content
    assert len(env.adapter.page_calls()) == after_first, (
        "второе чтение в пределах TTL сходило в сеть: кэша нет, и `fresh` бессмыслен")
    assert "из архива, возраст" in second.content, (
        "чтение из архива выдано за свежее: в выводе нет ни слова про возраст")

    # Кэш управляет ПОВТОРНЫМ ИСПОЛЬЗОВАНИЕМ, а не правом перечитать: `force`
    # обязан идти в сеть, иначе перепроверка цитаты — отчёт о непроведённой
    # проверке (E5).
    forced = await read_page(env, DOCS_PAGE, force=True)
    assert forced.from_cache is False
    assert len(env.adapter.page_calls()) == after_first + 1, (
        "force=True откатился к кэшу: перепроверка ничего не проверяет")


async def test_fresh_u_poiska_obhodit_kesh(env):
    """Вред (E2): `fresh` — мёртвый параметр.

    `normalize_source` считает `cache_ttl_seconds` как `max(0, int(... or 0))`,
    то есть отсутствие ключа в декларации даёт НОЛЬ: кэша нет вовсе, `fresh`
    ничего не меняет, а каждый повтор запроса — новые байты наружу и новый
    расход чужого лимита.

    Три вызова подряд: сеть → архив → снова сеть по требованию. Именно тройка,
    а не пара: без среднего шага «пошёл в сеть» неотличимо от «кэш не работает».
    """
    body = json.dumps(["солнце", ["Солнце"], ["звезда"],
                       ["https://ru.wikipedia.org/wiki/Sun"]], ensure_ascii=False)
    env.adapter.route("/w/api.php", body, headers={"content-type": "application/json"})

    first = await execute_tool(spec_of("web.search"), {"query": "что такое солнце"}, env.ctx())
    assert first.error is False, first.content
    after_first = len(env.adapter.page_calls())
    assert after_first == 1, "первый поиск обязан сходить в сеть"

    second = await execute_tool(spec_of("web.search"), {"query": "что такое солнце"}, env.ctx())
    assert second.error is False, second.content
    assert len(env.adapter.page_calls()) == after_first, (
        "повторный запрос в пределах TTL ушёл наружу: кэша нет")
    assert "из архива, возраст" in second.content, (
        "выдача из архива выдана за свежую")

    third = await execute_tool(spec_of("web.search"),
                               {"query": "что такое солнце", "fresh": True}, env.ctx())
    assert third.error is False, third.content
    assert len(env.adapter.page_calls()) == after_first + 1, (
        "fresh=true не пошёл в сеть: параметр мёртвый")
    assert "получено: сейчас" in third.content


# ------------------------------------------------- сырьё по содержимому (D1)


async def test_povtornoe_chtenie_ne_zatiraet_staroe_syrye(env):
    """Вред (D1): перепроверка уничтожает ровно ту улику, которую проверяет.

    Ключ сырья у OSIRIS — `sha256(source_id|url)`, то есть второе чтение того же
    адреса ПЕРЕЗАПИСЫВАЕТ файл, на который уже ссылается выданная цитата. Здесь
    ключ — подпись тела, поэтому новое чтение создаёт новую запись рядом со
    старой.

    Проверяется не только наличие двух файлов, но и то, что по старой подписи
    по-прежнему читается СТАРЫЙ текст: две записи, в которых лежит одно и то же
    новое тело, — это та же потеря, только с двумя именами.
    """
    env.adapter.route(DOCS_PAGE, PAGE_HTML)
    first = await read_page(env)
    old_digest = first.raw_digest
    old_text = first.extraction.text
    assert "Руководство" in first.extraction.title

    env.adapter.routes.clear()
    env.adapter.route("/robots.txt", ROBOTS_ALLOW, headers={"content-type": "text/plain"})
    env.adapter.route(DOCS_PAGE, PAGE_HTML_CHANGED)

    second = await read_page(env, force=True)
    assert second.raw_digest != old_digest, (
        "подпись сырья не изменилась при изменившемся теле: адресация не по содержимому")

    kept = env.store.read_raw(old_digest)
    assert kept is not None, "старое сырьё стёрто: доказательство под выданной цитатой исчезло"
    assert "версия вторая" not in kept["body"], (
        "по старой подписи лежит НОВОЕ тело: запись перезаписана, а не добавлена, "
        "и цитата теперь ссылается на текст, которого в тот раз не было")

    revived = await net.read_cached(env.svc, old_digest)
    assert revived is not None
    assert revived.extraction.text == old_text, (
        "перечитывание по старой подписи отдало другой текст: смещения цитат указывают мимо")

    assert len(env.raw_files()) == 2, "два чтения обязаны дать две записи, а не одну"
    assert len(env.page_observations()) == 2, "второе чтение обязано добавить наблюдение"


# ------------------------------------------------------- стенд и живая сеть (D5)


async def test_podmenennyy_adapter_ne_pomechaet_istochnik_zhivym(env):
    """Вред (D5): зелёный стенд, отмытый в след как настоящая сеть.

    Если подменённый адаптер может выставить источнику «проверен живьём», то
    отчёт «источник работает» означает «фикстура сработала», а цитата из
    стендового наблюдения неотличима от сетевой. Тогда весь слой происхождения
    доказывает только то, что тесты написаны.

    Пара обязательна: со стендом статус не меняется, с адаптером, объявившим
    себя живым, — меняется. Без второй половины утверждение прошло бы и на
    коде, который вообще никогда ничего не помечает.
    """
    env.adapter.route(DOCS_PAGE, PAGE_HTML)
    page = await read_page(env)

    assert page.transport == "stub"
    source = env.host_source()
    assert source is not None, "источник-на-хост не создан: конвейер не дошёл до пометки"
    assert source.live_status == "not_verified_live", (
        "стенд объявил источник проверенным живьём")
    assert source.live_checked_at is None

    observation = env.page_observations()[0]
    assert observation["value"]["transport"] == "stub", (
        "в паспорте наблюдения стенд выдан за сеть")

    live = LiveStubAdapter()
    live.route("/robots.txt", ROBOTS_ALLOW, headers={"content-type": "text/plain"})
    live.route(OTHER_PAGE, PAGE_HTML)
    env.store.adapter = live
    other = await read_page(env, OTHER_PAGE, subject="живая сеть")

    assert other.transport == "live"
    assert env.host_source(OTHER_HOST).live_status == "live_ok", (
        "настоящая сеть не может подтвердить источник — тогда поле бесполезно")


# ------------------------------------------------- три исхода поиска (E1)


def _searxng_backend() -> sources.Backend:
    """Backend формы SearXNG, объявленный как обычный источник OSIRIS.

    Свой инстанс владельца живёт на приватном адресе и потому опрашивается
    отдельной дверью, но РАЗБОР выдачи у него общий с остальными. Здесь
    объявлен источник той же формы на внешнем адресе — так проверяется путь,
    которым выдача действительно доходит до модели: сеть → `collect` → парсер →
    исход → текст. Разбор в отрыве от этого пути доказывал бы только то, что
    функция разбора умеет читать словарь.
    """
    decl = {
        "id": "searx-test",
        "category": "A",
        "method": "api",
        "base_url": "https://searx.example.org",
        "auth_mode": "none",
        "rate_limit_per_min": 30,
        "license": "условия собственного инстанса владельца",
        "provides": ["search.query", "search.result"],
        "not_provides": ["выдачу Google, Bing и Яндекса"],
        "tos_checked_at": sources.TOS_CHECKED_AT,
        "parser": "web.serp_searxng",
        "path_template": "/search?format=json&q={subject}",
        "cache_ttl_seconds": config.CACHE_TTL_SEARCH,
        "default_confidence": sources.CONFIDENCE_BACKEND,
        "contact": osiris.USER_AGENT,
        "notes": "стендовый инстанс формы SearXNG",
    }
    return sources.Backend(
        id="searx-test",
        honest_capability="общий веб-поиск через инстанс формы SearXNG",
        shape="searxng", via="osiris", keyless=True, order=1,
        hits_path="results", field_title="title", field_url="url",
        field_snippet="content", field_engine="engine", decl=decl)


async def test_dvizhki_ne_otvetili_eto_ne_nichego_ne_naydeno(env, monkeypatch):
    """Вред (E1), самый дорогой из «нечестных»: сломанный поиск отдаётся как
    факт об устройстве мира.

    SearXNG при капче в апстриме отвечает HTTP 200 и
    `{"results": [], "unresponsive_engines": [["google","CAPTCHA"]]}`. Выдать
    это как «ничего не найдено» — значит соврать: поиск НЕ СОСТОЯЛСЯ, движки
    молчали, и владелец обязан узнать именно это. Разница не стилистическая:
    из «не найдено» модель делает вывод об отсутствии, из «не состоялся» —
    вывод о неисправности.

    Второе требование того же исхода: байты ушли с машины владельца — значит
    след обязан остаться. Наблюдение `search.query` пишется независимо от
    непустоты выдачи, иначе неудачный поиск не виден в эпизодах вовсе.

    Тройка исходов проверяется вместе, потому что порознь каждый прошёл бы на
    коде, который печатает одну и ту же строку всегда.
    """
    backend = _searxng_backend()
    monkeypatch.setattr(sources, "BACKENDS", (backend,))
    monkeypatch.setattr(sources, "BACKENDS_BY_ID", {backend.id: backend})

    captcha = json.dumps({"results": [],
                          "unresponsive_engines": [["google", "CAPTCHA"]]})
    nothing = json.dumps({"results": []})
    found = json.dumps({"results": [
        {"title": "Ответ", "url": "https://docs.example.org/guide/api",
         "content": "нужный кусок", "engine": "duckduckgo"}]})

    env.adapter.route("q=%D0%BA%D0%B0%D0%BF%D1%87%D0%B0", captcha,
                      headers={"content-type": "application/json"})
    env.adapter.route("q=%D0%BF%D1%83%D1%81%D1%82%D0%BE", nothing,
                      headers={"content-type": "application/json"})
    env.adapter.route("/search", found, headers={"content-type": "application/json"})

    down = await execute_tool(spec_of("web.search"), {"query": "капча"}, env.ctx())
    assert down.error is False, down.content
    assert "поиск НЕ состоялся" in down.content, (
        "движки молчали, а владельцу сказано что-то другое")
    assert "движки не ответили" in down.content
    assert "google" in down.content, "имя молчавшего движка обязано быть названо"
    assert "engines_down" in down.content, "исход обязан быть назван машинным кодом тоже"
    assert config.MSG_EMPTY_RESULT not in down.content, (
        "сломанный поиск выдан за факт об отсутствии данных")

    empty = await execute_tool(spec_of("web.search"), {"query": "пусто"}, env.ctx())
    assert empty.error is False, empty.content
    assert config.MSG_EMPTY_RESULT in empty.content, (
        "ответивший движок с пустой выдачей — это НЕ «поиск не состоялся»")
    assert "поиск НЕ состоялся" not in empty.content, (
        "пустая выдача выдана за неисправность: два разных факта слиты в один")

    ok = await execute_tool(spec_of("web.search"), {"query": "ответ"}, env.ctx())
    assert ok.error is False, ok.content
    assert "выдача получена" in ok.content, (
        "исправный поиск отдан как отказ: три исхода слиплись в один")

    # След запроса: байты ушли — запись обязана быть, и она обязана нести
    # именно тот исход, о котором сказано модели.
    trail = env.store.observations("капча")
    heads = [o for o in trail if o.get("attribute") == "search.query"]
    assert heads, "неудачный поиск не оставил следа: владельцу нечего посмотреть"
    assert heads[0]["value"]["outcome"] == "engines_down"
    assert heads[0]["value"]["results"] == 0
    assert heads[0]["source_id"] == backend.id
    assert heads[0]["raw_ref"].startswith("raw:"), (
        "у следа нет ссылки на сырьё: перепроверить утверждение нечем")


# ------------------------------------------- приватная дверь к своему SearXNG


async def test_svoy_searxng_oprashivaetsya_privatnoy_dveryu(env, monkeypatch):
    """ЭТОТ ТЕСТ ПАДАЕТ НА ТЕКУЩЕМ КОДЕ, и это найденный дефект, а не подгонка.

    Вред: владельцу сказано одно, а делается другое. При заданном
    `BOSSMAN_WEB_SEARXNG_URL` готовность печатает «Общий веб-поиск доступен:
    searxng-local», `pick_backend` отдаёт этот backend первым — и КАЖДЫЙ поиск
    возвращает «источник недоступен (private_door: …)», ни разу не обратившись
    к инстансу владельца. Единственный объявленный честный путь к открытому
    вебу не работает вовсе, причём отказ выглядит как поломка чужого сервера, а
    не как неподключённая ветка у нас.

    Механика: `net.searxng_fetch` — единственное место пакета с
    `allow_private=True` — не вызывается НИОТКУДА. `tools.tool_search` зовёт
    `sources.run_search`, а тот для `via="private_door"` сразу отдаёт код
    `private_door`; `tool_search` переводит всякий незнакомый код в
    `source_unavailable`. Ветки, которая опрашивает приватную дверь и разбирает
    её тело через `parse_serp`, в пакете нет.

    Тест утверждает минимум, который обязан выполняться при любом решении
    ведущего: если готовность объявляет общий веб-поиск доступным, то поиск
    обязан хотя бы ОБРАТИТЬСЯ к настроенному инстансу. Если ведущий решит, что
    дверь пока не подключается, честной правкой будет снять обещание из
    `readiness` — и тогда упадёт первое утверждение, а не второе.
    """
    monkeypatch.setattr(config, "SEARXNG_URL", "http://127.0.0.1:8888")
    env.adapter.route("127.0.0.1:8888", json.dumps({"results": [
        {"title": "Солнце", "url": "https://docs.example.org/guide/api",
         "content": "звезда", "engine": "duckduckgo"}]}),
        headers={"content-type": "application/json"})

    ready = sources.readiness(env.svc)
    assert ready.get("general_web"), (
        "готовность больше не обещает общий веб-поиск — тогда и это утверждение "
        "надо снимать вместе с обещанием")

    result = await execute_tool(spec_of("web.search"),
                                {"query": "что такое солнце"}, env.ctx())

    assert any("127.0.0.1:8888" in call for call in env.adapter.calls), (
        "поиск с настроенным SearXNG не обратился к нему ни разу: приватная дверь "
        "net.searxng_fetch не вызывается ниоткуда, а владельцу при этом обещан "
        "работающий общий веб-поиск")
    assert "источник недоступен" not in result.content, (
        "неподключённая ветка у нас выдана владельцу за неисправность его сервера")
