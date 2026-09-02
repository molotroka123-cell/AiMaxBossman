"""Fail-closed durable safety state for Apprentice live capabilities.

SQLite/WAL is deliberately the single local fallback.  Values are small,
sanitized safety facts only; a claimed effect is *not* automatically released
after a crash because doing so could duplicate an external action.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable


class DurableSafetyError(RuntimeError):
    """Persistence failed: callers must deny an external effect."""


class DurableSafetyStore:
    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path, self.clock = str(path), clock
        self._lock = threading.RLock()
        try:
            self._db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
              CREATE TABLE IF NOT EXISTS effects (id TEXT PRIMARY KEY, state TEXT NOT NULL, receipt TEXT, at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS cooldowns (recipient TEXT PRIMARY KEY, until_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS blocks (recipient TEXT PRIMARY KEY, reason TEXT NOT NULL, at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS teacher_outcomes (key TEXT PRIMARY KEY, score REAL NOT NULL, samples INTEGER NOT NULL, detail TEXT, at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS pending_approvals (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS issued_approvals (nonce TEXT PRIMARY KEY, digest TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL, expires_at REAL, at REAL NOT NULL);
            """)
            self._db.commit()
        except sqlite3.Error as exc:
            raise DurableSafetyError(f"cannot open safety store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _tx(self, fn):
        try:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                value = fn()
                self._db.commit()
                return value
        except sqlite3.Error as exc:
            try: self._db.rollback()
            except sqlite3.Error: pass
            raise DurableSafetyError(f"safety transaction failed: {exc}") from exc

    def claim_side_effect(self, side_effect_id: str) -> tuple[bool, dict | None]:
        def claim():
            row = self._db.execute("SELECT state, receipt FROM effects WHERE id=?", (side_effect_id,)).fetchone()
            if row:
                return False, json.loads(row[1]) if row[0] == "complete" and row[1] else None
            self._db.execute("INSERT INTO effects(id,state,at) VALUES(?,?,?)", (side_effect_id, "claimed", self.clock()))
            return True, None
        return self._tx(claim)

    def complete_side_effect(self, side_effect_id: str, receipt: dict) -> None:
        clean = json.dumps(dict(receipt), sort_keys=True, default=str)[:8000]
        def complete():
            row = self._db.execute("SELECT state FROM effects WHERE id=?", (side_effect_id,)).fetchone()
            if not row: raise DurableSafetyError("cannot complete an unclaimed effect")
            self._db.execute("UPDATE effects SET state='complete',receipt=?,at=? WHERE id=?", (clean, self.clock(), side_effect_id))
        self._tx(complete)

    def abandon_side_effect(self, side_effect_id: str) -> None:
        # Explicitly abandoned pre-send work may be retried; crashes remain claimed.
        self._tx(lambda: self._db.execute("DELETE FROM effects WHERE id=? AND state='claimed'", (side_effect_id,)))

    def side_effect_seen(self, side_effect_id: str) -> bool:
        try:
            with self._lock:
                return self._db.execute("SELECT 1 FROM effects WHERE id=?", (side_effect_id,)).fetchone() is not None
        except sqlite3.Error as exc: raise DurableSafetyError(f"effect read failed: {exc}") from exc

    def consume_nonce_once(self, nonce: str) -> bool:
        def consume():
            try:
                self._db.execute("INSERT INTO nonces(nonce,at) VALUES(?,?)", (nonce, self.clock()))
                return True
            except sqlite3.IntegrityError: return False
        return self._tx(consume)

    def nonce_consumed(self, nonce: str) -> bool:
        with self._lock: return self._db.execute("SELECT 1 FROM nonces WHERE nonce=?", (nonce,)).fetchone() is not None

    def get_cooldown(self, recipient: str) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT until_at FROM cooldowns WHERE recipient=?", (recipient.lower(),)).fetchone()
            return float(row[0]) if row else None

    def set_cooldown(self, recipient: str, until_at: float) -> None:
        self._tx(lambda: self._db.execute("INSERT INTO cooldowns(recipient,until_at) VALUES(?,?) ON CONFLICT(recipient) DO UPDATE SET until_at=excluded.until_at", (recipient.lower(), until_at)))

    def block_recipient(self, recipient: str, reason: str = "") -> None:
        self._tx(lambda: self._db.execute("INSERT INTO blocks(recipient,reason,at) VALUES(?,?,?) ON CONFLICT(recipient) DO UPDATE SET reason=excluded.reason,at=excluded.at", (recipient.lower(), reason[:500], self.clock())))

    def recipient_blocked(self, recipient: str) -> bool:
        with self._lock: return self._db.execute("SELECT 1 FROM blocks WHERE recipient=?", (recipient.lower(),)).fetchone() is not None

    def record_teacher_outcome(self, key: str, delta: float, detail: dict | None = None) -> tuple[float, int]:
        def record():
            row = self._db.execute("SELECT score,samples FROM teacher_outcomes WHERE key=?", (key,)).fetchone()
            score, samples = (float(row[0]), int(row[1])) if row else (0.5, 0)
            score = max(0.0, min(1.0, round(score + delta, 4)))
            self._db.execute("INSERT INTO teacher_outcomes(key,score,samples,detail,at) VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET score=excluded.score,samples=excluded.samples,detail=excluded.detail,at=excluded.at", (key, score, samples + 1, json.dumps(detail or {}, default=str)[:4000], self.clock()))
            return score, samples + 1
        return self._tx(record)

    def teacher_outcome(self, key: str) -> tuple[float, int]:
        with self._lock:
            row = self._db.execute("SELECT score,samples FROM teacher_outcomes WHERE key=?", (key,)).fetchone()
            return (float(row[0]), int(row[1])) if row else (0.5, 0)

    def save_pending_approval(self, task_id: str, payload: dict) -> None:
        self._tx(lambda: self._db.execute("INSERT INTO pending_approvals(task_id,payload,at) VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload,at=excluded.at", (task_id, json.dumps(payload, sort_keys=True, default=str)[:12000], self.clock())))

    def resume_pending_approval(self, task_id: str, *, consume: bool = False) -> dict | None:
        def resume():
            row = self._db.execute("SELECT payload FROM pending_approvals WHERE task_id=?", (task_id,)).fetchone()
            if not row: return None
            if consume: self._db.execute("DELETE FROM pending_approvals WHERE task_id=?", (task_id,))
            return json.loads(row[0])
        return self._tx(resume) if consume else resume()

    # ---- owner-issued approvals (PASS 3): only the trusted issuer writes here; models cannot mint rows
    def record_issued_approval(self, *, nonce: str, digest: str, scope: str, owner: str, task_id: str, expires_at: float | None) -> None:
        self._tx(lambda: self._db.execute("INSERT INTO issued_approvals(nonce,digest,scope,owner,task_id,expires_at,at) VALUES(?,?,?,?,?,?,?)", (nonce, digest, scope, owner, task_id, expires_at, self.clock())))

    def issued_approval(self, nonce: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT digest,scope,owner,task_id,expires_at FROM issued_approvals WHERE nonce=?", (nonce,)).fetchone()
            return {"digest": row[0], "scope": row[1], "owner": row[2], "task_id": row[3], "expires_at": row[4]} if row else None
