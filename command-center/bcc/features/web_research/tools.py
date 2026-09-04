"""web_research: четыре инструмента модели — web.search, web.open, web.find, web.cite.

Это ГРАНИЦА фичи со стороны модели. Всё, что ниже по стеку, отдаёт отказ
исключением (`net.PageRefused`, осирисовская иерархия, `PluginSecurityError`);
здесь исключение обязано стать ДАННЫМИ — готовым текстом для модели с честным
следующим шагом. Прогон из-за веб-поиска не падает никогда: упавший инструмент
это не «мир сломался», это «в мире так бывает».

Почему у всех четырёх `permission=""`, хотя право «для аудита» напрашивается:
в `decide_effect` (`bcc/tools.py`) выданное агенту право ПОВЫШАЕТ эффект до
`auto`, и делает это ДО хука. Хук потом может ужесточить обратно, но правила
«право не гейт, а ускоритель» достаточно, чтобы одного `tool_rule` владельца
хватило для проезда `web.open` с сырым `url` в `auto`. Пустое право убирает эту
тропу целиком; управление у владельца остаётся через `agents.tools` и
`tool_rules`.

ЧТО ЭТОТ ФАЙЛ ЧЕСТНО НЕ ОБЕЩАЕТ

  * **Текст запроса ЕСТЬ в `tool_calls.args`** (поправка A4). Строку вызова
    пишет движок (`engine._record_tool_call`), до и мимо фичи, и отключить это
    изнутри инструмента нечем. Обещать «запроса нет в аудите» было бы обещанием,
    которого код не выполняет. Формулировка честная: запрос хранится в строке
    вызова, владелец видит его дословно, и это осознанно — он же субъект
    эпизода, то есть ответ на вопрос «что ушло с моей машины».
  * **Шлюз запроса не сверяет значения секретов** (см. `guard_query`). Проект
    предлагал сравнивать запрос с расшифрованными ключами провайдеров и токеном
    доступа. Обещание снято, а не подпёрто: сравнение требовало расшифровывать
    ВСЕ хранимые секреты на КАЖДЫЙ поиск (то есть доставать их из хранилища
    чаще, чем это делает сам провайдер), а положительная форма шлюза (поправка
    A3) отвергает любой токен длиннее 32 знаков — то есть ровно ту форму, в
    которой ключ или токен только и может оказаться в строке.
  * **Наблюдения страницы не связаны с наблюдением запроса** через
    `value["of"]`: идентификатор родительского наблюдения негде хранить между
    вызовами — у `ledger.RefEntry` такого поля нет, а `ledger.py` не мой файл.
    Поэтому `fetch_page(..., of="")`, и след эпизода плоский: запрос, страницы и
    цитаты лежат под одним субъектом, но дерева между ними нет.

ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ И ДЕЛАТЬ НЕ ДОЛЖЕН

  * не ходит в сеть сам: сеть живёт в `net.py` и в `osiris.collect`;
  * не пишет собственную шапку «это внешние данные» — её ставит движок по
    `external_output=True` (`ToolResult.render`), и вторая копия только
    научила бы модель считать шапку частью страницы;
  * не сочиняет текстов отказа: все строки берутся из `render.py` и
    `config.py`. Код отказа — ДАННЫЕ (`render.REFUSAL_CODES`), а не строковый
    литерал по месту: опечатку в литерале не видно, неизвестный код виден.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit

from ... import html_text
from ...db import utcnow
from ...plugin_security import PluginSecurityError, redact_text
from ...tools import REGISTRY, ToolContext, ToolResult, ToolSpec
from .. import osiris
from . import config, ledger, net, render, sources

__all__ = [
    "MAX_HITS", "MIN_PASSAGES", "MAX_PASSAGES", "SECTIONS_MAX", "NEAR_MAX",
    "coerce_args", "canon_query", "subject_of", "url_subject", "guard_query",
    "normalize_search_args", "normalize_open_args", "normalize_find_args",
    "normalize_cite_args",
    "search_effect", "open_effect", "find_effect", "cite_effect",
    "tool_search", "tool_open", "tool_find", "tool_cite",
    "SPECS", "register",
]


# ------------------------------------------------------------------ потолки

# Потолки вывода, а не транспорта. Ни один из них не выведен в `input_schema`:
# всё, что модель может назвать числом, она однажды назовёт большим числом.
MAX_HITS = sources.MAX_HITS_CAP          # столько же, сколько разбирает parse_serp
MIN_PASSAGES = 2
MAX_PASSAGES = 12
SECTIONS_MAX = 12                        # подсказка «есть разделы: …» при промахе
NEAR_MAX = 3                             # ближайших фрагментов при промахе цитаты

# Знаки, которые в человеческой фразе законны наравне с буквами. Всё прочее
# (`=`, `&`, `%`, `\`, `|`, `{`, `}`, `[`, `]`, `` ` ``) — это разметка или
# структура, а не язык, и считается в долю «не-буквенных» (поправка A3).
_LANG_PUNCT = ".,:;!?'\"«»()–—-…/+#№%$"

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-._]{0,251}[a-z0-9])?$")
_REF_NUM_RE = re.compile(r"^([wl])([0-9]{1,3})")
_TRUE_WORDS = frozenset({"1", "true", "yes", "y", "on", "да", "истина"})
_FALSE_WORDS = frozenset({"0", "false", "no", "n", "off", "нет", "ложь"})


# ------------------------------------------------- терпимость к аргументам

# Синонимы — не украшение. Локальная модель на 7B пишет `q` вместо `query` и
# `link` вместо `url` чаще, чем попадает в схему, а отказ по имени ключа она
# исправить не умеет: в её обучающих данных обе формы встречались одинаково
# часто. Ключ `_raw` кладёт `providers._parse_tool_arguments`, когда JSON от
# раннера не разбирается вовсе, — сегодня это отказ КАЖДОГО инструмента
# системы, и лечится он здесь одной функцией, а не четырьмя ветками.
_QUERY_KEYS = ("query", "q", "search", "input", "text", "prompt", "question", "запрос")
_REF_KEYS = ("ref", "id", "ref_id", "token", "ссылка")
_URL_KEYS = ("url", "address", "href", "page", "адрес")
_AMBIGUOUS_KEYS = ("link",)              # «link» бывает и токеном, и адресом


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _looks_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _looks_ref(value: str) -> bool:
    return bool(config.REF_RE.match(value))


def _from_raw(raw: str) -> dict[str, Any]:
    """`_raw` → аргументы. Сначала JSON (7B кодирует его дважды чаще, чем
    ошибается в схеме), потом трактовка по ФОРМЕ строки.

    Трактовка по форме — не догадка ради удобства: строка, которая выглядит как
    токен, не может быть ничем иным, а строка с `http://` не может быть
    запросом. Всё остальное считается фразой запроса, потому что это самый
    частый и самый безобидный случай: фраза уйдёт в шлюз `guard_query` и там
    будет проверена как обычный запрос.
    """
    text = raw.strip()
    for _ in range(2):                    # двойное кодирование, не больше
        if not (text.startswith("{") and text.endswith("}")):
            break
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            break
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            text = parsed.strip()
            continue
        break
    if _looks_ref(text):
        return {"ref": text}
    if _looks_url(text):
        return {"url": text}
    return {"query": text}


def _first(src: Mapping[str, Any], keys) -> str:
    for key in keys:
        value = _text(src.get(key))
        if value:
            return value
    return ""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _text(value).lower() in _TRUE_WORDS


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def canon_query(raw: Any) -> str:
    """Каноническая форма запроса: невидимое снято, пробелы схлопнуты, ≤200.

    Канонизация делается ЗДЕСЬ, а не в шлюзе, потому что через неё проходят и
    `normalize_args` (одобрение), и handler (исполнение). Разойдись они — и
    правило «одобренный путь == исполненный путь» перестало бы выполняться на
    невидимом знаке, которого владелец в предпросмотре всё равно не увидит.
    """
    return html_text.normalize_ws(_text(raw))[:config.QUERY_MAX_CHARS]


def canon_ref(raw: Any) -> str:
    """Токен в одной форме. Ведущий ноль (`w03`) — обычная описка маленькой
    модели, и приводится к `w3` ДО того, как попадёт в digest одобрения: иначе
    одобренная строка и исполненная различались бы на знак, которого нет."""
    token = _text(raw)
    match = _REF_NUM_RE.match(token)
    if not match:
        return token[:config.REF_MAX_CHARS]
    normal = f"{match.group(1)}{int(match.group(2))}{token[match.end():]}"
    return normal[:config.REF_MAX_CHARS]


def coerce_args(args: Any) -> dict[str, Any]:
    """ЕДИНАЯ терпимость ко входу. Никогда не бросает.

    Стоит и в `normalize_args` ВСЕХ спек, и первой строкой КАЖДОГО handler'а.
    Иначе одобренный путь не равен исполненному: движок показывает владельцу
    `normalize_args(args)`, а исполняет `handler(args)` с СЫРЫМИ аргументами
    (`engine._run_tool_now`), и любое расхождение между этими двумя формами —
    это одобрение одного, исполнение другого.
    """
    src = dict(args) if isinstance(args, Mapping) else {}
    raw = src.get("_raw")
    if isinstance(raw, str) and raw.strip():
        # Явные ключи сильнее восстановленных: если раннер дал и то и другое,
        # угадывать нам нечего.
        merged = _from_raw(raw)
        merged.update({k: v for k, v in src.items()
                       if k != "_raw" and v is not None and v != ""})
        src = merged

    out: dict[str, Any] = {}
    query = _first(src, _QUERY_KEYS)
    ref = _first(src, _REF_KEYS)
    url = _first(src, _URL_KEYS)
    for key in _AMBIGUOUS_KEYS:
        value = _text(src.get(key))
        if not value:
            continue
        if not ref and _looks_ref(value):
            ref = value
        elif not url:
            url = value

    if query:
        out["query"] = canon_query(query)
    if ref:
        out["ref"] = canon_ref(ref)
    if url:
        out["url"] = url[:2000]
    site = _text(src.get("site")) or _text(src.get("domain")) or _text(src.get("host"))
    if site:
        out["site"] = site.lower().rstrip(".")
    for key in ("quote", "claim"):
        value = _text(src.get(key))
        if value:
            out[key] = value
    if any(k in src for k in ("limit", "count", "n")):
        out["limit"] = _as_int(src.get("limit", src.get("count", src.get("n"))), MAX_HITS)
    if any(k in src for k in ("max_chars", "chars", "length")):
        out["max_chars"] = _as_int(
            src.get("max_chars", src.get("chars", src.get("length"))),
            config.PAGE_CHARS_DEFAULT)
    if any(k in src for k in ("fresh", "force", "refresh")):
        out["fresh"] = _as_bool(src.get("fresh", src.get("force", src.get("refresh"))))
    return out


# ----------------------------------------------------------- субъект эпизода


def subject_of(query: str) -> str:
    """Субъект эпизода поиска. Форму задаёт `sources.search_subject`, а не мы:
    он же уходит движку текстом запроса, и вторая форма разошлась бы с первой."""
    return sources.search_subject(query)


def url_subject(url: str) -> str:
    """Субъект для страницы, открытой по адресу без поиска (раздел 9 проекта).

    Хэш, а не сам адрес: в субъекте OSIRIS запрещён «/», а урезанный до 200
    знаков адрес перестал бы быть уникальным ровно там, где адреса длинные.
    sha256, а не sha1, потому что весь остальной проект считает sha256 и второй
    хэш-функции здесь заводить не за чем.
    """
    digest = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:16]
    return f"web:url:{digest}"


# ------------------------------------------------- шлюз исходящего запроса


def guard_query(query: Any) -> str | None:
    """ПОЛОЖИТЕЛЬНАЯ форма шлюза (поправка A3). None — можно отправлять.

    Чёрный список значений не может ловить того, чего не знает: содержимое
    `~/.ssh/id_rsa`, `.env` соседнего проекта, историю задач. Поэтому проверка
    не «нет ли здесь известного секрета», а «похоже ли это на фразу человека»:

      * длина в границах `config.QUERY_MIN_CHARS..QUERY_MAX_CHARS`;
      * ни одного токена длиннее `QUERY_MAX_TOKEN_CHARS` — человеческое слово
        короче, а ключ, хэш и base64-блоб длиннее. Это же правило заменяет
        отдельный порог «блоб больше 40 знаков»: два порога на одну тему
        однажды разойдутся;
      * доля не-языковых знаков не выше `QUERY_MAX_NONALPHA_RATIO`;
      * не больше `QUERY_MAX_WORDS` слов;
      * ни одного образца из `config.QUERY_DENY` (PEM, путь, URL, JSON, env);
      * `psec.redact_text` строку не меняет — то, что редактор секретов счёл бы
        секретом, наружу не уходит.

    Функция ЧИСТАЯ: её зовёт и `search_effect` (то есть до исполнения вообще),
    и сам handler. Второй вызов не лишний — правило владельца в `tool_rules`
    применяется ПОСЛЕ хука и может вернуть эффект в `auto`, и тогда handler
    остаётся единственной дверью.

    При срабатывании запрос НЕ отправляется в урезанном виде: урезанный запрос
    и поиск ломает, и событие прячет.
    """
    text = canon_query(query)
    if not text:
        return "запрос пуст"
    if len(text) < config.QUERY_MIN_CHARS:
        return f"запрос короче {config.QUERY_MIN_CHARS} знаков — искать нечего"
    if len(text) > config.QUERY_MAX_CHARS:
        return (f"запрос длиннее {config.QUERY_MAX_CHARS} знаков; это уже не вопрос, "
                f"а выгрузка")
    for pattern, why in config.QUERY_DENY:
        if pattern.search(text):
            return why
    words = text.split(" ")
    if len(words) > config.QUERY_MAX_WORDS:
        return (f"в запросе больше {config.QUERY_MAX_WORDS} слов; поисковый запрос "
                f"так не выглядит")
    for word in words:
        if len(word) > config.QUERY_MAX_TOKEN_CHARS:
            return (f"в запросе есть слово длиннее {config.QUERY_MAX_TOKEN_CHARS} знаков — "
                    f"так выглядит ключ, хэш или закодированный блок, а не слово")
    # Фраза человека такой длины содержит пробелы. Единый длинный токен с
    # разделителями внутри — это запись из файла, пара ключ-значение или строка
    # конфига. Проверка положительная: она описывает, как выглядит вопрос, а не
    # перечисляет известные секреты, которых мы всё равно не знаем.
    if len(words) == 1 and len(text) > config.QUERY_LONE_TOKEN_CHARS:
        if any(ch in text for ch in config.QUERY_SEPARATORS_STRONG):
            return ("это одно длинное слово со знаком присваивания или пути внутри: "
                    "так выглядит пара ключ-значение, а не вопрос")
        weak = sum(text.count(ch) for ch in config.QUERY_SEPARATORS_WEAK)
        if weak >= config.QUERY_LONE_TOKEN_SEPARATORS:
            return ("это одно длинное слово, набитое разделителями: так выглядит "
                    "строка из файла, а не вопрос")
    dense = [ch for ch in text if not ch.isspace()]
    if dense:
        odd = sum(1 for ch in dense if not ch.isalnum() and ch not in _LANG_PUNCT)
        if odd / len(dense) > config.QUERY_MAX_NONALPHA_RATIO:
            return ("в запросе слишком много не-словесных знаков; так выглядит "
                    "структура данных, а не фраза")
    if redact_text(text) != text:
        return "в запросе есть то, что редактор секретов считает ключом или токеном"
    return None


# --------------------------------------------------------------- эффекты

# Хуки ЧИСТЫЕ по сигнатуре (`bcc/tools.py`: `Callable[[dict], tuple | None]`):
# ни `svc`, ни `run_id`, ни доступа к реестру ссылок. Именно поэтому политика
# «ссылка со страницы требует одобрения» выражена ПРЕФИКСОМ токена, а сам токен
# самоописывающий (поправка A2): хост и путь живут в аргументе, поэтому
# попадают и в предпросмотр владельцу, и в `approval_digest`, и в `args_hash`.


def search_effect(args: dict) -> tuple[str, str] | None:
    """Только `deny` и только по форме аргументов; `ask` — один случай (B1).

    Почему поиск вообще `auto`: получателя выбирает не модель, а
    зарегистрированный владельцем backend. Ask на каждый поиск паркует прогон и
    за неделю приучает жать «одобрить» не читая — это не безопасность, а её
    имитация. Единственный реальный канал (текст запроса) закрывается шлюзом до
    сети, а не вопросом, который прочитают один раз из десяти.

    Исключение — `site` на backend'е ОБЩЕГО веб-поиска (поправка B1): там
    посылка «адрес выбрал backend» ложна, потому что выдача есть
    детерминированная функция от `query` и `site`, и инъекция со страницы умеет
    вторым прыжком получить чтение своего хоста.

    Признак «в игре общий поиск» здесь ровно один и он проверяемый:
    `config.SEARXNG_URL`. Второй общий backend (`brave-search`) требует ключа,
    а ключей этот файл не передаёт вовсе (см. `tool_search`), поэтому выбран он
    быть не может. Связка названа вслух: начнёт ведущий передавать ключи —
    эту функцию обязан переписать вместе с ним, и на этот случай handler
    страхует хук отдельной проверкой уже по ВЫБРАННОМУ backend'у.
    """
    args = coerce_args(args)
    query = args.get("query", "")
    why = guard_query(query)
    if why is not None:
        return "deny", (f"запрос наружу не отправлен: {why}. Переформулируйте вопрос "
                        f"обычными словами и вызовите web.search ещё раз")

    site = str(args.get("site") or "")
    if site:
        if not _HOSTNAME_RE.match(site):
            return "deny", ("site должен быть именем хоста вида example.org, "
                            "без схемы, пути и звёздочек")
        serp = config.serp_reason(site)
        if serp is not None:
            return "deny", f"сужение по этому хосту запрещено: {serp}"

    limit = args.get("limit")
    if limit is not None and not 1 <= int(limit) <= MAX_HITS:
        return "deny", f"limit должен быть числом от 1 до {MAX_HITS}"

    if site and config.SEARXNG_URL:
        return "ask", ("сужение общего веб-поиска по домену выбирает получателя "
                       "запроса; владелец должен видеть, какой это домен")
    return None


def _open_url_refusal(raw_url: str) -> str | None:
    """Причина `deny` для адреса, названного моделью. None — можно спрашивать.

    Поправка A1, и она не про аккуратность. Движок оборачивает предпросмотр
    одобрения в `_ps_redact_text`, а владелец в интерфейсе видит ТОЛЬКО
    `preview` — значит `?api_key=sk-…` будет показан ему без ключа, а
    исполнится сырой аргумент. Защита оказалась бы анти-коррелирована с
    опасностью: чем настоящее утечка, тем надёжнее её спрячут от человека.
    Поэтому:

      * непустая query-строка или фрагмент — отказ БЕЗ порога длины. У документа
        нет законной нужды в «?»: адрес с обязательным параметром ищется
        поиском, а не собирается моделью;
      * `redact_text(url) != url` — отказ. Всё, что редактор секретов изменил
        бы, нельзя честно показать человеку, а значит нельзя и спрашивать.
    """
    text = raw_url.strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.query and not config.URL_QUERY_ALLOWED:
        return ("у адреса есть строка параметров «?…»; такой адрес нельзя честно "
                "показать владельцу — предпросмотр одобрения вычищает из него "
                "похожее на ключи. Найдите страницу поиском")
    if parts.fragment and not config.URL_FRAGMENT_ALLOWED:
        return "у адреса есть якорь «#…»; уберите его — страница от этого не изменится"
    if redact_text(text) != text:
        return ("в адресе есть то, что редактор секретов считает ключом; владельцу "
                "он был бы показан уже без этой части, то есть одобрение вышло бы "
                "слепым")

    try:
        canonical = html_text.canon_url(text)
    except (ValueError, UnicodeError) as exc:
        return f"адрес отклонён до сети: {exc}"
    if redact_text(canonical) != canonical:
        return "в каноническом виде адреса есть похожее на ключ"
    if not canonical.lower().startswith("https://"):
        return ("только https: ответ по http можно не только прочитать, но и подменить, "
                "и тогда инъекция придёт с паспортом доверенного источника")
    host = (urlsplit(canonical).hostname or "")
    if not host or not host.isascii():
        return "имя хоста не приводится к ASCII"
    serp = config.serp_reason(canonical)
    if serp is not None:
        return serp
    if config.is_exfil_sink(host):
        return ("этот адрес принимает данные, а не отдаёт их: открыть его значит "
                "отправить, а не прочитать")
    return None


def open_effect(args: dict) -> tuple[str, str] | None:
    """`deny` / `ask` / None для web.open. Работает на СЫРЫХ аргументах и даёт
    ту же каноническую форму, что и handler (через общий `coerce_args`).

      * None (auto) — только `w`-токен. Байтов, управляемых моделью, в сетевом
        адресе ноль: адрес отчеканен backend'ом или владельцем до и независимо
        от воли модели, и одобрение не добавило бы человеку ни одного бита;
      * ask — любой непустой `url` и любой `l`-токен. Это два единственных
        случая, когда адрес выбирает модель или враждебная страница, и в обоих
        владелец видит полное назначение: у `url` — сам адрес, у `l`-токена —
        хост и путь ВНУТРИ аргумента (поправка A2);
      * deny — структурно негодное. Список причин см. `_open_url_refusal`.
    """
    args = coerce_args(args)
    ref = str(args.get("ref") or "")
    url = str(args.get("url") or "")

    if not ref and not url:
        return "deny", ('нужен ref из выдачи поиска либо url; вызовите '
                        'web.search {"query":"…"} и возьмите токен оттуда')
    if ref and url:
        return "deny", ("ref и url вместе — неизвестно, что открывать; оставьте "
                        "что-то одно")

    if ref:
        parsed = config.parse_ref(ref)
        if parsed is None:
            return "deny", ('токен не той формы: ждём «w3» или «l7@host/путь» — '
                            'ровно так, как он напечатан в выдаче')
        kind, host, _path = parsed
        if kind == "l":
            if config.is_exfil_sink(host):
                return "deny", "хост в токене принимает данные, а не отдаёт их"
            serp = config.serp_reason(host)
            if serp is not None:
                return "deny", serp
            return "ask", ("ссылка со страницы: адрес выбрала страница, а не поиск и "
                           "не владелец — владелец должен увидеть, куда она ведёт")
        return None

    why = _open_url_refusal(url)
    if why is not None:
        return "deny", why
    return "ask", ("адрес выбрала модель, а не источник; владелец должен увидеть "
                   "его целиком перед первым обращением")


def find_effect(args: dict) -> tuple[str, str] | None:
    """Только `deny`. Сети здесь нет вовсе: страница перечитывается из уже
    сохранённого сырья, ни байта наружу, ни расхода лимита источника — то есть
    спрашивать не о чем, а `ask` вытолкнул бы модель к повторной ВЫБОРКЕ той же
    страницы, что как раз стоит байтов."""
    args = coerce_args(args)
    if not args.get("ref"):
        return "deny", 'нужен ref уже прочитанной страницы, например {"ref":"w1"}'
    if config.parse_ref(str(args["ref"])) is None:
        return "deny", "токен не той формы: ждём «w3» или «l7@host/путь»"
    if not str(args.get("query") or "").strip():
        return "deny", "нужен query — одно-два слова, которые ищем внутри страницы"
    return None


def cite_effect(args: dict) -> tuple[str, str] | None:
    """Только `deny`. Наружу web.cite не ходит и ничего не отправляет.

    Почему здесь нет ни `ask`, ни трения: это единственный инструмент, который
    делает ответ проверяемым. Любое трение здесь толкает модель ровно к тому,
    ради борьбы с чем всё затевалось, — пересказать своими словами и не
    сослаться.
    """
    args = coerce_args(args)
    if not args.get("ref"):
        return "deny", 'нужен ref прочитанной страницы, например {"ref":"w1"}'
    if config.parse_ref(str(args["ref"])) is None:
        return "deny", "токен не той формы: ждём «w3» или «l7@host/путь»"
    quote = str(args.get("quote") or "").strip()
    if not quote:
        return "deny", "нужна quote — строка, скопированная из текста страницы ЗНАК В ЗНАК"
    if len(quote) > 600:
        return "deny", "цитата длиннее 600 знаков; возьмите одно предложение"
    if not str(args.get("claim") or "").strip():
        return "deny", "нужен claim — что именно эта цитата доказывает"
    return None


# -------------------------------------------------- канонизация для digest


def _safe_normalize(fn, args: Any) -> dict[str, Any]:
    """`normalized_args` в `bcc/tools.py` исключение НЕ глотает: сбой
    нормализации уронил бы вычисление digest'а посреди чужого прогона. Поэтому
    падать здесь нельзя вовсе, и худший случай — вернуть вход как есть."""
    try:
        return fn(args)
    except Exception:                     # noqa: BLE001 — канонизация обязана быть тихой
        return dict(args) if isinstance(args, Mapping) else {}


def normalize_search_args(args: dict) -> dict:
    return _safe_normalize(coerce_args, args)


def _canon_open(args: Any) -> dict[str, Any]:
    out = coerce_args(args)
    url = str(out.get("url") or "")
    if url:
        try:
            out["url"] = html_text.canon_url(url)
        except (ValueError, UnicodeError):
            # Негодный адрес остаётся как есть: его всё равно отвергнет
            # `open_effect`, а подмена его на «исправленный» означала бы, что
            # владелец одобряет один адрес, а отвергается другой.
            out["url"] = url
    if "max_chars" in out:
        out["max_chars"] = max(config.PAGE_CHARS_MIN,
                               min(config.PAGE_CHARS_MAX, int(out["max_chars"])))
    return out


def normalize_open_args(args: dict) -> dict:
    return _safe_normalize(_canon_open, args)


def normalize_find_args(args: dict) -> dict:
    return _safe_normalize(coerce_args, args)


def normalize_cite_args(args: dict) -> dict:
    return _safe_normalize(coerce_args, args)


# ------------------------------------------------------------- вспомогательное


def _ok(text: str, one_line: str, **data: Any) -> ToolResult:
    """Отказ по СОСТОЯНИЮ (лимит, нечего искать, сети нет) отдаётся с
    `error=False` намеренно. `error=True` маленькая модель читает как «попробуй
    ещё раз», и защита от перерасхода сама становится перерасходом до
    `max_steps`."""
    return ToolResult(content=text, one_line=one_line[:140], data=data)


def _fail(text: str, one_line: str, **data: Any) -> ToolResult:
    """`error=True` — только там, где повтор ИМЕЕТ смысл: кривой аргумент,
    несуществующий токен, ненайденная цитата."""
    return ToolResult(content=text, one_line=one_line[:140], error=True, data=data)


async def _emit(svc: Any, kind: str, **data: Any) -> None:
    bus = getattr(svc, "bus", None)
    if bus is None:
        return
    try:
        await bus.emit(kind, **data)
    except Exception:                     # noqa: BLE001 — шина не обязана быть жива
        pass


def _disabled() -> ToolResult:
    return _ok(render.render_refused("disabled",
                                     why=f"нужны {config.FLAG} и {config.OSIRIS_FLAG}"),
               "web: выключено настройкой")


def _passages_for(budget_chars: int) -> int:
    return max(MIN_PASSAGES, min(MAX_PASSAGES, budget_chars // 300))


def _parse_iso(value: Any) -> datetime | None:
    """ISO-время из сырья. Своя разборка, а не приватная функция соседа: чужая
    приватная деталь имеет полное право поменяться без предупреждения."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is None else stamp.astimezone(timezone.utc).replace(
        tzinfo=None)


def _age_of(value: Any) -> float | None:
    """Возраст в секундах по времени сетевого забора. None — времени нет, и
    подставлять «сейчас» нельзя: выдуманная свежесть хуже отсутствующей."""
    stamp = _parse_iso(value)
    if stamp is None:
        return None
    return max(0.0, (utcnow() - stamp).total_seconds())


def _backend_host(backend: Any) -> str:
    """Хост самого backend'а — им `ledger.mint` отличает адрес, ВЫБРАННЫЙ
    источником, от адреса, который источник лишь процитировал (поправка D2).
    У backend'а общего поиска декларации нет, и хост пуст — значит ни один
    адрес его выдачи не получит `w`, и это ровно то, чего требует D2."""
    return net.host_of(str((getattr(backend, "decl", None) or {}).get("base_url") or ""))


_PAGE_REFUSAL_CODES = {
    # Код `net.PageRefused` → код `render.REFUSAL_CODES`. Только точные пары:
    # приблизительная пара напечатала бы владельцу утверждение, которое неверно
    # (например, «это не текстовая страница» про сжатый ответ).
    "serp_denied": "serp_denied",
    "exfil_sink": "exfil_sink",
    "not_ascii_host": "idn_host",
    "content_type": "content_type",
    "empty_text": "too_short",
}


def _refuse_page(exc: net.PageRefused) -> ToolResult:
    """`PageRefused` → готовый текст. Код без точной пары отдаётся `render`
    как неизвестный: тогда печатается общий заголовок, а НАСТОЯЩАЯ причина
    уходит в поле «подробность». Это честнее, чем подобрать похожий код и
    показать владельцу неверную строку."""
    code = _PAGE_REFUSAL_CODES.get(getattr(exc, "code", ""), "")
    hint = ("" if code else 'web.search {"query":"те же слова"} — ищите другой источник')
    return _ok(render.render_refused(code, why=str(exc), hint=hint),
               f"web.open: отказ ({getattr(exc, 'code', 'refused')})")


def _refuse_osiris(exc: Exception) -> ToolResult:
    """Осирисовские отказы и отказ egress. Для каждого — свой код `render`,
    и «наружу ничего не уходило» нигде не написано там, где байты уже ушли."""
    if isinstance(exc, osiris.RobotsDisallowError):
        text = str(exc)
        code = "robots_unreachable" if "недоступ" in text.lower() else "robots"
        return _ok(render.render_refused(code, why=text), "web.open: robots запретил")
    if isinstance(exc, PluginSecurityError):
        text = str(exc)
        if "allowlist" in text.lower() or "pinned" in text.lower():
            return _ok(render.render_refused("redirect_offsite", why=text),
                       "web.open: редирект за пределы сайта")
        return _ok(render.render_refused("", why=text,
                                         hint="ничего по этому адресу. Возьмите адрес "
                                              "из выдачи поиска."),
                   "web.open: egress запретил")
    return _ok(render.render_refused("", why=str(exc),
                                     hint='web.search {"query":"те же слова"} — '
                                          'источник не ответил'),
               f"web.open: {type(exc).__name__}")


def _archive_rows(led: "ledger.Ledger") -> list[dict[str, Any]]:
    """Что есть в локальном архиве прогона. Возраст здесь не считается: строки
    печатает `render.render_offline`, и он же обязан сказать «свежесть НЕ
    подтверждена» — второй источник этой строки однажды разойдётся с первым."""
    rows = []
    for entry in led.opened_refs()[:12]:
        rows.append({"ref": entry.ref, "host": entry.host, "title": entry.title,
                     # Время в реестре — момент, когда страница была прочитана
                     # ЭТИМ прогоном. Это не то же самое, что время сетевого
                     # забора (чтение могло прийти из архива), поэтому строка
                     # архива и печатается с оговоркой «свежесть НЕ подтверждена».
                     "fetched_at": entry.opened_at,
                     "age_seconds": _age_of(entry.opened_at)})
    return rows


# ------------------------------------------------------------------ web.search


async def tool_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Поиск через зарегистрированный backend. Наружу уходит ТОЛЬКО текст запроса.

    Порядок здесь важен ровно в одном месте: шлюз запроса стоит ДО выбора
    backend'а и до любого списания. Пока запрос не признан фразой человека,
    наружу не уходит ничего и ни один счётчик не двигается.

    `api_keys` не передаётся сознательно (и это не забывчивость): единственное
    зашифрованное хранилище процесса держит ключи ПРОВАЙДЕРОВ моделей, а не
    ключи поисковых API, и заявить «ключ есть» без установленного
    `net.KeyedFetchAdapter` значило бы получить тихий 401, который выглядит как
    «источник сломался». Поэтому `brave-search` виден в реестре и НИКОГДА не
    опрашивается молча — ровно то поведение, которое обещано владельцу.
    """
    args = coerce_args(args)
    if not config.both_enabled():
        return _disabled()

    query = canon_query(args.get("query"))
    why = guard_query(query)
    if why is not None:
        # Событие несёт причину и длину, но НЕ сам текст: отвергнут он мог быть
        # именно потому, что содержал секрет, и класть его в ленту событий
        # значило бы вынести наружу то, что мы только что отказались вынести.
        await _emit(ctx.svc, "web.query_refused", run_id=ctx.run_id, why=why,
                    chars=len(query))
        return _fail(render.render_refused("query_refused", why=why),
                     "web.search: запрос не отправлен")

    subject = subject_of(query)
    site = str(args.get("site") or "")
    limit = min(MAX_HITS, max(1, _as_int(args.get("limit"), MAX_HITS)))
    fresh = _as_bool(args.get("fresh"))

    led = ledger.Ledger.load(ctx.svc, ctx.run_id)
    led.seed_from_task(ctx.task)

    ready = sources.readiness(ctx.svc)
    backend = sources.pick_backend(ctx.svc, query, site)
    if backend is None:
        if site:
            return _fail(render.render_refused(
                "", why=(f"ни один настроенный источник не объявил {site} своим: "
                         f"сузить чужой источник до чужого хоста нельзя"),
                hint='web.search {"query":"те же слова"} без site'),
                "web.search: site не поддержан")
        return _ok(render.render_no_backends(ready), "web.search: искать негде")

    # Страховка хука (см. `search_effect`): если общий backend всё-таки выбран
    # не через SearXNG, сужение по домену не должно проехать в auto молча.
    if site and backend.general_web and not config.SEARXNG_URL:
        return _fail(render.render_refused(
            "", why=("сужение общего веб-поиска по домену требует одобрения владельца, "
                     "а этот вызов его не проходил"),
            hint='web.search {"query":"те же слова"} без site'),
            "web.search: site требует одобрения")

    if not led.spend("search"):
        return _ok(render.render_budget("search", led.left(),
                                        [e.ref for e in led.refs()]),
                   "web.search: лимит поисков исчерпан")
    if not ledger.daily_take(ctx.svc):
        return _ok(render.render_budget("daily", led.left()),
                   "web.search: суточный лимит исчерпан")
    led.save()

    # A3: каждый исходящий запрос — событием в живую ленту дословно. Здесь
    # дословно можно и нужно: он уже признан фразой человека и он же станет
    # субъектом эпизода, который владелец увидит в GET /api/web/episodes.
    await _emit(ctx.svc, "web.query_sent", run_id=ctx.run_id, backend=backend.id,
                query=subject, site=site, fresh=fresh)

    result = await sources.run_search(ctx.svc, backend, subject, force_refresh=fresh)
    code = str(result.get("code") or "bad_response")

    if not result.get("ok"):
        # E1: три исхода — три разных отказа. «Движки не ответили» не имеет
        # права выглядеть как «в интернете этого нет».
        shown = code if code in ("empty_result", "engines_down") else "source_unavailable"
        detail = str(result.get("detail") or "")
        if shown == "source_unavailable" and code != "source_unavailable":
            detail = f"{code}: {detail}" if detail else code
        return _ok(render.render_offline(
            ready, _archive_rows(led), code=shown, detail=detail, query=query,
            backend=backend.id, honest_capability=backend.honest_capability,
            budget=led.left()),
            f"web.search: {shown}")

    origin_host = _backend_host(backend)
    trusted = list(result.get("trusted_hosts") or ())
    rows: list[dict[str, Any]] = []
    lost = int(result.get("dropped") or 0)
    for hit in list(result.get("hits") or ())[:limit]:
        if not isinstance(hit, Mapping):
            lost += 1
            continue
        token = led.mint(str(hit.get("url") or ""), kind="search", subject=subject,
                         title=str(hit.get("title") or ""),
                         snippet=str(hit.get("snippet") or ""),
                         origin=backend.id, origin_host=origin_host,
                         trusted_hosts=trusted, step=ctx.step)
        if not token:
            lost += 1
            continue
        rows.append({"ref": token, "host": str(hit.get("host") or ""),
                     "title": str(hit.get("title") or ""),
                     "snippet": str(hit.get("snippet") or "")})
    led.save()

    if not rows:
        return _ok(render.render_offline(
            ready, _archive_rows(led), code="empty_result", detail=str(result.get("detail") or ""),
            query=query, backend=backend.id,
            honest_capability=backend.honest_capability, budget=led.left()),
            "web.search: нечего показать")

    text = render.render_hits(
        rows, backend=backend.id, honest_capability=backend.honest_capability,
        query=query, budget=led.left(), from_cache=bool(result.get("from_cache")),
        fetched_at=result.get("fetched_at", ""),
        age_seconds=_age_of(result.get("fetched_at", "")),
        transport=result.get("transport", ""), dropped=lost)
    return _ok(text, f"web.search: {len(rows)} результатов ({backend.id})",
               backend=backend.id, subject=subject, refs=[r["ref"] for r in rows],
               dropped=lost, from_cache=bool(result.get("from_cache")))


# -------------------------------------------------------------------- web.open


def _resolve_open_target(led: "ledger.Ledger", args: Mapping[str, Any],
                         ctx: ToolContext) -> tuple[Any, ToolResult | None]:
    """Запись реестра для web.open либо готовый отказ.

    Токен резолвится с ПРИЧИНОЙ, потому что «модель назвала несуществующий
    номер» (обычная ошибка 7B) и «хост в токене разошёлся с записью» — разные
    события. Второе означает, что назначение подменили между одобрением и
    исполнением, и молчать об этом нельзя (поправка A2).
    """
    ref = str(args.get("ref") or "")
    url = str(args.get("url") or "")

    # Повтор проверок хука, и он не лишний: правило владельца в `tool_rules`
    # применяется ПОСЛЕ хука и может вернуть эффект в `auto` (`decide_effect` —
    # последнее слово за правилом). Тогда handler остаётся единственной дверью,
    # и A1 обязан держаться и здесь.
    if not ref and not url:
        return None, _fail(
            render.render_refused("", why="не назван ни ref, ни url",
                                  hint='web.search {"query":"то, что ищешь"}'),
            "web.open: нечего открывать")
    if ref and url:
        return None, _fail(
            render.render_refused("", why="ref и url вместе — неизвестно, что открывать",
                                  hint='web.open {"ref":"w1"}'),
            "web.open: ref и url вместе")
    if url:
        why = _open_url_refusal(url)
        if why is not None:
            return None, _fail(
                render.render_refused("", why=why,
                                      hint='web.search {"query":"название страницы"}'),
                "web.open: адрес отклонён")

    if ref:
        entry, reason = led.resolve_with_reason(ref)
        if entry is not None:
            return entry, None
        if reason == "mismatch":
            return None, _fail(render.render_refused("ref_mismatch", why=ref),
                               "web.open: токен не сходится с реестром")
        return None, _fail(
            render.render_refused("ref_unknown",
                                  why=f"токен {ref} в этом прогоне не чеканился"),
            "web.open: неизвестный токен")

    # Адрес назвала модель, и владелец его уже одобрил. Чеканим как `link`, а не
    # `owner`: одобрение относится к ЭТОМУ вызову, а `owner`-токен получил бы
    # префикс `w` и открывался бы дальше без вопросов.
    token = led.mint(url, kind="link", subject=url_subject(url),
                     origin="tool:url", step=ctx.step)
    if not token:
        return None, _fail(
            render.render_refused("", why=("адрес не удалось записать в реестр: он либо "
                                           "не канонизуется, либо его путь длиннее того, "
                                           "что помещается в токен ссылки"),
                                  hint='web.search {"query":"название страницы"}'),
            "web.open: адрес не отчеканен")
    entry, _reason = led.resolve_with_reason(token)
    if entry is None:                     # недостижимо: токен только что отчеканен
        return None, _fail(render.render_refused("ref_unknown", why=token),
                           "web.open: токен потерян")
    return entry, None


async def tool_open(args: dict, ctx: ToolContext) -> ToolResult:
    """Прочитать страницу по токену или по адресу.

    Заражение реестра (поправка B1) взводится ДО сети и независимо от её
    исхода: заражает не текст страницы, а сам факт, что страница выбрала, чем
    ответить. После первого же web.open даже выдача поиска перестаёт давать
    `w`-токены, потому что `query` и `site` — аргументы модели, а выдача от них
    детерминирована.
    """
    # Ровно та же функция, что стоит в `normalize_args`: одобренный путь обязан
    # совпасть с исполненным до последнего знака, а не «в основном».
    args = normalize_open_args(args)
    if not config.both_enabled():
        return _disabled()

    led = ledger.Ledger.load(ctx.svc, ctx.run_id)
    led.seed_from_task(ctx.task)

    entry, refusal = _resolve_open_target(led, args, ctx)
    if refusal is not None:
        return refusal

    query = canon_query(args.get("query"))
    max_chars = max(config.PAGE_CHARS_MIN,
                    min(config.PAGE_CHARS_MAX,
                        _as_int(args.get("max_chars"), config.PAGE_CHARS_DEFAULT)))

    # Резерв ДО действия, и он списывается ДАЖЕ ЕСЛИ страница придёт из архива.
    # Проект обещал не считать перелистывание из кэша, и это обещание здесь НЕ
    # выполнено — выполнить его нечем: попадание в кэш известно только ПОСЛЕ
    # `fetch_page`, а спрашивать кэш заранее значило бы завести вторую копию
    # его логики рядом с первой. Ошибка идёт в безопасную сторону (лимит
    # расходуется быстрее обещанного), и она названа, а не спрятана.
    if not led.spend("open"):
        return _ok(render.render_budget("open", led.left(),
                                        [e.ref for e in led.opened_refs()]),
                   "web.open: лимит открытий исчерпан")
    if not ledger.daily_take(ctx.svc):
        return _ok(render.render_budget("daily", led.left()),
                   "web.open: суточный лимит исчерпан")
    led.mark_tainted()

    subject = entry.subject or url_subject(entry.url)
    try:
        page = await net.fetch_page(ctx.svc, entry.url, subject,
                                    ensure_host_source=sources.ensure_host_source,
                                    extract_chars=max_chars)
    except net.PageRefused as exc:
        if getattr(exc, "code", "") == "raw_budget":
            used, limit = net.raw_budget_state(osiris.store(ctx.svc))
            return _ok(render.render_budget("disk", led.left(),
                                            used=used // 1_000_000,
                                            limit=limit // 1_000_000),
                       "web.open: дисковый бюджет исчерпан")
        return _refuse_page(exc)
    except (osiris.OsirisError, PluginSecurityError) as exc:
        return _refuse_osiris(exc)
    except Exception as exc:              # noqa: BLE001 — беда сети это данные, не авария
        return _ok(render.render_refused("", why=f"{type(exc).__name__}: {exc}",
                                         hint='web.search {"query":"те же слова"}'),
                   f"web.open: {type(exc).__name__}")

    # Учёт ПОСЛЕ действия: байты уже приняты, секунды уже потрачены. На попадании
    # в кэш оба равны нулю, и это не оптимизация, а правда о том, чего не было.
    led.spend("bytes", page.bytes_read)
    led.spend("seconds", page.net_seconds)

    selection = html_text.select_passages(page.extraction, query,
                                          budget_chars=max_chars,
                                          max_passages=_passages_for(max_chars))
    shown = "\n".join(p.text for p in selection.passages)
    _clean, defanged = html_text.defang(shown)
    if defanged:
        # Только счётчик, хост и токен. Текста в событии нет: он и так у модели,
        # а в ленте владельца это был бы второй экземпляр той же инъекции.
        await _emit(ctx.svc, "web.injection_suspected", run_id=ctx.run_id,
                    ref=entry.ref, host=page.host, lines=defanged)

    links: list[dict[str, str]] = []
    if config.MAX_PAGE_LINKS:
        for link in page.extraction.links:
            if len(links) >= config.MAX_PAGE_LINKS:
                break
            token = led.mint(link.url, kind="link", subject=subject,
                             title=link.text, origin=entry.ref,
                             origin_host=page.host, step=ctx.step)
            if token:
                links.append({"ref": token, "text": link.text})

    led.mark_opened(entry.ref, raw_digest=page.raw_digest,
                    body_sha256=page.body_sha256, chars=page.extraction.chars,
                    truncated=bool(page.extraction.truncated), status=str(page.status))
    led.save()

    text = render.render_page(entry, page, selection, links, query=query,
                              defanged=defanged, budget=led.left())
    # `truncated`/`more` выставляются ВСЕГДА, когда текст неполон. Движок
    # допишет свою строку про обрезку ПОСЛЕ нашей «ДАЛЬШЕ», и мы это не
    # подавляем: лишняя строка честнее умолчания о неполноте.
    incomplete = bool(page.extraction.truncated) or \
        len(selection.passages) < len(page.extraction.blocks)
    return ToolResult(
        content=text, one_line=f"web.open: {entry.ref} {page.host} ({page.status})",
        truncated=incomplete,
        more=(f'web.find {{"ref":"{entry.ref}","query":"…"}}' if incomplete else ""),
        data={"ref": entry.ref, "host": page.host, "raw_digest": page.raw_digest,
              "chars": page.extraction.chars, "from_cache": page.from_cache,
              "transport": page.transport, "defanged": defanged,
              "links": [row["ref"] for row in links]})


# -------------------------------------------------------------------- web.find


def _sections(page: Any) -> list[str]:
    """Первые слова первых блоков — подсказка, ПО КАКОМУ слову искать дальше.

    Без неё промах заставляет модель перебирать синонимы вслепую до конца
    бюджета шагов; с ней он стоит одного дешёвого локального вызова.
    """
    out: list[str] = []
    for block in (getattr(page.extraction, "blocks", ()) or ())[:SECTIONS_MAX * 3]:
        text = html_text.normalize_ws(block.text)[:render.SECTION_MAX]
        if len(text) >= 8:
            out.append(text)
        if len(out) >= SECTIONS_MAX:
            break
    return out


def _need_page(entry: Any) -> ToolResult:
    return _fail(
        render.render_refused("", why=("страница по этому токену ещё не читалась, "
                                       "поэтому искать внутри нечего"),
                              hint=f'web.open {{"ref":"{entry.ref}"}}'),
        "web.find: страница не прочитана")


async def tool_find(args: dict, ctx: ToolContext) -> ToolResult:
    """Поиск ВНУТРИ уже прочитанной страницы. Сети нет и быть не может.

    Это лечение главной беды маленькой модели на длинной странице — потери
    нужного абзаца, — и оно не стоит ни байта, ни секунды лимита, ни расхода
    вежливости к хосту. Поэтому же здесь нет ни списания бюджета, ни нового
    наблюдения: искать внутри прочитанного не является новым актом сбора.
    """
    args = coerce_args(args)
    if not config.both_enabled():
        return _disabled()

    led = ledger.Ledger.load(ctx.svc, ctx.run_id)
    entry, reason = led.resolve_with_reason(str(args.get("ref") or ""))
    if entry is None:
        code = "ref_mismatch" if reason == "mismatch" else "ref_unknown"
        return _fail(render.render_refused(code, why=str(args.get("ref") or "")),
                     "web.find: токен не резолвится")

    query = canon_query(args.get("query"))
    if not query:
        return _fail(render.render_refused("", why="нужен query — одно-два слова",
                                           hint=f'web.find {{"ref":"{entry.ref}",'
                                                f'"query":"одно слово"}}'),
                     "web.find: нет запроса")

    page = await net.read_cached(ctx.svc, entry)
    if page is None:
        return _need_page(entry)

    selection = html_text.select_passages(page.extraction, query,
                                          budget_chars=config.PAGE_CHARS_DEFAULT,
                                          max_passages=_passages_for(
                                              config.PAGE_CHARS_DEFAULT))
    text = render.render_find(entry, selection, query, _sections(page), page=page)
    hits = len(selection.passages) if selection.max_score > 0.0 else 0
    return _ok(text, f"web.find: {entry.ref} — совпадений {hits}",
               ref=entry.ref, matches=hits, max_score=selection.max_score)


# -------------------------------------------------------------------- web.cite


def _near_fragments(page: Any, quote: str) -> list[str]:
    """Три ближайших фрагмента при промахе цитаты.

    Модель промахивается не по злому умыслу, а потому что пересказала своими
    словами; три настоящие строки возвращают её к дословности дешевле, чем ещё
    одно чтение страницы.
    """
    wanted = set(html_text.tokenize(quote))
    if not wanted:
        return []
    scored: list[tuple[int, int, str]] = []
    for block in getattr(page.extraction, "blocks", ()) or ():
        common = wanted & set(html_text.tokenize(block.text))
        if common:
            scored.append((-len(common), block.index,
                           html_text.normalize_ws(block.text)[:render.NEAR_MAX]))
    scored.sort()
    return [text for _score, _index, text in scored[:NEAR_MAX]]


def _quote_index(st: Any, subject: str) -> int:
    """Номер ссылки в ответе — это порядковый номер цитаты по ЭПИЗОДУ, а не по
    прогону: владелец читает след эпизода, и нумерация обязана совпадать с ним."""
    try:
        rows = st.observations(subject)
    except Exception:                     # noqa: BLE001 — диск не обязан быть цел
        return 1
    return 1 + sum(1 for row in rows
                   if isinstance(row, Mapping) and row.get("attribute") == "quote")


async def tool_cite(args: dict, ctx: ToolContext) -> ToolResult:
    """Сверить цитату с текстом страницы дословно и записать наблюдение `quote`.

    Это единственный инструмент, который делает выдуманную цитату механически
    невозможной: наблюдение создаётся ТОЛЬКО для строки, найденной в тексте
    буквально. Из битого текста цитировать запрещено (`page.quotable`):
    «дословная» цитата из мусора — это выдумка с паспортом.

    Наружу не ходит вовсе, поэтому и бюджета не тратит.
    """
    args = coerce_args(args)
    if not config.both_enabled():
        return _disabled()

    led = ledger.Ledger.load(ctx.svc, ctx.run_id)
    entry, reason = led.resolve_with_reason(str(args.get("ref") or ""))
    if entry is None:
        code = "ref_mismatch" if reason == "mismatch" else "ref_unknown"
        return _fail(render.render_refused(code, why=str(args.get("ref") or "")),
                     "web.cite: токен не резолвится")

    quote = str(args.get("quote") or "").strip()
    claim = str(args.get("claim") or "").strip()
    if not quote or not claim:
        return _fail(render.render_refused(
            "", why="нужны и quote (дословно со страницы), и claim (что этим доказано)",
            hint=f'web.cite {{"ref":"{entry.ref}","quote":"…","claim":"…"}}'),
            "web.cite: не хватает аргументов")

    page = await net.read_cached(ctx.svc, entry)
    if page is None:
        return _need_page(entry)
    if not page.quotable:
        return _ok(render.render_refused(
            "mojibake",
            why=(f"доля символов-замен {page.replace_ratio:.3f}, признак двойного "
                 f"декодирования: {page.mojibake}")),
            "web.cite: цитирование запрещено")

    found = html_text.find_quote(page.extraction, quote)
    if found is None:
        return _fail(render.render_cite_miss(entry, _near_fragments(page, quote)),
                     "web.cite: такой цитаты в тексте нет")
    offset, length = found

    st = osiris.store(ctx.svc)
    index = _quote_index(st, entry.subject or url_subject(entry.url))
    try:
        obs_id = _save_quote(st, entry, page, quote=quote, claim=claim,
                             offset=offset, length=length)
    except (osiris.OsirisError, PluginSecurityError) as exc:
        # Паспорт не собрался — цитату выдавать нельзя: строка ссылки без
        # записанного наблюдения переживёт разговор, но не переживёт проверки.
        return _fail(render.render_refused(
            "", why=f"наблюдение цитаты не записано: {exc}",
            hint="скажи владельцу, что цитата не сохранена, и не ссылайся на неё"),
            "web.cite: наблюдение не записано")

    text = render.render_cite_ok(entry, quote, index, page=page,
                                 offset=offset, length=length)
    return _ok(text, f"web.cite: [{index}] {entry.ref}",
               ref=entry.ref, observation_id=obs_id, offset=offset, length=length)


def _save_quote(st: Any, entry: Any, page: Any, *, quote: str, claim: str,
                offset: int, length: int) -> str:
    """Наблюдение `quote`. Паспорт нигде не собирается «примерно».

    `observed_at` берётся из времени СЕТЕВОГО забора страницы (поправка E3), а
    не из «сейчас»: цитата наблюдена тогда, когда содержимое пришло по проводу.
    `max_chars` и версия извлекателя пишутся в значение (поправка D6), потому
    что смещение записано В ИЗВЛЕЧЁННЫЙ текст, а он зависит от параметров
    извлечения — показывать цитату нужно с теми же.
    """
    subject = entry.subject or url_subject(entry.url)
    source = sources.ensure_host_source(st, page.host, url=page.url)
    observed_at = _parse_iso(page.fetched_at)
    if observed_at is None:
        # Времени сетевого забора нет — подставлять «сейчас» нельзя: это ровно
        # тот класс лжи, ради исключения которого вся фича и существует.
        raise osiris.OsirisError(
            "в сырье нет времени сетевого забора; цитата без времени наблюдения "
            "неотличима от прошлогодней")
    obs = osiris.Observation(
        value={
            "quote": quote[:600],
            "claim": claim[:600],
            "offset": int(offset),
            "length": int(length),
            "ref": entry.ref,
            "url": page.url,
            "host": page.host,
            "text_sha256": page.text_sha256,
            "extractor": html_text.EXTRACTOR_VERSION,
            "max_chars": page.extract_max_chars,
            "transport": page.transport,
            "fetched_at": page.fetched_at,
            "from_cache": bool(page.from_cache),
        },
        subject=subject,
        source_id=page.source_id or source.id,
        source_url=page.url,
        method=source.method,
        license=source.license,
        observed_at=observed_at,
        collected_at=st.next_collected_at(),
        confidence=min(0.5, float(source.default_confidence or 0.5)),
        raw_ref=f"raw:{page.raw_digest}",
        attribute="quote",
    )
    st.save_observations(subject, [obs], [page.raw_digest])
    return obs.id


# ---------------------------------------------------------------------- спеки

# Схемы ПЛОСКИЕ: ни одного вложенного объекта и ни одного массива. Это не
# аскеза, а замер: локальная модель на 7B заполняет вложенный объект неверно
# чаще, чем верно, и разбирать её ошибку потом дороже, чем не дать её сделать.
# В описании каждого — одна строка примера вызова с НАСТОЯЩИМИ значениями:
# пример в описании модель копирует, абстрактное «строка запроса» — нет.

SPECS: list[ToolSpec] = [
    ToolSpec(
        name="web.search",
        handler=tool_search,
        description=(
            "Найти страницы в интернете через настроенный источник. Возвращает "
            "список токенов вида w1, которые потом открывает web.open. "
            "Пример: web.search {\"query\":\"python asyncio timeout\"}"),
        input_schema={
            "query": {"type": "string",
                      "description": "вопрос обычными словами, 3..200 знаков"},
            "site": {"type": "string",
                     "description": "необязательно: один хост, например docs.python.org"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_HITS},
            "fresh": {"type": "boolean",
                      "description": "true — не брать из локального архива"},
        },
        required=["query"],
        category="read", permission="", source="web",
        default_effect="auto", external_output=True, idempotent=True,
        timeout_seconds=config.TOOL_TIMEOUTS["web.search"],
        effect_hook=search_effect, normalize_args=normalize_search_args),
    ToolSpec(
        name="web.open",
        handler=tool_open,
        description=(
            "Прочитать страницу по токену из выдачи (ref) ИЛИ по адресу (url). "
            "Токен предпочтительнее: адрес требует одобрения владельца. "
            "Пример: web.open {\"ref\":\"w1\",\"query\":\"timeout\"}"),
        input_schema={
            "ref": {"type": "string",
                    "description": "токен из выдачи: w1 либо l3@docs.python.org/ru"},
            "url": {"type": "string",
                    "description": "https-адрес без «?» и «#»; требует одобрения"},
            "query": {"type": "string",
                      "description": "необязательно: чем сузить показанные абзацы"},
            "max_chars": {"type": "integer",
                          "minimum": config.PAGE_CHARS_MIN,
                          "maximum": config.PAGE_CHARS_MAX},
        },
        required=[],
        category="read", permission="", source="web",
        default_effect="auto", external_output=True, idempotent=True,
        timeout_seconds=config.TOOL_TIMEOUTS["web.open"],
        effect_hook=open_effect, normalize_args=normalize_open_args),
    ToolSpec(
        name="web.find",
        handler=tool_find,
        description=(
            "Найти слово ВНУТРИ уже прочитанной страницы. В интернет не ходит, "
            "лимиты не тратит — пользуйся свободно. "
            "Пример: web.find {\"ref\":\"w1\",\"query\":\"версия\"}"),
        input_schema={
            "ref": {"type": "string", "description": "токен прочитанной страницы"},
            "query": {"type": "string", "description": "одно-два слова"},
        },
        required=["ref", "query"],
        category="read", permission="", source="web",
        default_effect="auto", external_output=True, idempotent=True,
        timeout_seconds=config.TOOL_TIMEOUTS["web.find"],
        effect_hook=find_effect, normalize_args=normalize_find_args),
    ToolSpec(
        name="web.cite",
        handler=tool_cite,
        description=(
            "Сверить цитату со страницей дословно и получить готовую строку "
            "ссылки для ответа. В интернет не ходит. "
            "Пример: web.cite {\"ref\":\"w1\",\"quote\":\"точная строка со страницы\","
            "\"claim\":\"что этим доказано\"}"),
        input_schema={
            "ref": {"type": "string", "description": "токен прочитанной страницы"},
            "quote": {"type": "string",
                      "description": "строка со страницы ЗНАК В ЗНАК, до 600 знаков"},
            "claim": {"type": "string", "description": "что именно она доказывает"},
        },
        required=["ref", "quote", "claim"],
        category="read", permission="", source="web",
        default_effect="auto", external_output=True, idempotent=True,
        timeout_seconds=config.TOOL_TIMEOUTS["web.cite"],
        effect_hook=cite_effect, normalize_args=normalize_cite_args),
]


def register(svc: Any) -> None:
    """Регистрация в общем реестре. Зовётся ТОЛЬКО из `setup()` и только при
    включённых флагах: при выключенном флаге инструментов не существует вовсе,
    и модель их не видит.

    `generation` здесь не заполняется намеренно — его выдаёт `REGISTRY.register`,
    и своё значение затёрлось бы при первой же перерегистрации, унося с собой
    смысл `impl_fingerprint`. `svc` не используется и остаётся в сигнатуре
    потому, что подключение делает ведущий по общему для всех фич виду вызова.
    """
    del svc                               # нужен по форме вызова, не по делу
    for spec in SPECS:
        REGISTRY.register(spec)
