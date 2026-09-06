from __future__ import annotations

import asyncio
import multiprocessing as mp

import pytest

from bossman.computer_operator.models import (
    ActionKind,
    ComputerAction,
    ExpectedState,
    TaskState,
)
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager
from bossman.learning_guard.evidence_ledger import DurableEvidenceLedger, EvidenceLedger


def complete():
    return ComputerAction.make(ActionKind.COMPLETE)


def click():
    return ComputerAction.make(
        ActionKind.CLICK,
        expected=ExpectedState(contains_text="ok"),
    )


def test_at01_complete_without_effect_evidence_must_not_finalize(tmp_path):
    adapter = FakeAdapter()
    mgr = make_manager(
        tmp_path / "task.json",
        FakePlanner([complete()]),
        FakeObserver(summary="empty desktop"),
        adapter=adapter,
    )
    task = mgr.create_task(f"Create the required output file: {tmp_path / 'required-output.txt'}")
    state = asyncio.run(mgr.run(task.id))
    assert adapter.executed == []
    assert state is not TaskState.COMPLETED


def test_at01_verified_effect_can_complete(tmp_path):
    adapter = FakeAdapter()
    mgr = make_manager(
        tmp_path / "task.json",
        FakePlanner([click(), complete()]),
        FakeObserver(summary="ok"),
        adapter=adapter,
    )
    task = mgr.create_task("Click the fixture and finish")
    assert asyncio.run(mgr.run(task.id)) is TaskState.COMPLETED
    assert len(adapter.executed) == 1


def test_at01_non_effect_task_can_complete(tmp_path):
    mgr = make_manager(
        tmp_path / "task.json",
        FakePlanner([complete()]),
        FakeObserver(summary="ready"),
    )
    task = mgr.create_task("Inspect the current screen and report whether it is ready")
    assert asyncio.run(mgr.run(task.id)) is TaskState.COMPLETED


@pytest.mark.parametrize("owner_command, expected", [
    ("pause", TaskState.PAUSED),
    ("take_control", TaskState.USER_CONTROL),
])
def test_at02_owner_state_survives_restart(tmp_path, owner_command, expected):
    path = tmp_path / "task.json"
    mgr = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    task = mgr.create_task("owner recovery fixture")
    getattr(mgr, owner_command)(task.id)

    reopened = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    reopened.recover_all()
    assert reopened.store.get(task.id).state is expected
    assert asyncio.run(reopened.run(task.id)) is expected


def test_at02_waiting_approval_survives_restart(tmp_path):
    path = tmp_path / "task.json"
    mgr = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    task = mgr.create_task("approval fixture")
    task.state = TaskState.WAITING_APPROVAL
    task.waiting_approval_id = 77
    mgr._save(task)

    reopened = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    reopened.recover_all()
    fresh = reopened.store.get(task.id)
    assert fresh.state is TaskState.WAITING_APPROVAL
    assert fresh.waiting_approval_id == 77
    assert asyncio.run(reopened.run(task.id)) is TaskState.WAITING_APPROVAL


def test_at02_unknown_effect_stays_in_reconciliation(tmp_path):
    path = tmp_path / "task.json"
    mgr = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    task = mgr.create_task("click fixture")
    action = click()
    from bossman.computer_operator.models import StepRecord
    task.state = TaskState.RUNNING
    task.pending_action = action
    task.history.append(StepRecord(action=action))
    mgr._save(task)

    reopened = make_manager(path, FakePlanner([click()]), FakeObserver(summary="ok"))
    reopened.recover_all()
    fresh = reopened.store.get(task.id)
    assert fresh.state is TaskState.RECOVERING
    assert fresh.pending_action is not None
    assert "reconciliation required" in (fresh.last_error or "")
    assert asyncio.run(reopened.run(task.id)) is TaskState.RECOVERING


def test_at03_ui_change_during_planning_prevents_dispatch(tmp_path):
    adapter = FakeAdapter()
    observer = FakeObserver(observations=[
        {"summary": "ok", "foreground": {"title": "Fixture", "state_revision": 1}},
        {"summary": "ok", "foreground": {"title": "Fixture", "state_revision": 2}},
    ])
    mgr = make_manager(
        tmp_path / "task.json",
        FakePlanner([click(), complete()]),
        observer,
        adapter=adapter,
        observation_reuse_max_age_s=.75,
    )
    task = mgr.create_task("Click the fixture")
    state = asyncio.run(mgr.run(task.id))
    assert adapter.executed == []
    assert state is not TaskState.COMPLETED


def test_at04_eviction_never_reauthorizes_spent_evidence():
    ledger = EvidenceLedger(capacity=2)
    assert ledger.consume("measurement-A", "candidate-v1") is None
    assert ledger.consume("measurement-B", "candidate-v2") is None
    assert ledger.consume("measurement-C", "candidate-v3") is not None
    assert ledger.consume("measurement-A", "candidate-v9") is not None


def test_at04_corruption_is_fail_closed(tmp_path):
    path = tmp_path / "evidence.json"
    ledger = DurableEvidenceLedger(path)
    assert ledger.consume("measurement-A", "candidate-v1") is None
    path.write_text("{incomplete", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        DurableEvidenceLedger(path).consume("measurement-A", "candidate-v9")


def _consume_worker(path: str, consumer: str, gate, queue):
    gate.wait()
    result = DurableEvidenceLedger(path).consume("measurement-A", consumer)
    queue.put((consumer, result))


def test_at04_multiprocess_single_consumer(tmp_path):
    path = str(tmp_path / "evidence.json")
    ctx = mp.get_context("spawn")
    gate = ctx.Event()
    queue = ctx.Queue()
    p1 = ctx.Process(target=_consume_worker, args=(path, "candidate-v1", gate, queue))
    p2 = ctx.Process(target=_consume_worker, args=(path, "candidate-v9", gate, queue))
    p1.start(); p2.start(); gate.set()
    p1.join(10); p2.join(10)
    assert p1.exitcode == 0
    assert p2.exitcode == 0
    results = [queue.get(timeout=2), queue.get(timeout=2)]
    assert sum(result is None for _, result in results) == 1


def test_at04_restart_same_consumer_is_idempotent(tmp_path):
    path = tmp_path / "evidence.json"
    assert DurableEvidenceLedger(path).consume("measurement-A", "candidate-v1") is None
    assert DurableEvidenceLedger(path).consume("measurement-A", "candidate-v1") is None
    assert DurableEvidenceLedger(path).consume("measurement-A", "candidate-v2") is not None
