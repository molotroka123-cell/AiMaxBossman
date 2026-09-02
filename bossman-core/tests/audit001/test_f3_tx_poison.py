"""AUDIT-ONLY-001 / F3-DURABLE-TX-SILENT-FAIL — independent verification.

Fable's claim (severity P0): `DurableSafetyStore._tx` rolls back inside
`except sqlite3.Error: pass`, so a failed commit + failed rollback leaves the store in
an undefined state and a PARTIAL WRITE can bypass the nonce-once guarantee.

This file tests BOTH halves of that claim and deliberately mixes RED and GREEN:

GREEN (characterization — pins behaviour Fable got WRONG, must keep passing)
  G1  a REAL disk-full error (`PRAGMA max_page_count`, genuine SQLITE_FULL from the
      SQLite engine, not a monkeypatch) is rolled back atomically: no partial write,
      earlier committed rows intact, `PRAGMA integrity_check`/`quick_check` == ok,
      the store keeps working.
  G2  a REAL constraint violation (duplicate PRIMARY KEY in `issued_approvals`) is
      wrapped as `DurableSafetyError` and leaves nothing behind.
  G3  a genuinely closed connection (`store.close()` then use) — the realistic case
      where the rollback in the handler ALSO fails and is swallowed by
      `except sqlite3.Error: pass` — still fails CLOSED with `DurableSafetyError`.
  G4  commit failure + rollback failure together do NOT produce a durable partial
      write: SQLite owns atomicity, the open transaction dies with the connection, and
      a genuinely restarted process does not see the row.  The nonce-once guarantee is
      therefore NOT bypassable this way -> Fable's P0 exploit story is false.
  G5  the nonce-once guarantee survives a real process restart after all of the above.

RED (the defect that is actually there)
  R1  `_tx` only rolls back on `sqlite3.Error`.  When the transaction BODY raises
      anything else the `BEGIN IMMEDIATE` transaction is LEAKED: still open.
  R2  the leak poisons the NEXT, UNRELATED call on the same store, which fails
      spuriously with "cannot start a transaction within a transaction".
  R3  realistic trigger, no monkeypatching at all: `complete_side_effect` stores the
      receipt JSON truncated to 8000 chars, so a later `claim_side_effect` on that same
      effect does `json.loads` on truncated JSON and raises a raw `json.JSONDecodeError`
      — which (a) violates the store's documented `DurableSafetyError` contract and
      (b) leaks the write transaction.
  R4  the leaked transaction holds a real SQLite RESERVED write lock, so an independent
      connection/process using the same store file is blocked out until someone happens
      to touch the poisoned store again.

R1-R4 must fail against the current code.  If they ever go green the defect is fixed.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from bossman.apprentice.durable import DurableSafetyError, DurableSafetyStore


def _store(tmp_path, name="safety.db"):
    return DurableSafetyStore(str(tmp_path / name))


def _integrity(db: sqlite3.Connection) -> tuple[str, str]:
    return (db.execute("PRAGMA integrity_check").fetchone()[0],
            db.execute("PRAGMA quick_check").fetchone()[0])


# --------------------------------------------------------------- GREEN: real fault paths
def test_g1_real_disk_full_rolls_back_atomically(tmp_path):
    """REAL SQLITE_FULL (not a monkeypatch): capped page count -> genuine engine error."""
    store = _store(tmp_path)
    try:
        assert store.consume_nonce_once("committed-before-the-fault") is True
        pages = store._db.execute("PRAGMA page_count").fetchone()[0]
        store._db.execute(f"PRAGMA max_page_count={pages}")  # no room for a single new page

        err = None
        for i in range(2000):
            try:
                store.record_teacher_outcome(f"k{i}", 0.01, {"blob": "x" * 3000})
            except DurableSafetyError as exc:  # noqa: PERF203
                err = str(exc)
                break
        assert err is not None, "expected the capped store to hit a genuine SQLITE_FULL"
        assert "disk is full" in err

        store._db.execute("PRAGMA max_page_count=1073741823")
        assert _integrity(store._db) == ("ok", "ok")
        # committed data survived, no partial write, store still usable
        assert store.nonce_consumed("committed-before-the-fault") is True
        assert store._db.in_transaction is False
        assert store.consume_nonce_once("after-the-fault") is True
    finally:
        store.close()


def test_g2_real_constraint_error_is_wrapped_and_leaves_nothing_behind(tmp_path):
    """REAL sqlite3.IntegrityError from a duplicate PRIMARY KEY."""
    store = _store(tmp_path)
    try:
        store.record_issued_approval(nonce="n1", digest="d1", scope="s", owner="human:o",
                                     task_id="t1", expires_at=None)
        with pytest.raises(DurableSafetyError) as ei:
            store.record_issued_approval(nonce="n1", digest="OTHER", scope="s", owner="human:o",
                                         task_id="t2", expires_at=None)
        assert "safety transaction failed" in str(ei.value)
        assert store.issued_approval("n1")["digest"] == "d1"  # untouched
        assert store._db.in_transaction is False
        assert _integrity(store._db) == ("ok", "ok")
    finally:
        store.close()


def test_g3_closed_connection_fails_closed_even_though_rollback_also_fails(tmp_path):
    """Realistic broken handle: both commit AND rollback raise; the handler swallows the
    rollback error.  Behaviour is still fail-CLOSED (typed refusal), which is what the
    callers rely on -- so `except sqlite3.Error: pass` is error masking, not a bypass."""
    store = _store(tmp_path)
    store.close()
    for call in (lambda: store.consume_nonce_once("x"),
                 lambda: store.claim_side_effect("x"),
                 lambda: store.set_cooldown("a@b.c", 1.0),
                 lambda: store.record_issued_approval(nonce="x", digest="d", scope="s",
                                                      owner="o", task_id="t", expires_at=None)):
        with pytest.raises(DurableSafetyError) as ei:
            call()
        assert "safety transaction failed" in str(ei.value)


def test_g4_commit_and_rollback_failure_produce_no_durable_partial_write(tmp_path):
    """Fable's P0 story, tested directly: force commit AND rollback to blow up, then ask a
    genuinely NEW process whether the nonce got burned.  It did not -- SQLite discards the
    open transaction, so there is no partial write and no nonce-once bypass."""
    path = str(tmp_path / "p.db")
    store = DurableSafetyStore(path)
    real = store._db

    class ExplodingConn:
        def __init__(self, conn): self._c = conn
        def __getattr__(self, name): return getattr(self._c, name)
        def commit(self): raise sqlite3.OperationalError("commit exploded")
        def rollback(self): raise sqlite3.OperationalError("rollback exploded")

    store._db = ExplodingConn(real)
    with pytest.raises(DurableSafetyError) as ei:
        store.consume_nonce_once("victim-nonce")
    assert "commit exploded" in str(ei.value)
    store._db = real
    store.close()  # connection teardown discards the never-committed transaction

    probe = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import json
            from bossman.apprentice.durable import DurableSafetyStore as S
            s = S({path!r})
            print(json.dumps({{
                "seen": s.nonce_consumed("victim-nonce"),
                "reconsume": s.consume_nonce_once("victim-nonce"),
                "integrity": s._db.execute("PRAGMA integrity_check").fetchone()[0],
                "quick": s._db.execute("PRAGMA quick_check").fetchone()[0],
            }}))
        """)],
        capture_output=True, text=True, timeout=120)
    assert probe.returncode == 0, probe.stderr
    got = json.loads(probe.stdout.strip().splitlines()[-1])
    assert got == {"seen": False, "reconsume": True, "integrity": "ok", "quick": "ok"}


def test_g5_nonce_once_survives_restart_after_a_real_fault(tmp_path):
    path = str(tmp_path / "r.db")
    store = DurableSafetyStore(path)
    assert store.consume_nonce_once("burned") is True
    with pytest.raises(DurableSafetyError):
        store.record_issued_approval(nonce="a", digest="d", scope="s", owner="o",
                                     task_id="t", expires_at=None)
        store.record_issued_approval(nonce="a", digest="d", scope="s", owner="o",
                                     task_id="t", expires_at=None)
    store.close()

    probe = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import json
            from bossman.apprentice.durable import DurableSafetyStore as S
            s = S({path!r})
            print(json.dumps({{"replay": s.consume_nonce_once("burned"),
                              "integrity": s._db.execute("PRAGMA integrity_check").fetchone()[0]}}))
        """)],
        capture_output=True, text=True, timeout=120)
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout.strip().splitlines()[-1]) == {"replay": False, "integrity": "ok"}


# ------------------------------------------------------------------- RED: the real defect
def test_r1_non_sqlite_body_error_must_not_leak_the_transaction(tmp_path):
    """`complete_side_effect` on an unclaimed effect is a DESIGNED refusal (the benchmark
    asserts it, bossman/benchmark/sandbox_cases/safety.py:193).  It raises
    DurableSafetyError -- not a sqlite3.Error -- so `_tx`'s handler never fires and the
    BEGIN IMMEDIATE transaction is left open."""
    store = _store(tmp_path)
    try:
        assert store._db.in_transaction is False
        with pytest.raises(DurableSafetyError, match="cannot complete an unclaimed effect"):
            store.complete_side_effect("never-claimed", {})
        assert store._db.in_transaction is False, (
            "_tx leaked an open BEGIN IMMEDIATE transaction after a non-sqlite3 body error")
    finally:
        store.close()


def test_r2_leaked_transaction_poisons_the_next_unrelated_call(tmp_path):
    """The next caller -- a completely unrelated nonce consumption -- is refused for a
    reason that has nothing to do with it."""
    store = _store(tmp_path)
    try:
        with pytest.raises(DurableSafetyError):
            store.complete_side_effect("never-claimed", {})
        assert store.consume_nonce_once("innocent-bystander") is True
    finally:
        store.close()


def test_r3_truncated_receipt_makes_claim_raise_untyped_and_poisons_the_store(tmp_path):
    """Fully realistic: no monkeypatching, no injected fault.  `complete_side_effect`
    truncates the receipt JSON at 8000 chars; the idempotency re-check then json.loads()
    that truncated string."""
    store = _store(tmp_path)
    try:
        seid = "effect-with-a-big-receipt"
        assert store.claim_side_effect(seid) == (True, None)
        store.complete_side_effect(seid, {"receipt": {"dom": "y" * 9000}})
        assert store._db.execute("SELECT length(receipt) FROM effects WHERE id=?",
                                 (seid,)).fetchone()[0] == 8000

        # (a) contract: every failure of this store is documented as DurableSafetyError
        try:
            claimed, prior = store.claim_side_effect(seid)
        except DurableSafetyError:
            claimed = prior = None
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"claim_side_effect leaked an untyped {type(exc).__name__}: {exc}")
        # (b) state: whatever happened, the store must not be left mid-transaction
        assert store._db.in_transaction is False, "claim_side_effect leaked an open transaction"
        assert claimed is False, "a completed effect must never be re-claimable"
    finally:
        store.close()


def test_r4_leaked_transaction_holds_a_real_write_lock_against_other_connections(tmp_path):
    """The leak is not merely cosmetic: BEGIN IMMEDIATE took a RESERVED lock on the shared
    file, so an independent connection (the sandbox runtime really does drive this store
    from child processes -- bossman/benchmark/sandbox_runtime.py:42-49) is locked out."""
    path = str(tmp_path / "locked.db")
    store = DurableSafetyStore(path)
    other = sqlite3.connect(path, timeout=0.5)
    try:
        with pytest.raises(DurableSafetyError):
            store.complete_side_effect("never-claimed", {})
        other.execute("BEGIN IMMEDIATE")
        other.execute("INSERT INTO nonces(nonce,at) VALUES('from-other',1.0)")
        other.commit()
    finally:
        other.close()
        store.close()
        for f in glob.glob(path + "*"):
            try:
                os.remove(f)
            except OSError:
                pass
