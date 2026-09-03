"""Local SQLite. No Bossman imports. Bind 127.0.0.1 only at the HTTP layer."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import APP_ID


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, data_dir: Path | None = None):
        base = Path(data_dir or os.getenv("OSIRIS_DATA", str(Path.home() / ".bossman-apps" / APP_ID)))
        base.mkdir(parents=True, exist_ok=True)
        self.dir = base
        self.db_path = base / "osiris.db"
        self._lock = threading.RLock()
        self._init()

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def _init(self):
        with self.connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS grants(
                  id TEXT PRIMARY KEY,
                  body TEXT NOT NULL,
                  status TEXT NOT NULL,
                  expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS facts(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  subject TEXT NOT NULL,
                  predicate TEXT NOT NULL,
                  object TEXT NOT NULL,
                  passport TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes(
                  id TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  label TEXT NOT NULL,
                  attrs TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  src TEXT NOT NULL,
                  rel TEXT NOT NULL,
                  dst TEXT NOT NULL,
                  passport TEXT NOT NULL,
                  UNIQUE(src, rel, dst)
                );
                CREATE TABLE IF NOT EXISTS journal(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts TEXT NOT NULL,
                  event TEXT NOT NULL,
                  subject TEXT,
                  data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS robots_cache(
                  host TEXT PRIMARY KEY,
                  body TEXT NOT NULL,
                  fetched_at TEXT NOT NULL
                );
                """
            )

    def journal(self, event: str, subject: str | None, data: dict | None = None):
        with self._lock, self.connect() as c:
            c.execute(
                "INSERT INTO journal(ts,event,subject,data) VALUES(?,?,?,?)",
                (now_iso(), event, subject, json.dumps(data or {}, ensure_ascii=False)),
            )

    def journal_list(self, limit: int = 100) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM journal ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def grant_put(self, rec: dict):
        with self._lock, self.connect() as c:
            c.execute(
                """INSERT INTO grants(id,body,status,expires_at) VALUES(?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET body=excluded.body,status=excluded.status,expires_at=excluded.expires_at""",
                (rec["id"], json.dumps(rec, ensure_ascii=False), rec["status"], rec["expires_at"]),
            )

    def grant_get(self, gid: str) -> dict:
        with self.connect() as c:
            r = c.execute("SELECT body FROM grants WHERE id=?", (gid,)).fetchone()
        if not r:
            raise KeyError(gid)
        return json.loads(r["body"])

    def grant_list(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT body FROM grants ORDER BY expires_at DESC").fetchall()
        return [json.loads(r["body"]) for r in rows]

    def fact_insert(self, fact: dict) -> int:
        with self._lock, self.connect() as c:
            cur = c.execute(
                "INSERT INTO facts(subject,predicate,object,passport,created_at) VALUES(?,?,?,?,?)",
                (
                    fact["subject"],
                    fact["predicate"],
                    json.dumps(fact["object"], ensure_ascii=False),
                    json.dumps(fact["passport"], ensure_ascii=False),
                    now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def fact_list(self, subject: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM facts"
        args: tuple[Any, ...] = ()
        if subject:
            q += " WHERE subject=?"
            args = (subject,)
        q += " ORDER BY id DESC LIMIT ?"
        args = args + (min(max(limit, 1), 500),)
        with self.connect() as c:
            rows = c.execute(q, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["object"] = json.loads(d["object"])
            d["passport"] = json.loads(d["passport"])
            out.append(d)
        return out

    def node_upsert(self, nid: str, kind: str, label: str, attrs: dict | None = None):
        with self._lock, self.connect() as c:
            c.execute(
                """INSERT INTO nodes(id,kind,label,attrs,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,label=excluded.label,attrs=excluded.attrs,updated_at=excluded.updated_at""",
                (nid, kind, label, json.dumps(attrs or {}, ensure_ascii=False), now_iso()),
            )

    def edge_add(self, src: str, rel: str, dst: str, passport: dict):
        with self._lock, self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO edges(src,rel,dst,passport) VALUES(?,?,?,?)",
                (src, rel, dst, json.dumps(passport, ensure_ascii=False)),
            )

    def graph(self) -> dict:
        with self.connect() as c:
            nodes = c.execute("SELECT * FROM nodes").fetchall()
            edges = c.execute("SELECT src,rel,dst FROM edges").fetchall()
        return {
            "nodes": [
                {"id": r["id"], "kind": r["kind"], "label": r["label"], "attrs": json.loads(r["attrs"])}
                for r in nodes
            ],
            "edges": [{"src": r["src"], "rel": r["rel"], "dst": r["dst"]} for r in edges],
        }

    def robots_get(self, host: str) -> str | None:
        with self.connect() as c:
            r = c.execute("SELECT body FROM robots_cache WHERE host=?", (host,)).fetchone()
        return r["body"] if r else None

    def robots_put(self, host: str, body: str):
        with self._lock, self.connect() as c:
            c.execute(
                """INSERT INTO robots_cache(host,body,fetched_at) VALUES(?,?,?)
                   ON CONFLICT(host) DO UPDATE SET body=excluded.body,fetched_at=excluded.fetched_at""",
                (host, body, now_iso()),
            )

    def metrics(self) -> dict:
        with self.connect() as c:
            facts = c.execute("SELECT COUNT(*) n FROM facts").fetchone()["n"]
            grants = c.execute("SELECT status, COUNT(*) n FROM grants GROUP BY status").fetchall()
            nodes = c.execute("SELECT COUNT(*) n FROM nodes").fetchone()["n"]
            journal = c.execute("SELECT COUNT(*) n FROM journal").fetchone()["n"]
        g = {r["status"]: r["n"] for r in grants}
        return {"facts": facts, "nodes": nodes, "journal": journal, "grants": g}
