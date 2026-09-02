"""Fail-closed durable safety state for Apprentice live capabilities.

SQLite/WAL is deliberately the single local fallback.  Values are small,
sanitized safety facts only; a claimed effect is *not* automatically released
after a crash because doing so could duplicate an external action.

SIDE-EFFECT STATE MACHINE (table ``effects``, column ``state``)
---------------------------------------------------------------
    (absent) --claim------------------> CLAIMED
    CLAIMED  --complete(receipt)------> COMPLETE     terminal, receipt IMMUTABLE
    CLAIMED  --abandon---------------->  ABANDONED   terminal for replay (tombstone)
    ABANDONED --complete(receipt)-----> COMPLETE     a late receipt may still land
    COMPLETE --complete(same receipt)-> COMPLETE     safe-idempotent no-op
    COMPLETE --complete(other receipt)-> refused (DurableSafetyError)
    COMPLETE / ABANDONED --abandon----> no-op

Rules this module enforces, and why:

* Every transition is a CONDITIONAL ATOMIC UPDATE naming the expected current
  state in its WHERE clause, executed inside ``BEGIN IMMEDIATE`` and checked by
  rowcount.  Last-write-wins is therefore impossible, in-process, across threads
  and across OS processes.
* A stored receipt is immutable.  Re-completing with byte-identical content is a
  no-op (a caller may crash between our COMMIT and its own bookkeeping and must
  be able to re-assert the same fact); different content is refused.
* ``abandon`` does NOT delete the row.  Deleting it would re-open the
  side_effect_id, and every production abandon happens *after* the actuator or
  transport was already invoked -- the external effect may well have happened
  and only the receipt was unusable.  Keeping an ABANDONED tombstone makes the
  retry a refused duplicate instead of a second irreversible action.  The cost
  is deliberate and fail-closed: an effect whose transport genuinely never
  started cannot be retried under the same id.
* Removal of tombstones is never implicit: it belongs to the explicit,
  TTL-guarded :meth:`purge_terminal_effects`, which never touches CLAIMED rows.

RECONCILING / FAILED_FINAL states are intentionally NOT implemented: no caller
can drive them today, and an unreachable transition is untested weight in a
fail-closed store.  The two-terminal machine above is what production uses.

Schema compatibility: the tables are unchanged from the previous release (no new
columns, no migration step).  A safety.sqlite written by the old code opens
as-is; its 'claimed' and 'complete' rows keep exactly their old meaning, and the
new 'abandoned' state simply never occurs in such a file.
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
        self._poisoned: str | None = None
        self._connect()

    def _connect(self) -> None:
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

    def reopen(self) -> None:
        """Explicit recovery from a poisoned store: reconnect, then verify integrity.

        Poisoning is never cleared implicitly -- a store whose rollback failed has an
        unknown transaction state, so the operator (not a retry loop) decides to trust
        it again."""
        with self._lock:
            try: self._db.close()
            except sqlite3.Error: pass
            self._connect()
            try:
                verdict = self._db.execute("PRAGMA quick_check").fetchone()[0]
            except sqlite3.Error as exc:
                raise DurableSafetyError(f"safety store integrity check failed: {exc}") from exc
            if verdict != "ok":
                raise DurableSafetyError("safety store failed its integrity check; refusing to reopen")
            self._poisoned = None

    def _tx(self, fn):
        # The lock wraps the handler too: a rollback issued outside it could tear down a
        # transaction another thread has just begun.
        with self._lock:
            if self._poisoned is not None:
                raise DurableSafetyError(f"safety transaction failed: store is poisoned ({self._poisoned}); reopen required")
            try:
                self._db.execute("BEGIN IMMEDIATE")
                value = fn()
                self._db.commit()
                return value
            except BaseException as exc:                      # noqa: BLE001 — any body error must not leak the tx
                rollback_error: sqlite3.Error | None = None
                try:
                    self._db.rollback()
                except sqlite3.Error as rb:
                    rollback_error = rb
                if rollback_error is not None:
                    # The handle's transaction state is unknown: mark the store poisoned,
                    # drop the connection so nothing writes through it, and fail closed.
                    self._poisoned = f"rollback failed: {rollback_error}"
                    try: self._db.close()
                    except sqlite3.Error: pass
                    raise DurableSafetyError(
                        f"safety transaction failed: {exc}; rollback also failed: {rollback_error}") from exc
                if isinstance(exc, sqlite3.Error):
                    raise DurableSafetyError(f"safety transaction failed: {exc}") from exc
                raise                                         # typed refusals keep their own message

    @staticmethod
    def _receipt_blob(receipt: dict) -> str:
        return json.dumps(dict(receipt), sort_keys=True, default=str)[:8000]

    @staticmethod
    def _decode(blob: str | None) -> dict | None:
        """Stored blobs are truncated for size; an undecodable one yields no receipt.

        Fail-closed on purpose: the caller still sees the effect as taken (claim refused),
        it just gets no prior receipt to reuse."""
        if not blob:
            return None
        try:
            return json.loads(blob)
        except (ValueError, TypeError):
            return None

    def claim_side_effect(self, side_effect_id: str) -> tuple[bool, dict | None]:
        def claim():
            row = self._db.execute("SELECT state, receipt FROM effects WHERE id=?", (side_effect_id,)).fetchone()
            if row:                                            # any existing row -> the id is taken
                return False, self._decode(row[1]) if row[0] == "complete" else None
            self._db.execute("INSERT INTO effects(id,state,at) VALUES(?,?,?)", (side_effect_id, "claimed", self.clock()))
            return True, None
        return self._tx(claim)

    def complete_side_effect(self, side_effect_id: str, receipt: dict) -> None:
        """CLAIMED|ABANDONED -> COMPLETE.  A stored receipt is immutable."""
        clean = self._receipt_blob(receipt)
        def complete():
            row = self._db.execute("SELECT state, receipt FROM effects WHERE id=?", (side_effect_id,)).fetchone()
            if not row: raise DurableSafetyError("cannot complete an unclaimed effect")
            if row[0] == "complete":
                if row[1] == clean: return                     # idempotent re-assert of the SAME receipt
                raise DurableSafetyError("effect already completed with a different receipt")
            cur = self._db.execute(
                "UPDATE effects SET state='complete',receipt=?,at=? WHERE id=? AND state IN ('claimed','abandoned')",
                (clean, self.clock(), side_effect_id))
            if cur.rowcount != 1:
                raise DurableSafetyError(f"cannot complete an effect in state {row[0]!r}")
        self._tx(complete)

    def abandon_side_effect(self, side_effect_id: str) -> None:
        """CLAIMED -> ABANDONED.  The row is KEPT as a tombstone: the abandon always
        happens after the actuator/transport was invoked, so the external effect may have
        happened and the id must never re-open.  Crashes remain claimed."""
        self._tx(lambda: self._db.execute("UPDATE effects SET state='abandoned',at=? WHERE id=? AND state='claimed'",
                                          (self.clock(), side_effect_id)))

    def purge_terminal_effects(self, *, older_than: float) -> int:
        """Explicit, TTL-guarded GC: drop terminal rows last touched before `older_than`.

        CLAIMED rows are never removed.  Callers must pick a TTL well beyond any retry
        horizon -- removing a tombstone re-opens its side_effect_id."""
        def purge():
            cur = self._db.execute("DELETE FROM effects WHERE state IN ('complete','abandoned') AND at < ?",
                                   (float(older_than),))
            return int(cur.rowcount)
        return self._tx(purge)

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
            try:
                return json.loads(row[0])
            except (ValueError, TypeError) as exc:            # truncated/corrupt blob -> typed refusal, never a raw decode error
                raise DurableSafetyError("corrupt stored payload for pending approval") from exc
        return self._tx(resume) if consume else resume()

    # ---- owner-issued approvals (PASS 3): only the trusted issuer writes here; models cannot mint rows
    def record_issued_approval(self, *, nonce: str, digest: str, scope: str, owner: str, task_id: str, expires_at: float | None) -> None:
        self._tx(lambda: self._db.execute("INSERT INTO issued_approvals(nonce,digest,scope,owner,task_id,expires_at,at) VALUES(?,?,?,?,?,?,?)", (nonce, digest, scope, owner, task_id, expires_at, self.clock())))

    def issued_approval(self, nonce: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT digest,scope,owner,task_id,expires_at FROM issued_approvals WHERE nonce=?", (nonce,)).fetchone()
            return {"digest": row[0], "scope": row[1], "owner": row[2], "task_id": row[3], "expires_at": row[4]} if row else None
