"""SQLite store: source registry with rights, the Shorts queue with an explicit state machine, kv cache and a
hash-chained audit log (pattern and audit code adapted from apps/war-terminal/warterminal/store.py)."""
from __future__ import annotations

import functools
import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, doc TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shorts(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, state TEXT NOT NULL, doc TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL,
  payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""

KINDS = ("own", "generated", "licensed")
STATES = ("draft", "ready", "approved_by_owner", "scheduled", "published", "rejected")
# the only allowed queue moves; anything else raises StateError
TRANSITIONS = {
    "draft": {"ready", "rejected"},
    "ready": {"approved_by_owner", "rejected", "draft"},
    "approved_by_owner": {"scheduled", "published", "rejected", "ready"},
    "scheduled": {"published", "rejected"},
    "published": set(),
    "rejected": set(),
}


class StateError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrapper


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------------------------------------------------------------- sources
    @_locked
    def add_source(self, doc: dict) -> dict:
        if doc.get("kind") not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        sid = doc.get("id") or ("src_" + uuid.uuid4().hex[:10])
        full = dict(doc, id=sid, approved_by_owner=False, rights_approval=None,   # never approved at registration
                    consent_recorded=False, consent=None, people_in_frame=bool(doc.get("people_in_frame")))
        t = now()
        self.db.execute("INSERT INTO sources(id,doc,created_at,updated_at) VALUES(?,?,?,?)", (sid, _dump(full), t, t))
        self.db.commit()
        return self.get_source(sid)

    @_locked
    def get_source(self, sid: str) -> dict | None:
        r = self.db.execute("SELECT doc, created_at FROM sources WHERE id=?", (sid,)).fetchone()
        return dict(json.loads(r["doc"]), created_at=r["created_at"]) if r else None

    @_locked
    def sources(self) -> list[dict]:
        rows = self.db.execute("SELECT doc, created_at FROM sources ORDER BY created_at").fetchall()
        return [dict(json.loads(r["doc"]), created_at=r["created_at"]) for r in rows]

    @_locked
    def update_source(self, sid: str, changes: dict) -> dict:
        cur = self.get_source(sid)
        if cur is None:
            raise KeyError(sid)
        protected = {"approved_by_owner", "rights_approval", "id", "consent_recorded", "consent", "people_detected"}
        bad = protected & set(changes)
        if bad:
            raise PermissionError(f"fields {sorted(bad)} change only through the owner rights/consent action")
        if "people_in_frame" in changes and not changes["people_in_frame"] and (cur.get("people_in_frame") or cur.get("people_detected")):
            raise PermissionError("people_in_frame снимает только владелец (лица в кадре уже отмечены)")
        cur.pop("created_at", None)
        cur.update(changes)
        if {"kind", "rights_note", "licence_url", "licence_doc", "path"} & set(changes):
            cur["approved_by_owner"], cur["rights_approval"] = False, None    # rights facts changed -> approval void
        if "path" in changes:
            cur["consent_recorded"], cur["consent"] = False, None             # other video -> other people -> consent void
        self.db.execute("UPDATE sources SET doc=?, updated_at=? WHERE id=?", (_dump(cur), now(), sid))
        self.db.commit()
        return self.get_source(sid)

    @_locked
    def _set_rights(self, sid: str, approved: bool, approval: dict | None) -> dict:
        """Called ONLY by the owner rights action (api.approve_rights / cli approve-rights)."""
        cur = self.get_source(sid)
        if cur is None:
            raise KeyError(sid)
        cur.pop("created_at", None)
        cur["approved_by_owner"], cur["rights_approval"] = bool(approved), approval
        self.db.execute("UPDATE sources SET doc=?, updated_at=? WHERE id=?", (_dump(cur), now(), sid))
        self.db.commit()
        return self.get_source(sid)

    @_locked
    def _set_consent(self, sid: str, recorded: bool, consent: dict | None) -> dict:
        """Called ONLY by the owner consent action (api.record_consent)."""
        cur = self.get_source(sid)
        if cur is None:
            raise KeyError(sid)
        cur.pop("created_at", None)
        cur["consent_recorded"], cur["consent"] = bool(recorded), consent
        self.db.execute("UPDATE sources SET doc=?, updated_at=? WHERE id=?", (_dump(cur), now(), sid))
        self.db.commit()
        return self.get_source(sid)

    @_locked
    def mark_people_detected(self, sid: str, evidence: dict) -> dict:
        """Face detector found people: switch people_in_frame ON (detection can never switch it off)."""
        cur = self.get_source(sid)
        if cur is None:
            raise KeyError(sid)
        cur.pop("created_at", None)
        cur["people_in_frame"], cur["people_detected"] = True, evidence
        self.db.execute("UPDATE sources SET doc=?, updated_at=? WHERE id=?", (_dump(cur), now(), sid))
        self.db.commit()
        return self.get_source(sid)

    # ---------------------------------------------------------------- shorts queue
    @_locked
    def add_short(self, source_id: str, doc: dict, state: str = "draft") -> dict:
        if state not in ("draft", "ready"):
            raise StateError("new items start as draft or ready")
        iid = doc.get("id") or ("sh_" + uuid.uuid4().hex[:10])
        t = now()
        full = dict(doc, id=iid, source_id=source_id, state=state, history=[{"state": state, "at": t}])
        self.db.execute("INSERT INTO shorts(id,source_id,state,doc,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                        (iid, source_id, state, _dump(full), t, t))
        self.db.commit()
        return self.get_short(iid)

    @_locked
    def get_short(self, iid: str) -> dict | None:
        r = self.db.execute("SELECT doc, created_at, updated_at FROM shorts WHERE id=?", (iid,)).fetchone()
        return dict(json.loads(r["doc"]), created_at=r["created_at"], updated_at=r["updated_at"]) if r else None

    @_locked
    def shorts(self, state: str | None = None) -> list[dict]:
        q = "SELECT doc, created_at, updated_at FROM shorts" + (" WHERE state=?" if state else "") + " ORDER BY created_at"
        rows = self.db.execute(q, (state,) if state else ()).fetchall()
        return [dict(json.loads(r["doc"]), created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows]

    @_locked
    def update_short(self, iid: str, changes: dict) -> dict:
        cur = self.get_short(iid)
        if cur is None:
            raise KeyError(iid)
        if "state" in changes:
            raise StateError("use transition() to change state")
        for k in ("created_at", "updated_at"):
            cur.pop(k, None)
        cur.update(changes)
        self.db.execute("UPDATE shorts SET doc=?, updated_at=? WHERE id=?", (_dump(cur), now(), iid))
        self.db.commit()
        return self.get_short(iid)

    @_locked
    def transition(self, iid: str, to: str, actor: str, note: str = "", **extra) -> dict:
        cur = self.get_short(iid)
        if cur is None:
            raise KeyError(iid)
        frm = cur["state"]
        if to not in TRANSITIONS.get(frm, set()):
            raise StateError(f"переход {frm} -> {to} запрещён")
        for k in ("created_at", "updated_at"):
            cur.pop(k, None)
        t = now()
        cur.update(extra)
        cur["state"] = to
        cur.setdefault("history", []).append({"state": to, "at": t, "by": actor, "note": note})
        self.db.execute("UPDATE shorts SET state=?, doc=?, updated_at=? WHERE id=?", (to, _dump(cur), t, iid))
        self.db.commit()
        return self.get_short(iid)

    # ---------------------------------------------------------------- kv
    @_locked
    def kv_get(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r["v"]) if r else default

    @_locked
    def kv_set(self, k: str, v) -> None:
        self.db.execute("INSERT INTO kv(k,v,updated_at) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                        (k, _dump(v), now()))
        self.db.commit()

    # ---------------------------------------------------------------- audit (hash chain, any edit breaks verification)
    @_locked
    def audit(self, actor: str, event: str, payload: dict | None = None) -> dict:
        prev = self.db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else "0" * 64
        ts, text = now(), _dump(payload or {})
        h = sha(f"{prev_hash}|{ts}|{actor}|{event}|{text}")
        cur = self.db.execute("INSERT INTO audit(ts,actor,event,payload,prev_hash,hash) VALUES(?,?,?,?,?,?)",
                              (ts, actor, event, text, prev_hash, h))
        self.db.commit()
        return dict(seq=cur.lastrowid, ts=ts, actor=actor, event=event, hash=h)

    @_locked
    def audit_events(self, limit: int = 100) -> list[dict]:
        rows = self.db.execute("SELECT seq,ts,actor,event,payload,hash FROM audit ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [dict(seq=r["seq"], ts=r["ts"], actor=r["actor"], event=r["event"], payload=json.loads(r["payload"]), hash=r["hash"]) for r in rows]

    @_locked
    def verify_audit(self) -> dict:
        prev = "0" * 64
        n = 0
        for r in self.db.execute("SELECT * FROM audit ORDER BY seq"):
            if r["prev_hash"] != prev or sha(f"{prev}|{r['ts']}|{r['actor']}|{r['event']}|{r['payload']}") != r["hash"]:
                return {"ok": False, "broken_at": r["seq"], "events": n}
            prev = r["hash"]
            n += 1
        return {"ok": True, "broken_at": None, "events": n}
