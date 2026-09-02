"""Лексический индекс ПО КОДУ — BM25 на stdlib, без сервисов и без сети.

Откуда взялось: `docs/research/claude-context.md`. Вердикт исследования —
пакет `zilliztech/claude-context` не тащим (Milvus + выгрузка исходников в
эмбеддинг-провайдер), но берём пять его работающих идей. Собственный бенчмарк
авторов даёт F1 0.40 против 0.40 у голого grep: семантика ищет НЕ лучше, она
ищет компактнее. Значит цель этого модуля — компактность выдачи, а не «умный
поиск».

Что перенесено (ТЗ §5):
  1. чанк = синтаксическая единица (Python — stdlib `ast`, JS — скобки),
     а не N символов;
  2. заголовок `путь :: квалифицированное имя` ВНУТРИ тела чанка — он попадает
     в BM25 и резко поднимает скор;
  3. разбиение идентификаторов на термы: `decide_effect` → `decide`, `effect`;
     `readDOM` → `read`, `dom`;
  4. дедуп по перекрытию диапазонов строк (у них `deduplicateResults`);
  5. фазовый статус индексации + аддитивные игнор-паттерны с чтением
     `.gitignore` (вместо жёсткого списка каталогов).

Чего сознательно НЕТ: эмбеддингов, tree-sitter, векторной БД, фонового
таймера на 5 минут. Ноль зависимостей сверх стандартной библиотеки.

Стеммер и BM25-математика продублированы из `v2/memory/local_index.py`
намеренно: тот модуль принадлежит другому лейну и индексирует markdown по
заголовкам, здесь другой чанкер и другой словарь. Общий импорт связал бы два
независимых индекса одним форматом.
"""
from __future__ import annotations

import ast
import asyncio
import fnmatch
import hashlib
import json
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .permissions import PermissionPolicy

INDEX_VERSION = 1

# Только код. `.md` сознательно не берём: markdown уже индексирует
# `LocalMemoryBackend` по заголовкам, дубль раздул бы корпус вдвое.
DEFAULT_EXTENSIONS = frozenset({".py", ".pyi", ".js", ".mjs", ".cjs",
                                ".ts", ".tsx", ".jsx"})

DEFAULT_IGNORE_PATTERNS = (
    ".git/", ".hg/", ".svn/", "node_modules/", "__pycache__/", ".venv/",
    "venv/", ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", "dist/",
    "build/", ".next/", "coverage/", ".obsidian/", ".trash/",
    "*.min.js", "*.bundle.js", "*.map",
)

MAX_CHUNK_CHARS = 4000       # длинную функцию режем на куски, каждый со своим заголовком
MAX_CLASS_LINES = 60         # класс длиннее — заголовок отдельно, методы отдельно
MAX_FILE_BYTES = 1_000_000   # сгенерированные простыни не индексируем


# ------------------------------------------------------------------ лексика

_WORD = re.compile(r"[0-9A-Za-z_]+|[А-Яа-яЁё]+", re.UNICODE)
# camelCase / PascalCase / ACRONYMWord / trailing digits
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

_RU_SUFFIXES = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
                "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой", "ей",
                "ов", "ев", "ам", "ям", "ах", "ях", "ию", "ия", "ье", "ью",
                "а", "я", "ы", "и", "о", "е", "у", "ю", "ь", "й")
_EN_SUFFIXES = ("ing", "ies", "ed", "es", "s")


def stem(word: str) -> str:
    if len(word) <= 3:
        return word
    if word.endswith(("ся", "сь")) and len(word) >= 6:
        word = word[:-2]
    for suf in _EN_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3 and word.isascii():
            return word[: -len(suf)]
    for suf in _RU_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def split_identifier(word: str) -> list[str]:
    """`decide_effect` → [decide_effect, decide, effect]; `readDOM` →
    [readDOM, read, dom]. Идея №3 из ТЗ §5: без этого запрос «где принимается
    решение» не встречается с функцией `decide_effect` ни одним термом."""
    parts: list[str] = []
    for piece in word.split("_"):
        if not piece:
            continue
        parts.extend(_CAMEL.findall(piece))
    out = [p.lower() for p in parts if len(p) > 1]
    if len(out) <= 1:
        return out or ([word.lower()] if len(word) > 1 else [])
    return [word.lower(), *out]


def tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for raw in _WORD.findall(text or ""):
        for part in split_identifier(raw):
            if len(part) > 1:
                terms.append(stem(part))
    return terms


# ------------------------------------------------------------------ чанки

@dataclass(slots=True)
class CodeChunk:
    chunk_hash: str
    source: str          # путь относительно корня — им и цитируем
    qualname: str        # decide_effect / ToolRegistry.resolve / <module>
    kind: str            # module | function | class | method | block
    start_line: int      # 1-based, включительно — врать здесь нельзя (§9.4 ТЗ)
    end_line: int
    content: str         # ЗАГОЛОВОК + тело: заголовок обязан быть в BM25
    ordinal: int = 0

    @property
    def header(self) -> str:
        return f"{self.source} :: {self.qualname}"

    def to_json(self) -> dict:
        return {"source": self.source, "qualname": self.qualname, "kind": self.kind,
                "start_line": self.start_line, "end_line": self.end_line,
                "content": self.content, "ordinal": self.ordinal}

    @classmethod
    def from_json(cls, chunk_hash: str, raw: dict) -> "CodeChunk":
        return cls(chunk_hash=chunk_hash, source=raw.get("source", ""),
                   qualname=raw.get("qualname", ""), kind=raw.get("kind", "block"),
                   start_line=int(raw.get("start_line") or 1),
                   end_line=int(raw.get("end_line") or 1),
                   content=raw.get("content", ""), ordinal=int(raw.get("ordinal") or 0))


def _make(source: str, qualname: str, kind: str, lines: list[str],
          start: int, end: int) -> list[CodeChunk]:
    """Собрать чанк(и) из диапазона строк [start; end] (1-based, включительно).
    Слишком длинный кусок режется по строкам, заголовок повторяется в каждом."""
    start = max(1, start)
    end = min(len(lines), end)
    if end < start:
        return []
    body_lines = lines[start - 1:end]
    if not any(ln.strip() for ln in body_lines):
        return []
    header = f"{source} :: {qualname}"
    out: list[CodeChunk] = []
    ordinal = 0
    cursor = 0
    while cursor < len(body_lines):
        piece: list[str] = []
        size = 0
        while cursor < len(body_lines) and (not piece or size < MAX_CHUNK_CHARS):
            piece.append(body_lines[cursor])
            size += len(body_lines[cursor]) + 1
            cursor += 1
        first = start + (cursor - len(piece))
        content = header + "\n" + "\n".join(piece)
        h = hashlib.sha256(f"{source}\x00{qualname}\x00{first}\x00{ordinal}".encode()
                           ).hexdigest()[:16]
        out.append(CodeChunk(chunk_hash=h, source=source, qualname=qualname, kind=kind,
                             start_line=first, end_line=first + len(piece) - 1,
                             content=content, ordinal=ordinal))
        ordinal += 1
    return out


def _decorated_start(node: ast.AST) -> int:
    starts = [int(getattr(node, "lineno", 1))]
    for dec in getattr(node, "decorator_list", None) or []:
        starts.append(int(getattr(dec, "lineno", starts[0])))
    return min(starts)


def chunk_python(text: str, source: str) -> list[CodeChunk]:
    """Верхнеуровневые `def`/`class`; большой класс — заголовок + методы
    отдельными чанками; всё остальное (докстрока модуля, импорты, константы) —
    непрерывными блоками `<module>`. Границы чанков честные: `start_line`
    всегда соответствует телу (у claude-context это сломано, ТЗ §2.2)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return chunk_lines(text, source)

    lines = text.splitlines()
    total = len(lines)
    chunks: list[CodeChunk] = []
    covered: set[int] = set()

    def cover(a: int, b: int) -> None:
        covered.update(range(max(1, a), min(total, b) + 1))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start, end = _decorated_start(node), int(node.end_lineno or node.lineno)
            chunks.extend(_make(source, node.name, "function", lines, start, end))
            cover(start, end)
        elif isinstance(node, ast.ClassDef):
            start, end = _decorated_start(node), int(node.end_lineno or node.lineno)
            methods = [n for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if end - start + 1 > MAX_CLASS_LINES and methods:
                head_end = _decorated_start(methods[0]) - 1
                chunks.extend(_make(source, node.name, "class", lines, start, head_end))
                # каждый метод тянется до начала следующего: атрибуты класса между
                # методами не теряются и при этом не дублируются
                for i, m in enumerate(methods):
                    m_start = _decorated_start(m)
                    m_end = (_decorated_start(methods[i + 1]) - 1
                             if i + 1 < len(methods) else end)
                    chunks.extend(_make(source, f"{node.name}.{m.name}", "method",
                                        lines, m_start, m_end))
            else:
                chunks.extend(_make(source, node.name, "class", lines, start, end))
            cover(start, end)

    # непокрытые непрерывные участки — модульный уровень
    run_start = None
    for ln in range(1, total + 1):
        if ln not in covered:
            if run_start is None:
                run_start = ln
        elif run_start is not None:
            chunks.extend(_make(source, "<module>", "module", lines, run_start, ln - 1))
            run_start = None
    if run_start is not None:
        chunks.extend(_make(source, "<module>", "module", lines, run_start, total))

    return sorted(chunks, key=lambda c: (c.start_line, c.ordinal))


# --------------------------------------------------------------- JS/TS чанкер

_JS_DECL = re.compile(
    r"^[ \t]*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:"
    r"function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)"
    r"|class\s+(?P<cls>[A-Za-z_$][\w$]*)"
    r"|(?:const|let|var)\s+(?P<var>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)"
    r"|(?P<meth>[A-Za-z_$][\w$]*)\s*\([^;{]*\)\s*\{"
    r")")


def _js_code_mask(text: str) -> list[bool]:
    """True там, где символ — код, а не строка/комментарий. Нужен, чтобы счёт
    фигурных скобок не сбивался о `{` внутри строки. Регулярные литералы не
    разбираются — редкость, и максимум даёт чанк длиннее нужного."""
    mask = [True] * len(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                mask[i] = False
                i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                mask[i] = False
                i += 1
            for _ in range(min(2, n - i)):
                mask[i] = False
                i += 1
        elif ch in "'\"`":
            quote = ch
            mask[i] = False
            i += 1
            while i < n:
                if text[i] == "\\":
                    mask[i] = False
                    if i + 1 < n:
                        mask[i + 1] = False
                    i += 2
                    continue
                mask[i] = False
                if text[i] == quote:
                    i += 1
                    break
                if text[i] == "\n" and quote != "`":
                    i += 1
                    break
                i += 1
        else:
            i += 1
    return mask


def chunk_javascript(text: str, source: str) -> list[CodeChunk]:
    """Регексп по объявлениям + счёт фигурных скобок (ТЗ §5.1: «для JS —
    регексп по фигурным скобкам»). Всё вне найденных тел — блоки `<module>`."""
    lines = text.splitlines()
    total = len(lines)
    if not total:
        return []
    mask = _js_code_mask(text)
    # смещение начала каждой строки в тексте
    offsets: list[int] = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1

    chunks: list[CodeChunk] = []
    covered: set[int] = set()
    ln_no = 1
    while ln_no <= total:
        if ln_no in covered:
            ln_no += 1
            continue
        m = _JS_DECL.match(lines[ln_no - 1])
        if not m:
            ln_no += 1
            continue
        name = m.group("fn") or m.group("cls") or m.group("var") or m.group("meth") or ""
        kind = "class" if m.group("cls") else "function"
        if name in ("if", "for", "while", "switch", "catch", "return", "function"):
            ln_no += 1
            continue
        # найти открывающую скобку и её пару
        start_off = offsets[ln_no - 1]
        open_at = -1
        for i in range(start_off, min(len(text), start_off + 4000)):
            if text[i] == "{" and mask[i]:
                open_at = i
                break
            if text[i] == ";" and mask[i]:
                break
        if open_at < 0:
            ln_no += 1
            continue
        depth, close_at = 0, -1
        for i in range(open_at, len(text)):
            if not mask[i]:
                continue
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    close_at = i
                    break
        if close_at < 0:
            ln_no += 1
            continue
        end_line = text.count("\n", 0, close_at) + 1
        chunks.extend(_make(source, name, kind, lines, ln_no, end_line))
        covered.update(range(ln_no, end_line + 1))
        ln_no = end_line + 1

    run_start = None
    for ln in range(1, total + 1):
        if ln not in covered:
            if run_start is None:
                run_start = ln
        elif run_start is not None:
            chunks.extend(_make(source, "<module>", "module", lines, run_start, ln - 1))
            run_start = None
    if run_start is not None:
        chunks.extend(_make(source, "<module>", "module", lines, run_start, total))
    return sorted(chunks, key=lambda c: (c.start_line, c.ordinal))


def chunk_lines(text: str, source: str, *, window: int = 60) -> list[CodeChunk]:
    """Фолбэк для языков без чанкера и для файлов со сломанным синтаксисом."""
    lines = text.splitlines()
    out: list[CodeChunk] = []
    for i in range(0, len(lines), window):
        out.extend(_make(source, "<module>", "block", lines, i + 1, i + window))
    return out


def chunk_file(text: str, source: str) -> list[CodeChunk]:
    suffix = Path(source).suffix.lower()
    if suffix in (".py", ".pyi"):
        return chunk_python(text, source)
    if suffix in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
        return chunk_javascript(text, source)
    return chunk_lines(text, source)


# ------------------------------------------------------------------ игноры

class IgnoreRules:
    """Аддитивные паттерны (ТЗ §5.5): дефолт + `.gitignore` каждого корня +
    переданные вызовом. Отрицания (`!pattern`) не поддерживаются — они бы
    расширяли область индексации, а не сужали."""

    def __init__(self, patterns: Iterable[str] = ()) -> None:
        self.dirs: list[str] = []
        self.globs: list[str] = []
        for pat in patterns:
            self.add(pat)

    def add(self, pattern: str) -> None:
        pat = (pattern or "").strip()
        if not pat or pat.startswith("#") or pat.startswith("!"):
            return
        if pat.endswith("/"):
            self.dirs.append(pat.rstrip("/").lstrip("/"))
        else:
            self.globs.append(pat.lstrip("/"))

    def add_gitignore(self, path: Path) -> int:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        before = len(self.dirs) + len(self.globs)
        for line in text.splitlines():
            self.add(line)
        return len(self.dirs) + len(self.globs) - before

    def match(self, rel: str) -> bool:
        parts = rel.split("/")
        for d in self.dirs:
            if d in parts or fnmatch.fnmatch(rel, d) or fnmatch.fnmatch(rel, f"{d}/*"):
                return True
        for g in self.globs:
            if fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(parts[-1], g):
                return True
            if "/" not in g and g in parts:
                return True
            if fnmatch.fnmatch(rel, f"*/{g}") or fnmatch.fnmatch(rel, f"{g}/*"):
                return True
        return False


# ------------------------------------------------------------------ индекс

def _within(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            rr = root.resolve()
        except OSError:
            continue
        if rp == rr or rr in rp.parents:
            return True
    return False


@dataclass
class CodeIndex:
    """BM25-индекс по коду. Персистится одним JSON в data dir — таблиц в
    `bcc/db.py` не требует (ТЗ §7, путь B)."""

    index_path: Path
    roots: list[Path] = field(default_factory=list)
    extensions: frozenset[str] = DEFAULT_EXTENSIONS
    extra_ignores: tuple[str, ...] = ()
    # F-018: deny-лист чувствительных файлов (`*.env`, `*id_rsa*`, `*wallet*`)
    # из PermissionPolicy.safe_default — раньше не имел ни одного импортёра.
    read_policy: PermissionPolicy = field(default_factory=PermissionPolicy.safe_default)
    k1: float = 1.5
    b: float = 0.75

    files: dict[str, dict] = field(default_factory=dict, init=False)
    chunks: dict[str, CodeChunk] = field(default_factory=dict, init=False)
    status: dict = field(default_factory=lambda: {"phase": "idle"}, init=False)
    _postings: dict[str, dict[str, int]] = field(default_factory=dict, init=False)
    _lengths: dict[str, int] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    # -------------------------------------------------- persistence

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if raw.get("version") != INDEX_VERSION:
            return                      # формат сменился — индексируем с нуля
        self.files = raw.get("files") or {}
        for h, c in (raw.get("chunks") or {}).items():
            self.chunks[h] = CodeChunk.from_json(h, c)
        saved = raw.get("status")
        if isinstance(saved, dict) and saved.get("phase") == "ready":
            self.status = saved
        self._rebuild_postings()

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": INDEX_VERSION, "files": self.files,
                   "status": self.status,
                   "chunks": {h: c.to_json() for h, c in self.chunks.items()}}
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.index_path)

    # -------------------------------------------------- обход

    def _rel(self, path: Path) -> str:
        for root in self.roots:
            try:
                return path.resolve().relative_to(root.resolve()).as_posix()
            except (ValueError, OSError):
                continue
        return path.as_posix()

    def _rules_for(self, root: Path) -> IgnoreRules:
        rules = IgnoreRules(DEFAULT_IGNORE_PATTERNS)
        rules.add_gitignore(root / ".gitignore")
        for pat in self.extra_ignores:
            rules.add(pat)
        return rules

    def iter_files(self) -> list[Path]:
        """Файлы под разрешёнными корнями, прошедшие игноры и расширения.
        Ничего вне `roots` не индексируется — то же ограничение, что у
        `terminal.run` на `cwd`: symlink внутри корня, ведущий наружу,
        отбрасывается по `_within` (F-018: до этого `_within` не вызывался и
        цель такого symlink попадала в индекс). Пути из deny-листа
        `read_policy` (секреты/ключи/кошельки) не индексируются никогда."""
        out: list[Path] = []
        seen: set[Path] = set()
        roots = [Path(r) for r in self.roots]
        for root in self.roots:
            root = Path(root)
            if not root.exists():
                continue
            rules = self._rules_for(root if root.is_dir() else root.parent)
            candidates = [root] if root.is_file() else sorted(root.rglob("*"))
            base = root if root.is_dir() else root.parent
            for p in candidates:
                if not p.is_file() or p.suffix.lower() not in self.extensions:
                    continue
                try:
                    rel = p.resolve().relative_to(base.resolve()).as_posix()
                except (ValueError, OSError):
                    rel = p.name
                if rules.match(rel):
                    continue
                # deny-лист: и по относительному пути, и по имени symlink'а,
                # и по имени реальной цели (symlink `config.py` → `../.env`)
                if self.read_policy.denies_read(rel) or self.read_policy.denies_read(p.name):
                    continue
                try:
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                rp = p.resolve()
                if not _within(rp, roots) or self.read_policy.denies_read(rp.name):
                    continue
                if rp not in seen:
                    seen.add(rp)
                    out.append(rp)
        return out

    # -------------------------------------------------- индексация

    def index_sync(self, *, force: bool = False) -> dict:
        """Инкрементально по sha256. Фазы пишутся в `self.status` по ходу —
        `code.status` читает их, пока `code.index` уже вернулся (ТЗ §5.5)."""
        started = time.monotonic()
        self.load()
        self.status = {"phase": "scan", "files_total": 0, "files_done": 0,
                       "chunks": len(self.chunks), "started_at": time.time(),
                       "error": ""}
        try:
            found = self.iter_files()
        except Exception as exc:                    # pragma: no cover
            self.status = {"phase": "error", "error": f"{type(exc).__name__}: {exc}"}
            raise
        rels = {self._rel(p): p for p in found}
        self.status.update(phase="chunk", files_total=len(rels))

        added = updated = skipped = 0
        for done, (rel, path) in enumerate(rels.items(), start=1):
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            known = self.files.get(rel)
            if known and known.get("hash") == digest and not force:
                skipped += 1
                self.status["files_done"] = done
                continue
            self._drop_file(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks = chunk_file(text, rel)
            for c in chunks:
                self.chunks[c.chunk_hash] = c
            self.files[rel] = {"hash": digest, "chunks": [c.chunk_hash for c in chunks]}
            updated += 1 if known else 0
            added += 0 if known else 1
            self.status["files_done"] = done
            self.status["chunks"] = len(self.chunks)

        removed = 0
        for rel in list(self.files):
            if rel not in rels:
                self._drop_file(rel)
                removed += 1

        self.status.update(phase="index")
        self._rebuild_postings()
        self.status.update(phase="write")
        result = {"files": len(self.files), "chunks": len(self.chunks),
                  "terms": len(self._postings), "added": added, "updated": updated,
                  "skipped": skipped, "removed": removed,
                  "seconds": round(time.monotonic() - started, 3)}
        self.status = {"phase": "ready", "files_total": len(rels),
                       "files_done": len(rels), "files": len(self.files),
                       "chunks": len(self.chunks), "terms": len(self._postings),
                       "finished_at": time.time(),
                       "seconds": result["seconds"], "error": ""}
        self.save()
        return result

    def _drop_file(self, rel: str) -> None:
        old = self.files.pop(rel, None)
        if not old:
            return
        for h in old.get("chunks") or []:
            self.chunks.pop(h, None)

    def _rebuild_postings(self) -> None:
        postings: dict[str, dict[str, int]] = defaultdict(dict)
        lengths: dict[str, int] = {}
        for h, c in self.chunks.items():
            # Заголовок `путь :: квалифицированное имя` уже входит в content;
            # здесь он взвешивается ещё раз — идея №2 ТЗ §5, именно она выводит
            # нужную функцию в топ по запросу «где принимается решение».
            terms = tokenize(c.content) + 2 * tokenize(c.header)
            lengths[h] = max(1, len(terms))
            tf: dict[str, int] = defaultdict(int)
            for t in terms:
                tf[t] += 1
            for t, n in tf.items():
                postings[t][h] = n
        self._postings = dict(postings)
        self._lengths = lengths

    # -------------------------------------------------- поиск

    def search_sync(self, query: str, *, top_k: int = 8,
                    path_prefix: str = "") -> list[dict]:
        self.load()
        terms = tokenize(query)
        if not terms or not self.chunks:
            return []
        n_docs = len(self._lengths)
        avgdl = sum(self._lengths.values()) / max(1, n_docs)
        scores: dict[str, float] = defaultdict(float)
        for term in set(terms):
            posting = self._postings.get(term)
            if not posting:
                continue
            df = len(posting)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            for h, tf in posting.items():
                dl = self._lengths.get(h, 1)
                denom = tf + self.k1 * (1 - self.b + self.b * dl / avgdl)
                scores[h] += idf * (tf * (self.k1 + 1)) / denom
        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        hits: list[dict] = []
        for h, score in ranked:
            c = self.chunks.get(h)
            if c is None:
                continue
            if path_prefix and not c.source.startswith(path_prefix):
                continue
            hits.append({"chunk_hash": h, "source": c.source, "qualname": c.qualname,
                         "kind": c.kind, "start_line": c.start_line,
                         "end_line": c.end_line, "content": c.content,
                         "score": round(float(score), 4)})
            if len(hits) >= top_k * 4:      # запас на дедуп
                break
        return dedupe_overlaps(hits)[:top_k]

    def stats(self) -> dict:
        self.load()
        return {"files": len(self.files), "chunks": len(self.chunks),
                "terms": len(self._postings), "index_path": str(self.index_path),
                "roots": [str(r) for r in self.roots],
                "status": dict(self.status)}


def dedupe_overlaps(hits: list[dict], *, threshold: float = 0.5) -> list[dict]:
    """Идея №4 ТЗ §5 (`deduplicateResults`): два чанка из одного файла с
    перекрытием диапазонов строк больше половины — это один и тот же код
    (класс и его метод). Оставляем тот, что выше по скору."""
    kept: list[dict] = []
    for hit in hits:
        drop = False
        for k in kept:
            if k["source"] != hit["source"]:
                continue
            lo = max(k["start_line"], hit["start_line"])
            hi = min(k["end_line"], hit["end_line"])
            overlap = max(0, hi - lo + 1)
            shortest = min(k["end_line"] - k["start_line"] + 1,
                           hit["end_line"] - hit["start_line"] + 1)
            if shortest > 0 and overlap / shortest > threshold:
                drop = True
                break
        if not drop:
            kept.append(hit)
    return kept


# -------------------------------------------------- async-фасад

async def index_async(index: CodeIndex, *, force: bool = False) -> dict:
    return await asyncio.to_thread(index.index_sync, force=force)


async def search_async(index: CodeIndex, query: str, *, top_k: int = 8,
                       path_prefix: str = "") -> list[dict]:
    return await asyncio.to_thread(index.search_sync, query, top_k=top_k,
                                   path_prefix=path_prefix)
