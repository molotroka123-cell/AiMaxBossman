"""AT-01 through the production ComputerOperatorManager with fixture adapters.

Prepared for sequential execution by Opus; NOT RUN in the partial-source audit.
Run separately from root tests so Core retains ownership of its conftest.
The observer/planner/input adapter are doubles; filesystem checks are independent.
These tests do NOT prove live desktop capability or activate standing autonomy.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskState
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def _click():
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="desktop ready"))


def _complete():
    # Deliberately an observable but goal-irrelevant postcondition. A completion
    # gate must bind to the OWNER'S obligation, not the planner's chosen text.
    return ComputerAction.make(ActionKind.COMPLETE,
                               expected=ExpectedState(contains_text="desktop ready"))


class LocalFixtureAdapter(FakeAdapter):
    def __init__(self, writes):
        super().__init__()
        self.writes = writes

    async def execute(self, action, observation):
        for path, data in self.writes:
            path.write_bytes(data)
        return await super().execute(action, observation)


def _run(tmp_path, goal, writes, *, readonly=False):
    adapter = LocalFixtureAdapter(writes)
    actions = [_complete()] if readonly else [_click(), _complete()]
    manager = make_manager(tmp_path / "operator.json", FakePlanner(actions),
                           FakeObserver(summary="desktop ready"), adapter=adapter)
    task = manager.create_task(goal)
    state = asyncio.run(manager.run(task.id))
    return state, manager.store.get(task.id), adapter


@pytest.mark.parametrize("case", ["missing", "wrong-content", "old-file", "partial-obligations"])
def test_at01_unrelated_verified_mutation_cannot_complete_file_goal(tmp_path, case, record_property):
    record_property("evidence_tier", "MOCK")
    record_property("scope", "production_manager_with_fixture_adapters_not_live_UI")
    expected = ("BOSSMAN-AT01-" + uuid.uuid4().hex).encode()
    target, second, decoy = tmp_path / "required.txt", tmp_path / "required-2.txt", tmp_path / "decoy.txt"
    writes = [(decoy, b"unrelated but genuinely written fixture")]
    goal = f"Создай файл {target} с точным содержимым {expected.decode()}"
    if case == "wrong-content":
        writes.append((target, b"incorrect fresh bytes"))
    elif case == "old-file":
        target.write_bytes(b"STALE-PREVIOUS-MISSION-CONTENT")
        os.utime(target, (1, 1))
    elif case == "partial-obligations":
        writes.append((target, expected))
        goal += f" и файл {second} с тем же точным содержимым; оба обязательны"
    state, task, adapter = _run(tmp_path, goal, writes)
    # Independent oracle observes the real fixture after execution, not a model
    # assertion or helper return value. The test is not satisfied by no dispatch.
    assert decoy.read_bytes() == b"unrelated but genuinely written fixture"
    assert len(adapter.executed) >= 1
    if case == "partial-obligations":
        assert target.read_bytes() == expected and not second.exists()
    else:
        assert not target.exists() or target.read_bytes() != expected
    assert state is not TaskState.COMPLETED, (case, task)


def test_at01_readonly_observation_can_complete_without_mutation(tmp_path, record_property):
    record_property("evidence_tier", "MOCK")
    state, _, adapter = _run(tmp_path, "Опиши текущий экран", [], readonly=True)
    assert state is TaskState.COMPLETED
    assert adapter.executed == []


def test_at01_exact_requested_file_positive_control(tmp_path, record_property):
    record_property("evidence_tier", "MOCK")
    expected = ("BOSSMAN-AT01-" + uuid.uuid4().hex).encode()
    target = tmp_path / "positive.txt"
    state, _, adapter = _run(tmp_path,
        f"Создай файл {target} с точным содержимым {expected.decode()}", [(target, expected)])
    assert target.read_bytes() == expected
    assert len(adapter.executed) == 1
    assert state is TaskState.COMPLETED
