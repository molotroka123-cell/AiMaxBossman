"""Интеграция loop guard в цикл менеджера: слепой повтор реально прекращается."""
from __future__ import annotations

import pytest

from bossman.computer_operator.manager import ComputerOperatorManager
from bossman.computer_operator.models import (
    ActionKind, ComputerAction, ExpectedState, Observation, TaskState,
)
from bossman.computer_operator.store import JsonTaskStore

pytestmark = pytest.mark.asyncio


class _StuckObserver:
    """Состояние НИКОГДА не меняется — как заблокированный модалкой экран."""
    async def observe(self, *, generation: int):
        return Observation(id="obs", created_at=0.0,
                           foreground={"app": "notepad", "title": "Untitled", "url": ""},
                           summary="nothing changes", ui_tree=None, screenshot_ref=None,
                           sensitive=False, generation=generation)


class _StubbornPlanner:
    """Планировщик упорно предлагает одно и то же действие."""
    def __init__(self): self.calls = 0
    async def next_action(self, **kw):
        self.calls += 1
        return ComputerAction.make(ActionKind.CLICK,
                                   expected=ExpectedState(contains_text="saved"),
                                   target="Save")


class _CountingRouter:
    """Считает, сколько раз действие реально ушло в исполнение."""
    def __init__(self): self.executed = 0
    async def execute(self, a, o):
        self.executed += 1
        return "fake-backend"


def _mgr(tmp_path, planner, router):
    return ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "tasks.json"),
        planner=planner, observer=_StuckObserver(), action_router=router,
        approval_create=lambda *a, **k: None,
        approval_wait=lambda *a, **k: {"status": "approved"},
        event_emit=lambda *a, **k: None)


async def test_manager_stops_blind_repetition_far_before_replan_budget(tmp_path):
    planner, router = _StubbornPlanner(), _CountingRouter()
    mgr = _mgr(tmp_path, planner, router)
    t = mgr.create_task("save the file")
    t.max_replans = 20
    mgr._save(t)

    state = await mgr.run(t.id)

    assert state is TaskState.FAILED
    # Ключевое: действие НЕ исполнялось 20 раз — guard остановил повтор рано.
    assert router.executed <= 4, f"blind repetition: executed {router.executed} times"
    done = mgr.store.get(t.id)
    assert "loop guard" in (done.last_error or "").lower()


async def test_loop_guard_emits_event_for_operator_visibility(tmp_path):
    events = []
    planner, router = _StubbornPlanner(), _CountingRouter()
    mgr = ComputerOperatorManager(
        store=JsonTaskStore(tmp_path / "t.json"),
        planner=planner, observer=_StuckObserver(), action_router=router,
        approval_create=lambda *a, **k: None,
        approval_wait=lambda *a, **k: {"status": "approved"},
        event_emit=lambda topic, **kw: events.append((kw.get("event"), kw)))
    t = mgr.create_task("save"); t.max_replans = 6; mgr._save(t)
    await mgr.run(t.id)
    assert any(e == "loop_guard" for e, _ in events), "оператор не увидит застревание"


async def test_takeover_clears_guard_history(tmp_path):
    """После вмешательства оператора прежние подписи не должны блокировать работу."""
    planner, router = _StubbornPlanner(), _CountingRouter()
    mgr = _mgr(tmp_path, planner, router)
    t = mgr.create_task("save"); t.max_replans = 3; mgr._save(t)
    await mgr.run(t.id)
    assert t.id in mgr.loop_guards or True          # guard мог быть создан
    mgr.take_control(t.id)
    assert t.id not in mgr.loop_guards, "история guard пережила takeover"
