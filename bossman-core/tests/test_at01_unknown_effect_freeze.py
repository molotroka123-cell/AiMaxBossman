"""Freeze regression: an unextractable effect may never be purchased by an unrelated mutation."""
from __future__ import annotations

import asyncio

import pytest

from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskState
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def _typed():
    return ComputerAction.make(
        ActionKind.TYPE,
        text="unrelated",
        expected=ExpectedState(contains_text="invoice"),
    )


def _complete():
    return ComputerAction.make(
        ActionKind.COMPLETE,
        expected=ExpectedState(contains_text="invoice"),
    )


class _UnrelatedMutation(FakeAdapter):
    async def execute(self, action, observation):
        # This mutation is deliberately unrelated to the requested payment.
        # It is still a normal verified mutating operator step.
        return await super().execute(action, observation)


@pytest.mark.asyncio
async def test_pay_invoice_unrelated_verified_mutation_cannot_complete(tmp_path):
    observer = FakeObserver(summary="invoice open")
    manager = make_manager(
        tmp_path / "tasks.json",
        FakePlanner([_typed(), _complete()] + [_complete()] * 30),
        observer,
        adapter=_UnrelatedMutation(),
    )
    task = manager.create_task("pay invoice")

    state = await asyncio.wait_for(manager.run(task.id), timeout=20)
    row = manager.store.get(task.id)

    assert state is not TaskState.COMPLETED
    assert manager.completions_refused >= 1
    assert "not confirmed" in (row.last_error or "") or "verified" in (row.last_error or "")
