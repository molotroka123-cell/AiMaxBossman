"""TRUTH-003 §12 — receipt привязан к fence: журнал не переписывает закрытый шаг,
флот не принимает receipt, записанный под устаревшим fence после переназначения,
а нормальное продолжение (старый fence ДО нового lease) остаётся уликой."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

import bossman._shared  # noqa: F401
from bossman_shared.action_receipt import ActionReceipt
from bossman_v3.contracts import SideEffectClass, TypedAction
from bossman_v3.execution import PlanStep
from bossman_v3.fleet import FlightState
from bossman_v3.fleet.control_plane import _journal_evidence, _stale_fence_receipt
from bossman_v3.fleet.models import FlightRecord
from bossman_v3.memory.journal import JournalConflict, TaskJournal
from test_v3_fleet_e2e import Stack, _contract


def _receipt(step, fence):
    return ActionReceipt(task_id="m1__w1", step_id=step, capability="fs.write", tool="fs.write",
                         effect_type="IDEMPOTENT_WRITE", started_at="2026-09-05T12:00:00+00:00",
                         finished_at="2026-09-05T12:00:01+00:00", observed_at="2026-09-05T12:00:02+00:00",
                         executor_status="executed", observation_type="post_state", verification_status="VERIFIED",
                         fencing_token=fence).to_dict()


def test_journal_refuses_to_overwrite_a_finished_step(tmp_path):
    j = TaskJournal.start(task_id="m1__w1", plan=[("s1", "a"), ("s2", "b")], root=tmp_path)
    j.record("s1", receipt=_receipt("s1", 42), verified=True, by="B")
    sig = j.steps[0].sig
    with pytest.raises(JournalConflict):
        j.record("s1", receipt=_receipt("s1", 41), verified=True, by="A-zombie")
    reloaded = TaskJournal.load(task_id="m1__w1", root=tmp_path)
    assert reloaded.steps[0].sig == sig and reloaded.steps[0].receipt["fencing_token"] == 42
    j.fail("s2", error="x")                                    # незакрытый шаг менять можно
    j.record("s2", receipt=_receipt("s2", 42), verified=True)  # и закрыть — один раз
    with pytest.raises(JournalConflict):
        j.record("s2", receipt=_receipt("s2", 42), verified=True)


def test_zombie_receipt_under_stale_fence_is_not_evidence(tmp_path):
    """A (fence 41) пишет s2 ПОСЛЕ того, как B получил fence 42 → не улика, TASK_REJECTED.
    s1, записанный A под fence 41 ДО нового lease, остаётся уликой (продолжение, не зомби)."""
    plan = [PlanStep("s1", "a", TypedAction("fs.write", {"name": "a"}, side_effect=SideEffectClass.IDEMPOTENT_WRITE)),
            PlanStep("s2", "b", TypedAction("fs.write", {"name": "b"}, side_effect=SideEffectClass.IDEMPOTENT_WRITE))]
    j = TaskJournal.start(task_id="m1__w1", plan=[(s.step_id, s.intent) for s in plan], root=tmp_path)
    j.record("s1", receipt=_receipt("s1", 41), verified=True, by="A")
    t_leased_42 = time.time() + 0.05
    time.sleep(0.1)
    j.record("s2", receipt=_receipt("s2", 41), verified=True, by="A-zombie")      # записан после lease 42
    flight = FlightRecord("w1", "m1", state=FlightState.EXECUTING, node_id="node-2", fence=42,
                          history=[{"from": "PLACED", "to": "LEASED", "ts": t_leased_42}])
    assert not _stale_fence_receipt(j.steps[0], flight) and _stale_fence_receipt(j.steps[1], flight)

    class Journal:
        def __init__(self): self.events = []
        def emit(self, type_, **kw): self.events.append((type_.value, kw["payload"]))
    jr = Journal()
    ev = _journal_evidence(j, plan, flight=flight, journal=jr)
    assert [e.source for e in ev] == ["journal:m1__w1/s1"]
    assert jr.events and jr.events[0][0] == "TASK_REJECTED" and jr.events[0][1]["reason"] == "stale fence receipt"
    assert jr.events[0][1]["receipt_fence"] == 41 and jr.events[0][1]["current_fence"] == 42
    # без fence в receipt или без полёта — старое поведение (нет ложных отказов)
    assert _journal_evidence(j, plan) and len(_journal_evidence(j, plan)) == 2


def test_e2e_receipts_carry_fence_and_fresh_post_state(tmp_path):
    (tmp_path / "stack").mkdir()
    s = Stack(tmp_path / "stack")
    s.org.receive_mission("m1", title="x", department_id="engineering", contracts=[_contract(s.world, "w1", ["a.txt"])])
    status = s.org.run_mission("m1")
    assert status.done
    j = TaskJournal.load(task_id="m1__w1", root=tmp_path / "stack" / "journals")
    step = j.finished()[0]
    rec = ActionReceipt.from_dict(step.receipt)
    f = s.plane.flights.get("w1")
    assert rec.fencing_token == f.fence and rec.verified() and rec.fresh()[0]
    assert rec.observation_type == "post_state" and rec.executor_status == "executed"
    assert rec.idempotency_key == "m1__w1/w1-s1" and rec.request_digest and rec.executor_metadata["node_id"] == f.node_id
    assert step.signature_valid("m1__w1")                                 # подпись шага покрывает receipt
    assert s.plane.flights.duplicate_preventions == 0 and len(s.plane.store.verified_mutations()) == 1
