"""Встроенный локальный backend памяти — без тяжёлых зависимостей.

Зачем: единственный backend аддона (`MemSearchBridge`) — это обёртка над внешним
бинарём `memsearch` (Milvus/ONNX). В этой среде его нет и по умолчанию не будет,
поэтому память была бы мертва. Здесь — честная замена на чистом stdlib:

  * разбиение markdown на чанки ПО ЗАГОЛОВКАМ (секция = единица смысла);
  * лексический индекс BM25 (Okapi), persisted JSON в data dir;
  * инкрементальная переиндексация по sha256 содержимого файла;
  * `expand(chunk_hash)` отдаёт ПОЛНУЮ секцию — прогрессивное раскрытие.

Плотные эмбеддинги опциональны: `load_dense_encoder()` честно падает
`DenseUnavailable`, backend продолжает работать на одном BM25. Ничего не
устанавливается автоматически.

Гарантии безопасности живут в `ObsidianVault` (write только в `BOSSMAN Memory/`,
исключения `.obsidian/.trash/.git`) — этот модуль их не ослабляет: он индексирует
ровно те файлы, что отдал vault.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .memsearch_bridge import MemoryHit

INDEX_VERSION = 3
DEFAULT_EXCLUDED_DIRS = {".obsidian", ".trash", ".git", "node_modules", ".venv",
                         "__pycache__"}

# размер чанка: секция целиком, если влезает; иначе режем по абзацам
MAX_CHUNK_CHARS = 1400
CHUNK_OVERLAP_CHARS = 160
MAX_SECTION_CHARS = 8000        # сколько секции хранить для expand


# ------------------------------------------------------------------ лексика

_WORD = re.compile(r"\w+", re.UNICODE)

# грубый морфологический срез: он не «умный», но одинаково применяется и к
# документу, и к запросу, поэтому «база / базу / базы» попадают в один терм.
_RU_SUFFIXES = ("иями", "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими",
                "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "ой", "ей",
                "ов", "ев", "ам", "ям", "ах", "ях", "ию", "ия", "ье", "ью",
                "а", "я", "ы", "и", "о", "е", "у", "ю", "ь", "й")
_EN_SUFFIXES = ("ing", "ies", "ed", "es", "s")


def stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for suf in _EN_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3 and word.isascii():
            return word[: -len(suf)]
    for suf in _RU_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


def tokenize(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if len(w) > 1]


# ------------------------------------------------------------------ чанки

@dataclass(slots=True)
class Chunk:
    chunk_hash: str
    source: str          # путь относительно корня vault — им и цитируем
    heading: str         # «Архитектура > Выбор БД»
    content: str
    section_id: str
    ordinal: int = 0

    def to_json(self) -> dict:
        return {"source": self.source, "heading": self.heading,
                "content": self.content, "section_id": self.section_id,
                "ordinal": self.ordinal}


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


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


def split_sections(text: str) -> list[tuple[str, str]]:
    """Markdown → [(путь заголовков, тело секции)]. Преамбула до первого
    заголовка — отдельная секция с пустым путём."""
    body, _title = _strip_frontmatter(text)
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    cur_head = ""
    buf: list[str] = []
    in_fence = False

    def flush():
        content = "\n".join(buf).strip()
        if content:
            sections.append((cur_head, content))

    for line in body.splitlines():
        if line.lstrip().startswith("```"):
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
            buf.append(title)          # заголовок — часть текста секции
        else:
            buf.append(line)
    flush()
    return sections


def _split_long(content: str) -> list[str]:
    if len(content) <= MAX_CHUNK_CHARS:
        return [content]
    parts: list[str] = []
    paragraphs = [p for p in re.split(r"\n\s*\n", content) if p.strip()]
    cur = ""
    for p in paragraphs:
        if cur and len(cur) + len(p) + 2 > MAX_CHUNK_CHARS:
            parts.append(cur.strip())
            cur = cur[-CHUNK_OVERLAP_CHARS:] + "\n\n" + p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur.strip():
        parts.append(cur.strip())
    # абзац сам по себе может быть огромным — режем жёстко
    out: list[str] = []
    for part in parts:
        while len(part) > MAX_CHUNK_CHARS * 2:
            out.append(part[:MAX_CHUNK_CHARS])
            part = part[MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS:]
        out.append(part)
    return out


def chunk_markdown(text: str, source: str) -> tuple[list[Chunk], dict[str, str]]:
    """Разбить один markdown-файл. Возвращает (чанки, секции для expand)."""
    chunks: list[Chunk] = []
    sections: dict[str, str] = {}
    for s_i, (heading, content) in enumerate(split_sections(text)):
        section_id = hashlib.sha256(f"{source}\x00{s_i}\x00{heading}".encode()
                                    ).hexdigest()[:16]
        sections[section_id] = content[:MAX_SECTION_CHARS]
        for c_i, piece in enumerate(_split_long(content)):
            h = hashlib.sha256(f"{source}\x00{s_i}\x00{c_i}\x00{piece[:200]}".encode()
                               ).hexdigest()[:16]
            chunks.append(Chunk(chunk_hash=h, source=source, heading=heading,
                                content=piece, section_id=section_id, ordinal=c_i))
    return chunks, sections


# ------------------------------------------------------------------ dense (опц.)

class DenseUnavailable(RuntimeError):
    """Плотные эмбеддинги недоступны — это НЕ ошибка, а нормальный режим."""


def load_dense_encoder(model_name: str = "BAAI/bge-m3"):
    """Опциональный dense-энкодер. Ничего не ставит; при отсутствии зависимостей
    честно падает — вызывающий обязан деградировать до BM25."""
    try:
        import numpy  # noqa: F401
        from sentence_transformers import SentenceTransformer
    except Exception as exc:      # pragma: no cover — в этой среде всегда так
        raise DenseUnavailable(
            "numpy/sentence-transformers не установлены — работает только BM25"
        ) from exc
    return SentenceTransformer(model_name)   # pragma: no cover


# ------------------------------------------------------------------ backend

@dataclass
class LocalMemoryBackend:
    """BM25-индекс markdown'а. Интерфейс совместим с `MemSearchBridge`."""

    index_path: Path
    vault_root: Path | None = None
    excluded_dirs: set[str] = field(default_factory=lambda: set(DEFAULT_EXCLUDED_DIRS))
    dense: Any = None                 # опционально; None → чистый BM25
    k1: float = 1.5
    b: float = 0.75

    files: dict[str, dict] = field(default_factory=dict, init=False)
    chunks: dict[str, Chunk] = field(default_factory=dict, init=False)
    sections: dict[str, str] = field(default_factory=dict, init=False)
    _postings: dict[str, dict[str, int]] = field(default_factory=dict, init=False)
    _lengths: dict[str, int] = field(default_factory=dict, init=False)
    _loaded: bool = field(default=False, init=False)

    # -------------------------------------------------- persistence

    def available(self) -> bool:
        return True

    def load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if raw.get("version") != INDEX_VERSION:
            return                     # формат сменился — переиндексируем с нуля
        self.files = raw.get("files") or {}
        self.sections = raw.get("sections") or {}
        for h, c in (raw.get("chunks") or {}).items():
            self.chunks[h] = Chunk(chunk_hash=h, source=c.get("source", ""),
                                   heading=c.get("heading", ""),
                                   content=c.get("content", ""),
                                   section_id=c.get("section_id", ""),
                                   ordinal=int(c.get("ordinal") or 0))
        self._rebuild_postings()

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": INDEX_VERSION, "files": self.files,
                   "sections": self.sections,
                   "chunks": {h: c.to_json() for h, c in self.chunks.items()}}
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.index_path)

    # -------------------------------------------------- индексация

    def _rel(self, path: Path) -> str:
        if self.vault_root:
            try:
                return path.resolve().relative_to(self.vault_root.resolve()).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def _iter_files(self, roots: Iterable[Path]) -> list[Path]:
        out: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            root = Path(root)
            candidates = [root] if root.is_file() else sorted(root.rglob("*.md"))
            for p in candidates:
                if p.suffix.lower() != ".md":
                    continue
                if any(part in self.excluded_dirs for part in p.parts):
                    continue
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    out.append(rp)
        return out

    def index_sync(self, paths: list[Path], *, force: bool = False) -> dict:
        """Инкрементально: файл переиндексируется, только если сменился sha256."""
        self.load()
        found = self._iter_files(paths)
        rels = {self._rel(p): p for p in found}
        added = updated = skipped = 0

        for rel, path in rels.items():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            known = self.files.get(rel)
            if known and known.get("hash") == digest and not force:
                skipped += 1
                continue
            self._drop_file(rel)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chunks, sections = chunk_markdown(text, rel)
            for c in chunks:
                self.chunks[c.chunk_hash] = c
            self.sections.update(sections)
            self.files[rel] = {"hash": digest, "chunks": [c.chunk_hash for c in chunks],
                               "sections": list(sections)}
            if known:
                updated += 1
            else:
                added += 1

        # удалённые файлы — только в пределах просканированных корней
        scanned_roots = [Path(p).resolve() for p in paths]
        removed = 0
        for rel in list(self.files):
            if rel in rels:
                continue
            abs_path = (self.vault_root / rel) if self.vault_root else Path(rel)
            try:
                under = any(abs_path.resolve() == r or r in abs_path.resolve().parents
                            for r in scanned_roots)
            except OSError:
                under = False
            if under and not abs_path.exists():
                self._drop_file(rel)
                removed += 1

        self._rebuild_postings()
        self.save()
        return {"files": len(self.files), "chunks": len(self.chunks),
                "added": added, "updated": updated, "skipped": skipped,
                "removed": removed, "backend": "local"}

    def _drop_file(self, rel: str) -> None:
        old = self.files.pop(rel, None)
        if not old:
            return
        for h in old.get("chunks") or []:
            self.chunks.pop(h, None)
        for sid in old.get("sections") or []:
            self.sections.pop(sid, None)

    def _rebuild_postings(self) -> None:
        postings: dict[str, dict[str, int]] = defaultdict(dict)
        lengths: dict[str, int] = {}
        for h, c in self.chunks.items():
            # заголовок и имя файла весят вдвое: по ним чаще всего и ищут
            terms = tokenize(c.content) + 2 * tokenize(c.heading) \
                + 2 * tokenize(Path(c.source).stem)
            lengths[h] = max(1, len(terms))
            tf: dict[str, int] = defaultdict(int)
            for t in terms:
                tf[t] += 1
            for t, n in tf.items():
                postings[t][h] = n
        self._postings = dict(postings)
        self._lengths = lengths

    # -------------------------------------------------- поиск

    def search_sync(self, query: str, *, top_k: int = 16) -> list[MemoryHit]:
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
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        hits: list[MemoryHit] = []
        for h, score in ranked:
            c = self.chunks[h]
            hits.append(MemoryHit(content=c.content, source=c.source,
                                  heading=c.heading, score=round(float(score), 4),
                                  chunk_hash=h,
                                  metadata={"section_id": c.section_id,
                                            "ordinal": c.ordinal,
                                            "backend": "local"}))
        return hits

    def expand_sync(self, chunk_hash: str) -> dict[str, Any]:
        self.load()
        c = self.chunks.get(chunk_hash)
        if c is None:
            raise KeyError(f"неизвестный chunk_hash: {chunk_hash}")
        return {"content": self.sections.get(c.section_id, c.content),
                "source": c.source, "heading": c.heading,
                "chunk_hash": chunk_hash, "section_id": c.section_id}

    def stats_sync(self) -> dict[str, Any]:
        self.load()
        return {"backend": "local", "files": len(self.files),
                "chunks": len(self.chunks), "terms": len(self._postings),
                "dense": bool(self.dense), "index_path": str(self.index_path)}

    # -------------------------------------------------- async-фасад (как у моста)

    async def index(self, paths: list[Path], *, force: bool = False) -> dict:
        return await asyncio.to_thread(self.index_sync, list(paths), force=force)

    async def search(self, query: str, *, top_k: int = 16) -> list[MemoryHit]:
        return await asyncio.to_thread(self.search_sync, query, top_k=top_k)

    async def expand(self, chunk_hash: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.expand_sync, chunk_hash)

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.stats_sync)


# `LexicalReranker` жил ЗДЕСЬ и ОДНОВРЕМЕННО в `reranker.py` — два класса с
# одним именем и разными формулами. Экспортировался (через `memory/__init__`, а
# значит и в `tools_memory.py`) старый, с зашитыми константами; более новый, с
# весами-полями и заготовкой под dense-смешивание, не доставался никому. Правка
# весов «в переранжировщике» не дошла бы до боя вообще.
#
# Владелец переранжирования — `reranker.py`, модуль, названный по этой
# ответственности. Поведение по умолчанию то же: веса 2.0 / 1.5 / 0.05 и
# dense_weight=0 дают ровно прежнюю формулу.
