"""AUDIT-ONLY-001 / F2-DURABLE-ABANDON-RACE — independent verification.

Fable's claim (P1):
    `DurableSafetyStore.abandon_side_effect` runs
    `DELETE FROM effects WHERE id=? AND state='claimed'`.  A concurrent abandoner
    destroys the claim row of an executor that is still working, so the executor's
    `complete_side_effect` raises "cannot complete an unclaimed effect" (receipt lost)
    and the id becomes re-claimable, which lets a retry perform the external effect a
    SECOND time.

What these tests separate — deliberately — is the *mechanism* from the *harm*:

  MECHANISM (Fable's story: a cross-thread abandoner races an in-flight executor)
      test_abandon_of_an_inflight_claim_loses_the_receipt_and_reopens_the_id
      This is a real property of the primitive.  It is NOT reachable from any
      production caller — see the reachability pins below, which prove that abandon is
      only ever called by the thread that owns the claim, and only after the actuator
      call has already returned.

  HARM (the part that IS reachable in production, on a single thread)
      test_receipt_invalid_abandon_lets_a_retry_repeat_the_external_effect
      `engine._act` abandons the claim when the receipt fails validation
      (`bossman/apprentice/engine.py`, "receipt_invalid" branch).  The actuator may have
      already performed the external effect and merely returned an unusable receipt
      (skewed clock, missing evidence, wrong action_id).  Because abandon DELETES the
      row, the very next replan re-claims the same side_effect_id and the external
      effect happens twice.  The ledger exists precisely to make that impossible.

Contract these tests pin:

  R1  an effect that was claimed and then abandoned by someone else must never make the
      owner's receipt unrecordable: either the receipt is stored, or the store refuses
      in a way that keeps the id CLOSED (never silently re-openable).
  R2  after any abandon of a claim whose external effect may already have happened, the
      id must not be re-claimable, so a retry cannot repeat the effect.
  R3  (GREEN pin) abandon after `complete` is a no-op: a stored receipt is never
      destroyed by a late abandon.
  R4  (GREEN pin) a crash between claim and complete leaves the row `claimed`; a restart
      does NOT auto-release it.
  R5  (GREEN reachability pins) no production caller abandons another worker's claim:
      the loser of a concurrent claim in `OutreachGate.send` refuses and abandons
      nothing, and every abandon the engine/outreach performs comes from the same thread
      that made the claim.

R1/R2 are RED against the current code.  R3/R4/R5 are GREEN and stay green under the
recommended fix (mark `state='abandoned'` instead of DELETE), so they are safe pins.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parents[2]          # .../bossman-core  (import root for `bossman.*`)
_TESTS = Path(__file__).resolve().parents[1]         # .../bossman-core/tests (for `fixtures.*`)
for _p in (str(_CORE), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.durable import DurableSafetyError, DurableSafetyStore  # noqa: E402
from bossman.apprentice.engine import DefaultVerifier, UniversalComputerApprentice  # noqa: E402
from bossman.apprentice.guards import ApprovalRegistry, SideEffectLedger  # noqa: E402
from bossman.apprentice.models import (ApprenticeTask, AppIdentity, EffectReceipt, PlanStep,  # noqa: E402
                                       RiskClass, SemanticTarget)
from bossman.apprentice.outreach import (OutreachGate, OutreachPackage, approve_outreach,  # noqa: E402
                                         build_lead_card)
from bossman.computer_operator.models import ActionKind, ExpectedState  # noqa: E402
from fixtures.apprentice.sim import Element, ScriptedPlanner, SimActuator, SimObserver, World  # noqa: E402

TIMEOUT = 15.0          # generous: every wait below is released by an explicit event, never by a sleep
SID = "se-f2"
RECEIPT = {"receipt_id": "R-1", "action_type": "CLICK", "evidence_source": "sim"}


def _store(tmp_path: Path, name: str = "safety.db") -> DurableSafetyStore:
    return DurableSafetyStore(tmp_path / name)


class _RecordingStore(DurableSafetyStore):
    """Durable store that remembers which OS thread performed each ledger operation.

    Used only to answer the reachability question: does any production caller abandon a
    claim it does not own?
    """

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.ops: list[tuple[str, str, int]] = []

    def claim_side_effect(self, side_effect_id: str):
        self.ops.append(("claim", side_effect_id, threading.get_ident()))
        return super().claim_side_effect(side_effect_id)

    def complete_side_effect(self, side_effect_id: str, receipt: dict) -> None:
        self.ops.append(("complete", side_effect_id, threading.get_ident()))
        super().complete_side_effect(side_effect_id, receipt)

    def abandon_side_effect(self, side_effect_id: str) -> None:
        self.ops.append(("abandon", side_effect_id, threading.get_ident()))
        super().abandon_side_effect(side_effect_id)


# ---------------------------------------------------------------- R1/R2: the race itself
def test_abandon_of_an_inflight_claim_loses_the_receipt_and_reopens_the_id(tmp_path):
    """A claims -> A blocks on an event -> B abandons -> A records its receipt.

    Fully deterministic: A never proceeds until B's abandon has committed, B never runs
    until A holds the claim.  No sleeps.
    """
    store = _store(tmp_path)
    claimed, prior = store.claim_side_effect(SID)
    assert (claimed, prior) == (True, None)

    a_holds_claim = threading.Event()
    b_abandoned = threading.Event()
    outcome: dict[str, object] = {}

    def executor_a() -> None:
        a_holds_claim.set()                                  # A owns the claim, external work starts here
        assert b_abandoned.wait(TIMEOUT), "B never abandoned"
        try:                                                 # external effect finished; record the receipt
            store.complete_side_effect(SID, RECEIPT)
            outcome["error"] = None
        except Exception as exc:                             # noqa: BLE001 — the store's refusal is the measurement
            outcome["error"] = exc

    thread = threading.Thread(target=executor_a, name="executor-A")
    thread.start()
    assert a_holds_claim.wait(TIMEOUT), "A never started"
    store.abandon_side_effect(SID)                           # B: watchdog/orchestrator abandons an in-flight claim
    b_abandoned.set()
    thread.join(TIMEOUT)
    assert not thread.is_alive()

    # R1 — the receipt of a side effect that really happened must not be lost.
    assert outcome["error"] is None, (
        "receipt LOST: the executor that owned the claim could not record its receipt after a "
        f"concurrent abandon ({outcome['error']!r})")
    # R2 — and the id must stay closed, so a retry cannot repeat the external effect.
    reclaimed, _ = store.claim_side_effect(SID)
    assert reclaimed is False, "id became RE-CLAIMABLE after abandon: a retry can repeat the external effect"

    store.close()
    restarted = DurableSafetyStore(tmp_path / "safety.db")   # what survives a restart
    again, _ = restarted.claim_side_effect(SID)
    assert again is False, "after a restart the abandoned-but-executed effect is claimable again"
    restarted.close()


# ---------------------------------------------------------------- R2: the reachable harm
class _EffectfulBadReceiptActuator(SimActuator):
    """Performs the external effect for real, then returns a receipt the engine rejects.

    Models the realistic production case: the click/send actually happened but the
    receipt is unusable (clock skew here; a missing evidence_source or a mismatched
    action_id behave the same).
    """

    def __init__(self, world: World) -> None:
        super().__init__(world)
        self.external_effects: list[str] = []

    def act(self, step, obs, *, action_id: str = "", side_effect_id: str = ""):
        result = self._do(step, obs)
        if not side_effect_id:
            return result
        self.external_effects.append(side_effect_id)         # <-- the irreversible thing happened
        return EffectReceipt(side_effect_id=side_effect_id, action_id=action_id, action_type=step.kind.value,
                             observed_at=time.time() + 10_000.0,      # beyond allowed_skew_s -> receipt_invalid
                             evidence_source="sim:actuator")


def _world() -> World:
    world = World(app="Notes", title="Untitled - Notes", url="")

    def save(w: World) -> None:
        w.summary = "Saved"

    world.elements = [Element("button", "Save", neighbors=["File"], on_click=save)]
    return world


def _send_step() -> PlanStep:
    return PlanStep("s1", ActionKind.CLICK, AppIdentity(app="Notes", title_contains="Untitled"),
                    SemanticTarget("button", "Save"), side_effecting=True,
                    expected=ExpectedState(contains_text="Saved"), checkpoint="saved", is_goal=True,
                    risk=RiskClass.MEDIUM)


def test_receipt_invalid_abandon_lets_a_retry_repeat_the_external_effect(monkeypatch, tmp_path):
    """Single-threaded, production-shaped: this is how F2's harm is actually reachable."""
    monkeypatch.setenv(flags.MASTER, "1")
    world = _world()
    actuator = _EffectfulBadReceiptActuator(world)
    store = _RecordingStore(tmp_path / "safety.db")
    engine = UniversalComputerApprentice(planner=ScriptedPlanner([_send_step()]), observer=SimObserver(world),
                                         actuator=actuator, ledger=SideEffectLedger(store),
                                         verifier=DefaultVerifier({"saved": lambda o: (o.summary == "Saved", "")}))
    task = ApprenticeTask.create("save a note", session_id="sess_f2", run_id="run_f2", max_recoveries=1)
    engine.run(task)

    assert len(set(actuator.external_effects)) <= 1, "sanity: the retry must reuse the same side_effect_id"
    assert len(actuator.external_effects) == 1, (
        f"DUPLICATE external effect: the actuator performed the same side effect "
        f"{len(actuator.external_effects)} times because the receipt_invalid branch abandoned "
        f"(DELETEd) a claim whose effect may already have happened, re-opening the id")


# ---------------------------------------------------------------- R3/R4: guards that limit the blast radius
def test_abandon_after_complete_never_destroys_a_stored_receipt(tmp_path):
    store = _store(tmp_path)
    assert store.claim_side_effect(SID)[0] is True
    store.complete_side_effect(SID, RECEIPT)
    store.abandon_side_effect(SID)                            # late abandon, receipt already durable
    claimed, prior = store.claim_side_effect(SID)
    assert claimed is False and prior == RECEIPT
    assert store.side_effect_seen(SID) is True
    store.close()


def test_a_crash_between_claim_and_complete_does_not_auto_release_the_claim(tmp_path):
    store = _store(tmp_path)
    assert store.claim_side_effect(SID)[0] is True
    store.close()                                             # models a crash: no complete, no abandon
    restarted = DurableSafetyStore(tmp_path / "safety.db")
    claimed, prior = restarted.claim_side_effect(SID)
    assert claimed is False and prior is None, "a crashed claim must stay claimed (never auto-released)"
    restarted.close()


# ---------------------------------------------------------------- R5: reachability of a cross-owner abandon
LISTING = {"business_id": "b1", "name": "Blue Bakery", "category": "bakery", "city": "Lisbon", "website": "",
           "phone": "+351 000", "public_email": "hello@bluebakery.example", "maps_url": "https://maps.example/b1",
           "rating": 4.6, "reviews_count": 120, "source": "google_maps_public"}


def _package() -> OutreachPackage:
    card = build_lead_card(LISTING, site_probe={"status": "no_site"})
    return OutreachPackage(card=card, reason="no website found for a well-rated bakery",
                           demo_ref="demo://bluebakery-v1", proposal_text="Hi Blue Bakery, here is a demo site.",
                           recipient="hello@bluebakery.example", created_at=1_000.0)


def test_concurrent_outreach_sender_that_loses_the_claim_abandons_nothing(monkeypatch, tmp_path):
    """B runs while A's transport is still in flight; B must refuse and touch no claim."""
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    clock = lambda: 1_000.0                                   # noqa: E731
    store = _RecordingStore(tmp_path / "safety.db")
    a_in_transport = threading.Event()
    b_done = threading.Event()
    sent: list[str] = []

    def transport(package: OutreachPackage) -> dict:
        sent.append(package.recipient)                        # the message really leaves here
        a_in_transport.set()
        assert b_done.wait(TIMEOUT), "B never finished"
        return {"id": "msg-1"}

    gate = OutreachGate(ledger=SideEffectLedger(store), approvals=ApprovalRegistry(clock, store),
                        transport=transport, clock=clock)
    package = _package()
    approval = approve_outreach("t1", package, approver="human:owner", nonce="n1", expires_at=None)
    results: dict[str, object] = {}

    thread = threading.Thread(target=lambda: results.__setitem__("a", gate.send("t1", package, approval)),
                             name="sender-A")
    thread.start()
    assert a_in_transport.wait(TIMEOUT), "A never reached the transport"
    results["b"] = gate.send("t1", package, approval)         # B: concurrent duplicate attempt
    b_done.set()
    thread.join(TIMEOUT)
    assert not thread.is_alive()

    assert results["b"].sent is False and "duplicate" in results["b"].reason
    assert results["a"].sent is True
    assert sent == ["hello@bluebakery.example"], "the concurrent attempt must not produce a second message"
    assert [op for op, _sid, _t in store.ops if op == "abandon"] == [], \
        "a worker that lost the claim called abandon: cross-owner abandon IS reachable"
    assert store.claim_side_effect(package.side_effect_id())[0] is False
    store.close()


def test_every_production_abandon_comes_from_the_thread_that_owns_the_claim(monkeypatch, tmp_path):
    """Engine (actuator error + receipt_invalid) and outreach (transport error) abandon paths.

    The severity of F2 hinges on this: if abandon is always same-thread and post-actuator,
    Fable's concurrent-abandoner exploit has no production trigger.
    """
    monkeypatch.setenv(flags.MASTER, "1")
    monkeypatch.setenv(flags.EXTERNAL_OUTREACH, "1")
    store = _RecordingStore(tmp_path / "safety.db")

    # --- engine: actuator raises after the claim ---------------------------------
    world = _world()

    class _RaisingActuator(SimActuator):
        def act(self, step, obs, *, action_id: str = "", side_effect_id: str = ""):
            raise RuntimeError("actuator exploded")

    engine = UniversalComputerApprentice(planner=ScriptedPlanner([_send_step()]), observer=SimObserver(world),
                                         actuator=_RaisingActuator(world), ledger=SideEffectLedger(store),
                                         verifier=DefaultVerifier({"saved": lambda o: (o.summary == "Saved", "")}))
    engine.run(ApprenticeTask.create("save a note", session_id="sess_f2b", run_id="run_f2b", max_recoveries=1))

    # --- outreach: transport raises after the claim ------------------------------
    clock = lambda: 1_000.0                                   # noqa: E731

    def boom(_package: OutreachPackage) -> dict:
        raise RuntimeError("transport down")

    gate = OutreachGate(ledger=SideEffectLedger(store), approvals=ApprovalRegistry(clock, store),
                        transport=boom, clock=clock)
    package = _package()
    gate.send("t1", package, approve_outreach("t1", package, approver="human:owner", nonce="n2", expires_at=None))

    abandons = [(sid, tid) for op, sid, tid in store.ops if op == "abandon"]
    claims = {sid: tid for op, sid, tid in store.ops if op == "claim"}
    assert abandons, "expected both failure paths to abandon their own claim"
    for sid, tid in abandons:
        assert claims.get(sid) == tid, f"abandon of {sid} came from a thread that did not claim it"
        assert tid == threading.get_ident(), f"abandon of {sid} did not run on the caller's own thread"
    store.close()


@pytest.mark.parametrize("state_changer", ["complete", "abandon"])
def test_abandon_is_scoped_to_claimed_rows_only(tmp_path, state_changer):
    """Pin the WHERE state='claimed' scope: abandon is a no-op on anything else."""
    store = _store(tmp_path, f"{state_changer}.db")
    assert store.claim_side_effect(SID)[0] is True
    if state_changer == "complete":
        store.complete_side_effect(SID, RECEIPT)
    else:
        store.abandon_side_effect(SID)
    before = store.side_effect_seen(SID)
    store.abandon_side_effect(SID)                            # second abandon must never change anything
    assert store.side_effect_seen(SID) is before
    store.close()
