"""Извлечение читаемого текста из HTML — чистая библиотека без внешнего мира.

Модуль отвечает на один вопрос: «что из этих байтов можно показать модели и
владельцу, чтобы потом за каждое слово ответить ссылкой на источник». Всё
остальное — чужая работа.

Чего этот модуль НЕ делает и делать не будет:

  * не ходит в сеть и не трогает диск: ни одного `open`, ни одного сокета.
    Ошибку внешнего мира сюда приносят уже в виде байтов;
  * не импортирует ничего из `bcc` — ни `svc`, ни настроек, ни хранилища.
    Это позволяет тестировать извлечение без сервера и без фикстур и не даёт
    HTML со страницы дотянуться до состояния приложения;
  * не решает, можно ли идти по адресу. `canon_url` только приводит адрес к
    единственной форме и ОТКАЗЫВАЕТ на заведомо негодной; политика (SSRF,
    allowlist, robots, запреты источников) живёт у вызывающего;
  * не удаляет подозрительный текст. `defang` помечает и обезвреживает
    управляющие токены, но оставляет содержимое: удаление лжёт владельцу о
    том, что было на странице, и заодно цензурирует законные цитаты
    (фраза «ignore all previous instructions» — обычное дело в статье про
    инъекции);
  * не обещает удалить весь невидимый текст. Скрытие через CSS-класс
    (`.hidden{display:none}`) здесь неустранимо в принципе: `<style>` выброшен
    до того, как правило можно применить, а движка CSS в stdlib нет. Считается
    и возвращается ТОЛЬКО скрытие по атрибуту узла (`hidden`,
    `aria-hidden="true"`, inline `style`) — поле `Extraction.hidden_dropped`
    именно про это, и подпись в выдаче обязана говорить «снято N узлов по
    атрибуту; скрытие через CSS-классы не определяется» (поправка B3).

Границы разбора, каждая проверена прогоном на CPython 3.11:

  * `HTMLParser` РАСКРЫВАЕТ сущности в значениях атрибутов сам, независимо от
    `convert_charrefs` (`<a href="&amp;amp;">` даёт `&amp;`). Поэтому второго
    `html.unescape` здесь нет: он был бы не «доделкой», а дырой — `&amp;#x2f;`
    превратился бы в `/` уже ПОСЛЕ всех проверок пути. Обещание §2 проекта
    «атрибуты раскрываем сами» снято как основанное на неверной посылке;
  * подавленные зоны (`<script>`, скрытые узлы) считаются СТЕКОМ имён тегов, а
    не флагом и не голым счётчиком: лишний `</div>` не должен ни открывать
    подавленную зону, ни закрывать чужую;
  * void-теги (`<br>`, `<img>`, `<meta/>`) на глубину не влияют вообще — ни
    открытием, ни закрытием. Иначе `<meta/>` внутри `<head>` закрыл бы `head`
    и весь служебный блок протёк бы в текст;
  * комментарии, DOCTYPE, PI и `<![CDATA[…]]>` не выдают НИЧЕГО: инструкция,
    спрятанная в комментарии, до модели не доедет;
  * документ кормится порциями и обрывается по трём потолкам (вход, теги,
    знаки), поэтому 400 КБ мусора не превращаются в неограниченную память;
  * весь разбор — в `try/except Exception` с `close()` в `finally`. Битый HTML
    обязан дать частичный текст, а не исключение наружу.

`Extraction.stop_reason` — первый сработавший предел, пустая строка означает
«документ разобран целиком»: `input_limit`, `tag_limit`, `text_limit`,
`parse_error:<ИмяИсключения>`.

Отклонение от §2 проекта, сознательное: `select_passages` возвращает не кортеж
пассажей, а `Selection(passages, max_score)` — поправка E4. Без `max_score`
вызывающий не может отличить «нашли по запросу» от «запрос не совпал ни одним
словом, показываем начало страницы», и промах выдаётся за уверенный ответ.
`Selection` намеренно НЕ итерируется: старый код вида `for p in
select_passages(...)` упадёт сразу и громко, а не тихо покажет мусор.
"""
from __future__ import annotations

import codecs
import encodings.idna
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote as _urlquote, urljoin, urlsplit, urlunsplit

__all__ = [
    "EXTRACTOR_VERSION",
    "MAX_INPUT_CHARS", "MAX_TEXT_CHARS", "MAX_TAGS", "MAX_LINKS",
    "Block", "Link", "Passage", "Extraction", "Selection",
    "sniff_charset", "decode_body", "looks_mojibake",
    "extract", "resolve_link", "select_passages", "find_quote", "block_at",
    "defang", "normalize_ws", "tokenize", "page_sha256", "canon_url",
]

# Версия извлекателя уезжает в паспорт КАЖДОГО наблюдения: смена правил разбора
# меняет смещения цитат, и это обязано быть видно, а не угадываться по дате.
EXTRACTOR_VERSION = "html_text/1"

MAX_INPUT_CHARS = 400_000
MAX_TEXT_CHARS = 120_000
MAX_TAGS = 200_000
MAX_LINKS = 200

_CHUNK = 64 * 1024
_MAX_TITLE_CHARS = 300
_MAX_ANCHOR_CHARS = 200
_MAX_QUOTE_CHARS = 4_000


# --------------------------------------------------------------- структуры

@dataclass(frozen=True)
class Block:
    """Абзац извлечённого текста.

    `offset` — ТОЧНЫЙ индекс в `Extraction.text`, а не «примерно там»: на нём
    держится цитирование, поэтому инвариант
    `text[b.offset:b.offset + len(b.text)] == b.text` обязателен для каждого
    блока. `index` считается с единицы — метка `w1§1` показывается модели, а
    нулевой блок в такой метке читается как «ошибка».
    """

    index: int
    offset: int
    text: str


@dataclass(frozen=True)
class Link:
    """Ссылка со страницы: текст якоря, канонический адрес и его хост.

    Адрес уже прошёл `canon_url`, то есть годен для показа владельцу и для
    сверки с реестром. Решение «пускать ли туда» принимает вызывающий.
    """

    text: str
    url: str
    host: str


@dataclass(frozen=True)
class Passage:
    """Кусок страницы, отобранный под запрос. `block_index` — `Block.index`."""

    block_index: int
    text: str


@dataclass(frozen=True)
class Extraction:
    """Результат разбора страницы.

    `hidden_dropped` — только узлы, скрытые АТРИБУТОМ (см. границы в шапке
    модуля); CSS-классовое скрытие сюда не попадает и попасть не может.
    `truncated` истинно всегда, когда текст неполон, и тогда `stop_reason`
    объясняет причину — «неполно, но молча» здесь не бывает.
    """

    title: str
    text: str
    blocks: tuple[Block, ...]
    links: tuple[Link, ...]
    truncated: bool
    stop_reason: str
    hidden_dropped: int
    chars: int


@dataclass(frozen=True)
class Selection:
    """Отбор пассажей вместе с ЛУЧШИМ совпадением по запросу (поправка E4).

    `max_score == 0.0` означает буквально «ни одно слово запроса не встретилось
    на странице»: позиционный приор в `max_score` не входит именно затем, чтобы
    промах нельзя было принять за попадание. Показывать такие пассажи можно
    только с честной подписью «это НАЧАЛО страницы, а не ответ на запрос».
    """

    passages: tuple[Passage, ...]
    max_score: float


# --------------------------------------------------------------- юникод

def _char_class(*ranges: tuple[int, int]) -> str:
    """Класс символов re, собранный из кодов.

    Невидимый символ, вписанный в исходник буквально, нельзя ни прочитать в
    ревью, ни надёжно скопировать: диапазоны задаются числами именно поэтому.
    """
    return "[" + "".join(chr(a) if a == b else f"{chr(a)}-{chr(b)}"
                         for a, b in ranges) + "]"


# Невидимое, что переносит скрытый текст и переворачивает показ адреса:
# zero-width и bidi-управляющие (§2), плюс поправка B4 — Tag-символы
# U+E0000..U+E007F (ими кодируется целый скрытый абзац), U+FEFF и U+180E.
_INVISIBLE_RE = re.compile(_char_class(
    (0x200B, 0x200F),    # zero-width space/non-joiner/joiner, LRM, RLM
    (0x202A, 0x202E),    # bidi embedding и override
    (0x2066, 0x2069),    # bidi isolate
    (0x180E, 0x180E),    # mongolian vowel separator
    (0xFEFF, 0xFEFF),    # BOM в середине текста — не текст
    (0xE0000, 0xE007F),  # Tag-символы: целый абзац, невидимый глазом
))
# Bidi-управляющие отдельно: их наличие в АДРЕСЕ — повод отказать (B6), а не
# вычистить: вычищенный адрес уже не тот, который написан на странице.
_BIDI_RE = re.compile(_char_class(
    (0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069),
))
_BOM_CHAR = chr(0xFEFF)
_REPLACEMENT_CHAR = chr(0xFFFD)
# C0/C1-управляющие, кроме табуляции и переводов строки: после декодирования
# битых байтов их бывает много, а в тексте страницы им делать нечего.
_CTRL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_WS_RE = re.compile(r"\s+")


def _clean_chunk(s: str) -> str:
    """Чистка куска текста страницы: невидимое, управляющие, NFKC.

    NFKC (поправка B4) применяется ДО сборки блоков, иначе визуально одинаковые
    строки («ﬁle» и «file», «①» и «1») не совпали бы ни с запросом, ни с
    цитатой владельца. Нормализация меняет длину, поэтому она обязана
    происходить здесь — после сборки текста она сломала бы смещения блоков.
    """
    if not s:
        return ""
    s = _INVISIBLE_RE.sub("", s)
    s = _CTRL_RE.sub("", s)
    return unicodedata.normalize("NFKC", s)


def normalize_ws(s: str) -> str:
    """Схлопывает любые пробельные последовательности в один пробел.

    Заодно снимает невидимое: функция вызывается и на строках, пришедших со
    страницы мимо `extract` (текст якоря, заголовок выдачи), а многострочный
    якорь с bidi-символами — готовая подделка границы внешних данных (B2).
    `\\xa0` отдельной строкой не обрабатывается: это `\\s` для `re` в
    юникод-режиме, и он схлопывается тем же правилом.
    """
    if not s:
        return ""
    return _WS_RE.sub(" ", _CTRL_RE.sub("", _INVISIBLE_RE.sub("", s))).strip()


# --------------------------------------------------------------- кодировки

_CHARSET_ALIASES = {
    "utf8": "utf-8",
    "unicode-1-1-utf-8": "utf-8",
    # ASCII объявляют по привычке, а отдают utf-8; ASCII — его подмножество,
    # поэтому подмена ничего не портит, а вот замен на живом тексте избегает.
    "us-ascii": "utf-8",
    "ascii": "utf-8",
    # HTML5 предписывает читать iso-8859-1 как windows-1252: реальные страницы
    # с такой шапкой почти всегда содержат байты 0x80..0x9f (кавычки, тире).
    "iso-8859-1": "windows-1252",
    "iso8859-1": "windows-1252",
    "latin-1": "windows-1252",
    "latin1": "windows-1252",
}
_CT_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([-\w.:+]+)", re.I)
_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _known_encoding(name: str) -> str:
    """Имя кодировки, которое действительно умеет stdlib, иначе пустая строка."""
    cleaned = (name or "").strip().strip("\"'").lower()
    cleaned = _CHARSET_ALIASES.get(cleaned, cleaned)
    if not cleaned:
        return ""
    try:
        codecs.lookup(cleaned)
    except (LookupError, ValueError):
        return ""
    return cleaned


def sniff_charset(head: bytes, content_type: str) -> str:
    """Кодировка тела: Content-Type → BOM → `<meta>` → utf-8.

    Порядок именно такой и он спорный: UTF-16-тело с ошибочным `charset` в
    шапке будет прочитано как мусор. Выбран приоритет заголовка, потому что
    сервер знает про своё тело больше, чем разметка внутри него, а признак
    беды всё равно виден снаружи — `decode_body` возвращает долю замен, и выше
    по стеку она запрещает цитирование.

    Первые 1024 байта под `<meta>` читаются как latin-1: это единственная
    кодировка, которая не может отказать на произвольных байтах, а имя
    кодировки в разметке всегда ASCII.
    """
    declared = _CT_CHARSET_RE.search(content_type or "")
    if declared:
        from_header = _known_encoding(declared.group(1))
        if from_header:
            return from_header

    head = head or b""
    for bom, name in _BOMS:
        if head.startswith(bom):
            return name

    for tag in _META_RE.findall(head[:1024].decode("latin-1", "replace")):
        found = _CT_CHARSET_RE.search(tag)
        if found:
            name = _known_encoding(found.group(1))
            if name:
                return name
    return "utf-8"


def decode_body(raw: bytes, content_type: str) -> tuple[str, str, float]:
    """(текст, использованная кодировка, доля символов-замен).

    Всегда `errors="replace"`: отказ декодирования превратил бы сетевую
    неудачу в исключение, а наружу такое отдаётся данными. Доля замен — тот
    самый сигнал «кодировку определить не удалось»; порог (0.02) и запрет
    цитирования по нему — решение вызывающего, здесь только измерение.
    """
    raw = raw or b""
    encoding = sniff_charset(raw[:1024], content_type)
    try:
        text = raw.decode(encoding, "replace")
    except (LookupError, ValueError):
        # Имя прошло codecs.lookup, но декодер всё равно отказал — обиднее
        # молчать, чем прочитать как utf-8 с заменами.
        encoding = "utf-8"
        text = raw.decode("utf-8", "replace")
    if text.startswith(_BOM_CHAR):
        # BOM остаётся видимым символом для utf-16/32 — он не текст страницы.
        text = text[1:]
    ratio = (text.count(_REPLACEMENT_CHAR) / len(text)) if text else 0.0
    return text, encoding, ratio


# «Ð»/«Ñ» + символ Latin-1/кириллицы = двойное декодирование UTF-8; в живом
# русском тексте такой пары не бывает. Диапазоны — те же, что у MOJIBAKE в
# tests/test_mission_console.py (проверено перебором на совпадение вердиктов),
# чтобы «мусор в UI» и «мусор со страницы» ловились одним правилом, а не двумя
# разъезжающимися. Запись отличается только формой: [\\xd0-\\xd1] вместо
# перечисления тех же двух соседних кодов.
_MOJIBAKE_RE = re.compile(_char_class((0xD0, 0xD1))
                          + _char_class((0xAD, 0xFF), (0x2013, 0x2122),
                                        (0x400, 0x45F)))


def looks_mojibake(text: str) -> bool:
    """Похоже ли, что текст декодирован дважды (и цитировать из него нельзя)."""
    return bool(text) and _MOJIBAKE_RE.search(text) is not None


# --------------------------------------------------------------- разбор HTML

_HIDDEN_STYLE_RE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)(?![-\w])", re.I)


class _Halt(Exception):
    """Внутренний сигнал «дальше не кормим»: обрывает feed на месте.

    Наружу не выходит никогда — ловится в `extract`. Нужен именно как
    исключение: потолок может быть достигнут в середине порции, а дочитывать
    её после этого — ровно та трата памяти, от которой потолок и защищает.
    """


class _Harvester(HTMLParser):
    """Сборщик текста, ссылок и заголовка. Состояние — стеки, а не флаги.

    Почему стеки: подавленная зона обязана переживать битую разметку.
    Флаг залипает на первом лишнем `</script>`, голый счётчик уводится в минус
    и «открывает» зону, которую никто не открывал. Стек имён закрывается
    только своим тегом, а чужой закрывающий тег молча игнорируется.
    """

    DROP_TAGS = frozenset({
        "script", "style", "noscript", "template", "svg", "iframe", "object",
        "embed", "form", "head", "title", "meta", "link", "button", "select",
    })
    BLOCK_TAGS = frozenset({
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5",
        "h6", "section", "article", "pre", "blockquote", "figcaption",
    })
    VOID_TAGS = frozenset({
        "br", "img", "hr", "input", "meta", "link", "source", "area", "base",
        "col", "embed", "param", "track", "wbr",
    })

    def __init__(self, *, base_url: str, char_budget: int,
                 max_tags: int = MAX_TAGS, max_links: int = MAX_LINKS) -> None:
        # convert_charrefs=True раскрывает сущности в тексте силами stdlib;
        # значения атрибутов раскрываются парсером в любом случае (проверено),
        # поэтому второго раскрытия здесь нет — см. шапку модуля.
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.char_budget = max(0, char_budget)
        self.max_tags = max(0, max_tags)
        self.max_links = max(0, max_links)

        self.blocks: list[str] = []
        self.links: list[Link] = []
        self.hidden_dropped = 0
        self.tags_seen = 0
        self.chars_used = 0
        self.stop_reason = ""
        self.stopped = False

        self._drop_stack: list[str] = []
        self._hidden_stack: list[str] = []
        self._buf: list[str] = []
        self._buf_len = 0
        self._title: list[str] = []
        self._in_title = False
        self._anchor_url = ""
        self._anchor_parts: list[str] = []
        self._in_anchor = False
        self._seen_urls: set[str] = set()

    # ---- служебное

    def _stop(self, reason: str) -> None:
        if not self.stop_reason:
            self.stop_reason = reason
        self.stopped = True
        self._flush()
        raise _Halt

    def _flush(self) -> None:
        """Закрывает текущий блок. Бюджет знаков считается ЗДЕСЬ и один раз."""
        if not self._buf:
            return
        text = normalize_ws("".join(self._buf))
        self._buf.clear()
        self._buf_len = 0
        if not text:
            return
        # Разделитель между блоками — один "\n"; он тоже занимает бюджет,
        # иначе итоговый текст окажется длиннее обещанного.
        sep = 1 if self.blocks else 0
        room = self.char_budget - self.chars_used - sep
        if room <= 0:
            return
        if len(text) > room:
            text = text[:room].rstrip()
            if not text:
                return
        self.blocks.append(text)
        self.chars_used += len(text) + sep

    def _close_anchor(self) -> None:
        if not self._in_anchor:
            return
        text = normalize_ws("".join(self._anchor_parts))[:_MAX_ANCHOR_CHARS]
        url = self._anchor_url
        self._in_anchor = False
        self._anchor_url = ""
        self._anchor_parts = []
        # Якорь без текста показать нечем: «l3 |  | host» не помогает ни
        # владельцу, ни модели, а место в блоке ссылок занимает.
        if not text or not url or url in self._seen_urls:
            return
        if len(self.links) >= self.max_links:
            return
        host = urlsplit(url).hostname or ""
        self._seen_urls.add(url)
        self.links.append(Link(text=text, url=url, host=host))

    @staticmethod
    def _attr_map(attrs) -> dict[str, str]:
        return {(name or "").lower(): (value if value is not None else "")
                for name, value in attrs}

    @staticmethod
    def _is_hidden(attrs: dict[str, str]) -> bool:
        """Скрытие, видимое по САМОМУ узлу (поправка B3).

        `display:none` ищется регуляркой, устойчивой к регистру и пробелам:
        подстрочное сравнение не переживает ни `DISPLAY : NONE`, ни
        `display:none!important`. Классовое скрытие тут не ловится и не может
        ловиться — об этом сказано вслух в шапке модуля и обязано быть сказано
        в выдаче.
        """
        if "hidden" in attrs and attrs["hidden"].strip().lower() != "false":
            return True
        if attrs.get("aria-hidden", "").strip().lower() == "true":
            return True
        style = attrs.get("style", "")
        return bool(style) and _HIDDEN_STYLE_RE.search(style) is not None

    # ---- обработчики HTMLParser

    def handle_starttag(self, tag, attrs):
        if self.stopped:
            return
        tag = tag.lower()
        self.tags_seen += 1
        if self.tags_seen > self.max_tags:
            self._stop("tag_limit")

        if tag in self.BLOCK_TAGS:
            self._flush()

        if tag in self.VOID_TAGS:
            # Void-тег не открывает зону и не ждёт закрытия: иначе `<meta/>`
            # внутри `<head>` схлопнул бы подавление всего служебного блока.
            return

        if tag == "title" and not self._title and "svg" not in self._drop_stack:
            # `<title>` лежит в DROP_TAGS (служебный блок в текст не идёт), но
            # сам заголовок владельцу нужен — забираем его отдельным каналом.
            self._in_title = True

        if tag in self.DROP_TAGS:
            self._drop_stack.append(tag)
            return

        attr_map = self._attr_map(attrs) if attrs else {}
        if self._is_hidden(attr_map):
            self._hidden_stack.append(tag)
            self.hidden_dropped += 1
            return

        if tag == "a":
            # Незакрытый предыдущий `<a>` не должен съесть текст следующего.
            self._close_anchor()
            if not self._drop_stack and not self._hidden_stack:
                url = resolve_link(self.base_url, attr_map.get("href", ""))
                if url:
                    self._in_anchor = True
                    self._anchor_url = url
                    self._anchor_parts = []

    def handle_startendtag(self, tag, attrs):
        # Самозакрытый тег НИЧЕГО не заключает внутри себя, и это правило
        # ломается в обе стороны. Базовая реализация зовёт starttag + endtag —
        # для `<div/>` закрытие свернуло бы зону, которую никто не открывал.
        # Но одного starttag тоже мало: он кладёт тег в стек подавления, а
        # снять оттуда некому, и `<script/>` съедал ВСЮ оставшуюся страницу —
        # текст приходил пустым, то есть страница молча объявлялась пустой.
        # Поэтому: дать тегу отработать, а потом свернуть ровно то, что он
        # открыл, и ничего сверх этого.
        drop_before = len(self._drop_stack)
        hidden_before = len(self._hidden_stack)
        title_before = self._in_title
        anchor_before = self._in_anchor

        self.handle_starttag(tag, attrs)

        del self._drop_stack[drop_before:]
        del self._hidden_stack[hidden_before:]
        if self._in_title and not title_before:
            self._in_title = False
        if self._in_anchor and not anchor_before:
            # `<a/>` без содержимого ссылкой не является: закрываем сразу,
            # иначе следующий текст страницы стал бы её якорем.
            self._close_anchor()

    def handle_endtag(self, tag):
        if self.stopped:
            return
        tag = tag.lower()
        if tag in self.VOID_TAGS:
            return  # `</br>` не значит ничего

        if tag == "title":
            self._in_title = False
        if tag == "a":
            self._close_anchor()
        if tag in self.BLOCK_TAGS:
            self._flush()

        for stack in (self._drop_stack, self._hidden_stack):
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag:
                    del stack[i]
                    break

    def handle_data(self, data):
        if self.stopped or not data:
            return
        if self._in_title:
            if len("".join(self._title)) < _MAX_TITLE_CHARS:
                self._title.append(data)
            return
        if self._drop_stack or self._hidden_stack:
            return
        clean = _clean_chunk(data)
        if not clean:
            return
        self._buf.append(clean)
        self._buf_len += len(clean)
        if self._in_anchor:
            self._anchor_parts.append(clean)
        if self.chars_used + self._buf_len >= self.char_budget:
            self._stop("text_limit")

    # Комментарий, DOCTYPE, PI и CDATA не дают ни знака текста: спрятанная там
    # инструкция не должна доехать ни до модели, ни до владельца.
    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def unknown_decl(self, data):
        return

    def handle_pi(self, data):
        return

    def finish(self) -> None:
        """Дособрать последний блок, если документ кончился без закрывающего тега."""
        if not self.stopped:
            self._close_anchor()
            self._flush()

    def title_text(self) -> str:
        return normalize_ws("".join(self._title))[:_MAX_TITLE_CHARS]


def extract(html: str, *, base_url: str, max_chars: int = MAX_TEXT_CHARS) -> Extraction:
    """Разбор HTML в блоки текста, ссылки и заголовок.

    Битый вход обязан дать частичный результат: любое исключение разбора
    превращается в `stop_reason="parse_error:…"` и уже собранный текст, потому
    что «страница не открылась» и «страница открылась наполовину» — разные
    ответы владельцу, и второй полезнее.
    """
    if not isinstance(html, str):
        raise TypeError("extract ожидает str; байты декодируются decode_body")

    budget = max(0, min(int(max_chars), MAX_TEXT_CHARS))
    reason = ""
    if len(html) > MAX_INPUT_CHARS:
        html = html[:MAX_INPUT_CHARS]
        reason = "input_limit"

    harvester = _Harvester(base_url=base_url, char_budget=budget)
    try:
        try:
            for start in range(0, len(html), _CHUNK):
                harvester.feed(html[start:start + _CHUNK])
                if harvester.stopped:
                    break
        finally:
            # close() дочитывает буфер парсера; обработчики после stop уже
            # ничего не берут, поэтому это дёшево и безопасно.
            harvester.close()
    except _Halt:
        pass
    except Exception as exc:  # noqa: BLE001 — наружу отдаём данными, а не исключением
        if not harvester.stop_reason:
            harvester.stop_reason = f"parse_error:{type(exc).__name__}"
    harvester.finish()

    # Причина — ПЕРВАЯ сработавшая: обрезка входа случилась до разбора, и
    # именно она объясняет, почему конца документа никто не видел.
    if not reason:
        reason = harvester.stop_reason

    parts: list[str] = []
    blocks: list[Block] = []
    position = 0
    for i, body in enumerate(harvester.blocks):
        if i:
            parts.append("\n")
            position += 1
        blocks.append(Block(index=i + 1, offset=position, text=body))
        parts.append(body)
        position += len(body)
    text = "".join(parts)

    return Extraction(
        title=harvester.title_text(),
        text=text,
        blocks=tuple(blocks),
        links=tuple(harvester.links),
        truncated=bool(reason),
        stop_reason=reason,
        hidden_dropped=harvester.hidden_dropped,
        chars=len(text),
    )


# --------------------------------------------------------------- адреса

_DEFAULT_PORTS = {"http": 80, "https": 443}
_ALLOWED_SCHEMES = frozenset({"http", "https"})
# Пробел внутри адреса — либо склейка двух адресов, либо попытка развалить
# строку в логе; чинить его догадкой хуже, чем отказать.
_URL_SPACE_RE = re.compile(r"\s")
# Цифровые формы («127.0.0.1», «2130706433») намеренно проходят: их разбирает
# psec._literal_ip, и второй такой проверки здесь заводить нельзя.
_HOST_LABEL_RE = re.compile(r"[a-z0-9_-]+")


def canon_url(url: str) -> str:
    """Единственная форма адреса. `ValueError` — это отказ, а не авария.

    Зачем: `_PinnedBackend.connect_tcp` в `plugin_security` берёт
    `self._pins.get(host, host)`, и промах ключа означает ПОВТОРНЫЙ резолв, то
    есть fail-open. `evil.com.` и IDN промахиваются мимо словаря пинов. Здесь
    это закрывается со стороны вызывающего, без правки чужого файла: до сети
    уходит только форма без завершающей точки и с хостом в punycode, а если
    хост после канонизации всё ещё не ASCII — адрес не уходит вообще (C1).

    Bidi-символы в адресе — отказ (B6): ветка `ask` держится на том, что
    владелец видит настоящий адрес, а `\\u202e` переворачивает показ.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("пустой адрес")
    if _BIDI_RE.search(raw):
        raise ValueError("bidi-символы в адресе: показ владельцу нельзя считать честным")
    if _INVISIBLE_RE.search(raw):
        raise ValueError("невидимые символы в адресе")
    if _CTRL_RE.search(raw) or _URL_SPACE_RE.search(raw):
        raise ValueError("управляющие символы или пробел в адресе")

    split = urlsplit(raw)
    scheme = split.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"схема {scheme or '(пусто)'} не поддерживается")
    if "@" in split.netloc:
        # userinfo прячет настоящий хост за «https://bank.example@evil.tld».
        raise ValueError("userinfo в адресе запрещён")

    host = (split.hostname or "").strip()
    if host.endswith("."):
        # Снимается РОВНО одна завершающая точка — корневая метка. Срезать все
        # значило бы молча превращать негодный «example.com..» в годный адрес,
        # а тихое исправление чужого мусора — это и есть fail-open.
        host = host[:-1]
    if not host:
        raise ValueError("в адресе нет хоста")

    try:
        port = split.port
    except ValueError as exc:
        raise ValueError(f"негодный порт: {exc}") from exc

    if split.netloc.startswith("["):
        # IPv6-литерал: IDNA к нему неприменима, скобки возвращаем как были.
        netloc_host = f"[{host}]"
    else:
        labels = []
        for label in host.split("."):
            if not label:
                raise ValueError("пустая метка в имени хоста")
            if label.isascii():
                labels.append(label.lower())
                continue
            try:
                labels.append(encodings.idna.ToASCII(label).decode("ascii"))
            except (UnicodeError, ValueError) as exc:
                raise ValueError(f"имя хоста не приводится к punycode: {exc}") from exc
        for label in labels:
            # После IDNA законная метка состоит только из букв, цифр, дефиса и
            # (в реальных поддоменах) подчёркивания. Всё прочее — «%00», «\»,
            # «@» — это попытка показать владельцу один хост, а сходить на
            # другой; резолвер такое имя всё равно не примет, но отказать здесь
            # дешевле, чем узнать об этом из сетевой ошибки.
            if not _HOST_LABEL_RE.fullmatch(label):
                raise ValueError(f"недопустимые символы в метке хоста: {label!r}")
        netloc_host = ".".join(labels)

    if not netloc_host.isascii():
        raise ValueError("хост не ASCII после канонизации")

    netloc = netloc_host
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{port}"

    # `%` оставлен «безопасным», чтобы уже закодированный путь не закодировался
    # дважды (%20 → %2520 — это уже другой адрес).
    path = _urlquote(split.path, safe="/%:@!$&'()*+,;=~-._") or "/"
    query = _urlquote(split.query, safe="/%:@!$&'()*+,;=~-._?") if split.query else ""
    # Фрагмент снимается: на провод он не уходит никогда, а в предпросмотре
    # одобрения создаёт иллюзию, что владелец видел весь адрес.
    return urlunsplit((scheme, netloc, path, query, ""))


def resolve_link(base_url: str, href: str) -> str | None:
    """Абсолютный http(s)-адрес ссылки или None.

    None — это «сюда ходить нельзя или незачем»: `javascript:`, `data:`,
    `mailto:`, `blob:`, `file:` и всё прочее отсекается схемой, а негодная
    форма — тем же `canon_url`, что стоит на пути к сети. Одно правило в двух
    местах разъезжается, поэтому правило одно.
    """
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    try:
        joined = urljoin(base_url or "", href)
        return canon_url(joined)
    except (ValueError, UnicodeError):
        return None


# --------------------------------------------------------------- поиск в тексте

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_DIGIT_RE = re.compile(r"\d")
# Стоп-слова: короткий список служебных слов, которые есть в любом тексте и
# потому не различают блоки. Не лингвистика, а защита от «совпало по слову
# "the"» — расширять его в поисках качества смысла нет.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "how", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "what", "when", "where", "which", "who", "why",
    "with", "you", "your",
    "а", "без", "бы", "был", "была", "было", "были", "в", "во", "все", "всё",
    "да", "для", "до", "его", "ее", "её", "если", "есть", "же", "за", "и",
    "из", "или", "как", "к", "ко", "когда", "кто", "ли", "мы", "на", "не",
    "него", "нет", "но", "о", "об", "он", "она", "они", "от", "по", "при",
    "с", "со", "так", "также", "то", "тот", "ты", "у", "уже", "что", "чтобы",
    "эта", "эти", "это", "этот", "я",
})
_STEM_LEN = 5


def tokenize(s: str) -> list[str]:
    """Слова в нижнем регистре. Без стемминга и без отсечения стоп-слов.

    Функция используется и для скоринга, и вызывающими для показа «есть
    разделы: …», поэтому она обязана оставаться предсказуемой: любую фильтрацию
    делает тот, кому она нужна.
    """
    return _TOKEN_RE.findall((s or "").lower())


def _stems(tokens) -> set[str]:
    """Грубая нормализация окончаний: первые 5 знаков длинного слова.

    Русский запрос «цитаты» и текст «цитата» иначе не пересекаются вовсе, и
    попадание выглядит как промах. Это не морфология, а компромисс: он даёт
    ложные совпадения на общем корне и назван здесь прямо, а не выдан за
    понимание языка.
    """
    out = set()
    for token in tokens:
        if token in _STOPWORDS or len(token) < 2:
            continue
        out.add(token[:_STEM_LEN] if len(token) > _STEM_LEN else token)
    return out


def select_passages(ex: Extraction, query: str, *,
                    budget_chars: int, max_passages: int) -> Selection:
    """Отбор блоков под запрос. Возврат — В ПОРЯДКЕ ДОКУМЕНТА.

    Порядок документа, а не порядок скоров: перемешанные пассажи заставляют
    маленькую модель выдумывать причинно-следственные связи между кусками,
    которые в тексте стоят далеко друг от друга. Первый блок включается всегда
    как лид — он почти всегда несёт «о чём эта страница».

    `max_score` считается ТОЛЬКО по совпадению с запросом, без позиционного
    приора (E4): иначе ноль совпадений давал бы ненулевой скор, и начало
    страницы уехало бы к модели с видом уверенного ответа.
    """
    budget = max(0, int(budget_chars))
    limit = max(1, int(max_passages))
    if not ex.blocks or budget <= 0:
        return Selection(passages=(), max_score=0.0)

    q_stems = _stems(tokenize(query))
    q_has_digits = bool(_DIGIT_RE.search(query or ""))

    scored: list[tuple[float, float, Block]] = []
    for block in ex.blocks:
        tokens = tokenize(block.text)
        match = 0.0
        if q_stems:
            block_stems = _stems(tokens)
            hits = q_stems & block_stems
            match = float(len(hits))
            if match and q_has_digits and any(_DIGIT_RE.search(h) for h in hits):
                # Цифры и даты в запросе — почти всегда самое содержательное в
                # нём («версия 3.11», «2026»), и блок с ними ценнее пересказа.
                match += 0.75
        # Приор строго меньше единицы: блок с одним настоящим совпадением
        # обязан побеждать блок без совпадений, где бы тот ни стоял.
        prior = 0.5 / (1.0 + block.index)
        scored.append((match, match + prior, block))

    max_score = max((m for m, _rank, _b in scored), default=0.0)

    lead = ex.blocks[0]
    chosen: dict[int, Block] = {}
    used = 0
    text = lead.text if len(lead.text) <= budget else _cut(lead.text, budget)
    if text:
        chosen[lead.index] = Block(index=lead.index, offset=lead.offset, text=text)
        used = len(text)

    for _match, rank, block in sorted(scored, key=lambda row: (-row[1], row[2].index)):
        if len(chosen) >= limit:
            break
        if block.index in chosen:
            continue
        room = budget - used
        if room <= 0:
            break
        body = block.text if len(block.text) <= room else _cut(block.text, room)
        if not body:
            continue
        chosen[block.index] = Block(index=block.index, offset=block.offset, text=body)
        used += len(body)

    passages = tuple(Passage(block_index=idx, text=chosen[idx].text)
                     for idx in sorted(chosen))
    return Selection(passages=passages, max_score=max_score)


def _cut(text: str, room: int) -> str:
    """Обрезка по границе слова. Многоточие НЕ добавляется намеренно.

    Пассаж обязан остаться точной подстрокой извлечённого текста: добавленное
    «…» модель скопирует в цитату, и `find_quote` честно её не найдёт.
    """
    if room <= 0:
        return ""
    piece = text[:room]
    space = piece.rfind(" ")
    if space > room // 2:
        piece = piece[:space]
    return piece.rstrip()


def find_quote(ex: Extraction, quote: str) -> tuple[int, int] | None:
    """(offset, length) цитаты В `ex.text` или None.

    Сперва точное вхождение, затем — совпадение с точностью до пробелов:
    строка, скопированная моделью из пассажа, часто отличается только
    переносом. Смещение при этом возвращается в ИСХОДНОМ тексте, а не в
    нормализованном, потому что наблюдение цитаты хранит смещение именно в том
    тексте, который потом покажут владельцу.
    """
    if not quote or not ex.text:
        return None
    if len(quote) > _MAX_QUOTE_CHARS:
        return None
    position = ex.text.find(quote)
    if position >= 0:
        return position, len(quote)

    words = normalize_ws(quote).split(" ")
    words = [w for w in words if w]
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    found = re.search(pattern, ex.text)
    if not found:
        return None
    return found.start(), found.end() - found.start()


def block_at(ex: Extraction, offset: int) -> Block | None:
    """Блок, внутри которого лежит смещение; None — если это разделитель или мимо."""
    if offset < 0:
        return None
    low, high = 0, len(ex.blocks) - 1
    while low <= high:
        mid = (low + high) // 2
        block = ex.blocks[mid]
        if offset < block.offset:
            high = mid - 1
        elif offset >= block.offset + len(block.text):
            low = mid + 1
        else:
            return block
    return None


def page_sha256(text: str) -> str:
    """sha256 извлечённого текста — им подписывается наблюдение страницы."""
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------- обезвреживание

# Формы, а не намерения: регулярки ловят узнаваемые команды ассистенту и
# управляющие токены чат-шаблонов. Убедительную инъекцию обычной прозой без
# ключевых слов они не остановят — это названо вслух и здесь, и в проекте.
_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|above)", re.I),
    re.compile(r"system\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\b", re.I),
    # `<\s?` — чтобы уже экранированная форма «< |im_start|>» продолжала
    # опознаваться: иначе повторный вызов defang давал бы меньший счётчик на
    # том же тексте, то есть цифра в подвале зависела бы от числа вызовов.
    re.compile(r"<\s?/?\|[A-Za-z0-9_]{1,32}\|>"),
    re.compile(r"\[/?INST\]"),
    re.compile(r"<\s?/?tool_call>", re.I),
    re.compile(r"игнорир\w*\s+(?:все\s+)?предыдущ", re.I),
    re.compile(r"ты\s+теперь\b", re.I),
    re.compile(r"выполни\s+команд", re.I),
    # Длинный блоб без пробелов — это не текст для чтения, а полезная нагрузка.
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
)
# Пробел после "<" рвёт токен шаблона, не трогая ни одного знака смысла.
_TEMPLATE_TOKEN_RE = re.compile(r"<(?=/?\||/?tool_call\b)", re.I)
_DEFANG_MARK = "⚠ "


def defang(text: str) -> tuple[str, int]:
    """(обезвреженный текст, число помеченных строк). НЕ удаляет ни знака.

    Две разные вещи в одной функции, и обе обязательны:

    1. строка, похожая на команду ассистенту, получает префикс «⚠». Удалять её
       нельзя: владелец имеет право видеть, что было на странице, а фраза
       «ignore all previous instructions» бывает законной цитатой;
    2. управляющие токены чат-шаблона (`<|im_start|>`, `<|eot_id|>`,
       `<tool_call>`) экранируются вставкой пробела после «<». Это не
       косметика: нетронутый токен со страницы ломает сам шаблон
       llama.cpp/Ollama-сервера — беда, которой у облачного провайдера не
       бывает, и потому в чужих решениях её не лечат.
    """
    if not text:
        return "", 0
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    marked = 0
    out = []
    for line in lines:
        if line.strip() and any(p.search(line) for p in _INJECTION_PATTERNS):
            # Счётчик считает СВОЙСТВО ТЕКСТА («сколько строк похожи на
            # команду»), а не число сделанных правок, а метка не дублируется.
            # Поэтому повторный defang над уже обезвреженным текстом даёт тот
            # же текст и то же число: пассаж можно обезвредить по отдельности и
            # ещё раз в составе собранного ответа, не получив «⚠ ⚠ ⚠».
            marked += 1
            out.append(line if line.startswith(_DEFANG_MARK)
                       else _DEFANG_MARK + line)
        else:
            out.append(line)
    return _TEMPLATE_TOKEN_RE.sub("< ", "\n".join(out)), marked
