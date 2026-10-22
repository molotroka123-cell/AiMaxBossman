"""SQLite store: immutable source snapshots, versioned tenders, approvals and a hash-chained audit log."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .provenance import is_real, unverified_reason

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
CREATE TABLE IF NOT EXISTS contacts(id INTEGER PRIMARY KEY, slug TEXT UNIQUE, doc TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS match_feedback(id INTEGER PRIMARY KEY, tender_key TEXT NOT NULL, contact_id INTEGER NOT NULL,
  vote TEXT NOT NULL, reason TEXT, doc TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)


def _locked(fn):
    """Every Store call runs under one re-entrant lock: a single sqlite connection is shared by FastAPI worker threads,
    and a read racing a write on it gave random 500s / false 'not found' (found by the UI review)."""
    import functools

    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper



def freshen(doc: dict, today=None) -> dict:
    """Read-time actuality (owner 22.10: listed tenders were dead). Never trust the ingest-time status or URL:
    an `open` notice whose deadline has passed is shown as `closed`, and TED links use the form that resolves."""
    from datetime import date
    out = dict(doc)
    today = today or date.today()
    if out.get("status") == "open" and out.get("deadline"):
        try:
            if date.fromisoformat(str(out["deadline"])[:10]) < today:
                out["status"] = "closed"
                out["status_note"] = "срок подачи прошёл"
        except ValueError:
            pass
    if out.get("source_id") == "ted" and out.get("official_notice_id") and "/-/detail/" not in str(out.get("source_url", "")):
        out["source_url"] = f"https://ted.europa.eu/en/notice/-/detail/{out['official_notice_id']}"
    return out


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- snapshots (immutable, deduplicated by content hash) ----
    @_locked
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
    @_locked
    def upsert_tender(self, doc: dict) -> tuple[str, int, bool]:
        why = unverified_reason(doc)
        if why:                                                 # write-path guard: no record without provenance enters the store
            raise ValueError(f"UNVERIFIED_RECORD:{why}")
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

    @_locked
    def latest_tenders(self) -> list[dict]:
        rows = self.db.execute("SELECT doc FROM tenders t WHERE version=(SELECT MAX(version) FROM tenders WHERE key=t.key)").fetchall()
        return [freshen(d) for d in (json.loads(r["doc"]) for r in rows) if is_real(d)]

    @_locked
    def get_tender(self, key: str) -> dict | None:
        row = self.db.execute("SELECT doc FROM tenders WHERE key=? ORDER BY version DESC LIMIT 1", (key,)).fetchone()
        doc = json.loads(row["doc"]) if row else None
        return freshen(doc) if doc is not None and is_real(doc) else None

    @_locked
    def tender_versions(self, key: str) -> list[dict]:
        docs = (json.loads(r["doc"]) for r in self.db.execute("SELECT doc FROM tenders WHERE key=? ORDER BY version", (key,)))
        return [d for d in docs if is_real(d)]

    # ---- generic JSON docs ----
    @_locked
    def kv_get(self, k: str, default=None):
        row = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    @_locked
    def kv_set(self, k: str, v) -> None:
        with self._lock:
            self.db.execute("INSERT INTO kv(k,v,updated_at) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                            (k, _dump(v), now()))
            self.db.commit()

    @_locked
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

    @_locked
    def docs(self, table: str, where: str = "", args: tuple = ()) -> list[dict]:
        assert table in ("assessments", "decisions", "outcomes", "deals")
        cols = "id, doc, created_at" + ("" if table == "deals" else ", tender_key")
        rows = self.db.execute(f"SELECT {cols} FROM {table} {where} ORDER BY id", args).fetchall()
        return [dict(json.loads(r["doc"]), id=r["id"], created_at=r["created_at"], **({} if table == "deals" else {"tender_key": r["tender_key"]})) for r in rows]

    # ---- contacts (companies met at events) and owner feedback on tender <-> company matches ----
    @staticmethod
    def _contact(r) -> dict:
        return dict(json.loads(r["doc"]), id=r["id"], slug=r["slug"], created_at=r["created_at"], updated_at=r["updated_at"])

    @_locked
    def add_contact(self, doc: dict, slug: str | None = None) -> tuple[int, bool]:
        """(id, created). With a slug the insert is idempotent: an existing record is returned untouched (never overwritten)."""
        with self._lock:
            if slug:
                row = self.db.execute("SELECT id FROM contacts WHERE slug=?", (slug,)).fetchone()
                if row:
                    return row["id"], False
            ts = now()
            cur = self.db.execute("INSERT INTO contacts(slug,doc,created_at,updated_at) VALUES(?,?,?,?)", (slug, _dump(doc), ts, ts))
            self.db.commit()
            return cur.lastrowid, True

    @_locked
    def get_contact(self, cid: int) -> dict | None:
        r = self.db.execute("SELECT * FROM contacts WHERE id=?", (cid,)).fetchone()
        return self._contact(r) if r else None

    @_locked
    def list_contacts(self) -> list[dict]:
        return [self._contact(r) for r in self.db.execute("SELECT * FROM contacts ORDER BY id").fetchall()]

    @_locked
    def update_contact(self, cid: int, doc: dict) -> bool:
        with self._lock:
            cur = self.db.execute("UPDATE contacts SET doc=?, updated_at=? WHERE id=?", (_dump(doc), now(), cid))
            self.db.commit()
            return cur.rowcount == 1

    @_locked
    def add_match_feedback(self, tender_key: str, contact_id: int, vote: str, reason: str | None, doc: dict, actor: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO match_feedback(tender_key,contact_id,vote,reason,doc,actor,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (tender_key, contact_id, vote, reason, _dump(doc), actor, now()))
            self.db.commit()
            return cur.lastrowid

    @_locked
    def match_feedback(self) -> list[dict]:
        """Every vote ever given, oldest first (nothing is deleted; the latest vote per pair is the effective label)."""
        rows = self.db.execute("SELECT * FROM match_feedback ORDER BY id").fetchall()
        return [dict(json.loads(r["doc"]), id=r["id"], tender_key=r["tender_key"], contact_id=r["contact_id"], vote=r["vote"],
                     reason=r["reason"], actor=r["actor"], created_at=r["created_at"]) for r in rows]

    # ---- gate reviews signed by a human ----
    @_locked
    def add_review(self, tender_key: str, gate_id: str, decision: str, reviewer: str, note: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO reviews(tender_key,gate_id,decision,reviewer,note,created_at) VALUES(?,?,?,?,?,?)",
                                  (tender_key, gate_id, decision, reviewer, note, now()))
            self.db.commit()
            return cur.lastrowid

    @_locked
    def reviews(self, tender_key: str) -> dict[str, dict]:
        """Latest review per gate."""
        out: dict[str, dict] = {}
        for r in self.db.execute("SELECT * FROM reviews WHERE tender_key=? ORDER BY id", (tender_key,)):
            out[r["gate_id"]] = dict(r)
        return out

    # ---- audit: hash chain, any edit breaks verification ----
    @_locked
    def audit(self, actor: str, event: str, payload: dict | None = None) -> dict:
        with self._lock:
            # BEGIN IMMEDIATE: reading the last hash and appending are one write transaction, so a second process on the
            # same war.db (the seed CLI next to the running server) waits instead of forking the chain.
            own = not self.db.in_transaction
            if own:
                self.db.execute("BEGIN IMMEDIATE")
            try:
                prev = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
                prev_hash = prev["hash"] if prev else "0" * 64
                ts, text = now(), _dump(payload or {})
                h = sha(f"{prev_hash}|{ts}|{actor}|{event}|{text}")
                cur = self.db.execute("INSERT INTO audit(ts,actor,event,payload,prev_hash,hash) VALUES(?,?,?,?,?,?)",
                                      (ts, actor, event, text, prev_hash, h))
                self.db.commit()
            except BaseException:
                if own:
                    self.db.rollback()
                raise
            return dict(seq=cur.lastrowid, ts=ts, actor=actor, event=event, hash=h)

    @_locked
    def audit_events(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT seq,ts,actor,event,payload,hash FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(seq=r["seq"], ts=r["ts"], actor=r["actor"], event=r["event"], payload=json.loads(r["payload"]), hash=r["hash"]) for r in rows]

    @_locked
    def verify_audit(self) -> dict:
        prev = "0" * 64
        for r in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            if r["prev_hash"] != prev or sha(f"{prev}|{r['ts']}|{r['actor']}|{r['event']}|{r['payload']}") != r["hash"]:
                return {"ok": False, "broken_at": r["seq"]}
            prev = r["hash"]
        return {"ok": True, "broken_at": None}

    # ---- approvals ----
    @_locked
    def request_approval(self, action: str, payload: dict, requested_by: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO approvals(action,payload,status,requested_by,requested_at) VALUES(?,?,?,?,?)",
                                  (action, _dump(payload), "pending", requested_by, now()))
            self.db.commit()
            return cur.lastrowid

    @_locked
    def get_approval(self, approval_id: int) -> dict | None:
        r = self.db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(r, payload=json.loads(r["payload"])) if r else None

    @_locked
    def list_approvals(self, status: str | None = None) -> list[dict]:
        rows = self.db.execute("SELECT * FROM approvals" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                               (status,) if status else ()).fetchall()
        return [dict(r, payload=json.loads(r["payload"])) for r in rows]

    @_locked
    def decide_approval(self, approval_id: int, approve: bool, decided_by: str, note: str) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,)).fetchone()
            if not r:
                return None
            self.db.execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, note=? WHERE id=?",
                            ("approved" if approve else "rejected", decided_by, now(), note, approval_id))
            self.db.commit()
        return self.get_approval(approval_id)

    # ---- real-data gate: archive (never destroy) everything without provenance ----
    @_locked
    def count_tenders(self) -> dict:
        """Counts straight from the table (not through the provenance filter): rows = all versions, keys = distinct notices."""
        r = self.db.execute("SELECT COUNT(*) n, COUNT(DISTINCT key) k FROM tenders").fetchone()
        return {"rows": r["n"], "keys": r["k"], "visible_keys": len(self.latest_tenders())}

    @_locked
    def archive_unverified(self, archive_dir: str | Path, stamp: str) -> dict:
        """Move tender versions, stored conflict markers and dependent learning docs that lack provenance (demo / synthetic / no source URL
        or fetched_at) into archive_dir/demo-removed-<stamp>.jsonl, then delete them from the live tables. Returns exact counts."""
        before = self.count_tenders()
        lines, report = [], {"tenders_rows": 0, "tender_keys": 0, "conflicts": 0, "conflicts_demo_key": 0, "dependent_docs": 0, "reasons": {}}
        bad_keys: set[str] = set()
        with self._lock:
            for r in self.db.execute("SELECT id,key,version,doc FROM tenders").fetchall():
                doc = json.loads(r["doc"])
                why = unverified_reason(doc)
                if why:
                    lines.append({"table": "tenders", "key": r["key"], "version": r["version"], "reason": why, "doc": doc})
                    bad_keys.add(r["key"])
                    report["tenders_rows"] += 1
                    report["reasons"][why] = report["reasons"].get(why, 0) + 1
                    self.db.execute("DELETE FROM tenders WHERE id=?", (r["id"],))
            report["tender_keys"] = len(bad_keys)
            for table in ("assessments", "decisions", "outcomes"):
                for r in self.db.execute(f"SELECT id,tender_key,doc FROM {table}").fetchall():
                    if r["tender_key"] in bad_keys or json.loads(r["doc"]).get("demo"):
                        lines.append({"table": table, "id": r["id"], "tender_key": r["tender_key"], "reason": "depends_on_unverified_tender", "doc": json.loads(r["doc"])})
                        self.db.execute(f"DELETE FROM {table} WHERE id=?", (r["id"],))
                        report["dependent_docs"] += 1
            demo_kv = self.kv_get("conflicts_demo")
            if demo_kv is not None:                              # old DEMO conflict markers lived under their own key
                lines.append({"table": "kv", "key": "conflicts_demo", "reason": "demo_flag", "doc": demo_kv})
                report["conflicts_demo_key"] = len(demo_kv) if isinstance(demo_kv, list) else 1
                self.db.execute("DELETE FROM kv WHERE k='conflicts_demo'")
            conf = self.kv_get("conflicts")
            keep = None
            if isinstance(conf, list):
                keep = [c for c in conf if is_real(c)]
                for c in conf:
                    if not is_real(c):
                        lines.append({"table": "kv", "key": "conflicts", "reason": unverified_reason(c), "doc": c})
                report["conflicts"] = len(conf) - len(keep)
            if lines:                                            # the archive is written BEFORE anything is committed away
                try:
                    d = Path(archive_dir)
                    d.mkdir(parents=True, exist_ok=True)
                    path = d / f"demo-removed-{stamp}.jsonl"
                    with path.open("a", encoding="utf-8") as fh:
                        for ln in lines:
                            fh.write(json.dumps(ln, ensure_ascii=False, default=str) + "\n")
                except OSError:
                    self.db.rollback()
                    raise
                report["archive_file"] = str(path)
            self.db.commit()
            if keep is not None and report["conflicts"]:
                self.kv_set("conflicts", keep)
        report["before"], report["after"] = before, self.count_tenders()
        report["archived_total"] = len(lines)
        return report
