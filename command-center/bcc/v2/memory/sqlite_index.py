"""SQLite derived memory index.

Source of truth remains Markdown/Obsidian. The SQLite file is rebuildable.
This backend avoids the measured single-JSON rewrite and cold-deserialization costs.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .chunking_v22 import split_long_markdown
from .local_index import DEFAULT_EXCLUDED_DIRS, MAX_SECTION_CHARS, Chunk, split_sections, tokenize
from .memsearch_bridge import MemoryHit

SCHEMA_VERSION = 1


@dataclass(slots=True)
class ParsedFile:
    source: str
    digest: str
    chunks: list[Chunk]
    sections: dict[str, str]


def chunk_markdown_v22(text: str, source: str) -> tuple[list[Chunk], dict[str, str]]:
    chunks: list[Chunk] = []
    sections: dict[str, str] = {}
    for s_i, (heading, content) in enumerate(split_sections(text)):
        if not content.strip():
            continue
        section_id = hashlib.sha256(f"{source}\x00{s_i}\x00{heading}".encode()).hexdigest()[:16]
        sections[section_id] = content[:MAX_SECTION_CHARS]
        for c_i, piece in enumerate(split_long_markdown(content)):
            h = hashlib.sha256(
                f"{source}\x00{s_i}\x00{c_i}\x00{piece[:240]}".encode()
            ).hexdigest()[:16]
            chunks.append(Chunk(
                chunk_hash=h, source=source, heading=heading, content=piece,
                section_id=section_id, ordinal=c_i))
    return chunks, sections


@dataclass
class SQLiteMemoryBackend:
    index_path: Path
    vault_root: Path | None = None
    excluded_dirs: set[str] | None = None
    k1: float = 1.5
    b: float = 0.75

    def __post_init__(self) -> None:
        self.index_path = Path(self.index_path)
        self.vault_root = Path(self.vault_root) if self.vault_root is not None else None
        self.excluded_dirs = set(self.excluded_dirs or DEFAULT_EXCLUDED_DIRS)

    def available(self) -> bool:
        return True

    def _connect(self) -> sqlite3.Connection:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.index_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema(conn)
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(
          source TEXT PRIMARY KEY,
          digest TEXT NOT NULL,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sections(
          section_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          heading TEXT NOT NULL,
          content TEXT NOT NULL,
          FOREIGN KEY(source) REFERENCES files(source) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS chunks(
          chunk_hash TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          heading TEXT NOT NULL,
          content TEXT NOT NULL,
          section_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL DEFAULT 0,
          term_count INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(source) REFERENCES files(source) ON DELETE CASCADE,
          FOREIGN KEY(section_id) REFERENCES sections(section_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_chunks_source ON chunks(source);
        CREATE INDEX IF NOT EXISTS ix_chunks_section ON chunks(section_id);
        CREATE TABLE IF NOT EXISTS postings(
          term TEXT NOT NULL,
          chunk_hash TEXT NOT NULL,
          tf INTEGER NOT NULL,
          PRIMARY KEY(term, chunk_hash),
          FOREIGN KEY(chunk_hash) REFERENCES chunks(chunk_hash) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_postings_term ON postings(term);
        """)
        current = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if current is None:
            conn.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)",
                         (str(SCHEMA_VERSION),))
        elif int(current[0]) != SCHEMA_VERSION:
            raise RuntimeError(f"unsupported memory sqlite schema: {current[0]}")
        conn.commit()

    def _rel(self, path: Path) -> str:
        if self.vault_root:
            try:
                return path.resolve().relative_to(self.vault_root.resolve()).as_posix()
            except (ValueError, OSError):
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
                try:
                    rp = p.resolve()
                except OSError:
                    continue
                if rp not in seen:
                    seen.add(rp)
                    out.append(rp)
        return out

    def _parse_file(self, path: Path) -> ParsedFile | None:
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        digest = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", "replace")
        source = self._rel(path)
        chunks, sections = chunk_markdown_v22(text, source)
        return ParsedFile(source=source, digest=digest, chunks=chunks, sections=sections)

    @staticmethod
    def _weighted_terms(chunk: Chunk) -> list[str]:
        return tokenize(chunk.content) + 2 * tokenize(chunk.heading) + 2 * tokenize(Path(chunk.source).stem)

    def index_one_sync(self, path: Path, *, force: bool = False) -> dict:
        parsed = self._parse_file(Path(path))
        if parsed is None:
            return {"updated": 0, "skipped": 0, "error": f"cannot read {path}"}

        with self._connect() as conn:
            existing = conn.execute("SELECT digest FROM files WHERE source=?",
                                    (parsed.source,)).fetchone()
            if existing and existing["digest"] == parsed.digest and not force:
                return {"updated": 0, "skipped": 1, "source": parsed.source,
                        "backend": "sqlite"}

            conn.execute(
                "INSERT INTO files(source,digest) VALUES(?,?) "
                "ON CONFLICT(source) DO UPDATE SET digest=excluded.digest,updated_at=CURRENT_TIMESTAMP",
                (parsed.source, parsed.digest))
            conn.execute("DELETE FROM postings WHERE chunk_hash IN "
                         "(SELECT chunk_hash FROM chunks WHERE source=?)", (parsed.source,))
            conn.execute("DELETE FROM chunks WHERE source=?", (parsed.source,))
            conn.execute("DELETE FROM sections WHERE source=?", (parsed.source,))

            for sid, content in parsed.sections.items():
                heading = next((c.heading for c in parsed.chunks if c.section_id == sid), "")
                conn.execute("INSERT INTO sections(section_id,source,heading,content) VALUES(?,?,?,?)",
                             (sid, parsed.source, heading, content))

            for chunk in parsed.chunks:
                terms = self._weighted_terms(chunk)
                conn.execute(
                    "INSERT INTO chunks(chunk_hash,source,heading,content,section_id,ordinal,term_count) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (chunk.chunk_hash, chunk.source, chunk.heading, chunk.content,
                     chunk.section_id, int(chunk.ordinal), max(1, len(terms))))
                tf = Counter(terms)
                conn.executemany("INSERT INTO postings(term,chunk_hash,tf) VALUES(?,?,?)",
                                 [(term, chunk.chunk_hash, int(count)) for term, count in tf.items()])
            conn.commit()

        return {"updated": 1, "skipped": 0, "source": parsed.source,
                "chunks": len(parsed.chunks), "backend": "sqlite"}

    def remove_source_sync(self, source: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM files WHERE source=?", (source,)).fetchone()
            if row is None:
                return False
            conn.execute("DELETE FROM files WHERE source=?", (source,))
            conn.commit()
        return True

    def index_sync(self, paths: list[Path], *, force: bool = False) -> dict:
        found = self._iter_files(paths)
        rels = {self._rel(p): p for p in found}
        with self._connect() as conn:
            known = {row["source"]: row["digest"]
                     for row in conn.execute("SELECT source,digest FROM files")}

        added = updated = skipped = removed = 0
        for source, path in rels.items():
            before = source in known
            result = self.index_one_sync(path, force=force)
            if result.get("skipped"):
                skipped += 1
            elif result.get("updated"):
                updated += 1 if before else 0
                added += 0 if before else 1

        scanned_roots = [Path(x).resolve() for x in paths]
        for source in list(known):
            if source in rels:
                continue
            abs_path = (self.vault_root / source) if self.vault_root else Path(source)
            try:
                resolved = abs_path.resolve()
                under = any(resolved == root or root in resolved.parents for root in scanned_roots)
            except OSError:
                under = False
            if under and not abs_path.exists() and self.remove_source_sync(source):
                removed += 1

        stats = self.stats_sync()
        return {"backend": "sqlite", "files": stats["files"], "chunks": stats["chunks"],
                "terms": stats["terms"], "added": added, "updated": updated,
                "skipped": skipped, "removed": removed}

    def search_sync(self, query: str, *, top_k: int = 16) -> list[MemoryHit]:
        terms = list(dict.fromkeys(tokenize(query)))
        if not terms:
            return []
        with self._connect() as conn:
            n_docs = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            if not n_docs:
                return []
            avgdl = float(conn.execute("SELECT COALESCE(AVG(term_count),1) FROM chunks").fetchone()[0] or 1)
            placeholders = ",".join("?" for _ in terms)
            rows = conn.execute(
                f"SELECT p.term,p.chunk_hash,p.tf,c.term_count FROM postings p "
                f"JOIN chunks c ON c.chunk_hash=p.chunk_hash WHERE p.term IN ({placeholders})",
                terms).fetchall()
            if not rows:
                return []
            dfs = {term: int(conn.execute("SELECT COUNT(*) FROM postings WHERE term=?",
                                         (term,)).fetchone()[0]) for term in terms}
            scores: dict[str, float] = defaultdict(float)
            for row in rows:
                term, tf, dl = row["term"], int(row["tf"]), max(1, int(row["term_count"]))
                df = max(1, dfs.get(term, 1))
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1 - self.b + self.b * dl / avgdl)
                scores[row["chunk_hash"]] += idf * (tf * (self.k1 + 1)) / denom

            ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:max(1, int(top_k))]
            hits: list[MemoryHit] = []
            for chunk_hash, score in ranked:
                row = conn.execute("SELECT * FROM chunks WHERE chunk_hash=?", (chunk_hash,)).fetchone()
                if row:
                    hits.append(MemoryHit(content=row["content"], source=row["source"],
                                          heading=row["heading"], score=round(float(score), 4),
                                          chunk_hash=chunk_hash,
                                          metadata={"section_id": row["section_id"],
                                                    "ordinal": row["ordinal"],
                                                    "backend": "sqlite"}))
            return hits

    def expand_sync(self, chunk_hash: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.chunk_hash,c.source,c.heading,c.content,c.section_id,s.content AS section_content "
                "FROM chunks c LEFT JOIN sections s ON s.section_id=c.section_id WHERE c.chunk_hash=?",
                (chunk_hash,)).fetchone()
        if row is None:
            raise KeyError(f"неизвестный chunk_hash: {chunk_hash}")
        return {"content": row["section_content"] or row["content"], "source": row["source"],
                "heading": row["heading"], "chunk_hash": row["chunk_hash"],
                "section_id": row["section_id"]}

    def stats_sync(self) -> dict[str, Any]:
        with self._connect() as conn:
            files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
            chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            terms = int(conn.execute("SELECT COUNT(DISTINCT term) FROM postings").fetchone()[0])
        try:
            size = self.index_path.stat().st_size
        except OSError:
            size = 0
        return {"backend": "sqlite", "files": files, "chunks": chunks, "terms": terms,
                "dense": False, "index_path": str(self.index_path), "size_bytes": size,
                "schema_version": SCHEMA_VERSION}

    async def index_one(self, path: Path, *, force: bool = False) -> dict:
        return await asyncio.to_thread(self.index_one_sync, Path(path), force=force)

    async def index(self, paths: list[Path], *, force: bool = False) -> dict:
        return await asyncio.to_thread(self.index_sync, list(paths), force=force)

    async def search(self, query: str, *, top_k: int = 16) -> list[MemoryHit]:
        return await asyncio.to_thread(self.search_sync, query, top_k=top_k)

    async def expand(self, chunk_hash: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.expand_sync, chunk_hash)

    async def stats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.stats_sync)
