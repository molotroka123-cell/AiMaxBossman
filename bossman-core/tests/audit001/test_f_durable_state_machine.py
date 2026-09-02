"""AUDIT-ONLY-001 / durable store — state machine, backward compatibility, GC, poisoning.

Companion pins for the F1+F2+F3 fix in `bossman/apprentice/durable.py`.  Three things
this file exists to prove, none of which the F1/F2/F3 files cover:

  M   MIGRATION.  A `safety.sqlite` written by the PREVIOUS release must still open with
      the new code, and its rows must keep their meaning: a 'claimed' row is still a live
      claim (never auto-released), a 'complete' row still hands back its receipt and is
      still immutable.  The fix adds no columns, so the migration is a no-op -- this test
      is what makes that claim checkable instead of asserted.

  G   GC.  Tombstone removal is explicit and TTL-guarded: `purge_terminal_effects` never
      touches a CLAIMED row and never removes a tombstone newer than the cutoff.

  P   POISONING.  When a rollback itself fails the store has an unknown transaction
      state; it must refuse every later write with a typed error and only come back
      after an explicit `reopen()` that passes an integrity check.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bossman.apprentice.durable import DurableSafetyError, DurableSafetyStore

# Verbatim DDL of the release that shipped before this fix.
OLD_SCHEMA = """
  CREATE TABLE IF NOT EXISTS effects (id TEXT PRIMARY KEY, state TEXT NOT NULL, receipt TEXT, at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS cooldowns (recipient TEXT PRIMARY KEY, until_at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS blocks (recipient TEXT PRIMARY KEY, reason TEXT NOT NULL, at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS teacher_outcomes (key TEXT PRIMARY KEY, score REAL NOT NULL, samples INTEGER NOT NULL, detail TEXT, at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS pending_approvals (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, at REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS issued_approvals (nonce TEXT PRIMARY KEY, digest TEXT NOT NULL, scope TEXT NOT NULL, owner TEXT NOT NULL, task_id TEXT NOT NULL, expires_at REAL, at REAL NOT NULL);
"""
OLD_RECEIPT = '{"action_type": "CLICK", "receipt_id": "old-1"}'


def _legacy_db(tmp_path: Path, name: str = "legacy.sqlite") -> str:
    """Build a database exactly the way the previous release would have left it."""
    path = str(tmp_path / name)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(OLD_SCHEMA)
    db.execute("INSERT INTO effects(id,state,receipt,at) VALUES('old-claimed','claimed',NULL,100.0)")
    db.execute("INSERT INTO effects(id,state,receipt,at) VALUES('old-complete','complete',?,101.0)", (OLD_RECEIPT,))
    db.execute("INSERT INTO nonces(nonce,at) VALUES('old-nonce',102.0)")
    db.execute("INSERT INTO pending_approvals(task_id,payload,at) VALUES('t-old','{\"k\": 1}',103.0)")
    db.commit()
    db.close()
    return path


# ------------------------------------------------------------------ M: migration
def test_a_database_written_by_the_old_code_still_opens(tmp_path: Path) -> None:
    path = _legacy_db(tmp_path)
    store = DurableSafetyStore(path)
    try:
        cols = [r[1] for r in store._db.execute("PRAGMA table_info(effects)")]
        assert cols == ["id", "state", "receipt", "at"], "the fix must not require a schema change"
        assert store._db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        store.close()


def test_old_rows_keep_their_meaning_under_the_new_code(tmp_path: Path) -> None:
    store = DurableSafetyStore(_legacy_db(tmp_path))
    try:
        # a legacy 'claimed' row is still a live claim: not re-claimable, not auto-released
        assert store.claim_side_effect("old-claimed") == (False, None)
        assert store.side_effect_seen("old-claimed") is True
        # a legacy 'complete' row still hands its receipt to a duplicate caller ...
        claimed, prior = store.claim_side_effect("old-complete")
        assert claimed is False and prior == {"action_type": "CLICK", "receipt_id": "old-1"}
        # ... and is now immutable
        with pytest.raises(DurableSafetyError, match="different receipt"):
            store.complete_side_effect("old-complete", {"receipt_id": "new"})
        assert store.claim_side_effect("old-complete")[1] == {"action_type": "CLICK", "receipt_id": "old-1"}
        # other legacy tables are untouched
        assert store.consume_nonce_once("old-nonce") is False
        assert store.resume_pending_approval("t-old") == {"k": 1}
    finally:
        store.close()


def test_a_legacy_claim_can_still_be_completed_and_abandoned(tmp_path: Path) -> None:
    store = DurableSafetyStore(_legacy_db(tmp_path))
    try:
        store.complete_side_effect("old-claimed", {"receipt_id": "late"})     # CLAIMED -> COMPLETE
        assert store.claim_side_effect("old-claimed")[1] == {"receipt_id": "late"}
    finally:
        store.close()

    store = DurableSafetyStore(_legacy_db(tmp_path, "legacy2.sqlite"))
    try:
        store.abandon_side_effect("old-claimed")                              # CLAIMED -> ABANDONED
        assert store.claim_side_effect("old-claimed") == (False, None), "a tombstone must stay closed"
        assert store.side_effect_seen("old-claimed") is True
    finally:
        store.close()


# ------------------------------------------------------------------ state machine
def test_terminal_states_are_terminal(tmp_path: Path) -> None:
    store = DurableSafetyStore(tmp_path / "sm.sqlite")
    try:
        assert store.claim_side_effect("e1") == (True, None)
        store.abandon_side_effect("e1")
        assert store._db.execute("SELECT state FROM effects WHERE id='e1'").fetchone()[0] == "abandoned"
        store.complete_side_effect("e1", {"r": 1})                            # ABANDONED -> COMPLETE
        assert store._db.execute("SELECT state FROM effects WHERE id='e1'").fetchone()[0] == "complete"
        store.abandon_side_effect("e1")                                       # no-op on a terminal row
        assert store.claim_side_effect("e1") == (False, {"r": 1})
    finally:
        store.close()


# ------------------------------------------------------------------ G: TTL-guarded GC
def test_gc_removes_only_old_terminal_rows_and_never_a_claim(tmp_path: Path) -> None:
    now = [1_000.0]
    store = DurableSafetyStore(tmp_path / "gc.sqlite", clock=lambda: now[0])
    try:
        store.claim_side_effect("live-claim")                                 # stays claimed forever
        store.claim_side_effect("done"); store.complete_side_effect("done", {"r": 1})
        store.claim_side_effect("gone"); store.abandon_side_effect("gone")
        now[0] = 5_000.0
        store.claim_side_effect("fresh"); store.abandon_side_effect("fresh")  # newer than the cutoff

        assert store.purge_terminal_effects(older_than=2_000.0) == 2
        assert store.side_effect_seen("live-claim") is True, "GC must never release a claim"
        assert store.side_effect_seen("fresh") is True, "GC must respect the TTL cutoff"
        assert store.side_effect_seen("done") is False and store.side_effect_seen("gone") is False
        assert store.claim_side_effect("live-claim") == (False, None)
    finally:
        store.close()


# ------------------------------------------------------------------ P: poisoning / reopen
class _RollbackExplodes:
    def __init__(self, conn): self._c = conn
    def __getattr__(self, name): return getattr(self._c, name)
    def commit(self): raise sqlite3.OperationalError("commit exploded")
    def rollback(self): raise sqlite3.OperationalError("rollback exploded")


def test_failed_rollback_poisons_the_store_until_an_explicit_reopen(tmp_path: Path) -> None:
    path = str(tmp_path / "poison.sqlite")
    store = DurableSafetyStore(path)
    real = store._db
    store._db = _RollbackExplodes(real)
    with pytest.raises(DurableSafetyError, match="rollback also failed"):
        store.consume_nonce_once("n1")
    store._db = real

    # poisoned: every later write is refused, typed, without touching the file
    for call in (lambda: store.consume_nonce_once("n2"), lambda: store.claim_side_effect("e")):
        with pytest.raises(DurableSafetyError, match="poisoned"):
            call()

    store.reopen()                                                            # explicit operator action
    assert store.consume_nonce_once("n2") is True
    assert store.consume_nonce_once("n1") is True, "the aborted write must never have become durable"
    store.close()
