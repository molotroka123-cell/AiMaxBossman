from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from .models import Chunk, Document, MemoryKind, MemoryRecord, MemoryStatus
from .utils import json_dumps

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  text TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  content_hash TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  text TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  heading TEXT NOT NULL DEFAULT '',
  token_count INTEGER NOT NULL DEFAULT 0,
  project TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT '',
  source_uri TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  importance REAL NOT NULL DEFAULT 0.5,
  freshness REAL NOT NULL DEFAULT 1.0,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  content_hash TEXT NOT NULL DEFAULT '',
  metadata TEXT NOT NULL DEFAULT '{}',
  vector TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project);
CREATE TABLE IF NOT EXISTS memories (
  memory_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  project TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL,
  confidence REAL NOT NULL,
  importance REAL NOT NULL,
  source_refs TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  last_verified_at TEXT NOT NULL DEFAULT '',
  supersedes TEXT NOT NULL DEFAULT '[]',
  contradicted_by TEXT NOT NULL DEFAULT '[]',
  metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
"""


class ContextStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self._fts = self._ensure_fts()

    def _ensure_fts(self) -> bool:
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text, heading, project, source_uri)")
            self.db.commit()
            return True
        except sqlite3.OperationalError:
            return False

    def close(self) -> None:
        self.db.close()

    def upsert_document(self, doc: Document) -> None:
        self.db.execute(
            """INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(document_id) DO UPDATE SET source_type=excluded.source_type,source_uri=excluded.source_uri,text=excluded.text,
            project=excluded.project,created_at=excluded.created_at,updated_at=excluded.updated_at,author=excluded.author,
            metadata=excluded.metadata,sensitivity=excluded.sensitivity,content_hash=excluded.content_hash""",
            (doc.document_id, doc.source_type, doc.source_uri, doc.text, doc.project, doc.created_at, doc.updated_at,
             doc.author, json_dumps(doc.metadata), doc.sensitivity, doc.content_hash),
        )
        self.db.commit()

    def replace_chunks(self, document_id: str, chunks: Iterable[Chunk], vectors: dict[str, list[float]] | None = None) -> None:
        old = [r[0] for r in self.db.execute("SELECT chunk_id FROM chunks WHERE document_id=?", (document_id,))]
        if self._fts:
            for cid in old:
                self.db.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (cid,))
        self.db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
        for c in chunks:
            vector = None if not vectors or c.chunk_id not in vectors else json_dumps(vectors[c.chunk_id])
            self.db.execute(
                """INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (c.chunk_id,c.document_id,c.text,c.ordinal,c.heading,c.token_count,c.project,c.source_type,c.source_uri,
                 c.created_at,c.updated_at,c.importance,c.freshness,c.sensitivity,c.content_hash,json_dumps(c.metadata),vector),
            )
            if self._fts:
                self.db.execute("INSERT INTO chunks_fts(chunk_id,text,heading,project,source_uri) VALUES (?,?,?,?,?)",
                                (c.chunk_id,c.text,c.heading,c.project,c.source_uri))
        self.db.commit()

    def lexical_search(self, query: str, limit: int = 50, project: str = "") -> list[tuple[Chunk, float]]:
        if self._fts:
            # OR + prefix вместо implicit-AND: лишнее слово в запросе не обнуляет
            # весь match, а prefix (`term*`) ловит другую словоформу (RU-морфология).
            # bm25 ранжирует по релевантности, лучшие совпадения впереди.
            terms = [re.sub(r"\W+", "", x, flags=re.UNICODE).lower() for x in query.split()]
            terms = [t for t in terms if len(t) >= 2]
            if terms:
                match = " OR ".join(f"{t}*" for t in terms)
                sql = """SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts JOIN chunks c USING(chunk_id)
                         WHERE chunks_fts MATCH ?"""
                params: list[object] = [match]
                if project:
                    sql += " AND c.project=?"; params.append(project)
                sql += " ORDER BY rank LIMIT ?"; params.append(limit)
                try:
                    rows = self.db.execute(sql, params).fetchall()
                    if rows:
                        return [(self._row_chunk(r), 1.0 / (1.0 + max(0.0, float(r["rank"])))) for r in rows]
                except sqlite3.OperationalError:
                    pass
            # Пустой FTS-результат не обрывает retrieval — падаем на token-overlap.
        # Portable fallback: token overlap.
        q = {x.lower() for x in query.split() if len(x) > 1}
        if not q:
            return []
        sql = "SELECT * FROM chunks" + (" WHERE project=?" if project else "")
        rows = self.db.execute(sql, (project,) if project else ()).fetchall()
        scored: list[tuple[Chunk,float]] = []
        for r in rows:
            words = {x.lower().strip(".,:;!?()[]{}") for x in r["text"].split()}
            score = len(q & words) / max(1, len(q))
            if score > 0:
                scored.append((self._row_chunk(r), score))
        return sorted(scored, key=lambda x: x[1], reverse=True)[:limit]

    def all_vector_chunks(self, project: str = "") -> list[tuple[Chunk, list[float]]]:
        sql = "SELECT * FROM chunks WHERE vector IS NOT NULL" + (" AND project=?" if project else "")
        rows = self.db.execute(sql, (project,) if project else ()).fetchall()
        return [(self._row_chunk(r), json.loads(r["vector"])) for r in rows]

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        row = self.db.execute("SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)).fetchone()
        return self._row_chunk(row) if row else None

    def upsert_memory(self, m: MemoryRecord) -> None:
        self.db.execute(
            """INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(memory_id) DO UPDATE SET kind=excluded.kind,text=excluded.text,project=excluded.project,status=excluded.status,
            confidence=excluded.confidence,importance=excluded.importance,source_refs=excluded.source_refs,updated_at=excluded.updated_at,
            last_verified_at=excluded.last_verified_at,supersedes=excluded.supersedes,contradicted_by=excluded.contradicted_by,metadata=excluded.metadata""",
            (m.memory_id,m.kind.value,m.text,m.project,m.status.value,m.confidence,m.importance,json_dumps(m.source_refs),
             m.created_at,m.updated_at,m.last_verified_at,json_dumps(m.supersedes),json_dumps(m.contradicted_by),json_dumps(m.metadata)),
        )
        self.db.commit()

    def memories(self, project: str = "", statuses: tuple[MemoryStatus,...] = (MemoryStatus.ACTIVE,)) -> list[MemoryRecord]:
        placeholders = ",".join("?" for _ in statuses)
        sql = f"SELECT * FROM memories WHERE status IN ({placeholders})"
        params: list[object] = [x.value for x in statuses]
        if project:
            sql += " AND project=?"; params.append(project)
        rows = self.db.execute(sql, params).fetchall()
        return [self._row_memory(r) for r in rows]

    @staticmethod
    def _row_chunk(r: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=r["chunk_id"],document_id=r["document_id"],text=r["text"],ordinal=r["ordinal"],heading=r["heading"],
            token_count=r["token_count"],project=r["project"],source_type=r["source_type"],source_uri=r["source_uri"],
            created_at=r["created_at"],updated_at=r["updated_at"],importance=r["importance"],freshness=r["freshness"],
            sensitivity=r["sensitivity"],content_hash=r["content_hash"],metadata=json.loads(r["metadata"] or "{}")
        )

    @staticmethod
    def _row_memory(r: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=r["memory_id"],kind=MemoryKind(r["kind"]),text=r["text"],project=r["project"],status=MemoryStatus(r["status"]),
            confidence=r["confidence"],importance=r["importance"],source_refs=json.loads(r["source_refs"]),created_at=r["created_at"],
            updated_at=r["updated_at"],last_verified_at=r["last_verified_at"],supersedes=json.loads(r["supersedes"]),
            contradicted_by=json.loads(r["contradicted_by"]),metadata=json.loads(r["metadata"] or "{}")
        )
