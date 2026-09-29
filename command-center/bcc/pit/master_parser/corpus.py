"""Normalized, deduplicated, append-only conversation corpus per participant.

``<data>/pit-v1.7/master-parser/corpus.sqlite3``. Message text is sealed with
the PIT store key (same Fernet at-rest protection as Jeff's own store). Rows are
never deleted or rewritten: collecting again only adds new rows or new
provenance references to an existing row.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

from .sources import RawMessage

DEDUPE_WINDOW_SECONDS = 900.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  uid TEXT PRIMARY KEY, person_key TEXT NOT NULL, surface TEXT NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, role TEXT NOT NULL,
  ts REAL NOT NULL, kind TEXT NOT NULL, text_hash TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '', platform_message_id TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL, collected_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_person ON messages(person_key, ts);
CREATE INDEX IF NOT EXISTS messages_hash ON messages(person_key, role, text_hash);
CREATE TABLE IF NOT EXISTS refs(
  source TEXT NOT NULL, source_ref TEXT NOT NULL, uid TEXT NOT NULL,
  PRIMARY KEY(source, source_ref));
CREATE INDEX IF NOT EXISTS refs_uid ON refs(uid);
CREATE TABLE IF NOT EXISTS cursors(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyzed(
  uid TEXT NOT NULL, extractor TEXT NOT NULL, run_id TEXT NOT NULL, at REAL NOT NULL,
  PRIMARY KEY(uid, extractor));
CREATE TABLE IF NOT EXISTS provenance(
  fact_id TEXT NOT NULL, person_key TEXT NOT NULL, run_id TEXT NOT NULL,
  uids TEXT NOT NULL, PRIMARY KEY(person_key, fact_id));
"""


def normalize(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()[:4000]


class Corpus:
    def __init__(self, db: sqlite3.Connection, seal: Callable[[str], str],
                 unseal: Callable[[str], str]):
        self.db = db
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._seal, self._unseal = seal, unseal

    @classmethod
    def open(cls, path: Path, seal, unseal) -> "Corpus":
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return cls(db, seal, unseal)

    @classmethod
    def in_memory(cls, seal, unseal, *, copy_from: Path | None = None) -> "Corpus":
        """Dry run: a private copy; the real corpus file is only read."""
        db = sqlite3.connect(":memory:", isolation_level=None)
        if copy_from is not None and Path(copy_from).is_file():
            src = sqlite3.connect(Path(copy_from).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                src.backup(db)
            finally:
                src.close()
        return cls(db, seal, unseal)

    def close(self) -> None:
        self.db.close()

    # -- cursors ------------------------------------------------------------------------
    def cursors(self) -> dict[str, str]:
        return {row[0]: row[1] for row in self.db.execute("SELECT name, value FROM cursors")}

    # -- intake -------------------------------------------------------------------------
    def add_batch(self, messages: list[RawMessage], cursors: dict[str, str]) -> tuple[int, int]:
        """Insert one source pass atomically together with its advanced cursors."""
        new = dup = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for message in messages:
                added = self._add(message)
                if added is None:
                    continue
                if added:
                    new += 1
                else:
                    dup += 1
            for name, value in cursors.items():
                self.db.execute("INSERT INTO cursors VALUES(?,?) ON CONFLICT(name) "
                                "DO UPDATE SET value=excluded.value", (name, str(value)))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return new, dup

    def _add(self, m: RawMessage) -> bool | None:
        """True: new message; False: duplicate linked as provenance; None: already known."""
        if self.db.execute("SELECT 1 FROM refs WHERE source=? AND source_ref=?",
                           (m.source, m.source_ref)).fetchone():
            return None
        norm = normalize(m.text)
        text_hash = hashlib.sha256(
            f"{m.person_key}\0{m.role}\0{norm}".encode("utf-8")).hexdigest()
        if norm:
            for row in self.db.execute(
                    "SELECT uid, ts FROM messages WHERE person_key=? AND role=? AND text_hash=? "
                    "AND ts BETWEEN ? AND ? ORDER BY abs(ts-?)",
                    (m.person_key, m.role, text_hash, m.ts - DEDUPE_WINDOW_SECONDS,
                     m.ts + DEDUPE_WINDOW_SECONDS, m.ts)):
                claimed = self.db.execute("SELECT 1 FROM refs WHERE uid=? AND source=?",
                                          (row["uid"], m.source)).fetchone()
                if claimed:
                    continue   # that row already stands for another message of this source
                self.db.execute("INSERT INTO refs VALUES(?,?,?)", (m.source, m.source_ref, row["uid"]))
                if m.platform_message_id:
                    self.db.execute("UPDATE messages SET platform_message_id=? WHERE uid=? "
                                    "AND platform_message_id=''", (m.platform_message_id, row["uid"]))
                return False
        uid = hashlib.sha256(f"{m.person_key}\0{m.source}\0{m.source_ref}".encode("utf-8")).hexdigest()[:24]
        body = self._seal(json.dumps({"text": m.text[:20000]}, ensure_ascii=False))
        self.db.execute(
            "INSERT INTO messages(uid, person_key, surface, source, source_ref, role, ts, kind, "
            "text_hash, label, platform_message_id, body, collected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, m.person_key, m.surface, m.source, m.source_ref, m.role, m.ts, m.kind,
             text_hash, m.label, m.platform_message_id, body, time.time()))
        self.db.execute("INSERT INTO refs VALUES(?,?,?)", (m.source, m.source_ref, uid))
        return True

    # -- reads (always scoped to one participant) ----------------------------------------
    def persons(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT person_key, count(*) n, max(label) label, group_concat(DISTINCT surface) surfaces "
            "FROM messages GROUP BY person_key ORDER BY person_key").fetchall()
        return [{"person_key": r["person_key"], "messages": r["n"], "label": r["label"] or "",
                 "surfaces": sorted(set((r["surfaces"] or "").split(",")) - {""})} for r in rows]

    def timeline(self, person_key: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT uid, source, source_ref, role, ts, kind, label, platform_message_id, body "
            "FROM messages WHERE person_key=? ORDER BY ts, uid", (person_key,)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["text"] = json.loads(self._unseal(item.pop("body")))["text"]
            out.append(item)
        return out

    def analyzed_uids(self, person_key: str, extractor: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT a.uid FROM analyzed a JOIN messages m ON m.uid=a.uid "
            "WHERE m.person_key=? AND a.extractor=?", (person_key, extractor))}

    def mark_analyzed(self, uids: list[str], extractor: str, run_id: str,
                      provenance: list[tuple[str, str, list[str]]] = ()) -> None:
        now = time.time()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.executemany("INSERT OR IGNORE INTO analyzed VALUES(?,?,?,?)",
                                [(uid, extractor, run_id, now) for uid in uids])
            self.db.executemany("INSERT OR IGNORE INTO provenance VALUES(?,?,?,?)",
                                [(fact_id, person_key, run_id, json.dumps(ev))
                                 for fact_id, person_key, ev in provenance])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def count(self) -> int:
        return int(self.db.execute("SELECT count(*) FROM messages").fetchone()[0])
