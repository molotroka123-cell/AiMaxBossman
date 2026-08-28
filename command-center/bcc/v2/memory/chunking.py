"""Чанкинг chunk-v4 — задел, В БОЮ ПОКА НЕ ИСПОЛЬЗУЕТСЯ.

ЧИТАЙ ПЕРЕД ПРАВКОЙ. Модуль писался как общий для всех backend'ов памяти
(qdrant.md §11, этап 0.1), но переезд не был доведён до конца: ни один живой
backend сюда не ходит. Владелец лексики и чанков — `local_index.py`:

  * `LocalMemoryBackend` (JSON BM25) определяет `stem`/`tokenize`/`Chunk`/
    `split_sections`/`chunk_markdown` у себя;
  * `SQLiteMemoryBackend` импортирует их же из `local_index`;
  * `reranker.LexicalReranker` тоже берёт `tokenize` из `local_index`.

До правки V2.3 здесь лежала ПОБАЙТНАЯ копия `stem`/`tokenize` и таблиц
суффиксов, и `reranker.py` тянул токенизатор именно отсюда. То есть запрос
переранжировался одним токенизатором, а индекс строился другим — совпадали они
только потому, что были копией. Одна правка в любом из двух файлов разошлась бы
молча и испортила бы ранжирование. Теперь лексика здесь — ре-экспорт, и
реализация ровно одна.

Переход живых backend'ов на chunk-v4 — отдельное решение, а не побочный эффект
уборки: формула `chunk_hash` тут другая, а на `chunk_hash` держатся `expand()`
и цитирование в `context_pack`, поэтому смена схемы обязана идти вместе с
перестроением индексов.

Что здесь реализовано сверх переезда:

* **атом не режется** (ragflow.md §5.1 п.1–2): единица разбиения — блок
  (абзац / code-fence / markdown-таблица). Блок больше лимита становится
  своим чанком, а не пилится пополам;
* **контекст вокруг таблицы** (§5.1 п.5): таблица получает ближайшее
  предшествующее прозаическое предложение — иначе таблица из одних чисел не
  находится ничем;
* **пустые секции отбрасываются** (memsearch.md §5 п.2): заголовок без тела
  не становится чанком;
* **очистка перед индексацией** (§5 п.3): HTML-комментарии вырезаются,
  пустые строки схлопываются. Индексируется очищенное, ХРАНИТСЯ оригинал;
* **схема и имя модели входят в `chunk_hash`** (§5 п.5): сменили модель —
  индекс честно инвалидируется;
* **реестр парсеров** (§5.1 п.6) вместо `if suffix == ...`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, NamedTuple

# Владелец лексики — `local_index.py`, там же, куда за ней ходят оба живых
# backend'а. Здесь только имена, чтобы прежние импорты не сломались.
from .local_index import _WORD, _EN_SUFFIXES, _RU_SUFFIXES, stem, tokenize  # noqa: F401

# Версия схемы чанкинга. Меняется, когда меняется РАЗБИЕНИЕ или формула
# chunk_hash — тогда индекс обязан быть перестроен.
CHUNK_SCHEMA_VERSION = "chunk-v4"

# «Модель» лексического пути. Для BM25 это не нейросеть, но имя всё равно
# входит в chunk_hash: появится dense-энкодер — смена имени сама инвалидирует
# индекс, и не будет ситуации «сменили модель, а индекс остался».
DEFAULT_INDEX_MODEL = "bm25-stdlib"

MAX_CHUNK_CHARS = 1400
MAX_SECTION_CHARS = 8000
MAX_CONTEXT_CHARS = 240          # сколько «предыдущего» тащим как контекст

DEFAULT_EXCLUDED_DIRS = {".obsidian", ".trash", ".git", "node_modules", ".venv",
                         "__pycache__"}


# ------------------------------------------------------------------ лексика
#
# Реализации здесь НЕТ и быть не должно: `stem`/`tokenize` живут у владельца
# лексики (`local_index.py`, импорт в шапке модуля). Копии тут больше нет —
# разошедшиеся токенизаторы ломают ранжирование молча.


# ------------------------------------------------------------------ очистка

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def clean_for_index(text: str) -> str:
    """Текст, который идёт В ИНДЕКС. Оригинал не трогаем и храним отдельно.

    HTML-комментарий — это то, что автор явно спрятал от читателя; попадая в
    индекс, он даёт ложные попадания и утекает в контекст модели.
    """
    text = _HTML_COMMENT.sub(" ", text)
    text = _TRAILING_WS.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


_MD_NOISE = re.compile(r"[#>*_`~\-=|:\s\[\]()]+")


def has_meaningful_content(body: str) -> bool:
    """Есть ли в теле секции хоть что-то, кроме разметки и пробелов."""
    return bool(_MD_NOISE.sub("", clean_for_index(body)).strip())


# ------------------------------------------------------------------ структура

@dataclass(slots=True)
class Chunk:
    chunk_hash: str
    source: str          # путь относительно корня vault — им и цитируем
    heading: str         # «Архитектура > Выбор БД»
    content: str         # ОРИГИНАЛ блока (с комментариями, как в файле)
    section_id: str
    ordinal: int = 0
    kind: str = "text"   # text | code | table | mixed
    context: str = ""    # прозаический хвост предыдущего блока (см. §5.1 п.5)

    @property
    def index_text(self) -> str:
        """Что реально попадает в постинги: очищенное + контекст."""
        head = clean_for_index(self.context)
        body = clean_for_index(self.content)
        return f"{head}\n\n{body}".strip() if head else body

    def to_json(self) -> dict:
        return {"source": self.source, "heading": self.heading,
                "content": self.content, "section_id": self.section_id,
                "ordinal": self.ordinal, "kind": self.kind,
                "context": self.context}


class Section(NamedTuple):
    heading: str     # полный путь заголовков
    title: str       # только последний заголовок
    body: str        # тело БЕЗ строки заголовка
    content: str     # title + body — то, что отдаёт expand()


class Block(NamedTuple):
    """Атом разбиения. Резать его запрещено."""
    text: str
    kind: str        # text | code | table


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _strip_frontmatter(text: str) -> tuple[str, str]:
    """Возвращает (тело, title из frontmatter). YAML не парсим — только title."""
    m = _FRONTMATTER.match(text)
    if not m:
        return text, ""
    head = m.group(0)
    title = ""
    tm = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", head, re.MULTILINE)
    if tm:
        title = tm.group(1).strip()
    return text[m.end():], title


def split_sections_detailed(text: str) -> list[Section]:
    """Markdown → секции. Преамбула до первого заголовка — секция с пустым путём."""
    body, _title = _strip_frontmatter(text)
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    cur_head = ""
    cur_title = ""
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        raw = "\n".join(buf).strip()
        if not raw and not cur_title:
            return
        content = (f"{cur_title}\n{raw}".strip() if cur_title else raw)
        if content:
            out.append(Section(cur_head, cur_title, raw, content))

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            buf = []
            level, title = len(m.group(1)), m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_head = " > ".join(t for _, t in stack)
            cur_title = title
        else:
            buf.append(line)
    flush()
    return out


def split_sections(text: str) -> list[tuple[str, str]]:
    """Обратно совместимый вид: [(путь заголовков, тело секции)]."""
    return [(s.heading, s.content) for s in split_sections_detailed(text)]


# ------------------------------------------------------------------ блоки

def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if "-" not in s or "|" not in s:
        return False
    return all(ch in "|-: " for ch in s)


def _is_table_row(line: str) -> bool:
    return "|" in line and line.strip() != ""


def split_blocks(content: str) -> list[Block]:
    """Текст секции → атомы: code-fence, markdown-таблица, абзац.

    Ни один из них не может быть разрезан — это и есть «граница, которую
    нельзя пересекать» (ragflow.md §5.1 п.2).
    """
    lines = content.splitlines()
    blocks: list[Block] = []
    buf: list[str] = []

    def flush_text() -> None:
        raw = "\n".join(buf).strip()
        if raw:
            blocks.append(Block(raw, "text"))
        buf.clear()

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            flush_text()
            marker = fence.group(1)
            body = [line]
            i += 1
            while i < n:
                body.append(lines[i])
                if lines[i].strip().startswith(marker):
                    i += 1
                    break
                i += 1
            blocks.append(Block("\n".join(body).rstrip(), "code"))
            continue
        if (_is_table_row(line) and i + 1 < n and _is_table_separator(lines[i + 1])):
            flush_text()
            body = [line, lines[i + 1]]
            i += 2
            while i < n and _is_table_row(lines[i]):
                body.append(lines[i])
                i += 1
            blocks.append(Block("\n".join(body).rstrip(), "table"))
            continue
        if not line.strip():
            flush_text()
            i += 1
            continue
        buf.append(line)
        i += 1
    flush_text()
    return blocks


def last_sentence(text: str, *, limit: int = MAX_CONTEXT_CHARS) -> str:
    """Последнее «прозаическое» предложение — контекст для таблицы/продолжения."""
    flat = clean_for_index(text)
    if not flat:
        return ""
    parts = [p.strip() for p in _SENTENCE_END.split(flat.replace("\n", " ")) if p.strip()]
    if not parts:
        return ""
    return parts[-1][-limit:].strip()


def pack_blocks(blocks: list[Block]) -> list[tuple[str, str, str]]:
    """Блоки → куски (текст, kind, context), ни один атом не разрезан.

    Жадная упаковка до `MAX_CHUNK_CHARS`. Блок, который сам больше лимита,
    становится отдельным чанком целиком — это и есть «никогда не резать атом».
    """
    out: list[tuple[str, str, str]] = []
    cur: list[Block] = []
    cur_len = 0
    last_prose = ""       # ближайшее предшествующее прозаическое предложение

    def kind_of(group: list[Block]) -> str:
        kinds = {b.kind for b in group}
        if len(kinds) == 1:
            return kinds.pop()
        if "table" in kinds:
            return "table"
        return "mixed"

    def flush(context: str) -> str:
        nonlocal cur, cur_len
        if not cur:
            return context
        text = "\n\n".join(b.text for b in cur)
        out.append((text, kind_of(cur), context))
        tail = last_sentence(text) if cur[-1].kind == "text" else context
        cur = []
        cur_len = 0
        return tail

    pending_context = ""
    for blk in blocks:
        add = len(blk.text) + (2 if cur else 0)
        if cur and cur_len + add > MAX_CHUNK_CHARS:
            pending_context = flush(pending_context)
        if not cur and blk.kind == "table" and last_prose:
            # §5.1 п.5: таблица из одних чисел не находится ничем — тащим прозу
            pending_context = last_prose
        cur.append(blk)
        cur_len += add
        if blk.kind == "text":
            last_prose = last_sentence(blk.text)
    flush(pending_context)
    return out


# ------------------------------------------------------------------ хэши

def chunk_id(source: str, heading: str, ordinal: int, content: str, *,
             model: str = DEFAULT_INDEX_MODEL,
             schema: str = CHUNK_SCHEMA_VERSION) -> str:
    """Адрес чанка. Содержит СОДЕРЖИМОЕ (а не порядковый номер секции),
    поэтому правка одного абзаца меняет хэш ровно одного чанка, а вставка
    новой секции не сдвигает хэши соседних."""
    key = "\x00".join([schema, model, source, heading, str(ordinal), content])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def section_id(source: str, heading: str, occurrence: int, *,
               schema: str = CHUNK_SCHEMA_VERSION) -> str:
    key = "\x00".join([schema, source, heading, str(occurrence)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ документ

def chunk_markdown(text: str, source: str, *,
                   model_name: str = DEFAULT_INDEX_MODEL,
                   ) -> tuple[list[Chunk], dict[str, str]]:
    """Разбить один markdown-файл. Возвращает (чанки, секции для expand)."""
    chunks: list[Chunk] = []
    sections: dict[str, str] = {}
    seen_heads: dict[str, int] = {}

    for sec in split_sections_detailed(text):
        if not has_meaningful_content(sec.body):
            continue                      # заголовок без тела — не чанк
        occ = seen_heads.get(sec.heading, 0)
        seen_heads[sec.heading] = occ + 1
        sid = section_id(source, sec.heading, occ)
        sections[sid] = sec.content[:MAX_SECTION_CHARS]
        pieces = pack_blocks(split_blocks(sec.content))
        for c_i, (piece, kind, context) in enumerate(pieces):
            chunks.append(Chunk(
                chunk_hash=chunk_id(source, sec.heading, c_i, piece,
                                    model=model_name),
                source=source, heading=sec.heading, content=piece,
                section_id=sid, ordinal=c_i, kind=kind, context=context,
            ))
    return chunks, sections


# ------------------------------------------------------------------ реестр парсеров

Parser = Callable[[str, str], "tuple[list[Chunk], dict[str, str]]"]

PARSERS: dict[str, Parser] = {}


def register_parser(suffix: str, parser: Parser) -> None:
    """Реестр вместо `if suffix == ".pdf"` (ragflow.md §5.1 п.6).

    Первый не-markdown источник добавляется одной строкой и нигде больше.
    """
    PARSERS[suffix.lower()] = parser


def get_parser(suffix: str) -> Parser | None:
    return PARSERS.get(suffix.lower())


def indexable_suffixes() -> set[str]:
    return set(PARSERS)


def parse_document(source: str, text: str, *, suffix: str | None = None,
                   model_name: str = DEFAULT_INDEX_MODEL,
                   ) -> tuple[list[Chunk], dict[str, str]]:
    suffix = (suffix or Path(source).suffix).lower()
    parser = get_parser(suffix)
    if parser is None:
        return [], {}
    try:
        return parser(text, source, model_name=model_name)  # type: ignore[call-arg]
    except TypeError:
        return parser(text, source)


register_parser(".md", chunk_markdown)
register_parser(".markdown", chunk_markdown)


# ------------------------------------------------------------------ обход vault

@dataclass(slots=True)
class ScannedFile:
    rel: str
    path: Path
    mtime: float
    size: int


@dataclass
class VaultScanner:
    """Обход файлов, общий для всех backend'ов (qdrant.md §11, этап 0.2).

    Хождение по ФС, `mtime/size`, релативизация путей — одинаковы для любого
    хранилища, поэтому живут рядом с чанкингом, а не внутри движка.
    """
    vault_root: Path | None = None
    excluded_dirs: set[str] = field(
        default_factory=lambda: set(DEFAULT_EXCLUDED_DIRS))
    suffixes: set[str] = field(default_factory=indexable_suffixes)

    def rel(self, path: Path) -> str:
        if self.vault_root:
            try:
                return path.resolve().relative_to(
                    self.vault_root.resolve()).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def scan(self, roots: Iterable[Path]) -> Iterator[ScannedFile]:
        seen: set[Path] = set()
        for root in roots:
            root = Path(root)
            if root.is_file():
                candidates: Iterable[Path] = [root]
            else:
                candidates = sorted(
                    p for suf in sorted(self.suffixes)
                    for p in root.rglob(f"*{suf}"))
            for p in candidates:
                if p.suffix.lower() not in self.suffixes:
                    continue
                if any(part in self.excluded_dirs for part in p.parts):
                    continue
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = rp.stat()
                except OSError:
                    continue
                yield ScannedFile(self.rel(rp), rp, st.st_mtime, st.st_size)
