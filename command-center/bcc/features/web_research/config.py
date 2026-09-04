"""web_research: флаги, потолки, запреты и единственный текст готовности.

Это низ иерархии пакета `bcc/features/web_research/`: файл не импортирует ни
одного другого файла пакета, поэтому его импортируют все остальные и цикла не
возникает. Из чужого кода берётся ровно один модуль — `bcc.features.osiris`, и
только ради его `enabled()`: фича обязана требовать ОБА флага, а держать копию
имени чужого флага у себя значит однажды разойтись с ним.

Зачем один файл на все константы:

  * **переменная окружения читается ОДИН раз, при импорте, своей функцией с
    явными границами.** Голый `os.environ` по коду пакета запрещён: тогда одна
    и та же настройка читается в трёх местах, в двух из них с другим значением
    по умолчанию, и владелец получает поведение, которого нет ни в одном
    документе. Мусор в переменной не «подставляет дефолт молча»: он копится в
    `env_errors()`, и модуль отказывается стартовать (`check_env`), потому что
    испорченный потолок бюджета — это вопрос безопасности, а не удобства;
  * **ни один потолок и ни один таймаут не выводится в `input_schema`
    инструмента.** Всё, что модель может назвать числом, она однажды назовёт
    большим числом. Транспорт настраивает владелец переменными окружения, а не
    модель аргументами;
  * **запреты живут кодом, а не текстом в описании.** `SERP_DENY` и
    `EXFIL_SINKS` — данные, которые можно показать владельцу и проверить
    тестом, а не абзац в промпте.

Чего этот файл НЕ делает и делать не должен:

  * не ходит в сеть, не пишет на диск и не создаёт каталогов при импорте.
    Выключенный флаг обязан означать «приложение ведёт себя ровно как до
    модуля», а импорт пакета случается всегда — значит импорт обязан быть
    безобидным. `runs_dir()` только СОБИРАЕТ путь, каталог создаёт вызывающий;
  * не заводит второго словаря запретов вместо осирисовского
    `FORBIDDEN_PATTERNS`. `SERP_DENY` — один дополнительный кортеж на ОДНУ
    тему, которую чужой словарь не покрывает (проверено: ни один из его восьми
    regex не содержит `serp`, `search` или `google`), а не копия чужого;
  * не хранит и не читает ни одного секрета. Ключ Brave живёт в `svc.vault`,
    сюда не попадает даже именем переменной окружения;
  * не решает, что показать модели, а что владельцу. `readiness()` отдаёт ОДИН
    текст обоим — расхождение между «что видит владелец» и «что видит модель»
    невозможно by construction, а не по договорённости между двумя функциями.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from fastapi import HTTPException

from .. import osiris

FLAG = "BOSSMAN_WEB_RESEARCH_ENABLED"
OSIRIS_FLAG = osiris.FLAG                # имя берём у владельца флага, не дублируем строкой
DIRNAME = "web_runs"                     # внутри <data_dir>/osiris/, никогда не в git


class ConfigError(ValueError):
    """Настройка непригодна: модуль отказывается стартовать (fail-closed)."""


# --------------------------------------------------------------- окружение

# Сюда складываются ПОНЯТНЫЕ человеку жалобы на переменные окружения. Список
# пуст в подавляющем большинстве установок; непустой список означает, что фича
# не имеет права работать: молча взятое значение по умолчанию вместо заданного
# владельцем потолка — это ровно то «странное поведение через час», от которого
# правило и защищает.
_env_errors: list[str] = []


def _env_raw(name: str) -> str:
    return os.environ.get(name, "").strip()


def _env_int(name: str, default: int, low: int, high: int) -> int:
    """Целое из окружения с явными границами. Выход за границы — ОШИБКА, а не
    тихое обрезание: обрезанный потолок владелец обнаружит по счёту за трафик,
    а не по логу."""
    raw = _env_raw(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        _env_errors.append(f"{name}: ожидалось целое число, получено {raw!r}; "
                           f"допустимо {low}..{high}")
        return default
    if not (low <= value <= high):
        _env_errors.append(f"{name}={value} вне допустимого диапазона {low}..{high}")
        return default
    return value


def _env_searxng(name: str) -> str:
    """Адрес своего инстанса SearXNG. Пусто = общий веб-поиск не настроен.

    `http://` здесь допускается СОЗНАТЕЛЬНО и только здесь: это единственная
    приватная дверь модуля (`allow_private=True` в `net.searxng_fetch`), она
    ведёт на машину владельца, где TLS-сертификата обычно нет. Для источников
    из сети требование обратное — `https://` обязателен, иначе ответ можно не
    только прочитать, но и подменить, и инъекция придёт с паспортом доверенного
    источника.
    """
    raw = _env_raw(name)
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        _env_errors.append(f"{name}: ожидался адрес вида http://127.0.0.1:8888, "
                           f"получено {raw!r}")
        return ""
    if parts.username or parts.password:
        _env_errors.append(f"{name}: логин и пароль в адресе не принимаются")
        return ""
    if parts.query or parts.fragment:
        _env_errors.append(f"{name}: адрес инстанса задаётся без ?query и без #фрагмента "
                           f"(путь и параметры запроса модуль подставляет сам)")
        return ""
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


def enabled() -> bool:
    """Флаг читается на каждом обращении намеренно (так же, как в osiris):
    включение фичи не должно требовать перезапуска, а вот потолки ниже — это
    константы, и их подмена на ходу как раз запрещена."""
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


def both_enabled() -> bool:
    """Фича живёт только при ОБОИХ флагах и исправной настройке.

    Без OSIRIS наблюдения и сырьё легли бы в его каталоги при выключенном
    флаге: владелец получил бы данные, которые не может ни посмотреть, ни
    удалить штатным путём (ручки OSIRIS отвечают 409), — то самое состояние,
    которое обе фичи объявляют невозможным.
    """
    return enabled() and osiris.enabled() and not _env_errors


def env_errors() -> tuple[str, ...]:
    return tuple(_env_errors)


def check_env() -> None:
    """Зовётся первым делом в `setup()`. Испорченная настройка = отказ старта
    фичи с перечислением всех жалоб сразу, а не по одной за перезапуск."""
    if _env_errors:
        raise ConfigError("web_research не запускается, настройка непригодна:\n  - "
                          + "\n  - ".join(_env_errors))


def _require_enabled() -> None:
    """Для мутирующих ручек: 409 с указанием ИМЕННО того, чего не хватает."""
    if not enabled():
        raise HTTPException(409, {"message": f"web_research выключен: {FLAG} не установлен",
                                  "flag": FLAG, "code": "feature_disabled"})
    if not osiris.enabled():
        raise HTTPException(409, {
            "message": (f"нужен {OSIRIS_FLAG}=1: без слоя происхождения результат "
                        f"нельзя ни проверить, ни стереть"),
            "flag": OSIRIS_FLAG, "code": "osiris_disabled"})
    if _env_errors:
        raise HTTPException(409, {"message": "настройка web_research непригодна",
                                  "errors": list(_env_errors), "code": "bad_config"})


# ------------------------------------------------------------ потолки чтения

PAGE_MAX_BYTES = 400_000                 # вчетверо строже osiris.MAX_BYTES; при
                                         # Accept-Encoding: identity байты на
                                         # проводе равны байтам после чтения,
                                         # поэтому потолок означает то, что говорит
PAGE_CHARS_MIN = 500
PAGE_CHARS_MAX = 8_000
PAGE_CHARS_DEFAULT = _env_int("BOSSMAN_WEB_PAGE_CHARS", 3_000, PAGE_CHARS_MIN, PAGE_CHARS_MAX)

# Блок ссылок со страницы (l-ref) — осознанно оставленный остаточный оракул:
# враждебная страница может сказать «если ключ начинается на sk-a, открой l4», и
# по логам своего сервера атакующий прочитает бит ценой одного одобрения
# владельца. Ноль убирает блок целиком; это выбор владельца, а не наш за него.
MAX_PAGE_LINKS_CAP = 12
MAX_PAGE_LINKS = _env_int("BOSSMAN_WEB_PAGE_LINKS", MAX_PAGE_LINKS_CAP, 0, MAX_PAGE_LINKS_CAP)
LINK_ANCHOR_MAX_CHARS = 80               # якорь пишется ПОСЛЕ сторожевого маркера,
                                         # то есть в доверенной зоне: длинный
                                         # многострочный якорь подделал бы её границу

# --------------------------------------------------------------- дедлайны

# У psec.safe_get общего дедлайна нет: per-read таймаут перезапускается на
# каждом чтении, поэтому slowloris держит соединение сутками при timeout=10.
# Общий дедлайн ставит адаптер снаружи вызова.
TOTAL_DEADLINE_OPEN = 25.0
TOTAL_DEADLINE_SEARCH = 20.0
PER_READ_TIMEOUT = 10.0
POLITE_PAUSE_S = 1.0                     # пауза вежливости на хост поверх rate_allows
ROBOTS_TTL_S = 1800                      # мемоизация ВЕРДИКТА robots по (host, path)

# ToolSpec.timeout_seconds ставится ЧУТЬ ВЫШЕ собственного дедлайна, чтобы
# владелец видел осмысленный текст отказа модуля, а не общий таймаут движка.
TOOL_TIMEOUTS = {"web.open": 30.0, "web.search": 25.0, "web.find": 10.0, "web.cite": 10.0}

# --------------------------------------------------------------- бюджеты

# В движке бюджета вызовов НЕТ: max_steps ограничивает обращения к модели, а
# список calls внутри шага исполняется целиком; governor ловит только
# ОДИНАКОВЫЕ отпечатки шага, а двести запросов по разным адресам для него
# «прогресс». Поэтому бюджет свой и на диске (ask паркует прогон и освобождает
# воркер — счётчик в памяти процесса потерялся бы ровно в этот момент).
MAX_SEARCHES_PER_RUN = 5
MAX_OPENS_PER_RUN = 12                   # перелистывание из кэша и web.find не считаются
MAX_RUN_BYTES = 3_000_000
MAX_RUN_NET_SECONDS = 90.0
BUDGET_KINDS = ("search", "open", "bytes", "seconds")

HOST_RATE_PER_MIN = 10                   # ключ лимита = source.id = хост
LEDGER_TTL_HOURS = 24

DAILY_FETCHES = _env_int("BOSSMAN_WEB_DAILY_FETCHES", 500, 1, 1_000_000)
RAW_BUDGET_MB = _env_int("BOSSMAN_WEB_RAW_BUDGET_MB", 200, 1, 1_000_000)
RAW_BUDGET_BYTES = RAW_BUDGET_MB * 1_000_000

# E2. `osiris.normalize_source` делает
# `cache_ttl_seconds = max(0, int(decl.get("cache_ttl_seconds") or 0))`
# (osiris.py:489), то есть значение по умолчанию dataclass'а (3600) до
# декларации НЕ доезжает: отсутствие ключа даёт ровно 0. Ноль здесь опасен
# втройне. Первое — `raw_is_fresh` тогда ложен ВСЕГДА, кэша нет вовсе, и
# каждое повторное открытие той же страницы это новый выход в сеть: лишние
# байты наружу, лишний расход суточного лимита и лишний шанс нарваться на
# бан хоста. Второе — параметр `fresh` становится мёртвым: «свежо» и «из
# архива» перестают различаться, а различать их модуль обязан. Третье —
# тест «повторное чтение берётся из кэша» пройти не может, то есть обещание
# в документе живёт, а проверки под ним нет. Поэтому TTL — ОБЯЗАТЕЛЬНОЕ поле
# каждой декларации, и оба значения ниже ненулевые.
CACHE_TTL_SEARCH = 300                   # выдача устаревает быстро, но не за один шаг
CACHE_TTL_PAGE = 900                     # страница-хост: перелистывание в пределах
                                         # одного разговора обязано быть бесплатным

# --------------------------------------------------------------- источники

SEARXNG_URL = _env_searxng("BOSSMAN_WEB_SEARXNG_URL")

ACCEPT_HTML = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "text/plain;q=0.8,*/*;q=0.1")
ACCEPT_LANG = "ru,en;q=0.9"

# Что соглашаемся читать. `psec.safe_get` на Content-Type не смотрит вообще,
# поэтому отказ обязан быть здесь и ДО чтения тела: pdf, архив или видео мы всё
# равно не извлечём, а байты и суточный лимит потратим.
CONTENT_TYPE_OK = frozenset({
    "text/html", "application/xhtml+xml", "text/plain", "text/xml",
    "application/xml", "application/json", "application/atom+xml",
    "application/rss+xml", "application/rdf+xml",
})

# Content-Encoding ОТВЕТА: заголовок запроса `identity` — это просьба, а не
# гарантия, а `aiter_bytes` отдаёт уже распакованный chunk целиком, то есть
# max_bytes ловит зип-бомбу после аллокации.
CONTENT_ENCODING_OK = frozenset({"", "identity"})


def content_type_allowed(value: str) -> tuple[bool, str]:
    """(можно ли читать, человеческая причина отказа). Пустой Content-Type —
    отказ: угадывание типа по телу это ровно то место, где текст оказывается
    исполняемым файлом."""
    base = (value or "").split(";", 1)[0].strip().lower()
    if not base:
        return False, "сервер не назвал Content-Type; угадывать тип по телу мы не станем"
    if base in CONTENT_TYPE_OK:
        return True, ""
    # Семейство application/<что-то>+json (ld+json, hal+json, vnd.api+json)
    # перечислить списком нельзя — оно открытое, а разбирается одинаково.
    if base.startswith("application/") and base.endswith("+json"):
        return True, ""
    return False, f"тип {base} не текстовый: читать нечего, а байты и лимит потратятся"


def content_encoding_allowed(value: str) -> tuple[bool, str]:
    enc = (value or "").strip().lower()
    if enc in CONTENT_ENCODING_OK:
        return True, ""
    return False, (f"ответ сжат ({enc}) вопреки Accept-Encoding: identity; "
                   f"распакованный размер до чтения неизвестен")


# ---------------------------------------------------- запрет выдачи и стоков

# Общие поисковики и коммерческие SERP-скрейперы. Причина у каждого своя и
# человеческая: этот кортеж отдаётся владельцу наружу, а «denied by pattern 4»
# ничего ему не объясняет.
#
# Важно, чего здесь НЕТ: общего правила на путь `/search`. Свой SearXNG
# владельца живёт ровно по такому пути, и запрет «всё, что похоже на поиск»
# отрезал бы единственный честный путь к общему веб-поиску.
_SERP_DENY_RAW: tuple[tuple[str, str], ...] = (
    (r"(^|\.)google\.[a-z0-9.\-]+/(search|url|imgres)\b",
     "страница выдачи Google: её разбор запрещён условиями Google, "
     "а обход их анти-бот защиты запрещён нам самими условиями сбора"),
    (r"(^|\.)webcache\.googleusercontent\.com/",
     "кэш Google: та же выдача, только с другого адреса"),
    (r"(^|\.)bing\.com/(search|images/search)\b",
     "страница выдачи Bing: разбор запрещён условиями Microsoft"),
    (r"(^|\.)yandex\.[a-z0-9.\-]+/(search|images/search)\b",
     "страница выдачи Яндекса: разбор запрещён её условиями"),
    (r"(^|\.)duckduckgo\.com/(html|lite)\b",
     "HTML-интерфейс DuckDuckGo — обход их API, тот же запрет, что и у остальных"),
    (r"(^|\.)(startpage\.com|search\.brave\.com|ecosia\.org|mojeek\.com|qwant\.com)/"
     r"(search|sp/search)\b",
     "мета-поисковик отдаёт чужую выдачу; Brave подключается ключом к API, "
     "а не скрейпингом их же страницы"),
    (r"(^|\.)(baidu\.com|search\.naver\.com|search\.seznam\.cz)/",
     "страница выдачи поисковика: разбор запрещён её условиями"),
    (r"(^|\.)(serpapi\.com|serpstack\.com|serper\.dev|searchapi\.io|zenserp\.com|"
     r"scaleserp\.com|serply\.io|dataforseo\.com|brightdata\.com|oxylabs\.io|"
     r"scrapingbee\.com|scraperapi\.com|zenrows\.com|apify\.com)(/|$)",
     "коммерческий SERP-скрейпер: он перепродаёт чужую выдачу в обход условий "
     "поисковика, и запрет не перестаёт быть запретом от того, что кто-то берёт "
     "за него деньги"),
    # Хост, который сам называет себя serp-чем-то, почти всегда именно им и
    # является. Редкий добросовестный хост будет отвергнут — это осознанный
    # обмен: отказать одному честному дешевле, чем один раз молча скрейпить
    # выдачу.
    (r"(^|\.)serp[a-z0-9\-]{0,12}\.[a-z]{2,24}(/|$)|/serp(/|$|\?)",
     "адрес объявляет себя SERP-эндпоинтом"),
)
SERP_DENY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), why) for pattern, why in _SERP_DENY_RAW)

# Стоки утечки: адреса, чья единственная функция — принять и показать
# отправителю то, что ему прислали. Открытие такого адреса это не чтение, это
# отправка. Проверка по хосту, а не по полному адресу: путь у стока произвольный.
EXFIL_SINKS = frozenset({
    "webhook.site", "requestbin.com", "requestbin.net", "postb.in", "pipedream.net",
    "requestcatcher.com", "beeceptor.com", "mockbin.org", "hookb.in", "webhookrelay.com",
    "typedwebhook.tools", "smee.io",
    "ngrok.io", "ngrok-free.app", "ngrok.app", "ngrok.dev", "trycloudflare.com",
    "loca.lt", "localtunnel.me", "serveo.net",
    "pastebin.com", "paste.ee", "hastebin.com", "dpaste.org", "dpaste.com",
    "ghostbin.co", "termbin.com", "0x0.st", "transfer.sh", "file.io", "bashupload.com",
    "oastify.com", "burpcollaborator.net", "interact.sh", "oast.pro", "oast.fun",
    "oast.live", "oast.site", "canarytokens.com", "canarytokens.org",
})

_BAD_HOST_CHARS = re.compile(r"[\s@\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def _host_and_path(url_or_host: str) -> tuple[str, str]:
    """Разбирает и полный адрес, и голый хост: обе формы приходят в проверки
    (open_effect смотрит сырой url, ledger — уже разобранный хост)."""
    raw = (url_or_host or "").strip().lower()
    if "://" in raw:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        path = parts.path or "/"
        query = parts.query
    else:
        host, _, tail = raw.partition("/")
        path = f"/{tail}" if tail else "/"
        query = ""
    return host.rstrip("."), (f"{path}?{query}" if query else path)


def serp_reason(url_or_host: str) -> str | None:
    """Причина отказа как ТЕКСТ (не булево): её печатают владельцу и модели.
    Проверяется ДО normalize_source и до чеканки любого ref — полагаться на то,
    что нас случайно спасёт robots.txt Google, значит не запрещать, а везти."""
    host, path = _host_and_path(url_or_host)
    if not host:
        return None
    haystack = f"{host}{path}"
    for pattern, why in SERP_DENY:
        if pattern.search(haystack):
            return why
    return None


def is_exfil_sink(host: str) -> bool:
    """Хост или любой его поддомен. Суффикс проверяется через точку: правило
    для `file.io` не должно ловить `notfile.io`."""
    clean = (host or "").strip().lower().rstrip(".")
    if not clean:
        return False
    return any(clean == sink or clean.endswith("." + sink) for sink in EXFIL_SINKS)


# ------------------------------------------------------------------- ref'ы

# Префикс ref НЕСЁТ ПОЛИТИКУ, а не косметику: "w" — адрес отчеканен backend'ом
# или владельцем и не заражён, "l" — адрес пришёл из тела уже прочитанной, то
# есть потенциально враждебной, страницы. Только это делает правило «ссылка со
# страницы требует одобрения» выразимым в ЧИСТОМ effect_hook, у которого по
# сигнатуре нет ни run_id, ни svc, ни доступа к реестру.
#
# И ровно потому, что доступа к реестру у хука нет, "l"-ref обязан быть
# САМООПИСЫВАЮЩИМ: `l7@docs.example/a7`. Непрозрачный `l7` означал бы, что
# владелец одобряет строку, не зная назначения, approval_digest фиксирует `l7`,
# а не адрес за ним, и правило «одобренный путь == исполненный путь» на этой
# ветке не выполняется. Хост и путь в самом аргументе попадают и в
# предпросмотр, и в digest, и в args_hash бесплатно.
REF_W_RE = re.compile(r"^w[0-9]{1,3}$")
REF_L_RE = re.compile(r"^l[0-9]{1,3}@[a-z0-9\-._]{1,253}(/[^\s\]]{0,120})?$")
REF_RE = re.compile(r"^(w[0-9]{1,3}|l[0-9]{1,3}@[a-z0-9\-._]{1,253}(/[^\s\]]{0,120})?)$")
REF_MAX_CHARS = 400


def parse_ref(ref: str) -> tuple[str, str, str] | None:
    """`w3` → ("w", "", ""); `l7@docs.example/a7` → ("l", "docs.example", "/a7").
    None — форма негодна. Хост и путь возвращаются для СВЕРКИ с записью
    реестра: расхождение токена и записи — отказ, иначе самоописание токена
    было бы украшением, а не проверкой."""
    token = (ref or "").strip()
    if not token or len(token) > REF_MAX_CHARS:
        return None
    if REF_W_RE.match(token):
        return "w", "", ""
    if not REF_L_RE.match(token):
        return None
    head, _, tail = token.partition("@")
    host, _, path = tail.partition("/")
    del head
    return "l", host.rstrip("."), (f"/{path}" if path else "")


# ------------------------------------------------- шлюз исходящего запроса

# Наружу с машины владельца уходит ровно два вида байтов: текст запроса и
# адрес в web.open. Чёрный список значений («знаем секрет — не пустим») не
# может ловить того, чего не знает: содержимое ~/.ssh/id_rsa, .env соседнего
# проекта, историю задач. Поэтому шлюз запроса — ПОЛОЖИТЕЛЬНАЯ форма: запрос
# обязан выглядеть как фраза на естественном языке, всё остальное отвергается,
# даже если мы не знаем, что именно это было.
QUERY_MIN_CHARS = 3
QUERY_MAX_CHARS = 200
QUERY_MAX_WORDS = 24
QUERY_MAX_TOKEN_CHARS = 32               # человеческое слово короче; ключ, хэш и
                                         # base64-блоб — длиннее. Это же правило
                                         # заменяет отдельный порог на «блоб >40»:
                                         # два порога на одну тему однажды разойдутся
QUERY_MAX_NONALPHA_RATIO = 0.3

# Фраза человека такой длины содержит пробелы. Единый токен в три десятка
# знаков, набитый разделителями, — это запись из файла, пара ключ-значение или
# строка конфига, а не вопрос: ровно так выглядит строка /etc/passwd. Порог
# считается по РАЗДЕЛИТЕЛЯМ, а не по любым знакам, иначе «TCP/IP», «и/или» и
# «10:30» перестали бы искаться. Это по-прежнему положительная форма: правило
# описывает, как выглядит фраза, а не перечисляет известные секреты.
QUERY_LONE_TOKEN_CHARS = 24
# Разделители делятся надвое, и это не придирка. «Сильные» в человеческой фразе
# не встречаются вовсе: равенство — это присваивание, обратный слэш — путь,
# собака — адрес, вертикальная черта — конвейер. Одного такого знака внутри
# длинного единого токена достаточно: «DB_PASSWORD=…» вопросом не бывает.
# «Слабые» бывают: «TCP/IP», «и/или», «10:30» — поэтому для них нужен ПОРОГ,
# и строка вида root:x:0:0:root:/root:/bin/bash отсекается их плотностью.
QUERY_SEPARATORS_STRONG = "=\\@|"
QUERY_SEPARATORS_WEAK = ":/"
QUERY_LONE_TOKEN_SEPARATORS = 3

_QUERY_DENY_RAW: tuple[tuple[str, str], ...] = (
    (r"-----BEGIN [A-Z ]+-----", "это похоже на ключ или сертификат, а не на вопрос"),
    # Путь ищется только В НАЧАЛЕ токена: «TCP/IP» и «и/или» обязаны проходить,
    # а «/home/user/.ssh/id_rsa» и «C:\\Users\\...» — нет.
    (r"(^|\s)(/|~/|\.{1,2}/|[A-Za-z]:\\|\\\\)", "это похоже на путь к файлу, а не на вопрос"),
    (r"https?://|(^|\s)www\.[a-z0-9\-]+\.[a-z]{2,}",
     "адрес внутри запроса: чтобы открыть страницу, есть web.open — там владелец "
     "видит полный адрес"),
    (r"[{}]|\"\s*:\s*\"", "это похоже на JSON или структуру данных, а не на вопрос"),
    (r"(^|\s)(export|set|env)\s+[A-Z_]{3,}=", "это похоже на переменные окружения"),
)
QUERY_DENY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), why) for pattern, why in _QUERY_DENY_RAW)

# A1. У документа нет законной нужды в `?`: адрес с обязательным параметром
# ищется поиском, а не собирается моделью. Причина жёсткости — предпросмотр
# одобрения прогоняется движком через редактор секретов, поэтому
# `?api_key=sk-…` владельцу показан НЕ БУДЕТ, а исполнится сырой аргумент:
# защита оказалась бы анти-коррелирована с опасностью. Порога на длину
# query-строки здесь нет и не должно быть — любая непустая означает отказ.
URL_QUERY_ALLOWED = False
URL_FRAGMENT_ALLOWED = False

# ------------------------------------------------------------------ утилиты

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(host: str) -> str:
    return _SLUG_RE.sub("-", (host or "").strip().lower()).strip("-")


def host_source_id(host: str) -> str:
    """id автосозданного источника-на-хост.

    Хвост из хэша обязателен: `slug()` не инъективен — `docs.python.org` и
    `docs-python.org` дают одну и ту же строку, то есть делили бы одну карточку
    источника вместе с её пометкой «проверен живьём» и одним лимитом запросов
    на два разных сайта.
    """
    clean = (host or "").strip().lower().rstrip(".")
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]
    return f"web-{slug(clean)[:40]}-{digest}"


def runs_dir(svc) -> Path:
    """Только СОБИРАЕТ путь. Каталог создаёт тот, кто пишет файл, и только при
    включённых флагах: выключенная фича не имеет права оставить на диске даже
    пустой каталог."""
    return Path(svc.settings.data_dir) / osiris.DIRNAME / DIRNAME


def atomic_write_json(path: Path, data: Any) -> None:
    """tmp + os.replace: полуфайла не бывает. Свои четыре строки вместо
    приватного `OsirisStore._write_json` — чтобы не зависеть от чужой приватной
    детали, которая имеет полное право поменяться без предупреждения."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Any | None:
    """Битый или отсутствующий файл — это None, а не исключение наружу: реестр
    прогона не то место, из-за которого прогон обязан упасть."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------- тексты-шаблоны отказов

# Исчерпание лимита отдаётся с error=False и явным сценарием «что делать
# дальше». error=True здесь провоцирует маленькую модель повторить вызов, и
# лимит превращается в цикл до max_steps — то есть защита от перерасхода сама
# становится перерасходом.
MSG_BUDGET_SEARCH = (
    "ЛИМИТ ПОИСКА ИСЧЕРПАН ({used} из {limit}). Уже найдено: {refs}.\n"
    "Отвечай по тому, что есть. Если данных не хватает — так и скажи владельцу.\n"
    "ДАЛЬШЕ: ничего. Заверши ответ.")
MSG_BUDGET_OPEN = (
    "ЛИМИТ ОТКРЫТИЯ СТРАНИЦ ИСЧЕРПАН ({used} из {limit}). Прочитано: {refs}.\n"
    "Ищи ответ в уже прочитанном: web.find {{\"ref\":\"w1\",\"query\":\"…\"}}.\n"
    "ДАЛЬШЕ: web.find по уже прочитанным страницам либо заверши ответ.")
MSG_BUDGET_BYTES = (
    "ЛИМИТ ТРАФИКА ПРОГОНА ИСЧЕРПАН ({used} из {limit} байт). Новых страниц не будет.\n"
    "ДАЛЬШЕ: web.find по уже прочитанным страницам либо заверши ответ.")
MSG_BUDGET_SECONDS = (
    "ЛИМИТ СЕТЕВОГО ВРЕМЕНИ ИСЧЕРПАН ({used} из {limit} с). Новых обращений не будет.\n"
    "ДАЛЬШЕ: web.find по уже прочитанным страницам либо заверши ответ.")
MSG_BUDGET_DAILY = (
    "СУТОЧНЫЙ ЛИМИТ ОБРАЩЕНИЙ ИСЧЕРПАН ({used} из {limit}). Это лимит машины владельца, "
    "а не источника.\nДАЛЬШЕ: ничего. Заверши ответ и скажи владельцу, что суточный "
    "лимит веб-поиска исчерпан.")
MSG_BUDGET_DISK = (
    "ДИСКОВЫЙ БЮДЖЕТ СЫРЬЯ ИСЧЕРПАН ({used} из {limit} МБ). Новое сырьё не сохраняется, "
    "а без сохранённого сырья цитата недоказуема, поэтому страница не читается.\n"
    "Уже собранное сырьё НЕ удаляется: доказательство под выданной цитатой дороже "
    "нового чтения.\nДАЛЬШЕ: ничего. Заверши ответ.")

MSG_REDIRECT_OFFSITE = ("страница перенаправляет за пределы сайта; автоматический переход "
                        "запрещён — найдите целевую страницу поиском")
MSG_REQUESTED_URL_ONLY = ("редиректы разрешены только внутри этого сайта; конечный адрес "
                          "после них транспорт не возвращает, поэтому здесь записан "
                          "ЗАПРОШЕННЫЙ адрес")
MSG_NO_NETWORK = ("наружу не ходил ({reason}); ниже — то, что есть в локальном архиве. "
                  "Свежесть НЕ подтверждена.")
MSG_ENGINES_DOWN = ("поиск НЕ состоялся: движки не ответили ({detail}). Это НЕ значит, "
                    "что в интернете этого нет.")
MSG_EMPTY_RESULT = "движок ответил: по этому запросу ничего не найдено."
MSG_SOURCE_UNAVAILABLE = "источник недоступен ({detail}); поиск не состоялся."
MSG_QUERY_REFUSED = ("запрос НЕ отправлен: {why}. Переформулируйте его обычными словами — "
                     "наружу уходит ровно то, что вы напишете, и владелец увидит это "
                     "дословно.")
MSG_QUOTE_NOT_FOUND = ("такой цитаты нет в тексте страницы; процитируйте дословно. "
                       "Ближайшие фрагменты:")
MSG_HIDDEN_HONEST = ("скрытый текст: снято {n} узлов по атрибуту; скрытие через "
                     "CSS-классы не определяется")

# ------------------------------------------------------------ готовность

# Про общий веб-поиск говорим ОДНИМ абзацем и всегда целиком: без него «поиск
# ничего не нашёл» и «искать негде» сливаются в один ответ, а это разные вещи,
# и путать их — способ выдать пустоту за факт.
_NO_GENERAL_WEB = (
    "Общий веб-поиск НЕДОСТУПЕН: выдачу Google, Bing и Яндекса мы не разбираем — "
    "это нарушает их условия. Полноценного индекса открытого веба без своего "
    "SearXNG или ключа не существует, и подменять его энциклопедией мы не станем.\n"
    "Два честных пути: поднять свой SearXNG и задать BOSSMAN_WEB_SEARXNG_URL, "
    "либо добавить ключ Brave Search API.")


def _plural_sources(n: int) -> str:
    """Согласование числа с существительным. Мелочь, но текст читает владелец, а
    «работают 2 источников» он читает как машинный вывод, а не как ответ."""
    tail = n % 100
    if 11 <= tail <= 14:
        return "источников"
    return {1: "источника", 2: "источника", 3: "источника", 4: "источника"}.get(n % 10, "источников")


def readiness(*, backends: Sequence[Mapping[str, Any]] = (), osiris_on: bool | None = None,
              page_chars: int | None = None) -> dict[str, Any]:
    """ЕДИНСТВЕННЫЙ источник правды о готовности — и для GET /api/web, и для
    текста, который получает модель.

    Одна функция здесь не архитектурная опрятность, а свойство: пока текст
    владельцу и текст модели собираются одной функцией из одних фактов,
    расхождение между «что видит владелец» и «что модель говорит владельцу»
    невозможно. Две функции разойдутся — не в первый месяц, так в третий, и
    заметить это будет некому.

    Функция ЧИСТАЯ относительно источников: `backends` приходят готовыми
    фактами (`id`, `ready`, `keyless`, `general_web`, `honest_capability`,
    `reason`) из `sources.py`. Этот файл ничего не знает про сеть и не имеет
    права ничего про неё выдумывать: количество работающих источников
    СЧИТАЕТСЯ по переданному списку, а не написано числом в тексте.
    """
    web_on = enabled()
    osiris_state = osiris.enabled() if osiris_on is None else bool(osiris_on)
    chars = PAGE_CHARS_DEFAULT if page_chars is None else int(page_chars)

    rows: list[dict[str, Any]] = []
    for item in backends:
        rows.append({
            "id": str(item.get("id") or ""),
            "ready": bool(item.get("ready")),
            "keyless": bool(item.get("keyless")),
            "general_web": bool(item.get("general_web")),
            "honest_capability": str(item.get("honest_capability") or ""),
            "reason": str(item.get("reason") or ""),
        })
    ready_rows = [r for r in rows if r["ready"]]
    keyless_ready = [r for r in ready_rows if r["keyless"]]
    general_ready = [r for r in ready_rows if r["general_web"]]

    recommendations: list[str] = []
    # Дефолт подобран под контекст 4–8k. Для окна 4k нужно меньше, и это
    # обязано быть НАПИСАНО, а не сделано молчаливой обрезкой: молчаливая
    # обрезка выглядит как ответ, а не как нехватка места.
    if chars > 1500:
        recommendations.append(
            f"страница отдаётся модели по {chars} знаков; если окно модели 4k — "
            f"задайте BOSSMAN_WEB_PAGE_CHARS=1500, иначе страница вытеснит разговор")
    if MAX_PAGE_LINKS:
        recommendations.append(
            f"в конце страницы печатается до {MAX_PAGE_LINKS} ссылок с неё; открыть "
            f"такую ссылку можно только с вашего одобрения. Блок убирается целиком: "
            f"BOSSMAN_WEB_PAGE_LINKS=0")

    if not web_on:
        code, text = "feature_disabled", (
            f"Веб-поиск выключен: переменная {FLAG} не установлена. Инструментов web.* "
            f"не существует, файлов на диске нет, наружу ничего не уходит.")
    elif not osiris_state:
        code, text = "osiris_disabled", (
            f"Веб-поиск не включится: нужен {OSIRIS_FLAG}=1. Без слоя происхождения "
            f"результат нельзя ни проверить, ни стереть: наблюдения и сырьё легли бы в "
            f"каталоги OSIRIS, а его ручки при выключенном флаге отвечают 409 — вы "
            f"получили бы данные, которые не можете ни посмотреть, ни удалить.")
    elif _env_errors:
        code, text = "bad_config", (
            "Веб-поиск не запущен: настройка непригодна.\n  - "
            + "\n  - ".join(_env_errors))
    elif not ready_rows:
        code, text = "no_backends", (
            "ИСКАТЬ НЕГДЕ: ни один источник не готов. Я НЕ ИСКАЛ В ИНТЕРНЕТЕ.\n"
            + _NO_GENERAL_WEB)
    elif general_ready:
        names = ", ".join(r["id"] for r in general_ready)
        code, text = "ready_general", (
            f"Общий веб-поиск доступен: {names}. Кроме него работают "
            f"{len(keyless_ready)} {_plural_sources(len(keyless_ready))} без ключа — "
            f"справки, документация, пакеты, научные работы.")
    else:
        code, text = "ready_keyless", (
            f"Ключи не нужны: работают {len(keyless_ready)} {_plural_sources(len(keyless_ready))} без ключа — "
            f"справки, документация, пакеты, научные работы, но НЕ общий веб-поиск.\n"
            + _NO_GENERAL_WEB)

    # Советы по настройке печатаются только тому, у кого фича действительно
    # работает. Владельцу с выключенным флагом они говорят о поведении, которого
    # у него нет, а это ровно тот случай, когда документация выдаётся за код.
    if recommendations and code in ("ready_general", "ready_keyless"):
        text = text + "\n" + "\n".join(f"Совет: {line}" for line in recommendations)
    else:
        recommendations = []

    return {
        "enabled": web_on,
        "flag": FLAG,
        "osiris_flag": OSIRIS_FLAG,
        "osiris_enabled": osiris_state,
        "ok": bool(web_on and osiris_state and not _env_errors and ready_rows),
        "code": code,
        "text": text,
        "backends": rows,
        "backends_ready": len(ready_rows),
        "keyless_ready": len(keyless_ready),
        "general_web": [r["id"] for r in general_ready],
        "page_chars": chars,
        "page_links": MAX_PAGE_LINKS,
        "searxng_configured": bool(SEARXNG_URL),
        "env_errors": list(_env_errors),
        "recommendations": recommendations,
        "limits": {
            "searches_per_run": MAX_SEARCHES_PER_RUN,
            "opens_per_run": MAX_OPENS_PER_RUN,
            "run_bytes": MAX_RUN_BYTES,
            "run_net_seconds": MAX_RUN_NET_SECONDS,
            "daily_fetches": DAILY_FETCHES,
            "raw_budget_mb": RAW_BUDGET_MB,
            "page_max_bytes": PAGE_MAX_BYTES,
            "host_rate_per_min": HOST_RATE_PER_MIN,
        },
        "forbidden_serp": [{"pattern": p.pattern, "why": w} for p, w in SERP_DENY],
    }


def as_dict() -> dict[str, Any]:
    """Снимок настройки для GET /api/web и для диагностики. Секретов здесь нет
    и быть не может: ключ Brave живёт в svc.vault и в этот файл не попадает
    даже именем."""
    return {
        "flag": FLAG, "osiris_flag": OSIRIS_FLAG, "dirname": DIRNAME,
        "page_chars_default": PAGE_CHARS_DEFAULT, "page_links": MAX_PAGE_LINKS,
        "page_max_bytes": PAGE_MAX_BYTES,
        "deadline_open": TOTAL_DEADLINE_OPEN, "deadline_search": TOTAL_DEADLINE_SEARCH,
        "per_read_timeout": PER_READ_TIMEOUT, "polite_pause_s": POLITE_PAUSE_S,
        "robots_ttl_s": ROBOTS_TTL_S, "ledger_ttl_hours": LEDGER_TTL_HOURS,
        "cache_ttl_search": CACHE_TTL_SEARCH, "cache_ttl_page": CACHE_TTL_PAGE,
        "searches_per_run": MAX_SEARCHES_PER_RUN, "opens_per_run": MAX_OPENS_PER_RUN,
        "run_bytes": MAX_RUN_BYTES, "run_net_seconds": MAX_RUN_NET_SECONDS,
        "daily_fetches": DAILY_FETCHES, "raw_budget_mb": RAW_BUDGET_MB,
        "host_rate_per_min": HOST_RATE_PER_MIN,
        "searxng_configured": bool(SEARXNG_URL),
        "content_types": sorted(CONTENT_TYPE_OK),
        "exfil_sinks": sorted(EXFIL_SINKS),
        "serp_deny": [{"pattern": p.pattern, "why": w} for p, w in SERP_DENY],
        "env_errors": list(_env_errors),
    }
