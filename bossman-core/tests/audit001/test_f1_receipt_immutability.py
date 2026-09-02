"""AUDIT-ONLY-001 / F1-DURABLE-LOST-UPDATE — independent verification.

Fable's claim: `DurableSafetyStore.complete_side_effect` runs
`UPDATE effects SET state='complete',receipt=?,at=? WHERE id=?` with no guard on the
current state, so a second completion silently overwrites the first receipt
(last-write-wins) and the original receipt is lost forever.

These tests pin the *contract we believe the durable store must offer*:

  R1  a completed effect's receipt is IMMUTABLE.  Once a receipt is durably stored,
      no later call may replace it with different content.
  R2  an attempt to complete an already-complete effect with a DIFFERENT receipt is a
      typed refusal (`DurableSafetyError`), not a silent overwrite.
  R3  repeating a completion with a BYTE-IDENTICAL receipt is SAFE-IDEMPOTENT: it must
      NOT raise, and it must leave the stored receipt unchanged.

      Contract decision (documented deliberately, see DELIVERABLE note in the report):
      identical-repeat is idempotent rather than a refusal because a caller can crash
      between the store's COMMIT and its own bookkeeping; on restart it must be able to
      re-assert the *same* receipt without being punished.  Divergent content is the
      only thing that is dangerous, and R2 covers that.

  R4  the rule holds across real OS processes, not just threads inside one interpreter:
      two independent processes completing the same effect -> exactly one succeeds.

  R5  the rule survives a real process restart: a genuinely new interpreter reading the
      store back sees exactly one receipt, unchanged.

Also included (reachability, deliberately GREEN): the guard that decides the REAL
severity — every production caller (`bossman.apprentice.engine._execute_step` and
`bossman.apprentice.outreach.OutreachGate.send`) only ever calls `complete` after its own
`claim` returned claimed=True, and `claim_side_effect` refuses a second claim.  If that
guard ever regresses, the reachability test below goes red and F1 becomes exploitable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bossman.apprentice.durable import DurableSafetyError, DurableSafetyStore
from bossman.apprentice.guards import SideEffectLedger

SID = "se-f1"
RECEIPT_A = {"receipt_id": "A", "action_type": "CLICK", "refund": True}
RECEIPT_B = {"receipt_id": "B", "action_type": "CLICK", "refund": False}

_ROOT = str(Path(__file__).resolve().parents[2])  # .../bossman-core (import root for `bossman.*`)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_child(code: str, *, check: bool = True) -> dict:
    """Run a REAL separate OS process and parse its single JSON line."""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=_child_env(), cwd=_ROOT, timeout=120)
    if check and proc.returncode != 0:
        raise AssertionError(f"child failed rc={proc.returncode}\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


_COMPLETE_CHILD = """
import json, time, sys
from bossman.apprentice.durable import DurableSafetyStore, DurableSafetyError
db, sid, receipt, start_at = {db!r}, {sid!r}, json.loads({receipt!r}), {start_at!r}
s = DurableSafetyStore(db)
while time.time() < start_at:      # rendezvous: both processes push at the same instant
    time.sleep(0.001)
try:
    s.complete_side_effect(sid, receipt)
    out = {{"ok": True, "err": "", "receipt": receipt}}
except DurableSafetyError as exc:
    out = {{"ok": False, "err": str(exc), "receipt": receipt}}
finally:
    s.close()
print(json.dumps(out))
"""

_READ_CHILD = """
import json
from bossman.apprentice.durable import DurableSafetyStore
s = DurableSafetyStore({db!r})
claimed, prior = s.claim_side_effect({sid!r})   # a complete row hands back its stored receipt
s.close()
print(json.dumps({{"claimed": claimed, "receipt": prior}}))
"""


def _store(tmp_path: Path) -> DurableSafetyStore:
    return DurableSafetyStore(tmp_path / "safety.sqlite")


def _durable_receipt(store: DurableSafetyStore, sid: str = SID) -> dict | None:
    """The only public read path for a stored receipt: a re-claim of a complete effect."""
    claimed, prior = store.claim_side_effect(sid)
    assert claimed is False, "a completed effect must never be re-claimable"
    return prior


# ------------------------------------------------------------------ (1) baseline
def test_first_complete_stores_receipt_a(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert store.claim_side_effect(SID) == (True, None)
        store.complete_side_effect(SID, RECEIPT_A)
        assert _durable_receipt(store) == RECEIPT_A
    finally:
        store.close()


# ------------------------------------------------------------------ (2) R2 refusal
def test_second_complete_with_different_receipt_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.claim_side_effect(SID)
        store.complete_side_effect(SID, RECEIPT_A)
        with pytest.raises(DurableSafetyError):
            store.complete_side_effect(SID, RECEIPT_B)
    finally:
        store.close()


# ------------------------------------------------------------------ (3) R1 immutability
def test_durable_receipt_after_refused_overwrite_is_still_a(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.claim_side_effect(SID)
        store.complete_side_effect(SID, RECEIPT_A)
        try:
            store.complete_side_effect(SID, RECEIPT_B)
        except DurableSafetyError:
            pass
        assert _durable_receipt(store) == RECEIPT_A, "the original receipt was overwritten (lost update)"
    finally:
        store.close()


# ------------------------------------------------------------------ (6) R3 identical repeat
def test_repeat_complete_with_identical_receipt_is_safe_idempotent(tmp_path: Path) -> None:
    """DOCUMENTED CONTRACT: identical content re-completion is a no-op, never a refusal."""
    store = _store(tmp_path)
    try:
        store.claim_side_effect(SID)
        store.complete_side_effect(SID, RECEIPT_A)
        store.complete_side_effect(SID, dict(RECEIPT_A))   # must not raise
        assert _durable_receipt(store) == RECEIPT_A
    finally:
        store.close()


# ------------------------------------------------------------------ (4) R4 two real processes
def test_two_real_processes_completing_same_effect_exactly_one_wins(tmp_path: Path) -> None:
    db = str(tmp_path / "safety.sqlite")
    store = DurableSafetyStore(db)
    assert store.claim_side_effect(SID) == (True, None)
    store.close()                                   # hand the file over to the children

    start_at = time.time() + 1.0
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _COMPLETE_CHILD.format(db=db, sid=SID, receipt=json.dumps(r), start_at=start_at)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_child_env(), cwd=_ROOT)
        for r in (RECEIPT_A, RECEIPT_B)
    ]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"child crashed rc={p.returncode}\nSTDOUT:{out}\nSTDERR:{err}"
        results.append(json.loads(out.strip().splitlines()[-1]))

    winners = [r for r in results if r["ok"]]
    assert len(winners) == 1, (
        f"exactly one process may complete the effect, got {len(winners)} winners: {results}")


# ------------------------------------------------------------------ (5) R5 real restart
def test_after_real_restart_exactly_one_unchanged_receipt(tmp_path: Path) -> None:
    db = str(tmp_path / "safety.sqlite")
    store = DurableSafetyStore(db)
    store.claim_side_effect(SID)
    store.close()

    start_at = time.time() + 1.0
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _COMPLETE_CHILD.format(db=db, sid=SID, receipt=json.dumps(r), start_at=start_at)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_child_env(), cwd=_ROOT)
        for r in (RECEIPT_A, RECEIPT_B)
    ]
    results = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"child crashed rc={p.returncode}\nSTDOUT:{out}\nSTDERR:{err}"
        results.append(json.loads(out.strip().splitlines()[-1]))
    winners = [r["receipt"] for r in results if r["ok"]]

    # a genuinely new interpreter reads the store back
    restart = _run_child(_READ_CHILD.format(db=db, sid=SID))
    assert restart["claimed"] is False
    assert len(winners) == 1, f"more than one writer was allowed to win: {results}"
    assert restart["receipt"] == winners[0], (
        f"durable receipt {restart['receipt']} is not the single accepted receipt {winners[0]}")


# ------------------------------------------------------ reachability (severity input)
def test_production_callers_cannot_reach_a_second_complete(tmp_path: Path) -> None:
    """GREEN characterization: this is the guard that keeps F1 unreachable in production.

    `engine._execute_step` and `OutreachGate.send` both call `ledger.complete(...)` only on
    the branch where their own `ledger.claim(...)` returned claimed=True.  A second claim on
    a completed effect returns (False, stored_receipt), so the duplicate caller takes the
    'duplicate external effect' branch and never completes.  If this ever regresses, the
    lost-update above becomes reachable.
    """
    store = _store(tmp_path)
    try:
        ledger = SideEffectLedger(store=store, live=True)
        assert ledger.claim(SID) == (True, None)
        ledger.complete(SID, {"receipt": RECEIPT_A})
        second = ledger.claim(SID)
        assert second[0] is False, "a second claim must be refused, otherwise F1 is reachable"
        assert second[1] == {"receipt": RECEIPT_A}, "the duplicate caller must see the FIRST receipt"
        assert ledger.seen(SID) is True
    finally:
        store.close()
