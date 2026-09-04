"""web_research: единственная дверь наружу и конвейер чтения страницы.

Файл отвечает на два вопроса и ни на один больше: «как достать байты, ничего
при этом не открыв нараспашку» и «в каком ПОРЯДКЕ проверить страницу, чтобы
отказ случился раньше траты». Что именно открывать, зачем и по чьей воле —
решают `tools.py` (чистые `effect_hook`) и владелец; здесь только исполнение
уже принятого решения.

Чего этот файл НЕ делает и делать не должен:

  * **не заводит второго слоя SSRF.** Проверка адреса, резолв и pinned-коннект
    целиком берутся у `plugin_security` (`validate_url`, `resolve_pinned_ip`,
    `PinnedTransport`). Второй слой означал бы две границы вместо одной, и
    однажды они разойдутся — а расхождение границ это и есть дыра;
  * **не знает конечного адреса после редиректов и не притворяется, что знает.**
    `psec.safe_get` собирает `httpx.Response(status, headers, content)` без
    `url` и без `request`, конечный адрес наружу не выходит. Поэтому и в
    `RawResponse.url`, и в паспорте наблюдения стоит ЗАПРОШЕННЫЙ канонический
    адрес, и это сказано прямо (`config.MSG_REQUESTED_URL_ONLY`), а не спрятано;
  * **не чеканит ref'ов, не считает бюджет прогона и не рисует текст модели.**
    Это `ledger.py`, `render.py` и `tools.py`. Отсюда наружу идут ФАКТЫ
    (`PageRecord`) и типизированные отказы, а не готовые фразы;
  * **не отправляет cookie и не сохраняет `Set-Cookie`, не ходит POST'ом, не
    пользуется прокси и не даёт модели настроить транспорт.** Все потолки,
    дедлайны, заголовки и число редиректов — константы `config`, ни одна из них
    не выведена в `input_schema` инструмента;
  * **не помечает источник проверенным живьём сам.** Это делает
    `osiris._mark_live` и только когда `adapter.live` истинно. Подменённый в
    тесте адаптер обязан оставить источник `not_verified_live`, иначе зелёный
    стенд означал бы «источник работает», хотя наружу никто не ходил.

Три вещи, которые здесь сделаны иначе, чем «как обычно», и каждая по причине:

1. **Общий дедлайн вокруг `safe_get`.** У `safe_get` общего дедлайна нет: его
   `timeout` — per-read, и он ПЕРЕЗАПУСКАЕТСЯ на каждом чтении, поэтому сервер,
   отдающий по байту раз в девять секунд, держит соединение сутками при
   `timeout=10`. Снаружи вызова стоит `asyncio.timeout`, и он ограничивает весь
   обмен целиком, включая редиректы.

2. **Канонизация адреса ДО каждого обращения к сети (поправка C1).**
   `_PinnedBackend.connect_tcp` ищет хост в словаре пинов; ключ, под которым
   транспорт спросит, задаёт httpx, а он оставляет завершающую точку и
   разворачивает punycode обратно в юникод. Сегодня промах ключа в чужом файле
   — отказ (`host not pinned, refusing to resolve again`), то есть fail-closed,
   и раньше был fail-open (повторный резолв мимо проверки). Обе беды лечатся с
   нашей стороны одинаково: наружу уходит только форма без завершающей точки и
   с хостом в punycode, а если хост после канонизации всё ещё не ASCII — адрес
   не уходит вообще. Это не косметика и не забота о чужом файле: пока
   канонизации нет, «хост, который проверили» и «хост, к которому подключились»
   — две разные строки, и любое рассуждение о том, куда мы сходили, повисает.

3. **Сырьё адресуется ПО СОДЕРЖИМОМУ (поправка D1).** `OsirisStore.raw_key` —
   это `sha256(source_id|url)`, то есть повторное чтение того же адреса
   ПЕРЕЗАПИСЫВАЕТ файл, на который уже ссылается выданная цитата: перепроверка
   уничтожала бы ровно ту улику, которую проверяет. Здесь ключ — `sha256` тела
   ответа, новое чтение создаёт новую запись, старая остаётся навсегда. Ценой
   этого кэшу нужен указатель «какое тело последним пришло с этого адреса»: он
   лежит в `<data_dir>/osiris/web_runs/_pages/`, не содержит НИ ОДНОГО байта
   содержимого и потерять его безопасно — промах указателя означает лишний
   поход в сеть, а не потерю доказательства.

Честный предел, названный вслух (поправка D3): `allowed_hosts` в
`psec.validate_url` сверяется СУФФИКСОМ — `any(host == d or host.endswith("." + d))`.
Мы передаём ровно `{host, host без www., www. + host}`, но суффиксное правило
всё равно означает, что редирект на ПОДДОМЕН регистрируемого домена (например,
`evil.example.com` при запросе `example.com`) будет выполнен. Уточнить это со
своей стороны нечем: конечный адрес транспорт не возвращает, а собственная
проверка «после `safe_get`» проверять было бы нечего. Поэтому запись в паспорте
— всегда ЗАПРОШЕННЫЙ адрес, и никакое место модуля не утверждает, будто знает,
где мы оказались в итоге.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
from dataclasses import dataclass, replace as dc_replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx

from ... import html_text
from ... import plugin_security as psec
from ...db import utcnow
from ...plugin_security import PluginSecurityError
from .. import osiris
from . import config

__all__ = [
    "MIN_PAGE_TEXT_CHARS", "EXTRACT_MAX_CHARS", "QUOTABLE_REPLACE_RATIO",
    "PAGES_DIRNAME", "SEARXNG_PATHS", "SEARXNG_PARAM_KEYS",
    "PageRefused", "RawResponse", "PageRecord", "EnsureHostSource",
    "WebFetchAdapter", "KeyedFetchAdapter",
    "same_site", "host_of", "https_required", "precheck_target",
    "content_type_ok", "content_encoding_ok", "header_value",
    "robots_ok", "robots_cache_clear",
    "searxng_fetch", "is_searxng_url", "split_searxng_target",
    "fetch_page", "read_cached", "install_adapter",
    "raw_bytes_used", "raw_budget_state", "prune_pointers",
]

# Код отказа → HTTP-статус для ручек владельца. Таблица, а не «по месту»:
# один и тот же отказ обязан выглядеть одинаково из инструмента и из ручки.
_REFUSAL_STATUS = {
    "bad_url": 400,
    "not_https": 403,
    "not_ascii_host": 403,
    "serp_denied": 403,
    "exfil_sink": 403,
}

# Ниже этого порога «страница» — это меню, капча или заглушка. Отдавать такое
# модели как прочитанный документ значит выдать пустоту за факт.
MIN_PAGE_TEXT_CHARS = 200

# Параметры извлечения пишутся в паспорт наблюдения (поправка D6): смещение
# цитаты записано В ИЗВЛЕЧЁННЫЙ текст, а он зависит от `max_chars`. Показывать
# цитату нужно с ТЕМИ ЖЕ параметрами, иначе смещение указывает мимо.
EXTRACT_MAX_CHARS = html_text.MAX_TEXT_CHARS

# Выше этой доли символов-замен текст декодирован неверно, и цитировать из него
# нельзя: «дословная» цитата из мусора — это выдумка с паспортом.
QUOTABLE_REPLACE_RATIO = 0.02

PAGES_DIRNAME = "_pages"                 # указатели url → дайджест тела, без содержимого

# Приватная дверь. Путь и набор параметров фиксированы списком, а не «проверкой
# на плохое»: аргумент модели не имеет права выбирать ни путь, ни имя параметра.
SEARXNG_PATHS = frozenset({"/search"})
SEARXNG_PARAM_KEYS = frozenset({
    "q", "format", "language", "categories", "engines", "time_range",
    "pageno", "safesearch",
})

_ROBOTS_CACHE_MAX = 512
_HOST_PAUSE_MAX = 4096


# --------------------------------------------------------------- отказы

class PageRefused(osiris.OsirisError):
    """Отказ конвейера чтения с МАШИННО-ЧИТАЕМОЙ причиной.

    Форма скопирована с `osiris.ForbiddenSourceError` намеренно: код живёт на
    исключении, чтобы вызывающий не разбирал текст ошибки регулярками, а второй
    иерархии ошибок в проекте не заводится — это подкласс осирисовской, поэтому
    и ручки, и рендер обрабатывают его уже написанным кодом.

    Почему исключение, а не возврат данными: конвейер чтения — это одна длинная
    последовательность условий, и «отдать данными» на каждом шаге означало бы
    проверку возврата после каждой строки, то есть ровно то место, где однажды
    забудут проверить. Границей модуля отказ становится ДАННЫМИ: `tools.py`
    ловит `OsirisError` и превращает в `ToolResult`, прогон не падает.
    """

    def __init__(self, message: str, *, code: str, http_status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


# --------------------------------------------------------------- структуры

@dataclass(frozen=True)
class RawResponse:
    """Ответ в терминах модуля: байты, а не текст.

    Байты, а не `str`, потому что кодировку определяет `html_text.decode_body`
    по заголовку, BOM и `<meta>`, а `httpx.Response.text` угадывает её сам и
    молча — и тогда «доля символов-замен», на которой держится запрет цитировать
    из битого текста, оказывается невычислимой.

    `url` — ЗАПРОШЕННЫЙ канонический адрес, а НЕ конечный после редиректов:
    конечный `safe_get` не возвращает вовсе.
    """

    status: int
    content: bytes
    headers: dict[str, str]              # ключи приведены к нижнему регистру
    url: str


@dataclass(frozen=True)
class PageRecord:
    """Всё, что известно о прочитанной странице, как ФАКТЫ, а не как текст.

    Отдельный тип вместо словаря нужен ровно для одного: поля `transport`,
    `from_cache`, `age_seconds` и `quotable` невозможно «забыть напечатать»,
    когда они есть в структуре — а именно на их отсутствии держатся три самых
    неприятных вида лжи (стенд выдан за сеть, кэш выдан за свежее чтение,
    цитата из мусора выдана за дословную).
    """

    url: str                             # запрошенный канонический адрес
    host: str
    source_id: str
    status: int
    raw_digest: str                      # sha256 ТЕЛА (D1), он же имя файла сырья
    body_sha256: str                     # sha256 декодированного текста
    text_sha256: str                     # sha256 ИЗВЛЕЧЁННОГО текста (им подписан паспорт)
    charset: str
    replace_ratio: float
    mojibake: bool
    quotable: bool                       # False = из этого текста цитировать нельзя
    extraction: Any                      # html_text.Extraction
    extract_max_chars: int
    content_type: str
    from_cache: bool
    fetched_at: str                      # ISO, время СЕТЕВОГО чтения, не показа
    age_seconds: float
    transport: str                       # live | stub — берётся у adapter.live (D5)
    robots_note: str
    bytes_read: int                      # 0 на попадании в кэш: байтов не было
    net_seconds: float                   # то же самое; расход бюджета считает ledger
    observation_id: str


# Инъекция зависимости вместо импорта: `sources.py` импортирует ЭТОТ файл
# (ему нужен транспорт), поэтому обратный импорт замкнул бы цикл. Конвейер
# обязан создавать источник-на-хост САМ и в правильном месте порядка, поэтому
# функция приходит параметром, а не вызывается снаружи до `fetch_page`.
EnsureHostSource = Callable[[osiris.OsirisStore, str], osiris.Source]


# --------------------------------------------------------------- адреса

def same_site(host: str) -> set[str]:
    """`{host, host без www., www. + host}` — ровно то, что уходит в `allowed_hosts`.

    Поддомены сюда не добавляются, но и не спасают: правило в `validate_url`
    суффиксное (см. шапку модуля), поэтому набор фактически разрешает поддомены
    регистрируемого домена. Набор всё равно передаётся ВСЕГДА: с ним следующий
    hop падает на понятной проверке `validate_url`, а без него — на pinning'е,
    то есть беда одного класса выглядела бы как беда другого.
    """
    clean = (host or "").strip().lower().rstrip(".")
    if not clean:
        return set()
    bare = clean[4:] if clean.startswith("www.") else clean
    return {clean, bare, f"www.{bare}"}


def host_of(url: str) -> str:
    """Хост канонического адреса. Пустая строка — адрес негоден."""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def https_required(base_url: str) -> str | None:
    """Причина отказа, если адрес источника не `https://`. `None` — годится.

    Поправка A5. `psec.validate_url` разрешает `http` наравне с `https`, и это
    правильно для него: он про то, КУДА нельзя ходить, а не про то, кому можно
    верить. Разница существенна именно здесь: ответ по `http` можно не только
    прочитать по дороге, но и ПОДМЕНИТЬ, и тогда инъекция приезжает с паспортом
    доверенного источника — с его лицензией, его `honest_capability` и его
    правом чеканить `w`-ref. Источник без https обязан выключаться совсем, а не
    «работать с оговоркой».

    Функция живёт здесь, потому что это свойство транспорта, а применяется в
    `sources.py` НА ЭТАПЕ ОБЪЯВЛЕНИЯ: отказ на объявлении означает, что
    негодного источника нет в реестре вовсе, а отказ на вызове означал бы, что
    он там есть и однажды кто-то позовёт его в обход проверки.

    Единственное исключение — `BOSSMAN_WEB_SEARXNG_URL`: он ведёт на машину
    владельца, где сертификата обычно нет, и его читает только `searxng_fetch`,
    который сюда не заходит. Исключение названо в `config._env_searxng` и
    сделано ровно в одном месте.
    """
    raw = (base_url or "").strip()
    if not raw:
        return "у источника нет base_url: объявлять нечего"
    scheme = urlsplit(raw).scheme.lower()
    if scheme == "https":
        return None
    return (f"источник объявлен по {scheme or '(без схемы)'}: ответ по незащищённому "
            f"каналу можно подменить по дороге, и подделка придёт с паспортом "
            f"доверенного источника. Требуется https://")


def precheck_target(url: str) -> tuple[str, str] | None:
    """(код, человеческая причина) отказа ДО сети; `None` — можно идти дальше.

    Тут собраны только те отказы, которые видны из самого адреса и потому не
    имеют права стоить ни одного байта: негодная форма, не https, страница
    выдачи поисковика, сток утечки. Всё, что требует ответа сервера
    (Content-Type, robots, размер), проверяется дальше по конвейеру.

    Ту же функцию зовёт чистый `open_effect` в `tools.py` — и это не
    дублирование, а обратное: одна функция на «можно ли туда» и в предпросмотре
    одобрения, и в исполнении. Разъехавшись, эти две проверки дали бы владельцу
    одобрение одного адреса и поход по другому.
    """
    try:
        canon = html_text.canon_url(url)
    except (ValueError, UnicodeError) as exc:
        return "bad_url", f"адрес отклонён до сети: {exc}"

    parts = urlsplit(canon)
    if parts.scheme != "https":
        return "not_https", ("страница читается только по https: ответ по http можно "
                             "подменить по дороге, а подделку мы покажем владельцу "
                             "как прочитанный источник")
    host = (parts.hostname or "").lower()
    if not host:
        return "bad_url", "в адресе нет хоста"
    if not host.isascii() or host.endswith("."):
        # `canon_url` это уже гарантирует. Проверка оставлена намеренно: она
        # стоит наносекунду и переживёт любую будущую правку канонизации, а
        # цена ошибки здесь — расхождение проверенного и подключённого хоста.
        return "not_ascii_host", ("хост не приводится к ASCII без завершающей точки; "
                                  "проверенное имя и имя, по которому пойдёт коннект, "
                                  "разошлись бы — идти туда нельзя")
    why = config.serp_reason(canon)
    if why:
        return "serp_denied", why
    if config.is_exfil_sink(host):
        return "exfil_sink", ("это сток утечки: его единственная работа — принять и "
                              "показать отправителю то, что ему прислали. Открытие "
                              "такого адреса это не чтение, а отправка")
    return None


# --------------------------------------------------------------- заголовки

def header_value(headers: Mapping[str, str] | None, name: str) -> str:
    """Значение заголовка без оглядки на регистр (HTTP его не различает)."""
    if not headers:
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""


def content_type_ok(headers: Mapping[str, str] | None) -> tuple[bool, str]:
    """Согласны ли читать это тело. `safe_get` на Content-Type не смотрит вовсе."""
    return config.content_type_allowed(header_value(headers, "content-type"))


def content_encoding_ok(headers: Mapping[str, str] | None) -> tuple[bool, str]:
    """Поправка C2. `Accept-Encoding: identity` — просьба, а не гарантия.

    Честный предел, названный вслух: «до чтения тела» через `psec.safe_get`
    недостижимо в принципе — он возвращает уже собранный ответ, а разбирать
    поток самим значило бы завести второй транспорт и второй слой SSRF. Поэтому
    проверка стоит ПЕРВОЙ после получения ответа: до декодирования, до разбора,
    до записи на диск и до того, как хоть один байт тела кому-то показан. От
    зип-бомбы при этом защищает не она, а `max_bytes=PAGE_MAX_BYTES` внутри
    `safe_get`: `aiter_bytes` отдаёт уже распакованные куски и счётчик обрывает
    чтение, так что аллокация ограничена потолком плюс один кусок.
    """
    return config.content_encoding_allowed(header_value(headers, "content-encoding"))


# --------------------------------------------------------------- транспорт

class WebFetchAdapter(osiris.HttpFetchAdapter):
    """Настоящая сеть с общим дедлайном и без сжатия. `live = True` наследуется.

    Наследование здесь несёт смысл, а не экономит строки: право пометить
    источник «проверен живьём» принадлежит атрибуту `live`, и подменённый в
    тесте адаптер с `live = False` этого права не получает ни при каком стечении
    обстоятельств. Свой транспорт с нуля пришлось бы отдельно вспоминать об
    этом свойстве — а такое вспоминают не всегда.

    Заголовок `Accept-Encoding: identity` закрывает сразу два дефекта, которые
    иначе неустранимы снаружи: `psec.safe_get` копирует `Content-Encoding` в
    ответ, но кладёт туда УЖЕ РАСПАКОВАННОЕ тело (`httpx.DecodingError` на любом
    последующем разборе), и распакованный кусок аллоцируется до проверки
    потолка. `osiris.collect` зовёт `adapter.fetch` с одним заголовком и без
    таймаута, поэтому передать это снаружи нельзя — только изнутри адаптера.
    """

    async def fetch_bytes(self, url: str, *, headers: dict[str, str] | None = None,
                          timeout: float | None = None, max_bytes: int | None = None,
                          allowed_hosts: set[str] | None = None,
                          allow_private: bool = False) -> RawResponse:
        """GET с ОБЩИМ дедлайном. `timeout` — весь обмен, а не одно чтение.

        Имя параметра совпадает с осирисовским `fetch`, а смысл другой, и это
        сказано здесь, потому что молчаливое расхождение смыслов у одинаковых
        имён — способ получить сутки висящего соединения на ровном месте:
        `safe_get.timeout` перезапускается на КАЖДОМ чтении.
        """
        total = float(timeout if timeout is not None else config.TOTAL_DEADLINE_OPEN)
        cap = int(max_bytes if max_bytes is not None else config.PAGE_MAX_BYTES)

        # C1: до сети уходит только каноническая форма. Отказ канонизации — это
        # egress-отказ («нам туда нельзя»), а не «источник сломался», поэтому и
        # тип исключения тот же, что у остального egress: иначе причина
        # растворилась бы в общем 502.
        try:
            canon = html_text.canon_url(url)
        except (ValueError, UnicodeError) as exc:
            raise PluginSecurityError(f"адрес отклонён до сети: {exc}") from exc

        host = host_of(canon)
        hosts = set(allowed_hosts) if allowed_hosts is not None else same_site(host)
        if not hosts:
            raise PluginSecurityError("не для кого разрешать хосты: адрес без хоста")

        sent = {
            "User-Agent": osiris.USER_AGENT,
            "Accept-Encoding": "identity",
            "Accept": config.ACCEPT_HTML,
            "Accept-Language": config.ACCEPT_LANG,
            **(headers or {}),
        }
        # Cookie не отправляются никогда: даже пустая заготовка под них однажды
        # окажется заполненной, а сессионная кука превращает чтение публичной
        # страницы в действие от имени владельца.
        sent.pop("Cookie", None)
        sent.pop("cookie", None)

        try:
            async with asyncio.timeout(total):
                resp = await psec.safe_get(
                    canon,
                    allow_private=allow_private,
                    allowed_hosts=hosts,
                    max_bytes=cap,
                    timeout=min(config.PER_READ_TIMEOUT, total),
                    max_redirects=3,
                    headers=sent,
                )
        except TimeoutError as exc:
            raise osiris.SourceUnavailableError(
                f"общий дедлайн {total:g} с истёк: сервер отдавал ответ дольше, "
                f"чем мы согласны ждать") from exc
        except httpx.DecodingError as exc:
            # Тот же вред, что и ниже, только обнаруженный чужой библиотекой
            # раньше нас: сервер объявил сжатие, которого в теле нет (или
            # наоборот). Распаковка идёт внутри httpx, до возврата из
            # `safe_get`, поэтому проверить заголовок раньше неё мы физически
            # не можем — но и выпускать наружу чужое исключение нельзя: у
            # отказа обязана быть СВОЯ машинно-читаемая причина, иначе он
            # растворится в общем «источник недоступен» и разбираться будет
            # не с чем. Тело при этом в дело не идёт ни в каком виде.
            raise PageRefused(
                "сервер объявил сжатие, которое не разбирается: заголовок "
                "Content-Encoding не соответствует телу",
                code="content_encoding") from exc

        out = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        allowed, why = content_encoding_ok(out)
        if not allowed:
            # C2: тело сюда доходит уже распакованным чужой библиотекой, но в
            # дело оно не идёт — отказ до разбора и до записи на диск.
            raise PageRefused(why, code="content_encoding")
        return RawResponse(status=int(resp.status_code), content=resp.content,
                           headers=out, url=canon)

    async def fetch(self, url: str, *, headers: dict[str, str] | None = None,
                    timeout: float = 15.0) -> osiris.FetchResult:
        """Протокол `osiris.FetchAdapter`: его зовёт `osiris.collect` и
        `OsirisStore.robots_allows`.

        Через этот же метод идёт и robots.txt (поправка C3): тот же транспорт,
        та же канонизация, тот же общий дедлайн и те же `allowed_hosts`. Иначе
        для robots анти-rebinding был бы выключен полностью — а он запрашивается
        до КАЖДОГО чтения нового пути, то есть чаще самой страницы.

        Кодировка определяется `html_text.decode_body`, а не `httpx.Response.text`:
        служебные `x-bossman-charset` и `x-bossman-replace-ratio` кладутся в
        заголовки результата, потому что `FetchResult` — frozen dataclass с
        четырьмя полями, и другого канала для этих двух фактов нет.
        """
        if is_searxng_url(url):
            # Свой SearXNG живёт на приватном адресе, а весь остальной модуль
            # приватные адреса запрещает. Развилка стоит ЗДЕСЬ, в единственном
            # шве транспорта, а не отдельным конвейером в sources.py: иначе у
            # поиска было бы два пути в сеть, и однажды они разошлись бы в
            # проверках. Путь и имена параметров при этом всё равно проходят
            # положительную проверку внутри `searxng_fetch` — она и остаётся
            # единственным местом с `allow_private=True`.
            path, params = split_searxng_target(url)
            raw = await searxng_fetch(path, params, adapter=self, timeout=timeout)
        else:
            raw = await self.fetch_bytes(url, headers=headers, timeout=timeout,
                                         max_bytes=config.PAGE_MAX_BYTES)
        text, encoding, ratio = html_text.decode_body(
            raw.content, header_value(raw.headers, "content-type"))
        merged = dict(raw.headers)
        merged["x-bossman-charset"] = encoding
        merged["x-bossman-replace-ratio"] = f"{ratio:.4f}"
        return osiris.FetchResult(status=raw.status, body=text, url=raw.url,
                                  headers=merged)


class KeyedFetchAdapter(WebFetchAdapter):
    """Тот же транспорт, но с ключом источника — и только на ЕГО хосте.

    Ключ подставляется, если хост адреса ТОЧНО равен хосту `base_url` источника.
    Не суффиксом и не по `same_site`: право читать ключ выдано конкретному
    имени, и редирект на соседний поддомен не должен превращаться в отправку
    ключа тому, кому его не давали. Промах — не ошибка: запрос уходит без ключа
    и источник честно ответит 401.

    Ключ не попадает ни в одно сообщение об ошибке: сюда он приходит уже
    расшифрованным из `svc.vault`, а всё, что модуль печатает, собирается из
    адреса и кода ответа. `__repr__` переопределён по той же причине — объект
    легко оказывается в тексте исключения чужого кода.
    """

    def __init__(self, *, base_url: str, header: str, key: str) -> None:
        self._host = host_of(base_url)
        self._header = (header or "").strip()
        self._key = key or ""

    def __repr__(self) -> str:
        return f"<KeyedFetchAdapter host={self._host!r} header={self._header!r}>"

    async def fetch_bytes(self, url: str, *, headers: dict[str, str] | None = None,
                          timeout: float | None = None, max_bytes: int | None = None,
                          allowed_hosts: set[str] | None = None,
                          allow_private: bool = False) -> RawResponse:
        extra = dict(headers or {})
        if self._key and self._header and self._host and host_of(url) == self._host:
            extra[self._header] = self._key
        return await super().fetch_bytes(url, headers=extra, timeout=timeout,
                                         max_bytes=max_bytes,
                                         allowed_hosts=allowed_hosts,
                                         allow_private=allow_private)


def install_adapter(svc: Any) -> None:
    """Поставить наш транспорт в `store(svc).adapter`. Только из включённой фичи.

    Условие `isinstance(..., osiris.HttpFetchAdapter)` — не вежливость, а
    защита стенда: подменённый в тесте стаб не является наследником настоящего
    транспорта, значит его не затопчут, и «сеть в тестах подменяется» остаётся
    правдой после установки фичи.

    Побочный эффект на СОБСТВЕННЫЕ сборы OSIRIS есть и он строго улучшающий
    (сегодня любой gzip-ответ роняет `collect` через `httpx.DecodingError`), но
    это влияние за пределы своей фичи, и санкционирует его ведущий.
    """
    st = osiris.store(svc)
    if isinstance(st.adapter, WebFetchAdapter):
        return                                # идемпотентность: setup зовут дважды
    if not isinstance(st.adapter, osiris.HttpFetchAdapter):
        return
    st.adapter = WebFetchAdapter()


async def _adapter_bytes(adapter: Any, url: str, *, timeout: float,
                         max_bytes: int, allowed_hosts: set[str]) -> RawResponse:
    """Байты у ЛЮБОГО адаптера, в том числе у стенда, знающего только `fetch`.

    Стенд по протоколу `osiris.FetchAdapter` обязан уметь `fetch` и не обязан
    уметь `fetch_bytes`. Требовать от него второго метода значило бы либо
    запретить подмену сети, либо заставить каждый тест повторять наш транспорт.
    Текст стенда кодируется в utf-8 — это ровно та кодировка, в которой он его и
    написал, а `transport="stub"` в паспорте всё равно не даст выдать стенд за
    сеть.
    """
    getter = getattr(adapter, "fetch_bytes", None)
    if callable(getter):
        return await getter(url, timeout=timeout, max_bytes=max_bytes,
                            allowed_hosts=allowed_hosts)
    result = await adapter.fetch(url, headers={"User-Agent": osiris.USER_AGENT},
                                 timeout=timeout)
    body = (result.body or "").encode("utf-8", "replace")
    headers = {str(k).lower(): str(v) for k, v in (result.headers or {}).items()}
    headers.setdefault("content-type", "text/html; charset=utf-8")
    return RawResponse(status=int(result.status), content=body, headers=headers,
                       url=url)


# ------------------------------------------------- приватная дверь SearXNG

def is_searxng_url(url: str) -> bool:
    """Адрес ведёт в свой SearXNG.

    Сравнение по хосту И порту: сосед на другом порту того же 127.0.0.1 — это
    другая служба, и пускать к ней через приватную дверь нельзя.
    """
    base = config.SEARXNG_URL
    if not base:
        return False
    try:
        want = urlsplit(base)
        got = urlsplit(str(url or ""))
    except ValueError:
        return False
    return bool(got.netloc) and got.netloc.lower() == want.netloc.lower()


def split_searxng_target(url: str) -> tuple[str, dict[str, str]]:
    """Адрес → (путь, параметры) для проверки внутри приватной двери.

    Разбор именно здесь, а не в `searxng_fetch`: та принимает путь и параметры
    ПО ОТДЕЛЬНОСТИ ровно затем, чтобы их можно было проверить поимённо. Если
    склеенный адрес принесёт что-то за пределами разрешённого набора, дверь
    откажет — здесь мы ничего не решаем, только раскладываем.
    """
    parts = urlsplit(str(url or ""))
    params: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        params[str(key)] = str(value)
    return parts.path or "/", params


async def searxng_fetch(url_path: str, params: Mapping[str, Any],
                        *, adapter: Any = None,
                        timeout: float | None = None) -> RawResponse:
    """ЕДИНСТВЕННОЕ место во всём пакете с `allow_private=True`.

    Своему инстансу SearXNG нужен приватный адрес (`127.0.0.1`, адрес в
    домашней сети), а вся остальная защита от SSRF держится на том, что
    приватные адреса запрещены. Поэтому исключение сделано ровно один раз,
    именем функции, и обставлено так, что расширить его нечем:

      * хост берётся ТОЛЬКО из `config.SEARXNG_URL` (переменная окружения,
        проверенная при импорте), никогда из аргумента;
      * `allowed_hosts` — ровно этот хост, поэтому редирект куда угодно ещё
        падает на `validate_url`;
      * путь обязан быть из `SEARXNG_PATHS`, имя каждого параметра — из
        `SEARXNG_PARAM_KEYS`. Проверка положительная: незнакомое имя это отказ,
        а не «пропустим, вдруг пригодится».

    Значение `q` — это текст запроса, и он действительно приходит от модели.
    Так и должно быть: это ЕДИНСТВЕННЫЕ байты, которые модуль сознательно
    отправляет наружу, они проходят шлюз `_guard_query` в `tools.py` до вызова и
    попадают владельцу в ленту дословно. Адрес, путь и имена параметров модель
    не выбирает вовсе.

    `adapter` передаётся вызывающим (обычно `store(svc).adapter`) — это тот же
    подменяемый шов, что и везде; своего транспорта функция не заводит.
    """
    if not config.SEARXNG_URL:
        raise PageRefused("свой SearXNG не настроен: BOSSMAN_WEB_SEARXNG_URL пуст",
                          code="searxng_not_configured", http_status=409)
    path = str(url_path or "")
    if path not in SEARXNG_PATHS:
        raise PageRefused(f"путь {path!r} не входит в фиксированный набор {sorted(SEARXNG_PATHS)}",
                          code="searxng_path")

    pairs: list[tuple[str, str]] = []
    for key, value in dict(params or {}).items():
        name = str(key)
        if name not in SEARXNG_PARAM_KEYS:
            raise PageRefused(f"параметр {name!r} не разрешён для SearXNG",
                              code="searxng_param")
        if value is None:
            continue
        pairs.append((name, str(value)))
    query = urlencode(sorted(pairs), doseq=False)

    base = config.SEARXNG_URL.rstrip("/")
    url = f"{base}{path}?{query}" if query else f"{base}{path}"
    host = host_of(base)
    if not host:
        raise PageRefused("в BOSSMAN_WEB_SEARXNG_URL нет хоста", code="searxng_url",
                          http_status=409)

    transport = adapter if adapter is not None else WebFetchAdapter()
    getter = getattr(transport, "fetch_bytes", None)
    if not callable(getter):
        # Стенд без `fetch_bytes` обслуживается общим путём: приватная дверь не
        # повод заводить второй способ ходить в сеть.
        return await _adapter_bytes(
            transport, url,
            timeout=float(timeout if timeout is not None else config.TOTAL_DEADLINE_SEARCH),
            max_bytes=config.PAGE_MAX_BYTES, allowed_hosts={host})
    return await getter(
        url,
        timeout=float(timeout if timeout is not None else config.TOTAL_DEADLINE_SEARCH),
        max_bytes=config.PAGE_MAX_BYTES,
        allowed_hosts={host},
        allow_private=True,
    )


# --------------------------------------------------------------- robots.txt

# Вердикт, а не содержимое robots.txt: свой RobotFileParser здесь не поднимается
# (он уже есть в `OsirisStore.robots_allows`), и fail-closed наследуется вместе
# с ним. Ключ включает корень хранилища, иначе два `svc` в одном тестовом
# процессе делили бы память о чужих запретах.
_ROBOTS_CACHE: dict[tuple[str, str, str], tuple[float, bool, str]] = {}


def robots_cache_clear() -> None:
    """Сброс памяти вердиктов. Нужен тесту и `tick()`, больше никому."""
    _ROBOTS_CACHE.clear()


async def robots_ok(st: osiris.OsirisStore, source: osiris.Source,
                    url: str) -> tuple[bool, str]:
    """Разрешает ли robots.txt этот адрес. Мемоизация вердикта на ROBOTS_TTL_S.

    Ужесточение против `osiris.collect`: там robots спрашивают только для
    категории C, здесь — для ВСЕХ. Разница не теоретическая: страницы читаются
    именно по категории C, но одна и та же функция обслуживает и перепроверку
    цитаты, и владельческую ручку, и однажды кто-нибудь передаст сюда источник
    другой категории.

    Кэшируется и ЗАПРЕТ тоже. Недоступный robots.txt — это отказ (fail-closed
    наследуется у `robots_allows`), и повторять безуспешный запрос к упавшему
    серверу на каждое чтение значит долбить его вместо того, чтобы отступить.
    Цена — до получаса задержки перед тем, как починенный сайт снова станет
    читаемым; она названа здесь и снимается `robots_cache_clear()`.

    Важное для тех, кто подменяет сеть: robots тянется через
    `OsirisStore.robots_allows`, а он зовёт `adapter.fetch`, а не `fetch_bytes`.
    Стенд обязан реализовать осирисовский протокол целиком, иначе его страница
    честно упрётся в fail-closed «robots.txt недоступен» — и это не оплошность
    конвейера, а тот самый отказ по умолчанию.
    """
    parts = urlsplit(url)
    key = (str(st.root), (parts.hostname or "").lower(), parts.path or "/")
    now = time.monotonic()
    cached = _ROBOTS_CACHE.get(key)
    if cached is not None and (now - cached[0]) < config.ROBOTS_TTL_S:
        return cached[1], cached[2]

    allowed, note = await st.robots_allows(source, url)
    if len(_ROBOTS_CACHE) >= _ROBOTS_CACHE_MAX:
        # Память вердиктов — удобство, а не состояние: переполнение сбрасывает
        # её целиком, потому что выборочное вытеснение здесь не стоит ни строки.
        _ROBOTS_CACHE.clear()
    _ROBOTS_CACHE[key] = (now, bool(allowed), str(note))
    return bool(allowed), str(note)


# ------------------------------------------------- пауза вежливости на хост

_LAST_HIT: dict[str, float] = {}


async def _polite_pause(host: str) -> None:
    """Не меньше POLITE_PAUSE_S между обращениями к одному хосту.

    `rate_allows` считает 10 запросов в минуту, но НЕ мешает им прийти десятью
    подряд за одну секунду — для сайта это неотличимо от маленькой DDoS-атаки, и
    отвечает он на такое баном, а не жалобой. Пауза стоит СНАРУЖИ общего
    дедлайна намеренно: она не должна съедать время, отведённое на ответ сервера.
    """
    if not host:
        return
    now = time.monotonic()
    if len(_LAST_HIT) >= _HOST_PAUSE_MAX:
        for name, stamp in list(_LAST_HIT.items()):
            if now - stamp > 60.0:
                _LAST_HIT.pop(name, None)
    previous = _LAST_HIT.get(host)
    if previous is not None:
        wait = config.POLITE_PAUSE_S - (now - previous)
        if wait > 0:
            await asyncio.sleep(wait)
    _LAST_HIT[host] = time.monotonic()


# ------------------------------------------------------- сырьё по содержимому

def _pages_dir(svc: Any) -> Path:
    return config.runs_dir(svc) / PAGES_DIRNAME


def _pointer_path(svc: Any, source_id: str, url: str) -> Path:
    key = hashlib.sha256(f"{source_id}|{url}".encode("utf-8")).hexdigest()
    return _pages_dir(svc) / f"{key}.json"


def _remember_digest(svc: Any, source_id: str, url: str, digest: str) -> None:
    """Указатель «с этого адреса последним пришло вот это тело».

    Содержимого здесь нет и быть не может — только адрес и дайджест. Потеря
    указателя безопасна: она стоит одного лишнего похода в сеть, тогда как
    потеря сырья стоила бы доказательства под уже выданной цитатой. Именно
    поэтому перезаписывается указатель, а не файл сырья (D1).
    """
    config.atomic_write_json(_pointer_path(svc, source_id, url), {
        "url": url, "source_id": source_id, "digest": digest,
        "written_at": utcnow().isoformat(),
    })


def _last_digest(svc: Any, source_id: str, url: str) -> str:
    record = config.read_json(_pointer_path(svc, source_id, url))
    if isinstance(record, dict):
        return str(record.get("digest") or "")
    return ""


def prune_pointers(svc: Any) -> int:
    """Убрать указатели, чьё сырьё уже удалено. Возвращает число удалённых.

    Указатель на несуществующее сырьё безвреден (промах = поход в сеть), но
    каталог не должен расти вечно. Зовётся из `tick()`; сырьё эта функция не
    трогает НИКОГДА — удаление доказательств живёт только в праве на удаление.
    """
    directory = _pages_dir(svc)
    if not directory.is_dir():
        return 0
    st = osiris.store(svc)
    removed = 0
    for path in sorted(directory.glob("*.json")):
        record = config.read_json(path)
        digest = str(record.get("digest") or "") if isinstance(record, dict) else ""
        if not digest or st.read_raw(digest) is None:
            with contextlib.suppress(OSError):
                path.unlink()
                removed += 1
    return removed


def raw_bytes_used(st: osiris.OsirisStore) -> int:
    """Сколько байт занимает сырьё OSIRIS на диске (все источники, не только наши).

    Считается по всем файлам: дисковый бюджет владельца общий, и «это не наши
    файлы» ему на переполненном диске не поможет.
    """
    directory = st.raw_dir
    if not directory.is_dir():
        return 0
    total = 0
    with contextlib.suppress(OSError):
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file():
                    with contextlib.suppress(OSError):
                        total += entry.stat().st_size
    return total


def raw_budget_state(st: osiris.OsirisStore) -> tuple[int, int]:
    """(занято, потолок) в байтах. Отдельной функцией — её печатает ручка."""
    return raw_bytes_used(st), config.RAW_BUDGET_BYTES


def _check_disk_budget(st: osiris.OsirisStore) -> None:
    """Переполнение бюджета = отказ СОБИРАТЬ НОВОЕ, а не удаление старого.

    Это добавление к §8 проекта, и оно только ужесточает: проверка стоит ДО
    сети, потому что прочитать страницу, а потом обнаружить, что сохранить её
    сырьё некуда, значит потратить байты и получить наблюдение без
    доказательства. Уже собранное сырьё не удаляется никогда — даже здесь,
    особенно здесь: на него ссылаются выданные цитаты.
    """
    used, limit = raw_budget_state(st)
    if used >= limit:
        raise PageRefused(
            f"дисковый бюджет сырья исчерпан: занято {used / 1_000_000:.1f} МБ из "
            f"{limit / 1_000_000:.0f} МБ. Без сохранённого сырья цитата недоказуема, "
            f"поэтому страница не читается; уже собранное сырьё не удаляется",
            code="raw_budget", http_status=507)


def _write_page_raw(st: osiris.OsirisStore, source: osiris.Source, subject: str,
                    url: str, *, raw: RawResponse, text: str, encoding: str,
                    replace_ratio: float, mojibake: bool, extract_chars: int,
                    transport: str, fetched_at: datetime) -> str:
    """Записать сырьё под ключом-содержимым и вернуть дайджест (D1).

    Ключ — `sha256` ТЕЛА ОТВЕТА (байтов), а не `sha256(source_id|url)`, как в
    `OsirisStore.write_raw`: адресация по URL означает, что второе чтение того
    же адреса перезаписывает файл, на который уже ссылается выданная цитата, то
    есть перепроверка уничтожает проверяемую улику. Здесь новое чтение создаёт
    новую запись, а старая остаётся навсегда.

    В файле лежит ДЕКОДИРОВАННЫЙ текст, а не исходные байты: именно из него
    извлекался показанный текст и в него записаны смещения цитат, а хранить
    вдобавок base64 удвоило бы дисковый бюджет ради того же содержимого. Ключ
    при этом всё равно берётся от байтов — он подписывает то, что пришло с
    провода. Кодировка и доля замен записаны рядом, чтобы разбор был
    воспроизводим, а не «как получилось в тот раз».

    Формат записи совпадает с `OsirisStore.write_raw` полем в поле, поэтому
    `st.read_raw` и `st.raw_is_fresh` читают её без единой оговорки.
    """
    digest = hashlib.sha256(raw.content).hexdigest()
    ttl = int(source.cache_ttl_seconds or 0)
    record = {
        "hash": digest,
        "source_id": source.id,
        "subject": subject,
        "url": url,
        "status": int(raw.status),
        "fetched_at": fetched_at.isoformat(),
        "expires_at": (fetched_at + timedelta(seconds=ttl)).isoformat(),
        "ttl_seconds": ttl,
        "transport": transport,
        "body_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
        "body": text,
        # Своё сверх осирисовского: без этих полей повторное извлечение из кэша
        # дало бы другой текст и другие смещения цитат (поправка D6).
        "raw_sha256": digest,
        "raw_bytes": len(raw.content),
        "content_type": header_value(raw.headers, "content-type"),
        "encoding": encoding,
        "replace_ratio": float(replace_ratio),
        "mojibake": bool(mojibake),
        "extract_max_chars": int(extract_chars),
        "extractor": html_text.EXTRACTOR_VERSION,
    }
    # Пишем СВОЕЙ атомарной записью, а не приватным `OsirisStore._write_json`:
    # приватная деталь чужого модуля имеет полное право поменяться без
    # предупреждения, а формат файла здесь и так задан полем в поле выше.
    config.atomic_write_json(st.raw_dir / f"{digest}.json", record)
    return digest


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------- конвейер чтения страницы

def _extraction_of(record: Mapping[str, Any]) -> tuple[Any, int]:
    """Извлечь текст из записи сырья ТЕМИ ЖЕ параметрами, что и в первый раз."""
    try:
        chars = int(record.get("extract_max_chars") or EXTRACT_MAX_CHARS)
    except (TypeError, ValueError):
        chars = EXTRACT_MAX_CHARS
    extraction = html_text.extract(str(record.get("body") or ""),
                                   base_url=str(record.get("url") or ""),
                                   max_chars=chars)
    return extraction, chars


def _page_from_record(record: Mapping[str, Any], *, source_id: str, host: str,
                      from_cache: bool, robots_note: str, transport: str,
                      bytes_read: int, net_seconds: float,
                      observation_id: str) -> PageRecord:
    extraction, chars = _extraction_of(record)
    try:
        ratio = float(record.get("replace_ratio") or 0.0)
    except (TypeError, ValueError):
        ratio = 0.0
    fetched = _parse_iso(record.get("fetched_at")) or utcnow()
    # Признак берётся из записи: он посчитан по полному телу в момент чтения.
    # Пересчёт по извлечённому тексту — запасной путь для записей, сделанных до
    # появления поля; он мягче (обрезанный текст мог не захватить признак), и
    # это причина предпочитать записанное значение, а не считать заново всегда.
    if "mojibake" in record:
        mojibake = bool(record.get("mojibake"))
    else:
        mojibake = html_text.looks_mojibake(extraction.text)
    return PageRecord(
        url=str(record.get("url") or ""),
        host=host,
        source_id=source_id,
        status=int(record.get("status") or 0),
        raw_digest=str(record.get("hash") or ""),
        body_sha256=str(record.get("body_sha256") or ""),
        text_sha256=html_text.page_sha256(extraction.text),
        charset=str(record.get("encoding") or ""),
        replace_ratio=ratio,
        mojibake=mojibake,
        # Битый текст читать можно, а цитировать нельзя: владельцу полезнее
        # увидеть, что страница прочиталась криво, чем не увидеть ничего, но
        # «дословная» цитата из мусора — это выдумка с паспортом.
        quotable=(ratio <= QUOTABLE_REPLACE_RATIO and not mojibake),
        extraction=extraction,
        extract_max_chars=chars,
        content_type=str(record.get("content_type") or ""),
        from_cache=from_cache,
        fetched_at=fetched.isoformat(),
        age_seconds=max(0.0, (utcnow() - fetched).total_seconds()),
        transport=str(record.get("transport") or transport),
        robots_note=robots_note,
        bytes_read=bytes_read,
        net_seconds=net_seconds,
        observation_id=observation_id,
    )


def _observe_page(st: osiris.OsirisStore, source: osiris.Source, subject: str,
                  page: PageRecord, *, digest: str, of: str) -> str:
    """Наблюдение `page.text` и его запись. Возвращает id наблюдения.

    `observed_at = fetched_at` ВСЕГДА (поправка E3), в том числе на попадании в
    кэш: наблюдено содержимое тогда, когда оно пришло по проводу, а не тогда,
    когда мы показали его модели. Подставить сюда `utcnow()` значит записать в
    паспорт выдуманную свежесть — ровно тот класс лжи, ради исключения которого
    вся фича и существует.

    `transport` в значении говорит, была ли это настоящая сеть (поправка D5):
    цитата из наблюдения со `stub` не имеет права выдаваться за сетевую.
    """
    ex = page.extraction
    observed_at = _parse_iso(page.fetched_at) or utcnow()
    value: dict[str, Any] = {
        "url": page.url,
        "host": page.host,
        "chars": ex.chars,
        "text_sha256": page.text_sha256,
        "body_sha256": page.body_sha256,
        "extractor": html_text.EXTRACTOR_VERSION,
        "max_chars": page.extract_max_chars,
        "encoding": page.charset,
        "replace_ratio": page.replace_ratio,
        "mojibake": page.mojibake,
        "quotable": page.quotable,
        "title": ex.title,
        "truncated": bool(ex.truncated),
        "stop_reason": ex.stop_reason,
        "hidden_dropped": ex.hidden_dropped,
        "status": page.status,
        "content_type": page.content_type,
        "transport": page.transport,
        "fetched_at": page.fetched_at,
        "from_cache": page.from_cache,
        "age_seconds": round(page.age_seconds, 3),
        "robots": page.robots_note,
        "url_note": config.MSG_REQUESTED_URL_ONLY,
    }
    if of:
        # Связь «из чего это выросло»: §9 проекта связывает наблюдения эпизода
        # через value["of"], и без неё дерево следа не собирается.
        value["of"] = of
    obs = osiris.Observation(
        value=value,
        subject=subject,
        source_id=source.id,
        source_url=page.url,
        method=source.method,
        license=source.license,
        observed_at=observed_at,
        collected_at=st.next_collected_at(),
        confidence=min(0.5, float(source.default_confidence or 0.5)),
        raw_ref=f"raw:{digest}",
        attribute="page.text",
    )
    st.save_observations(subject, [obs], [digest])
    return obs.id


async def fetch_page(svc: Any, url: str, subject: str, *,
                     ensure_host_source: EnsureHostSource,
                     force: bool = False,
                     extract_chars: int = EXTRACT_MAX_CHARS,
                     of: str = "") -> PageRecord:
    """Прочитать страницу. Порядок проверок — раздел 8 проекта, дословно.

        канонизация → предпроверка адреса → egress → источник-на-хост →
        robots → кэш → дисковый бюджет → лимит хоста → пауза вежливости →
        сеть → статус → Content-Encoding → Content-Type → декодирование →
        mojibake → извлечение → минимум текста → сырьё → наблюдение →
        пометка живой сети

    Перестановка любой пары ослабляет fail-closed, и почти каждая перестановка
    выглядит безобидно. Три места, где это особенно неочевидно:

      * `robots` СТРОГО до кэша: иначе запрет, появившийся после первого
        чтения, не действует до истечения TTL;
      * сырьё пишется ПОСЛЕ успешного извлечения — единственное отличие от
        `osiris.collect`, и оно в безопасную сторону: на диске не появляется
        файла, на который не ссылается ни одно наблюдение;
      * `_mark_live` — самым последним и только через `osiris._mark_live`,
        который сам смотрит на `adapter.live`. Своя пометка здесь означала бы,
        что подменённый адаптер может объявить источник рабочим.

    `force=True` (поправка E5) НИКОГДА не откатывается к кэшу: перепроверка,
    ответившая «цела» из архива, — это отчёт о непроведённой проверке. Сети нет
    — будет `SourceUnavailableError`, и вызывающий обязан назвать это
    `unreachable`, а не `intact`.

    Отказы уходят исключениями (см. `PageRefused`); границей модуля они
    становятся данными в `tools.py`. `PluginSecurityError` пропускается наверх
    как есть — «нам туда нельзя» не должно раствориться в общем 502.
    """
    st = osiris.store(svc)

    subject = (subject or "").strip()
    if not subject or "/" in subject or len(subject) > osiris.MAX_SUBJECT:
        raise osiris.OsirisError(
            f"субъект обязателен, без «/», не длиннее {osiris.MAX_SUBJECT} знаков")

    # 1. Канонизация ДО всего (C1).
    try:
        canon = html_text.canon_url(url)
    except (ValueError, UnicodeError) as exc:
        raise PageRefused(f"адрес отклонён до сети: {exc}", code="bad_url",
                          http_status=400) from exc

    # 2. Что видно из самого адреса — бесплатно и до сети.
    refusal = precheck_target(canon)
    if refusal is not None:
        code, why = refusal
        raise PageRefused(why, code=code, http_status=_REFUSAL_STATUS.get(code, 403))
    host = host_of(canon)

    # 3. Чужая дверь egress: SSRF, приватные диапазоны, метаданные облака.
    osiris.checked_url(canon)

    # 4. Источник-на-хост. Именно здесь имя хоста проходит ЧУЖОЙ словарь
    #    запретов (`normalize_source` → `_forbidden_reason`), поэтому
    #    dehashed.com и pimeyes.com отклоняются не нашим кодом и с чужим кодом
    #    причины — это переиспользование запрета, а не его копия.
    source = ensure_host_source(st, host)

    # 5. robots. Fail-closed наследуется: недоступный robots.txt = отказ.
    allowed, robots_note = await robots_ok(st, source, canon)
    if not allowed:
        raise osiris.RobotsDisallowError(robots_note)

    # 6. Кэш. При force сюда не заходим вовсе (E5).
    if not force:
        digest = _last_digest(svc, source.id, canon)
        cached = st.read_raw(digest) if digest else None
        if isinstance(cached, dict) and st.raw_is_fresh(cached):
            page = _page_from_record(cached, source_id=source.id, host=host,
                                     from_cache=True, robots_note=robots_note,
                                     transport=str(cached.get("transport") or "stub"),
                                     bytes_read=0, net_seconds=0.0,
                                     observation_id="")
            # Показ из кэша — тоже событие эпизода, и наблюдение о нём честное:
            # observed_at равен времени СЕТЕВОГО чтения, from_cache=True, возраст
            # посчитан. Байтов и живой пометки здесь нет и не будет.
            obs_id = _observe_page(st, source, subject, page, digest=digest, of=of)
            # `replace`, а не второе `_page_from_record`: повторное извлечение
            # ради одного поля стоило бы разбора всей страницы во второй раз.
            return dc_replace(page, observation_id=obs_id)

    # 7. Дисковый бюджет — до сети (см. `_check_disk_budget`).
    _check_disk_budget(st)

    # 8. Лимит хоста. Ключ лимита у OSIRIS — source.id, а у нас источник на
    #    каждый хост, поэтому лимит впервые становится ПОХОСТОВЫМ.
    if not st.rate_allows(source):
        raise osiris.RateLimitedError(
            f"лимит {source.rate_limit_per_min} запросов в минуту для хоста {host} исчерпан")

    # 9. Пауза вежливости.
    await _polite_pause(host)

    # 10. Сеть — только через подменяемый адаптер хранилища.
    adapter = st.adapter
    live_call = bool(getattr(adapter, "live", False))
    transport = "live" if live_call else "stub"
    started = time.monotonic()
    try:
        raw = await _adapter_bytes(adapter, canon,
                                   timeout=config.TOTAL_DEADLINE_OPEN,
                                   max_bytes=config.PAGE_MAX_BYTES,
                                   allowed_hosts=same_site(host))
    except PluginSecurityError:
        raise
    except osiris.OsirisError:
        # Уже типизировано (дедлайн, Content-Encoding) — не переклеивать причину.
        raise
    except Exception as exc:                   # noqa: BLE001 — беда сети = данные, не авария
        osiris._mark_live(st, source, ok=False,                     # noqa: SLF001
                          error=f"{exc.__class__.__name__}: {exc}", live_call=live_call)
        raise osiris.SourceUnavailableError(
            f"страница недоступна: {exc.__class__.__name__}") from exc
    net_seconds = time.monotonic() - started

    # 11. Статус.
    if raw.status != 200:
        osiris._mark_live(st, source, ok=False, error=f"HTTP {raw.status}",   # noqa: SLF001
                          live_call=live_call)
        raise osiris.SourceUnavailableError(f"страница ответила HTTP {raw.status}")

    # 12. Content-Encoding и Content-Type — до разбора тела.
    #     Проверка кодировки передачи стоит ЗДЕСЬ, хотя она же есть в
    #     `WebFetchAdapter.fetch_bytes`, и это не дублирование по недосмотру:
    #     адаптер — подменяемый шов, значит проверка, живущая только в нём,
    #     снимается вместе с ним. Транспорт защищает себя, конвейер — себя.
    ok_enc, why_enc = content_encoding_ok(raw.headers)
    if not ok_enc:
        raise PageRefused(why_enc, code="content_encoding")
    ok_type, why_type = content_type_ok(raw.headers)
    if not ok_type:
        raise PageRefused(why_type, code="content_type")

    # 13-15. Декодирование, признак двойного декодирования, извлечение.
    #     `looks_mojibake` считается по ПОЛНОМУ декодированному телу и до
    #     разбора: признак касается кодировки, а не вёрстки, и на обрезанном
    #     извлечении он зависел бы от того, докуда дотянулся потолок знаков.
    #     Отказа здесь нет намеренно: битую страницу владельцу полезнее увидеть
    #     битой, чем не увидеть вовсе, — но цитировать из неё запрещено, и это
    #     выражено полем `quotable`, а не оговоркой в документации.
    content_type = header_value(raw.headers, "content-type")
    text, encoding, ratio = html_text.decode_body(raw.content, content_type)
    mojibake = html_text.looks_mojibake(text)
    extraction = html_text.extract(text, base_url=canon,
                                   max_chars=int(extract_chars or EXTRACT_MAX_CHARS))

    # 16. Минимум текста: меню, капча и заглушка — это не прочитанный документ.
    if extraction.chars < MIN_PAGE_TEXT_CHARS:
        raise PageRefused(
            f"на странице {extraction.chars} знаков читаемого текста (нужно не меньше "
            f"{MIN_PAGE_TEXT_CHARS}): это меню, капча или страница, собираемая скриптом. "
            f"Скрипты мы не исполняем и делать вид, что прочитали, не станем",
            code="empty_text")

    # 17. Сырьё — ПОСЛЕ успешного извлечения и по содержимому (D1).
    fetched_at = utcnow()
    digest = _write_page_raw(st, source, subject, canon, raw=raw, text=text,
                             encoding=encoding, replace_ratio=ratio, mojibake=mojibake,
                             extract_chars=int(extract_chars or EXTRACT_MAX_CHARS),
                             transport=transport, fetched_at=fetched_at)
    _remember_digest(svc, source.id, canon, digest)

    stored = st.read_raw(digest) or {}
    page = _page_from_record(stored, source_id=source.id, host=host, from_cache=False,
                             robots_note=robots_note, transport=transport,
                             bytes_read=len(raw.content), net_seconds=net_seconds,
                             observation_id="")

    # 18. Наблюдение.
    obs_id = _observe_page(st, source, subject, page, digest=digest, of=of)

    # 19. Право пометить источник живым — только у настоящей сети. Функция
    #     осирисовская и приватная сознательно: своя пометка здесь означала бы,
    #     что подменённый в тесте адаптер может объявить источник рабочим.
    osiris._mark_live(st, source, ok=True, error="", live_call=live_call)   # noqa: SLF001

    return dc_replace(page, observation_id=obs_id)


async def read_cached(svc: Any, entry: Any) -> PageRecord | None:
    """Перечитать УЖЕ СОБРАННОЕ сырьё без сети. `None` — сырья нет.

    Этим живут `web.find` и `web.cite`: ни байта наружу, ни расхода лимитов, ни
    нового наблюдения. Нового наблюдения здесь нет намеренно — модели не
    показывают ничего, чего она не видела: поиск внутри уже прочитанного не
    является новым актом сбора, и записывать его как сбор значило бы раздувать
    след эпизода событиями, которых не было.

    `entry` — утиный объект реестра (`ledger.RefEntry`) либо просто дайджест
    строкой. Импорта `ledger` здесь нет сознательно: направление зависимостей в
    пакете `config ← ledger/net ← tools`, и обратная стрелка замкнула бы цикл
    ради одного обращения к двум полям.

    `async` при отсутствии сети — не оплошность: вызов стоит рядом с
    `fetch_page` в одних и тех же ветках `tools.py`, и разная форма вызова у
    двух соседних строк однажды кончается забытым `await`.
    """
    st = osiris.store(svc)
    digest = ""
    url = ""
    host = ""
    if isinstance(entry, str):
        digest = entry.split(":", 1)[-1] if entry.startswith("raw:") else entry
    else:
        digest = str(getattr(entry, "raw_digest", "") or "")
        url = str(getattr(entry, "url", "") or "")
        host = str(getattr(entry, "host", "") or "")

    record = st.read_raw(digest) if digest else None
    if not isinstance(record, dict) and url:
        # Запись реестра могла родиться до чтения (ref отчеканен, страница не
        # открыта) — тогда дайджест ищется по указателю адреса.
        source_id = config.host_source_id(host or host_of(url))
        fallback = _last_digest(svc, source_id, url)
        record = st.read_raw(fallback) if fallback else None
        digest = fallback if isinstance(record, dict) else digest
    if not isinstance(record, dict):
        return None

    return _page_from_record(
        record,
        source_id=str(record.get("source_id") or ""),
        host=host or host_of(str(record.get("url") or "")),
        from_cache=True,
        robots_note="",
        transport=str(record.get("transport") or "stub"),
        bytes_read=0,
        net_seconds=0.0,
        observation_id="",
    )
