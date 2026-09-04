"""web_research: реестр поисковых backend'ов, источник-на-хост и парсеры OSIRIS.

Второго реестра источников в проекте нет и не появится. Всё, что этот файл
умеет объявить, он объявляет ДЕКЛАРАЦИЕЙ и пропускает через ЧУЖУЮ
`osiris.normalize_source`: она сама зовёт `_forbidden_reason` (словарь запретов
раздела 2 ТЗ) и `checked_url` (`plugin_security.validate_url`). Своей проверки
адреса и своего словаря запретов здесь нет — второй такой слой означал бы две
границы вместо одной, и однажды они разошлись бы.

Несущий приём файла — источник категории C НА КАЖДЫЙ ПРОЧИТАННЫЙ ХОСТ
(`ensure_host_source`). Он не бухгалтерия, он даёт две вещи, которых иначе не
было бы вовсе:

  * имя хоста прогоняется через ЧУЖОЙ `FORBIDDEN_PATTERNS`, поэтому
    `dehashed.com` отклоняется с кодом `leaked_database`, `pimeyes.com` — с
    `biometrics`, `12ft.io` — с `paywall_bypass`. Причина приходит из чужого
    словаря и чужим кодом: это доказательство переиспользования, а не копии;
  * `OsirisStore.rate_allows` ключуется по `source.id`, поэтому лимит запросов
    впервые становится ПОХОСТОВЫМ, а не общим на всю фичу.

Чего этот файл НЕ делает и делать не должен:

  * **не ходит в сеть.** Ни одного `await` наружу: сеть — это `net.py`
    (транспорт) и `osiris.collect` (порядок проверок). Здесь только
    декларации, разбор уже полученного тела и подсчёт готовности. Из `net`
    берётся ровно одно ЧИСТОЕ правило — `https_required` (A5), потому что
    «кому можно верить» обязано быть сформулировано в одном месте с тем, кто
    ходит наружу; обратного импорта нет и быть не может: конвейер получает
    `ensure_host_source` параметром, а не импортом, и цикла не возникает;
  * **не хранит и не читает ни одного секрета.** Ключ Brave не появляется здесь
    ни значением, ни именем файла: присутствие ключа приходит ПАРАМЕТРОМ
    `api_keys` от того, кто владеет хранилищем. Без него backend с
    `auth_mode="api_key"` виден в реестре с пометкой «ключ не задан» и НИКОГДА
    не опрашивается молча;
  * **не решает, что показать владельцу, а что модели.** Готовность собирает
    `config.readiness()` — одна функция на обоих, поэтому расхождение между
    «что видит владелец» и «что модель говорит владельцу» невозможно by
    construction;
  * **не превращает отказ внешнего мира в исключение наружу.** `run_search`
    возвращает ДАННЫЕ с кодом исхода. Исключения остаются ровно там, где отказ
    обязан быть fail-closed и заметным программисту: в объявлении источника
    (`ensure_source`/`ensure_host_source`) — и там они осирисовские, с чужим
    кодом причины.

Три исхода поиска вместо одного (поправка E1). `osiris.collect` при пустом
списке наблюдений бросает общий `OsirisError`, и «движки не ответили»,
«ничего не найдено» и «источник сломался» становятся неразличимы. Здесь это
закрыто без правки чужого файла: парсер ВСЕГДА выдаёт наблюдение
`search.query` — до и независимо от разбора результатов, — поэтому список
никогда не пуст, байты, ушедшие наружу, всегда оставляют след, а сам исход
лежит в `value["outcome"]` и разбирается `run_search` в отдельные коды.

Чего в файле НЕТ намеренно:

  * **arXiv.** Его API живёт на `http://export.arxiv.org`, а поправка A5 не
    знает исключений: ответ по http можно не только прочитать, но и подменить,
    и инъекция придёт с паспортом доверенного источника;
  * **парсера Atom.** `osiris.collect` делает `json.loads(body)` ДО вызова
    парсера, поэтому XML-тело через штатный путь до парсера не доезжает
    никогда. Зарегистрировать имя `web.serp_atom` значило бы дать декларации
    пройти проверку и молча падать на разборе JSON — обещание, которого код не
    выполняет. Единственный Atom-источник проекта (arXiv) исключён предыдущим
    пунктом, поэтому обещание снято целиком, а не подпёрто заглушкой.
"""
from __future__ import annotations

import contextlib
import copy
import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlsplit

from ... import html_text
from ... import plugin_security as psec
from .. import osiris
from . import config, net

__all__ = [
    "TOS_CHECKED_AT", "PARSER_NAMES", "SEARCH_OUTCOMES", "RUN_CODES",
    "HIT_TITLE_MAX", "HIT_SNIPPET_MAX", "MAX_HITS_CAP",
    "Backend", "BACKENDS", "BACKENDS_BY_ID", "DECL_PROBLEMS",
    "backend_by_id", "decl_of", "host_source_decl",
    "ensure_source", "ensure_host_source",
    "install_parsers", "parsers",
    "parse_serp", "serp_observations", "page_text_observations",
    "search_subject", "query_of",
    "backend_status", "readiness", "pick_backend", "run_search",
]

# Дата последней проверки условий использования объявленных источников.
# `normalize_source` отвергнет дату в будущем, но не отвергнет устаревшую: это
# обязанность владельца, и она названа вслух в `notes` каждой декларации.
TOS_CHECKED_AT = "2026-09-03"

PARSER_NAMES = ("web.serp_opensearch", "web.serp_json", "web.serp_searxng", "web.page_text")

# Исход РАЗБОРА выдачи. «Движки не ответили» отделено от «ничего не найдено»
# намеренно и это главное требование поправки E1: SearXNG при капче в апстриме
# отвечает HTTP 200 и пустым списком, и выдать это за «в интернете такого нет»
# значит соврать владельцу с полным паспортом.
SEARCH_OUTCOMES = ("ok", "empty_result", "engines_down", "bad_response")

# Исход ВЫЗОВА `run_search`. Сюда добавлены отказы, которые случаются до
# разбора: их тоже нельзя сваливать в один код.
RUN_CODES = SEARCH_OUTCOMES + (
    "disabled", "not_ready", "private_door", "source_unavailable",
    "robots_disallow", "rate_limited", "egress_blocked", "forbidden_source",
    "source_unknown",
)

HIT_TITLE_MAX = 200          # ровно как ledger.TITLE_MAX: два потолка на одну
HIT_SNIPPET_MAX = 200        # величину однажды разойдутся, и обрежет меньший
MAX_HITS_CAP = 10

# Осмысленный потолок доверия для всего, что пришло из сети (раздел 9 проекта:
# confidence ≤ 0.5). Страница произвольного хоста слабее ответа объявленного
# API, и это разница по существу, а не оттенок: у API есть условия
# использования и формат, у страницы — только вёрстка.
CONFIDENCE_BACKEND = 0.5
CONFIDENCE_PAGE = 0.35


# --------------------------------------------------------------- декларации


@dataclass(frozen=True)
class Backend:
    """Поисковый источник: декларация для OSIRIS плюс карта его выдачи.

    Карта выдачи (`hits_path`, `field_*`, `url_template`) живёт ЗДЕСЬ, а не в
    декларации, по механической причине: `normalize_source` строит `Source` с
    фиксированным набором полей и любой лишний ключ декларации до парсера не
    доезжает. Прятать карту в `notes` строкой JSON было бы хуже — `notes`
    читает владелец.

    `trusted_hosts` — поправка D2 и она не косметика. `w`-токен означает
    «адрес выбрал не тот, кто может быть враждебен», а URL внутри тела выдачи
    PyPI, HN или SearXNG выбирает ТРЕТЬЕ ЛИЦО. Поэтому список доверенных
    хостов выдачи объявляется явно, а у источников общего веба он ПУСТ: ни
    один адрес из их выдачи не считается выбранным backend'ом, и открытие
    любого из них стоит одобрения владельца.
    """

    id: str
    honest_capability: str          # одна строка про то, что источник РЕАЛЬНО умеет
    shape: str                      # opensearch | json | searxng
    via: str = "osiris"             # osiris | private_door
    keyless: bool = True
    general_web: bool = False
    needs_key: str = ""             # ключ ищется вызывающим по этому имени; см. api_keys
    env_flag: str = ""              # без этой переменной окружения backend'а не существует
    trusted_hosts: tuple[str, ...] = ()
    decl: Mapping[str, Any] | None = None
    hits_path: str = ""             # точечный путь до списка результатов; "" = само тело
    field_title: str = ""
    field_url: str = ""
    field_snippet: str = ""
    field_engine: str = ""
    url_template: str = ""          # запасной адрес из полей результата: "https://…/{id}"
    max_hits: int = MAX_HITS_CAP
    order: int = 100                # порядок предпочтения в pick_backend
    keywords: tuple[str, ...] = dc_field(default_factory=tuple)


def _api_decl(*, source_id: str, base_url: str, path_template: str, license_: str,
              honest: str, rate: int, notes_tail: str = "") -> dict[str, Any]:
    """Декларация backend'а категории A. Полей ровно столько, сколько нужно.

    `honest_capability` кладётся в `notes` намеренно: `notes` — это то, что
    владелец видит в `GET /osiris/sources`, и текст обязан быть один и тот же в
    реестре и в шапке выдачи. Собранная из одного значения строка не может
    разойтись сама с собой, а два поля — могут.
    """
    return {
        "id": source_id,
        "category": "A",
        "method": "api",
        "base_url": base_url,
        "auth_mode": "none",
        "rate_limit_per_min": rate,
        "license": license_,
        "provides": ["search.query", "search.result"],
        "not_provides": ["содержимое страниц", "персональные данные о частных лицах",
                         "выдачу Google, Bing и Яндекса"],
        "tos_checked_at": TOS_CHECKED_AT,
        "parser": "",                      # проставляется ниже по shape
        "path_template": path_template,
        # E2: TTL кэша обязателен и ненулевой в КАЖДОЙ декларации.
        # `normalize_source` делает max(0, int(decl.get(...) or 0)) — отсутствие
        # ключа даёт НОЛЬ, то есть `raw_is_fresh` навсегда False и кэша нет
        # вовсе. Ноль здесь не «по умолчанию», а молчаливая поломка.
        "cache_ttl_seconds": config.CACHE_TTL_SEARCH,
        "default_confidence": CONFIDENCE_BACKEND,
        "contact": osiris.USER_AGENT,
        "notes": honest + (" " + notes_tail if notes_tail else "")
                 + f" Условия использования проверялись {TOS_CHECKED_AT}; "
                   f"перепроверьте перед тем, как полагаться на источник.",
    }


_SHAPE_PARSER = {"opensearch": "web.serp_opensearch",
                 "json": "web.serp_json",
                 "searxng": "web.serp_searxng"}


def _backend(bk: Backend) -> Backend:
    """Дописать в декларацию имя парсера по форме выдачи: два места, где живёт
    одна и та же величина, — это одно место, где она однажды разойдётся."""
    if bk.decl is None:
        return bk
    decl = dict(bk.decl)
    decl["parser"] = _SHAPE_PARSER[bk.shape]
    return Backend(**{**bk.__dict__, "decl": decl})


_WIKI_HONEST_RU = ("энциклопедические статьи Википедии на русском языке; это НЕ открытый "
                   "веб, не новости и не документация")
_WIKI_HONEST_EN = ("энциклопедические статьи Википедии на английском языке; это НЕ открытый "
                   "веб, не новости и не документация")

_WIKI_PATH = ("/w/api.php?action=opensearch&format=json&formatversion=1"
              "&namespace=0&limit=10&search={subject}")

BACKENDS: tuple[Backend, ...] = tuple(_backend(b) for b in (
    Backend(
        id="wikipedia-opensearch-ru",
        honest_capability=_WIKI_HONEST_RU,
        shape="opensearch",
        order=40,
        keywords=("кто такой", "что такое", "биография", "определение", "история"),
        # Доверенные хосты выдачи — только сам этот вики-хост и его мобильная
        # форма. Весь домен викимедиа сюда не входит: opensearch отдаёт адреса
        # ровно той вики, к которой обратились, а расширить список «на всякий
        # случай» значит раздать `w` тому, чего мы в выдаче не видели.
        trusted_hosts=("ru.wikipedia.org", "ru.m.wikipedia.org"),
        decl=_api_decl(source_id="wikipedia-opensearch-ru",
                       base_url="https://ru.wikipedia.org",
                       path_template=_WIKI_PATH,
                       license_="CC BY-SA 4.0",
                       honest=_WIKI_HONEST_RU,
                       rate=30,
                       notes_tail="Текст требует указания авторства."),
    ),
    Backend(
        id="wikipedia-opensearch-en",
        honest_capability=_WIKI_HONEST_EN,
        shape="opensearch",
        order=41,
        keywords=("who is", "what is", "biography", "definition", "history"),
        trusted_hosts=("en.wikipedia.org", "en.m.wikipedia.org"),
        decl=_api_decl(source_id="wikipedia-opensearch-en",
                       base_url="https://en.wikipedia.org",
                       path_template=_WIKI_PATH,
                       license_="CC BY-SA 4.0",
                       honest=_WIKI_HONEST_EN,
                       rate=30,
                       notes_tail="Текст требует указания авторства."),
    ),
    Backend(
        id="stackexchange",
        honest_capability=("вопросы и ответы Stack Overflow по программированию; "
                           "ответы пишут люди и они бывают устаревшими"),
        shape="json",
        order=30,
        keywords=("error", "exception", "traceback", "ошибка", "не работает",
                  "как исправить", "stack overflow", "stacktrace"),
        hits_path="items",
        field_title="title",
        field_url="link",
        field_snippet="",
        trusted_hosts=("stackoverflow.com",),
        decl=_api_decl(source_id="stackexchange",
                       base_url="https://api.stackexchange.com",
                       path_template=("/2.3/search/advanced?order=desc&sort=relevance"
                                      "&pagesize=10&site=stackoverflow&q={subject}"),
                       license_="CC BY-SA 4.0",
                       honest=("вопросы и ответы Stack Overflow по программированию; "
                               "ответы пишут люди и они бывают устаревшими"),
                       rate=20,
                       notes_tail="Без ключа доступна квота 300 запросов в сутки на адрес."),
    ),
    Backend(
        id="hn-algolia",
        honest_capability=("обсуждения Hacker News; ссылки из выдачи ведут на ПРОИЗВОЛЬНЫЕ "
                           "сайты, и ни один такой адрес не считается выбранным источником"),
        shape="json",
        order=60,
        keywords=("hacker news", "hn", "обсуждение", "discussion", "show hn"),
        hits_path="hits",
        field_title="title",
        field_url="url",
        field_snippet="story_text",
        url_template="https://news.ycombinator.com/item?id={objectID}",
        # Доверенная только собственная поверхность HN: страница обсуждения. Всё
        # остальное в выдаче — чужие сайты, выбранные третьим лицом (D2).
        trusted_hosts=("news.ycombinator.com",),
        decl=_api_decl(source_id="hn-algolia",
                       base_url="https://hn.algolia.com",
                       path_template="/api/v1/search?tags=story&hitsPerPage=10&query={subject}",
                       license_="MIT (API), содержимое принадлежит авторам",
                       honest=("обсуждения Hacker News; ссылки из выдачи ведут на "
                               "ПРОИЗВОЛЬНЫЕ сайты"),
                       rate=20),
    ),
    Backend(
        id="openalex",
        honest_capability=("научные работы и их метаданные (OpenAlex); полных текстов нет, "
                           "переход на издателя — отдельный адрес"),
        shape="json",
        order=50,
        keywords=("paper", "статья", "исследование", "doi", "публикация", "research"),
        hits_path="results",
        field_title="display_name",
        field_url="doi",
        field_snippet="",
        url_template="{id}",
        trusted_hosts=("doi.org", "openalex.org", "api.openalex.org"),
        decl=_api_decl(source_id="openalex",
                       base_url="https://api.openalex.org",
                       path_template="/works?per-page=10&search={subject}",
                       license_="CC0",
                       honest=("научные работы и их метаданные; полных текстов нет"),
                       rate=20,
                       notes_tail=("Вежливый пул просит контакт — он уже есть в "
                                   "User-Agent слоя происхождения.")),
    ),
    Backend(
        id="pypi",
        honest_capability=("карточка ОДНОГО пакета PyPI по точному имени; это справка, "
                           "а не поиск: запрос из нескольких слов вернёт «нет такого»"),
        shape="json",
        order=20,
        keywords=("pypi", "pip install", "python package", "питон пакет"),
        hits_path="info",
        field_title="name",
        field_url="package_url",
        field_snippet="summary",
        max_hits=1,
        trusted_hosts=("pypi.org", "files.pythonhosted.org"),
        decl=_api_decl(source_id="pypi",
                       base_url="https://pypi.org",
                       path_template="/pypi/{subject}/json",
                       license_="метаданные PyPI, содержимое принадлежит авторам пакетов",
                       honest=("карточка ОДНОГО пакета PyPI по точному имени"),
                       rate=20),
    ),
    Backend(
        id="crates-io",
        honest_capability=("пакеты crates.io для Rust по имени и описанию; это реестр "
                           "пакетов, а не документация и не открытый веб"),
        shape="json",
        order=21,
        keywords=("crate", "cargo", "rust", "крейт"),
        hits_path="crates",
        field_title="name",
        field_url="",
        field_snippet="description",
        url_template="https://crates.io/crates/{name}",
        trusted_hosts=("crates.io", "docs.rs"),
        decl=_api_decl(source_id="crates-io",
                       base_url="https://crates.io",
                       path_template="/api/v1/crates?per_page=10&q={subject}",
                       license_="метаданные crates.io, содержимое принадлежит авторам",
                       honest=("пакеты crates.io для Rust по имени и описанию"),
                       rate=20,
                       notes_tail="Реестр требует осмысленного User-Agent — он задан слоем."),
    ),
    Backend(
        id="brave-search",
        honest_capability=("общий веб-поиск через официальный API Brave; выдачу выбирает "
                           "Brave, поэтому НИ ОДИН её адрес не считается выбранным нами"),
        shape="json",
        general_web=True,
        keyless=False,
        needs_key="brave-search",
        order=11,
        hits_path="web.results",
        field_title="title",
        field_url="url",
        field_snippet="description",
        # D2: у источника ОБЩЕГО веба доверенных хостов выдачи нет вовсе.
        trusted_hosts=(),
        decl=_api_decl(source_id="brave-search",
                       base_url="https://api.search.brave.com",
                       path_template="/res/v1/web/search?count=10&q={subject}",
                       license_="условия Brave Search API",
                       honest=("общий веб-поиск через официальный API Brave"),
                       rate=20,
                       notes_tail=("Ключ задаётся владельцем и подставляется транспортом "
                                   "только на хост самого источника; без ключа источник "
                                   "виден в реестре и НЕ опрашивается.")),
    ),
    # SearXNG владельца НЕ объявляется источником OSIRIS, и это не упущение.
    # `normalize_source` зовёт `checked_url` → `psec.validate_url` без
    # `allow_private`, а свой инстанс живёт на 127.0.0.1 — то есть декларация
    # отклоняется чужой проверкой egress, и правильно делает. Обойти её своей
    # копией значило бы завести вторую границу вместо одной; поэтому SearXNG
    # опрашивается через ЕДИНСТВЕННУЮ приватную дверь модуля
    # (`net.searxng_fetch`), а разбор его тела делает `parse_serp` — та же
    # функция, что и для остальных, без отдельной ветки правды.
    Backend(
        id="searxng-local",
        honest_capability=("общий веб-поиск через ваш собственный SearXNG; это единственный "
                           "путь к открытому вебу, который не нарушает ничьих условий"),
        shape="searxng",
        via="private_door",
        general_web=True,
        keyless=True,
        env_flag="BOSSMAN_WEB_SEARXNG_URL",
        order=10,
        hits_path="results",
        field_title="title",
        field_url="url",
        field_snippet="content",
        field_engine="engine",
        trusted_hosts=(),
        decl=None,
    ),
))


def _declaration_problem(bk: Backend) -> str:
    """Причина, по которой backend НЕ будет объявлен. Пустая строка — годен.

    Проверка живёт здесь, а не в `normalize_source`, потому что это требования
    ЭТОЙ фичи поверх общих: чужая функция обязана пускать http (её зовут и
    другие), а нам http нельзя ни при каких условиях.
    """
    if not bk.honest_capability.strip():
        return "нет honest_capability: источник без честного описания не объявляется"
    if bk.shape not in _SHAPE_PARSER:
        return f"неизвестная форма выдачи {bk.shape!r}"
    if bk.decl is None:
        return ""                       # backend приватной двери, декларации нет
    base = str(bk.decl.get("base_url") or "")
    # A5, без исключений. Формулировка правила взята у транспорта
    # (`net.https_required`), а не написана здесь второй раз: «кому можно
    # верить» и «куда мы ходим» обязаны быть одним утверждением, иначе они
    # разойдутся, и источник окажется объявлен по одному правилу, а прочитан по
    # другому. Отказ именно на ОБЪЯВЛЕНИИ: негодного источника нет в реестре
    # вовсе, а отказ на вызове означал бы, что он там есть и однажды кто-то
    # позовёт его в обход проверки.
    why_http = net.https_required(base)
    if why_http is not None:
        return why_http
    ttl = bk.decl.get("cache_ttl_seconds")
    # E2: ноль здесь означал бы «кэша нет», а `fresh=false` — мёртвый параметр.
    if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
        return "cache_ttl_seconds обязателен и должен быть больше нуля"
    if not bk.decl.get("provides"):
        return "provides обязателен"
    why = config.serp_reason(base)
    if why is not None:
        return f"адрес источника попадает под запрет разбора выдачи: {why}"
    return ""


BACKENDS_BY_ID: Mapping[str, Backend] = MappingProxyType({b.id: b for b in BACKENDS})
DECL_PROBLEMS: Mapping[str, str] = MappingProxyType(
    {b.id: p for b in BACKENDS if (p := _declaration_problem(b))})


def backend_by_id(source_id: str) -> Backend | None:
    return BACKENDS_BY_ID.get(str(source_id or ""))


def decl_of(backend: Backend) -> dict[str, Any] | None:
    """Свежая копия декларации. Общий словарь наружу не отдаётся: один вызов,
    случайно дописавший в него ключ, менял бы объявление источника для всего
    процесса и для всех последующих прогонов."""
    return None if backend.decl is None else copy.deepcopy(dict(backend.decl))


# ------------------------------------------------- источник на прочитанный хост

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-._]{0,251}[a-z0-9])?(:[0-9]{1,5})?$")


def host_source_decl(host: str) -> dict[str, Any]:
    """Декларация источника категории C для прочитанного хоста.

    `id` несёт хвост из sha256 (поправка D4): `slug()` НЕ инъективен —
    `docs.python.org` и `docs-python.org` дают одну и ту же строку, то есть
    делили бы одну карточку источника вместе с её пометкой «проверен живьём» и
    один лимит запросов на два разных сайта. Хвост восемь знаков достаточно
    длинный, чтобы столкновение перестало быть бытовым событием, и достаточно
    короткий, чтобы владелец узнавал источник по имени.
    """
    clean = (host or "").strip().lower().rstrip(".")
    return {
        "id": config.host_source_id(clean),
        "category": "C",
        "method": "fetch",
        "base_url": f"https://{clean}",
        "auth_mode": "none",
        "rate_limit_per_min": config.HOST_RATE_PER_MIN,
        "license": "не определена (страница из сети)",
        "provides": ["page.text"],
        "not_provides": ["лицензию на текст страницы", "гарантию, что страница не изменится",
                         "содержимое, которое отдаётся только по входу"],
        "tos_checked_at": TOS_CHECKED_AT,
        "parser": "web.page_text",
        # Шаблон пути не используется: страницу читает свой конвейер в net.py,
        # потому что `osiris.collect` делает json.loads(body) и HTML через него
        # не проходит принципиально. Значение оставлено осмысленным, чтобы
        # карточка источника в реестре не выглядела сломанной.
        "path_template": "/{subject}",
        "cache_ttl_seconds": config.CACHE_TTL_PAGE,   # E2
        "default_confidence": CONFIDENCE_PAGE,
        "contact": osiris.USER_AGENT,
        "notes": ("Источник создан автоматически при первом чтении страницы этого хоста. "
                  "Лицензия текста неизвестна, свежесть подтверждается только временем "
                  "загрузки, конечный адрес после редиректов транспорт не возвращает."),
    }


def _guard_target(value: str, *, what: str) -> str:
    """Общий разбор запретов ДО чужой нормализации.

    Порядок обязателен: свой кортеж `SERP_DENY` и стоки утечки проверяются
    ПЕРВЫМИ, потому что чужой словарь запретов эту тему не покрывает (в его
    восьми regex нет ни `serp`, ни `search`, ни `google`), а полагаться на то,
    что нас случайно спасёт чей-то robots.txt, — это не запрет, а везение.
    """
    why = config.serp_reason(value)
    if why is not None:
        raise osiris.ForbiddenSourceError(
            f"{what} отклонён: {why}", code="serp_scrape")
    host = urlsplit(value).hostname if "://" in value else value.split("/")[0]
    if config.is_exfil_sink((host or "").rstrip(".")):
        raise osiris.ForbiddenSourceError(
            f"{what} отклонён: единственная функция этого адреса — принять и показать "
            f"отправителю то, что ему прислали; открытие такого адреса это не чтение, "
            f"а отправка", code="exfil_sink")
    return value


def ensure_source(st, decl: Mapping[str, Any]) -> osiris.Source:
    """Объявить источник, если его ещё нет. Отказ — ИСКЛЮЧЕНИЕ, и это верно.

    Отказы внешнего мира этот пакет отдаёт данными, но объявление источника —
    не внешний мир, а решение «имеем ли мы право сюда ходить». Здесь fail-closed
    обязан быть громким и обязан нести ЧУЖОЙ код причины: `dehashed.com`
    отклоняется как `leaked_database`, `pimeyes.com` — как `biometrics`. Это и
    есть доказательство переиспользования словаря, а не его копии.

    Запись на диск ленивая: уже известный источник возвращается как есть, чтобы
    первое чтение хоста не переписывало его карточку вместе с честным
    `live_status`, добытым настоящей сетью.
    """
    if not isinstance(decl, Mapping):
        raise osiris.OsirisError("декларация источника должна быть объектом")
    if not config.both_enabled():
        # Выключенная фича обязана вести себя ровно как её отсутствие, а это
        # единственная функция файла, которая пишет на диск. Проверка стоит
        # здесь, а не только у вызывающих: их будет несколько, и однажды один
        # из них её забудет.
        raise osiris.OsirisError(
            f"источник не объявляется: нужны {config.FLAG} и {config.OSIRIS_FLAG}")
    source_id = str(decl.get("id") or "")
    existing = st.sources().get(source_id)
    if existing is not None:
        return existing
    _guard_target(str(decl.get("base_url") or ""), what="адрес источника")
    source = osiris.normalize_source(dict(decl))
    st.save_source(source)
    return source


def ensure_host_source(st, host: str, *, url: str = "") -> osiris.Source:
    """Источник категории C на прочитанный хост — создаётся при первом чтении.

    `url` необязателен, но его стоит передавать: проверка по ОДНОМУ ХОСТУ не
    ловит страницу выдачи. `google.com` сам по себе не запрещён (это домашняя
    страница), запрещён `google.com/search`, и увидеть разницу можно только по
    полному адресу. Проверка полного адреса есть и в конвейере чтения до сети —
    здесь она вторая, потому что источник, однажды попавший в реестр, потом
    выглядит как разрешённый.

    Хост сюда приходит УЖЕ канонизованным (`html_text.canon_url` снял
    завершающую точку и привёл IDN к punycode). Проверка на ASCII повторяется
    всё равно: если сюда однажды придёт не-ASCII имя, значит канонизацию
    пропустили, и уходить наружу с таким именем нельзя — словарь пинов
    `_PinnedBackend` промахнётся мимо ключа, а промах у него означает повторный
    резолв, то есть fail-open (C1).
    """
    clean = (host or "").strip().lower().rstrip(".")
    if not clean:
        raise osiris.OsirisError("хост обязателен")
    if not clean.isascii() or not _HOST_RE.fullmatch(clean):
        raise osiris.OsirisError(
            f"имя хоста {host!r} не приведено к ASCII-форме: до сети такой адрес "
            f"не уходит, иначе анти-rebinding молча откатывается на повторный резолв")
    if url:
        _guard_target(url, what="адрес страницы")
    return ensure_source(st, host_source_decl(clean))


# --------------------------------------------------------------- разбор выдачи


def _dig(payload: Any, path: str) -> Any:
    """Значение по точечному пути; None, если пути нет. Своя копия в шесть
    строк вместо чужого приватного `osiris._dig`: приватное имя имеет полное
    право поменяться без предупреждения, и тогда сломается разбор выдачи."""
    if not path:
        return payload
    cur = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _text(value: Any, limit: int) -> str:
    """Внешний текст к печати: невидимое снято, пробелы схлопнуты, длина
    ограничена, команды ассистенту помечены.

    `defang` здесь, а не только в рендере (поправка B5): меры против инъекции
    применены ко ВСЕМУ тексту внешнего происхождения, а заголовок результата —
    такой же внешний текст, как абзац страницы. Функция идемпотентна, поэтому
    повторное обезвреживание в рендере ничего не испортит и не удвоит счётчик.
    Сырое тело выдачи при этом остаётся на диске нетронутым: доказательство —
    это `raw_ref`, а не то, что мы показали.
    """
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    clean = html_text.normalize_ws(value)[:limit]
    return html_text.defang(clean)[0]


_TEMPLATE_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,40})\}")
_TEMPLATE_VALUE_RE = re.compile(r"^[A-Za-z0-9._~:/\-]{1,300}$")


def _from_template(template: str, hit: Mapping[str, Any]) -> str:
    """Собрать адрес из полей результата. Любое подозрительное значение — отказ.

    Подстановка ограничена узким набором знаков намеренно: значение приходит из
    тела выдачи, то есть от третьего лица, и «собрать адрес из чужой строки» —
    это ровно тот приём, которым делают открытый редирект. Проще отказаться от
    одного результата, чем один раз собрать адрес, которого никто не выбирал.
    """
    out = template
    for name in set(_TEMPLATE_FIELD_RE.findall(template)):
        raw = hit.get(name)
        if raw is None:
            return ""
        text = str(raw)
        if not _TEMPLATE_VALUE_RE.fullmatch(text):
            return ""
        out = out.replace("{" + name + "}", text)
    return out


def _clean_hit_url(raw: Any) -> tuple[str, str]:
    """(канонический https-адрес, хост) либо ("", "") — результат отбрасывается.

    Отбрасываем молча, но СЧИТАЕМ: число отброшенных уходит в наблюдение и в
    ответ `run_search`, потому что «источник дал десять результатов, шесть из
    них мы не показали» — это факт, который владелец имеет право знать.
    """
    if not isinstance(raw, str) or not raw.strip():
        return "", ""
    try:
        url = html_text.canon_url(raw)
    except (ValueError, TypeError):
        return "", ""
    # http-адрес до сети всё равно не дойдёт (precheck в net.py), а токен на
    # него был бы обещанием, которого код не выполнит.
    if not url.startswith("https://"):
        return "", ""
    host = (urlsplit(url).hostname or "").rstrip(".")
    if not host or config.serp_reason(url) is not None or config.is_exfil_sink(host):
        return "", ""
    return url, host


def _hit_rows(backend: Backend, node: Any) -> tuple[list[dict[str, Any]], int]:
    """Список результатов → строки выдачи. Второе значение — сколько отброшено."""
    items = [node] if isinstance(node, dict) else list(node)
    rows: list[dict[str, Any]] = []
    dropped = 0
    for raw in items:
        if len(rows) >= max(1, min(backend.max_hits, MAX_HITS_CAP)):
            break
        if not isinstance(raw, dict):
            dropped += 1
            continue
        candidate = _dig(raw, backend.field_url) if backend.field_url else None
        if not isinstance(candidate, str) or not candidate.strip():
            candidate = _from_template(backend.url_template, raw) if backend.url_template else ""
        url, host = _clean_hit_url(candidate)
        if not url:
            dropped += 1
            continue
        rows.append({
            "rank": len(rows) + 1,
            "title": _text(_dig(raw, backend.field_title) if backend.field_title else "",
                           HIT_TITLE_MAX) or host,
            "url": url,
            "host": host,
            "snippet": _text(_dig(raw, backend.field_snippet) if backend.field_snippet else "",
                             HIT_SNIPPET_MAX),
            "engine": _text(_dig(raw, backend.field_engine) if backend.field_engine else "",
                            40) or backend.id,
            # D2: сам backend говорит, считается ли этот адрес выбранным ИМ.
            # Ledger.mint получит этот факт списком trusted_hosts и решит,
            # `w` (открытие без вопроса) или `l` (одобрение владельца).
            "trusted": host in backend.trusted_hosts,
        })
    return rows, dropped


def _parse_opensearch(backend: Backend, payload: Any) -> dict[str, Any]:
    """OpenSearch отвечает массивом [запрос, заголовки, описания, адреса]."""
    if not isinstance(payload, list) or len(payload) < 4:
        return {"outcome": "bad_response", "detail": "ответ не похож на OpenSearch",
                "hits": [], "dropped": 0}
    titles, descs, urls = payload[1], payload[2], payload[3]
    if not all(isinstance(x, list) for x in (titles, descs, urls)):
        return {"outcome": "bad_response", "detail": "ответ не похож на OpenSearch",
                "hits": [], "dropped": 0}
    if not titles:
        return {"outcome": "empty_result", "detail": "", "hits": [], "dropped": 0}
    node = [{"title": t,
             "snippet": descs[i] if i < len(descs) else "",
             "url": urls[i] if i < len(urls) else ""}
            for i, t in enumerate(titles)]
    shim = Backend(id=backend.id, honest_capability=backend.honest_capability,
                   shape="json", trusted_hosts=backend.trusted_hosts,
                   field_title="title", field_url="url", field_snippet="snippet",
                   max_hits=backend.max_hits)
    rows, dropped = _hit_rows(shim, node)
    if not rows:
        return {"outcome": "empty_result",
                "detail": f"источник вернул {len(node)} строк, ни одна не годна к показу",
                "hits": [], "dropped": dropped}
    return {"outcome": "ok", "detail": "", "hits": rows, "dropped": dropped}


def _parse_searxng(backend: Backend, payload: Any) -> dict[str, Any]:
    """SearXNG — единственный формат, который САМ сообщает о смерти движков.

    Ради этого и написана поправка E1: при капче в апстриме инстанс отвечает
    HTTP 200 и `{"results": [], "unresponsive_engines": [["google","CAPTCHA"]]}`.
    Отдать это как «ничего не найдено» значит соврать: поиск НЕ СОСТОЯЛСЯ.
    """
    if not isinstance(payload, dict) or "results" not in payload:
        return {"outcome": "bad_response", "detail": "ответ не похож на выдачу SearXNG",
                "hits": [], "dropped": 0}
    down = payload.get("unresponsive_engines") or []
    names: list[str] = []
    for entry in down if isinstance(down, list) else ():
        if isinstance(entry, (list, tuple)) and entry:
            names.append(_text(entry[0], 40))
        elif isinstance(entry, str):
            names.append(_text(entry, 40))
    results = payload.get("results")
    rows, dropped = _hit_rows(backend, results) if isinstance(results, list) else ([], 0)
    if not rows and names:
        return {"outcome": "engines_down",
                "detail": "не ответили: " + ", ".join(names[:8]),
                "hits": [], "dropped": dropped}
    if not rows:
        return {"outcome": "empty_result", "detail": "", "hits": [], "dropped": dropped}
    detail = ("часть движков не ответила: " + ", ".join(names[:8])) if names else ""
    return {"outcome": "ok", "detail": detail, "hits": rows, "dropped": dropped}


# Запасные имена полей для источника, которого нет в BACKENDS: владелец имеет
# право объявить свой JSON-API через POST /osiris/sources, и парсер обязан хоть
# что-то с ним уметь. Порядок — от самого частого к редкому.
_GENERIC_LIST_KEYS = ("results", "items", "hits", "data", "docs", "entries")
_GENERIC_TITLE = ("title", "name", "display_name", "headline")
_GENERIC_URL = ("url", "link", "href", "landing_page_url", "html_url")
_GENERIC_SNIPPET = ("snippet", "description", "summary", "content", "abstract", "excerpt")


def _generic_backend(backend: Backend, payload: Any) -> tuple[Backend, Any]:
    """Догадаться о форме тела, если карты выдачи нет. Догадка честная: если ни
    одно из известных имён не нашлось, вернётся None и исход станет
    `bad_response`, а не «ничего не найдено»."""
    node = _dig(payload, backend.hits_path) if backend.hits_path else None
    if node is None and not backend.hits_path:
        if isinstance(payload, list):
            node = payload
        elif isinstance(payload, dict):
            for key in _GENERIC_LIST_KEYS:
                if isinstance(payload.get(key), list):
                    node = payload[key]
                    break
    if node is None:
        return backend, None
    sample = node[0] if isinstance(node, list) and node and isinstance(node[0], dict) else \
        (node if isinstance(node, dict) else {})

    def pick(declared: str, names: Sequence[str]) -> str:
        if declared:
            return declared
        return next((n for n in names if n in sample), "")

    guessed = Backend(
        id=backend.id, honest_capability=backend.honest_capability, shape="json",
        trusted_hosts=backend.trusted_hosts, url_template=backend.url_template,
        field_title=pick(backend.field_title, _GENERIC_TITLE),
        field_url=pick(backend.field_url, _GENERIC_URL),
        field_snippet=pick(backend.field_snippet, _GENERIC_SNIPPET),
        field_engine=backend.field_engine, max_hits=backend.max_hits)
    return guessed, node


def parse_serp(backend: Backend, payload: Any) -> dict[str, Any]:
    """Тело выдачи → {"outcome", "detail", "hits", "dropped"}. Чистая функция.

    Сети здесь нет и не будет: разбор обязан быть проверяем на записанном теле,
    иначе тест на «движки не ответили» пришлось бы ставить против живого
    интернета, а такой тест не тест.

    Четыре исхода, и они РАЗНЫЕ по существу (E1): `ok`, `empty_result`
    («движок ответил: ничего не найдено»), `engines_down` («поиск НЕ
    состоялся»), `bad_response` («ответ не той формы» — то есть источник ответил
    не тем, и выдавать это за отсутствие результата нельзя).
    """
    if backend.shape == "opensearch":
        return _parse_opensearch(backend, payload)
    if backend.shape == "searxng":
        return _parse_searxng(backend, payload)

    node = _dig(payload, backend.hits_path) if backend.hits_path else payload
    if node is None or (not backend.field_title and not backend.field_url
                        and not backend.url_template):
        backend, node = _generic_backend(backend, payload)
    if node is None:
        return {"outcome": "bad_response",
                "detail": "в ответе нет списка результатов там, где он объявлен",
                "hits": [], "dropped": 0}
    if not isinstance(node, (list, dict)):
        return {"outcome": "bad_response", "detail": "список результатов не список",
                "hits": [], "dropped": 0}
    if isinstance(node, list) and not node:
        return {"outcome": "empty_result", "detail": "", "hits": [], "dropped": 0}
    rows, dropped = _hit_rows(backend, node)
    if not rows:
        return {"outcome": "empty_result",
                "detail": "источник ответил, но ни один адрес не годен к показу",
                "hits": [], "dropped": dropped}
    return {"outcome": "ok", "detail": "", "hits": rows, "dropped": dropped}


# ------------------------------------------------------- парсеры для osiris


def serp_observations(source: osiris.Source, subject: str, payload: Any, *, url: str,
                      raw_ref: str, collected_at, fetched_at,
                      shape: str = "") -> list[osiris.Observation]:
    """Выдача → наблюдения. Сигнатура осирисовская, ею же пользуется `collect`.

    Наблюдение `search.query` выдаётся ВСЕГДА и ПЕРВЫМ — до разбора результатов
    и независимо от их наличия. Это поправка E1 и она держит сразу две вещи:

      * `collect` бросает `OsirisError`, если парсер вернул пустой список, и
        тогда «движки умерли», «ничего не нашлось» и «ответ не той формы»
        становятся одним отказом. Непустой список эту склейку убирает без
        единой правки чужого файла;
      * байты ушли с машины владельца — значит след обязан остаться, даже
        когда поиск не удался. След без результата честнее, чем отсутствие
        следа.

    `observed_at = fetched_at` (E3): на попадании в кэш `collect` передаёт сюда
    время НАСТОЯЩЕЙ загрузки, поэтому наблюдение не утверждает свежести,
    которой никто не проверял.
    """
    backend = BACKENDS_BY_ID.get(source.id)
    if backend is None:
        # Источник объявлен владельцем, а не нами: карты выдачи нет, форму
        # берём из имени парсера, поля угадываем. Честнее угадать и сказать об
        # этом в `detail`, чем отказать владельцу в его собственном источнике.
        backend = Backend(id=source.id, honest_capability=source.notes or source.id,
                          shape=shape or "json")
    elif shape and backend.shape != shape:
        # Декларация и имя зарегистрированного парсера разошлись — это ошибка
        # объявления, а не выдачи, и она не должна тихо разбираться «как-нибудь».
        return _only_head(source, subject, backend, url=url, raw_ref=raw_ref,
                          collected_at=collected_at, fetched_at=fetched_at,
                          outcome="bad_response",
                          detail=f"источник объявлен как {backend.shape}, "
                                 f"а разбирается как {shape}",
                          hits=[], dropped=0)

    parsed = parse_serp(backend, payload)
    return _only_head(source, subject, backend, url=url, raw_ref=raw_ref,
                      collected_at=collected_at, fetched_at=fetched_at,
                      outcome=parsed["outcome"], detail=parsed["detail"],
                      hits=parsed["hits"], dropped=parsed["dropped"])


def _only_head(source: osiris.Source, subject: str, backend: Backend, *, url: str,
               raw_ref: str, collected_at, fetched_at, outcome: str, detail: str,
               hits: Sequence[Mapping[str, Any]], dropped: int) -> list[osiris.Observation]:
    """Собрать наблюдение запроса и наблюдения результатов.

    `source_url` у КАЖДОГО результата — адрес запроса к API, а не адрес самого
    результата. Это не небрежность: мы наблюдали утверждение «такой адрес
    существует» именно у источника, а не по самому адресу (по нему мы ещё не
    ходили). Побочно это закрывает хрупкость: конструктор `Observation`
    прогоняет `source_url` через проверку egress, и один негодный адрес в
    выдаче уронил бы разбор всей страницы результатов.
    """
    head = osiris.Observation(
        value={"query": subject, "backend": source.id, "engine_url": url,
               "outcome": outcome, "detail": detail, "results": len(hits),
               "dropped": dropped, "honest_capability": backend.honest_capability,
               "trusted_hosts": list(backend.trusted_hosts)},
        subject=subject, source_id=source.id, source_url=url, method=source.method,
        license=source.license, observed_at=fetched_at, collected_at=collected_at,
        confidence=source.default_confidence, raw_ref=raw_ref, attribute="search.query")
    out = [head]
    for hit in hits:
        out.append(osiris.Observation(
            value={"of": head.id, **{k: hit[k] for k in
                                     ("rank", "title", "url", "host", "snippet",
                                      "engine", "trusted") if k in hit}},
            subject=subject, source_id=source.id, source_url=url, method=source.method,
            license=source.license, observed_at=fetched_at, collected_at=collected_at,
            confidence=source.default_confidence, raw_ref=raw_ref,
            attribute="search.result"))
    return out


def _p_opensearch(source, subject, payload, *, url, raw_ref, collected_at, fetched_at):
    return serp_observations(source, subject, payload, url=url, raw_ref=raw_ref,
                             collected_at=collected_at, fetched_at=fetched_at,
                             shape="opensearch")


def _p_json(source, subject, payload, *, url, raw_ref, collected_at, fetched_at):
    return serp_observations(source, subject, payload, url=url, raw_ref=raw_ref,
                             collected_at=collected_at, fetched_at=fetched_at,
                             shape="json")


def _p_searxng(source, subject, payload, *, url, raw_ref, collected_at, fetched_at):
    return serp_observations(source, subject, payload, url=url, raw_ref=raw_ref,
                             collected_at=collected_at, fetched_at=fetched_at,
                             shape="searxng")


_PAGE_REQUIRED = ("chars", "text_sha256", "extractor", "encoding", "replace_ratio",
                  "truncated", "from_cache", "transport")


def page_text_observations(source: osiris.Source, subject: str, payload: Any, *, url: str,
                           raw_ref: str, collected_at, fetched_at) -> list[osiris.Observation]:
    """Прочитанная страница → одно наблюдение `page.text`.

    Через `osiris.collect` эта функция НЕ вызывается никогда: `collect` делает
    `json.loads(body)` до вызова парсера, и HTML через него не проходит
    принципиально. Имя `web.page_text` зарегистрировано потому, что
    `normalize_source` требует `parser in PARSERS`, иначе декларацию
    источника-на-хост нельзя объявить вовсе. А сама функция вызывается ПРЯМО из
    конвейера чтения страницы: там уже есть и извлечение, и время загрузки.
    Заглушки здесь нет — есть одна реализация с двумя входами.

    `payload` — факты об уже выполненном извлечении, а не тело страницы: тело
    лежит в сырье под `raw_ref`, и дублировать его в наблюдении значило бы
    завести второе хранилище содержимого.

    `observed_at = fetched_at` ВСЕГДА (E3): «получено сейчас» на попадании в
    кэш — это выдуманная свежесть, а цитата с выдуманной свежестью хуже
    отсутствия цитаты.
    """
    if not isinstance(payload, Mapping):
        raise osiris.OsirisError(
            "page.text ожидает факты извлечения объектом, а не тело страницы: "
            "HTML через osiris.collect не проходит, читайте страницу конвейером net.py")
    missing = [k for k in _PAGE_REQUIRED if k not in payload]
    if missing:
        raise osiris.OsirisError(f"page.text: в фактах извлечения нет полей {missing}")
    transport = str(payload.get("transport") or "")
    if transport not in ("live", "stub"):
        # D5: подменённый адаптер не имеет права отмыться в след как настоящая
        # сеть. Пустое или выдуманное значение здесь означало бы, что цитату
        # нельзя отличить от стендовой, — а это ровно то, что фича обязана
        # исключать.
        raise osiris.OsirisError("page.text: transport обязан быть 'live' или 'stub'")
    value = {
        "chars": int(payload.get("chars") or 0),
        "text_sha256": str(payload.get("text_sha256") or ""),
        "extractor": str(payload.get("extractor") or html_text.EXTRACTOR_VERSION),
        # D6: смещение цитаты зависит от параметров извлечения, поэтому они
        # записываются вместе с ним, а показ обязан извлекать теми же.
        "max_chars": int(payload.get("max_chars") or 0),
        "encoding": str(payload.get("encoding") or ""),
        "replace_ratio": float(payload.get("replace_ratio") or 0.0),
        "title": _text(payload.get("title"), HIT_TITLE_MAX),
        "truncated": bool(payload.get("truncated")),
        "stop_reason": str(payload.get("stop_reason") or ""),
        "hidden_dropped": int(payload.get("hidden_dropped") or 0),
        "defanged_lines": int(payload.get("defanged_lines") or 0),
        "status": int(payload.get("status") or 0),
        "from_cache": bool(payload.get("from_cache")),
        "fetched_at": getattr(fetched_at, "isoformat", lambda: str(fetched_at))(),
        "transport": transport,
        # КОНФЛИКТ 10: конечный адрес после редиректов транспорт не возвращает
        # (safe_get собирает httpx.Response без url и request), поэтому в
        # паспорте стоит ЗАПРОШЕННЫЙ канонический адрес и это сказано прямо.
        "requested_url": url,
    }
    if payload.get("of"):
        value["of"] = str(payload["of"])
    return [osiris.Observation(
        value=value, subject=subject, source_id=source.id, source_url=url,
        method=source.method, license=source.license, observed_at=fetched_at,
        collected_at=collected_at, confidence=source.default_confidence,
        raw_ref=raw_ref, attribute="page.text")]


def parsers() -> dict[str, Callable[..., list[osiris.Observation]]]:
    """Имя в `osiris.PARSERS` → функция. Отдельной таблицей, чтобы её можно было
    сверить тестом, не включая флаг и не трогая чужой словарь."""
    return {"web.serp_opensearch": _p_opensearch,
            "web.serp_json": _p_json,
            "web.serp_searxng": _p_searxng,
            "web.page_text": page_text_observations}


def install_parsers() -> bool:
    """Зарегистрировать парсеры в `osiris.PARSERS`. Идемпотентно.

    Это ШТАТНЫЙ и единственный механизм: `normalize_source` проверяет
    `parser in PARSERS`, то есть без регистрации ни одна наша декларация не
    объявится вовсе. Регистрация только при ОБОИХ включённых флагах —
    выключенная фича обязана оставить чужой словарь ровно таким, каким он был.
    """
    if not config.both_enabled():
        return False
    for name, fn in parsers().items():
        osiris.PARSERS[name] = fn
    return True


# ------------------------------------------------------------- субъект поиска


def search_subject(query: str) -> str:
    """Субъект эпизода OSIRIS для поиска — ОН ЖЕ текст, уходящий движку.

    Здесь единственное сознательное расхождение с разделом 9 проекта, и оно
    механическое, а не вкусовое. Раздел 9 предлагает субъект `web:<запрос>`, но
    `Source.url_for` подставляет субъект В САМ АДРЕС запроса
    (`quote(subject, safe="")`), а `osiris.collect` другого канала для текста
    запроса не имеет вовсе. С префиксом движку ушло бы `web%3A...`, и Википедия
    честно ответила бы «ничего не найдено» на каждый вопрос — то есть
    молчаливый провал с полным паспортом, худший класс отказа во всём проекте.

    Поэтому субъектом становится сам канонический запрос, а пространство имён
    несёт `source_id` (все наши идентификаторы начинаются с имени backend'а или
    с `web-`). Функция идемпотентна и снимает префикс `web:`, если вызывающий
    всё-таки собрал субъект по букве раздела 9: расходиться должны не мы с ним,
    а он с самим собой.
    """
    text = html_text.normalize_ws(query or "")
    if text.lower().startswith("web:"):
        text = text[4:].strip()
    # "/" в субъекте запрещён самим OSIRIS (он же имя каталога), а обрезать
    # запрос молча нельзя: заменяем на пробел, смысл фразы при этом сохраняется.
    text = html_text.normalize_ws(text.replace("/", " "))
    return text[:osiris.MAX_SUBJECT]


def query_of(subject: str) -> str:
    """То же самое с другой стороны: субъект → текст запроса для показа."""
    return search_subject(subject)


# ---------------------------------------------------------------- готовность


def _env_values() -> dict[str, str]:
    """Настройки, от которых зависит само существование backend'а.

    Значение берётся у `config`, а не из `os.environ`: одна настройка обязана
    читаться в одном месте с одними границами, иначе владелец получит
    поведение, которого нет ни в одном документе. Функция, а не константа, —
    чтобы подмена значения в тесте была видна без перезагрузки модуля.
    """
    return {"BOSSMAN_WEB_SEARXNG_URL": config.SEARXNG_URL}


def _ready(backend: Backend, *, api_keys: Mapping[str, str] | None) -> tuple[bool, str]:
    """Готов ли backend и ПОЧЕМУ нет. Причина — текст, а не код: её читает
    владелец, и «not_ready» ему ничего не объясняет."""
    problem = DECL_PROBLEMS.get(backend.id, "")
    if problem:
        return False, f"объявление отклонено: {problem}"
    if backend.env_flag and not _env_values().get(backend.env_flag, ""):
        return False, (f"не настроен: задайте {backend.env_flag} — это адрес вашего "
                       f"собственного инстанса")
    if backend.needs_key:
        key = (api_keys or {}).get(backend.needs_key) or (api_keys or {}).get(backend.id)
        if not key:
            # Источник ВИДЕН в реестре и НИКОГДА не опрашивается молча: молчаливая
            # попытка без ключа даёт 401, который выглядит как «источник сломался».
            return False, "ключ не задан: источник виден в реестре и не опрашивается"
    return True, ""


def backend_status(svc, *, api_keys: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Готовые факты о backend'ах — ровно те поля, которых ждёт `config.readiness`.

    `api_keys` приходит ПАРАМЕТРОМ и по значению: этот файл не читает и не
    хранит секретов, а присутствие ключа — единственное, что ему нужно знать.
    Отсутствие словаря означает «ключей нет», то есть fail-closed: источник с
    ключом не опрашивается, пока никто явно не сказал, что ключ есть.
    """
    registered: dict[str, osiris.Source] = {}
    if config.both_enabled():
        try:
            registered = osiris.store(svc).sources()
        except Exception:                     # noqa: BLE001 — диск не обязан быть цел
            registered = {}
    rows: list[dict[str, Any]] = []
    for bk in BACKENDS:
        ready, reason = _ready(bk, api_keys=api_keys)
        src = registered.get(bk.id)
        rows.append({
            "id": bk.id,
            "ready": ready,
            "keyless": bk.keyless,
            "general_web": bk.general_web,
            "honest_capability": bk.honest_capability,
            "reason": reason,
            "via": bk.via,
            "needs_key": bk.needs_key,
            "trusted_hosts": list(bk.trusted_hosts),
            "base_url": str((bk.decl or {}).get("base_url") or ""),
            "license": str((bk.decl or {}).get("license") or ""),
            "cache_ttl_seconds": int((bk.decl or {}).get("cache_ttl_seconds") or 0),
            "registered": src is not None,
            "live_status": src.live_status if src is not None else "not_verified_live",
            "live_error": src.live_error if src is not None else "",
        })
    return rows


def readiness(svc, *, api_keys: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Готовность целиком — и владельцу в `GET /api/web`, и модели в шапке.

    Собирается ОДНОЙ чужой функцией из фактов этого файла. Своего текста
    готовности здесь нет намеренно: два текста разойдутся не в первый месяц,
    так в третий, и заметить это будет некому.
    """
    return config.readiness(backends=backend_status(svc, api_keys=api_keys),
                            osiris_on=osiris.enabled(),
                            page_chars=config.PAGE_CHARS_DEFAULT)


_SITE_RE = re.compile(r"^[a-z0-9]([a-z0-9\-._]{0,251}[a-z0-9])?$")


def pick_backend(svc, query: str, site: str = "", *,
                 api_keys: Mapping[str, str] | None = None) -> Backend | None:
    """Кому отдать запрос. None — отдавать некому, и это НЕ «ничего не найдено».

    Правила простые и объяснимые вслух, потому что владелец имеет право знать,
    почему его вопрос ушёл в Википедию, а не в поиск:

      1. общий веб-поиск, если он настроен, — он умеет всё остальное;
      2. `site` без общего поиска: годится только тот узкий источник, который
         сам объявил этот хост доверенным. «Сузить Википедию до docs.python.org»
         невозможно, и делать вид, что получилось, нельзя;
      3. ключевые слова запроса — маршрут к узкому источнику;
      4. язык запроса — Википедия ru или en.

    Подстановки одного источника вместо другого здесь не происходит: выбранный
    backend всегда печатается в шапке результата вместе со своим
    `honest_capability`, поэтому «спросили про пакет — ответила энциклопедия»
    видно сразу, а не по итогу.
    """
    ready = [b for b in BACKENDS if _ready(b, api_keys=api_keys)[0]]
    if not ready:
        return None
    ready.sort(key=lambda b: b.order)
    general = [b for b in ready if b.general_web]
    if general:
        return general[0]

    host = (site or "").strip().lower().rstrip(".")
    if host:
        if not _SITE_RE.fullmatch(host):
            return None
        for bk in ready:
            if host in bk.trusted_hosts:
                return bk
        # Узкий источник нельзя сузить чужим хостом. Отказ честнее подмены.
        return None

    text = html_text.normalize_ws(query or "").lower()
    for bk in ready:
        if any(word in text for word in bk.keywords):
            return bk
    cyrillic = sum(1 for ch in text if "а" <= ch <= "я" or ch == "ё")
    want = "wikipedia-opensearch-ru" if cyrillic * 3 >= max(1, len(text)) \
        else "wikipedia-opensearch-en"
    return next((b for b in ready if b.id == want), ready[0])


# -------------------------------------------------------------------- поиск


def _fail(backend: Backend, subject: str, code: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "detail": detail, "backend": backend.id,
            "honest_capability": backend.honest_capability, "subject": subject,
            "trusted_hosts": list(backend.trusted_hosts), "hits": [], "dropped": 0,
            "from_cache": False, "transport": "", "raw_ref": "", "fetched_at": "",
            "observations": []}


def _private_door_source(backend: Backend) -> osiris.Source:
    """Паспорт своего SearXNG — в памяти, а не в реестре OSIRIS.

    В реестр он не кладётся сознательно: правило A5 («источник обязан быть
    https») действует для ВСЕХ объявленных источников без исключений, а свой
    SearXNG живёт на петлевом адресе по http, где перехватывать нечего и
    некому. Исключение из правила опаснее, чем отсутствие записи: однажды его
    расширят на «ну там же почти локально». Поэтому источник здесь есть ровно
    столько, сколько нужно наблюдению для паспорта, и ни секундой дольше.

    Лицензия честная: чужого текста мы не присваиваем, а выдача агрегатора
    принадлежит тем, кого он опросил.
    """
    base = (config.SEARXNG_URL or "http://127.0.0.1").rstrip("/")
    return osiris.Source(
        id=backend.id, category="A", base_url=base, auth_mode="none",
        rate_limit_per_min=60, license="не определена (свой инстанс агрегатора)",
        provides=("search.query", "search.result"), not_provides=("контент страниц",),
        tos_checked_at=TOS_CHECKED_AT,
        method="api", parser="web.serp_searxng", path_template="/search",
        cache_ttl_seconds=config.CACHE_TTL_SEARCH, default_confidence=0.4,
        notes="свой инстанс владельца; опрашивается через приватную дверь net.searxng_fetch")


async def _run_private_door(svc, backend: Backend, subject: str, *,
                            force_refresh: bool = False) -> dict[str, Any]:
    """Поиск через свой SearXNG: сеть — приватной дверью, разбор — общий.

    Почему не через `osiris.collect`, как все остальные. `collect` строит адрес
    из объявленного источника и идёт в него общим транспортом, а общий
    транспорт приватные адреса запрещает — иначе запрет ничего не стоил бы.
    Свой инстанс на 127.0.0.1 проходит ровно одной дверью, у которой путь и
    имена параметров проверяются положительным списком.

    Что при этом НЕ дублируется: разбор выдачи (`serp_observations` — та же
    функция, что у остальных), паспорт наблюдения, запись сырья и индекс. То
    есть расходятся только те два шага, которые обязаны расходиться: как
    получить байты и по какому правилу проверять адрес.

    Кэш здесь свой и намеренно простой: `raw_is_fresh` по той же записи сырья.
    Лимита частоты нет — инстанс принадлежит владельцу, и ограничивать его
    самого значило бы притворяться вежливым перед собой.
    """
    st = osiris.store(svc)
    source = _private_door_source(backend)
    url = f"{source.base_url}/search?q={quote(subject, safe='')}&format=json"

    cached = None if force_refresh else st.read_raw(st.raw_key(source, url))
    from_cache = bool(cached and st.raw_is_fresh(cached))
    if from_cache:
        body = str(cached.get("body") or "")
        digest = str(cached.get("hash") or "")
        transport = str(cached.get("transport") or "")
        fetched_at = osiris.utcnow()
        with contextlib.suppress(Exception):
            fetched_at = datetime.fromisoformat(str(cached.get("fetched_at")))
    else:
        try:
            raw = await net.searxng_fetch(
                "/search", {"q": subject, "format": "json"}, adapter=st.adapter)
        except net.PageRefused as exc:
            return _fail(backend, subject, "not_ready", str(exc))
        except psec.PluginSecurityError as exc:
            return _fail(backend, subject, "egress_blocked", f"{exc.__class__.__name__}: {exc}")
        except Exception as exc:               # noqa: BLE001 — инстанс бывает мёртвым
            return _fail(backend, subject, "source_unavailable",
                         f"свой SearXNG недоступен: {exc.__class__.__name__}")
        status = int(getattr(raw, "status", 0) or 0)
        if status != 200:
            return _fail(backend, subject, "source_unavailable",
                         f"свой SearXNG ответил HTTP {status}")
        body, _enc, _ratio = html_text.decode_body(
            getattr(raw, "content", b"") or b"",
            str((getattr(raw, "headers", {}) or {}).get("content-type", "")))
        transport = "live" if getattr(st.adapter, "live", False) else "stub"
        fetched_at = osiris.utcnow()
        digest = st.write_raw(source, subject, url,
                              osiris.FetchResult(status=status, body=body, url=url, headers={}),
                              transport=transport)

    try:
        payload = json.loads(body)
    except ValueError as exc:
        return _fail(backend, subject, "bad_response",
                     f"ответ своего SearXNG не разбирается как JSON: {exc}")

    observations = serp_observations(
        source, subject, payload, url=url, raw_ref=f"raw:{digest}",
        collected_at=st.next_collected_at(), fetched_at=fetched_at, shape="searxng")
    st.save_observations(subject, observations, [digest])

    rows = [o.as_dict() for o in observations]
    head = next((r for r in rows if r.get("attribute") == "search.query"), None)
    value = (head or {}).get("value") if isinstance((head or {}).get("value"), dict) else {}
    outcome = str((value or {}).get("outcome") or "bad_response")
    return {
        "ok": outcome == "ok", "code": outcome,
        "detail": str((value or {}).get("detail") or ""),
        "backend": backend.id, "honest_capability": backend.honest_capability,
        "subject": subject, "trusted_hosts": list(backend.trusted_hosts),
        "hits": [r.get("value") or {} for r in rows if r.get("attribute") == "search.result"],
        "dropped": int((value or {}).get("dropped") or 0),
        "from_cache": from_cache, "transport": transport,
        "raw_ref": f"raw:{digest}",
        "fetched_at": str((head or {}).get("observed_at") or ""),
        "observations": rows,
    }


async def run_search(svc, backend: Backend, subject: str, *,
                     force_refresh: bool = False,
                     api_keys: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Выполнить поиск через `osiris.collect`. Отказ возвращается ДАННЫМИ.

    Порядок проверок здесь НЕ воспроизводится: субъект → источник → egress →
    robots → кэш → лимит → сеть делает `collect`, и повторять его своими
    словами значило бы завести вторую границу, которая однажды отстанет от
    первой. Этот файл добавляет ровно две вещи: объявление источника до вызова
    и РАЗЛИЧЕНИЕ ИСХОДОВ после (E1).

    `fresh=true` у инструмента превращается в `force_refresh=True` и означает
    именно обход кэша, а не тихий откат к нему при исчерпанном лимите: лимит
    отдаётся кодом `rate_limited`, а не старым телом под видом свежего (E2).
    """
    subject = search_subject(subject)
    if not subject:
        return _fail(backend, subject, "bad_response", "пустой запрос")
    if not config.both_enabled():
        return _fail(backend, subject, "disabled",
                     f"нужны {config.FLAG} и {config.OSIRIS_FLAG}")
    if backend.via != "osiris" or backend.decl is None:
        return await _run_private_door(svc, backend, subject, force_refresh=force_refresh)
    # `api_keys` не содержит ключа сюда, а лишь ОТВЕЧАЕТ, есть ли он у того, кто
    # владеет хранилищем: без него источник с auth_mode="api_key" не
    # опрашивается вовсе, потому что молчаливая попытка без ключа даёт 401,
    # который выглядит как «источник сломался».
    ready, reason = _ready(backend, api_keys=api_keys)
    if not ready:
        return _fail(backend, subject, "not_ready", reason)

    st = osiris.store(svc)
    try:
        ensure_source(st, backend.decl)
    except osiris.ForbiddenSourceError as exc:
        return _fail(backend, subject, "forbidden_source", str(exc))
    except osiris.OsirisError as exc:
        return _fail(backend, subject, "not_ready", f"источник не объявлен: {exc}")
    except Exception as exc:                  # noqa: BLE001 — PluginSecurityError и прочее
        # Отказ egress — это не «источник плохой», а «нам туда нельзя», и он
        # обязан читаться отдельным кодом, а не растворяться в общей неготовности.
        return _fail(backend, subject, "egress_blocked",
                     f"{exc.__class__.__name__}: {exc}")

    try:
        result = await osiris.collect(svc, backend.id, subject, force_refresh=force_refresh)
    except osiris.RobotsDisallowError as exc:
        return _fail(backend, subject, "robots_disallow", str(exc))
    except osiris.RateLimitedError as exc:
        return _fail(backend, subject, "rate_limited", str(exc))
    except osiris.SourceUnavailableError as exc:
        return _fail(backend, subject, "source_unavailable", str(exc))
    except osiris.SourceUnknownError as exc:
        return _fail(backend, subject, "source_unknown", str(exc))
    except osiris.OsirisError as exc:
        # Сюда приходит и «ответ не разбирается как JSON»: источник ответил не
        # тем. Это НЕ «ничего не найдено», и код у него другой.
        return _fail(backend, subject, "bad_response", str(exc))
    except Exception as exc:                  # noqa: BLE001 — PluginSecurityError и прочее
        return _fail(backend, subject, "egress_blocked",
                     f"{exc.__class__.__name__}: {exc}")

    rows = [r for r in result.get("observations", []) if isinstance(r, dict)]
    head = next((r for r in rows if r.get("attribute") == "search.query"), None)
    if head is None:
        # Такого быть не может: парсер выдаёт `search.query` всегда. Если всё же
        # случилось — значит источник объявлен с ЧУЖИМ парсером, и молчать об
        # этом нельзя.
        return _fail(backend, subject, "bad_response",
                     "источник разобран парсером, который не оставляет следа запроса")
    value = head.get("value") if isinstance(head.get("value"), dict) else {}
    outcome = str(value.get("outcome") or "bad_response")
    hits = [r.get("value") or {} for r in rows if r.get("attribute") == "search.result"]
    return {
        "ok": outcome == "ok",
        "code": outcome,
        "detail": str(value.get("detail") or ""),
        "backend": backend.id,
        "honest_capability": backend.honest_capability,
        "subject": subject,
        "trusted_hosts": list(backend.trusted_hosts),
        "hits": hits,
        "dropped": int(value.get("dropped") or 0),
        # E2 (3): возраст печатается ВСЕГДА, поэтому и `from_cache`, и время
        # настоящей загрузки уходят наверх вместе, а не «когда пригодится».
        "from_cache": bool(result.get("from_cache")),
        # D5: подменённый адаптер не должен отмываться в след как настоящая сеть.
        "transport": str(result.get("transport") or ""),
        "raw_ref": str(result.get("raw_ref") or ""),
        "fetched_at": str(head.get("observed_at") or ""),
        "observations": rows,
    }
