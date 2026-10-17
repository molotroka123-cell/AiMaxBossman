"""SQLite store: immutable source snapshots, versioned tenders, approvals and a hash-chained audit log."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, fetched_at TEXT NOT NULL,
  url TEXT NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(source_id, sha256));
CREATE TABLE IF NOT EXISTS tenders(id INTEGER PRIMARY KEY, key TEXT NOT NULL, version INTEGER NOT NULL,
  doc TEXT NOT NULL, retrieved_at TEXT NOT NULL, UNIQUE(key, version));
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY, tender_key TEXT NOT NULL, created_at TEXT NOT NULL,
  dry_run INTEGER NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, tender_key TEXT NOT NULL, gate_id TEXT NOT NULL,
  decision TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL, requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
  payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, tender_key TEXT NOT NULL, doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outcomes(id INTEGER PRIMARY KEY, tender_key TEXT NOT NULL, doc TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- snapshots (immutable, deduplicated by content hash) ----
    def save_snapshot(self, source_id: str, url: str, payload: object) -> tuple[int, str, bool]:
        text = _dump(payload)
        digest = sha(text)
        with self._lock:
            row = self.db.execute("SELECT id FROM snapshots WHERE source_id=? AND sha256=?", (source_id, digest)).fetchone()
            if row:
                return row["id"], digest, False
            cur = self.db.execute("INSERT INTO snapshots(source_id,fetched_at,url,sha256,payload) VALUES(?,?,?,?,?)",
                                  (source_id, now(), url, digest, text))
            self.db.commit()
            return cur.lastrowid, digest, True

    # ---- tenders: key = jurisdiction + official_notice_id; a changed notice gets a new version ----
    def upsert_tender(self, doc: dict) -> tuple[str, int, bool]:
        key = f"{doc['jurisdiction']}:{doc['official_notice_id']}"
        volatile = ("retrieved_at", "version", "key", "snapshot_sha256")
        body = _dump({k: v for k, v in doc.items() if k not in volatile})
        with self._lock:
            row = self.db.execute("SELECT version, doc FROM tenders WHERE key=? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
            if row:
                old = _dump({k: v for k, v in json.loads(row["doc"]).items() if k not in volatile})
                if old == body:
                    return key, row["version"], False            # duplicate notice: nothing new
                version = row["version"] + 1
            else:
                version = 1
            full = dict(doc, key=key, version=version)
            self.db.execute("INSERT INTO tenders(key,version,doc,retrieved_at) VALUES(?,?,?,?)",
                            (key, version, _dump(full), doc["retrieved_at"]))
            self.db.commit()
            return key, version, True

    def latest_tenders(self) -> list[dict]:
        rows = self.db.execute("SELECT doc FROM tenders t WHERE version=(SELECT MAX(version) FROM tenders WHERE key=t.key)").fetchall()
        return [json.loads(r["doc"]) for r in rows]

    def get_tender(self, key: str) -> dict | None:
        row = self.db.execute("SELECT doc FROM tenders WHERE key=? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        return json.loads(row["doc"]) if row else None

    def tender_versions(self, key: str) -> list[dict]:
        return [json.loads(r["doc"]) for r in self.db.execute("SELECT doc FROM tenders WHERE key=? ORDER BY version", (key,))]

    # ---- generic JSON docs ----
    def kv_get(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def kv_set(self, k: str, v) -> None:
        with self._lock:
            self.db.execute("INSERT INTO kv(k,v,updated_at) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                            (k, _dump(v), now()))
            self.db.commit()

    def add_doc(self, table: str, tender_key: str | None, doc: dict, dry_run: bool = True) -> int:
        assert table in ("assessments", "decisions", "outcomes", "deals")
        with self._lock:
            if table == "assessments":
                cur = self.db.execute("INSERT INTO assessments(tender_key,created_at,dry_run,doc) VALUES(?,?,?,?)",
                                      (tender_key, now(), int(dry_run), _dump(doc)))
            elif table == "deals":
                cur = self.db.execute("INSERT INTO deals(doc,created_at) VALUES(?,?)", (_dump(doc), now()))
            else:
                cur = self.db.execute(f"INSERT INTO {table}(tender_key,doc,created_at) VALUES(?,?,?)", (tender_key, _dump(doc), now()))
            self.db.commit()
            return cur.lastrowid

    def docs(self, table: str, where: str = "", args: tuple = ()) -> list[dict]:
        assert table in ("assessments", "decisions", "outcomes", "deals")
        cols = "id, doc, created_at" + ("" if table == "deals" else ", tender_key")
        rows = self.db.execute(f"SELECT {cols} FROM {table} {where} ORDER BY id", args).fetchall()
        return [dict(json.loads(r["doc"]), id=r["id"], created_at=r["created_at"], **({} if table == "deals" else {"tender_key": r["tender_key"]})) for r in rows]

    # ---- gate reviews signed by a human ----
    def add_review(self, tender_key: str, gate_id: str, decision: str, reviewer: str, note: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO reviews(tender_key,gate_id,decision,reviewer,note,created_at) VALUES(?,?,?,?,?,?)",
                                  (tender_key, gate_id, decision, reviewer, note, now()))
            self.db.commit()
            return cur.lastrowid

    def reviews(self, tender_key: str) -> dict[str, dict]:
        """Latest review per gate."""
        out: dict[str, dict] = {}
        for r in self.db.execute("SELECT * FROM reviews WHERE tender_key=? ORDER BY id", (tender_key,)):
            out[r["gate_id"]] = dict(r)
        return out

    # ---- audit: hash chain, any edit breaks verification ----
    def audit(self, actor: str, event: str, payload: dict | None = None) -> dict:
        with self._lock:
            prev = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev_hash = prev["hash"] if prev else "0" * 64
            ts, text = now(), _dump(payload or {})
            h = sha(f"{prev_hash}|{ts}|{actor}|{event}|{text}")
            cur = self.db.execute("INSERT INTO audit(ts,actor,event,payload,prev_hash,hash) VALUES(?,?,?,?,?,?)",
                                  (ts, actor, event, text, prev_hash, h))
            self.db.commit()
            return dict(seq=cur.lastrowid, ts=ts, actor=actor, event=event, hash=h)

    def audit_events(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT seq,ts,actor,event,payload,hash FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(seq=r["seq"], ts=r["ts"], actor=r["actor"], event=r["event"], payload=json.loads(r["payload"]), hash=r["hash"]) for r in rows]

    def verify_audit(self) -> dict:
        prev = "0" * 64
        for r in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            if r["prev_hash"] != prev or sha(f"{prev}|{r['ts']}|{r['actor']}|{r['event']}|{r['payload']}") != r["hash"]:
                return {"ok": False, "broken_at": r["seq"]}
            prev = r["hash"]
        return {"ok": True, "broken_at": None}

    # ---- approvals ----
    def request_approval(self, action: str, payload: dict, requested_by: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO approvals(action,payload,status,requested_by,requested_at) VALUES(?,?,?,?,?)",
                                  (action, _dump(payload), "pending", requested_by, now()))
            self.db.commit()
            return cur.lastrowid

    def get_approval(self, approval_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(r, payload=json.loads(r["payload"])) if r else None

    def list_approvals(self, status: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                               (status,) if status else ()).fetchall()
        return [dict(r, payload=json.loads(r["payload"])) for r in rows]

    def decide_approval(self, approval_id: int, approve: bool, decided_by: str, note: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
            if not r:
                return None
            self.db.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                            ("approved" if approve else "rejected", decided_by, now(), note, approval_id))
            self.db.commit()
        return self.get_approval(approval_id)
