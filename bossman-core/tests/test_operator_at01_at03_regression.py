"""AT-01/AT-03 regressions (docs/testing/TOTAL_LOCAL_ACCEPTANCE_20260906.md §7).

AT-01: COMPLETE без проверяемого постусловия — или с постусловием, которое не
подтверждается наблюдением, — НЕ делает задачу COMPLETED: replan, а после
исчерпания бюджета — честный FAILED. Положительный контроль: COMPLETE с
постусловием, подтверждённым наблюдением, завершает задачу как раньше.

AT-03: наблюдение, по которому действие планировалось и одобрялось, могло
устареть за время ожидания владельца. Перед эффектом оператор обязан снять
СВЕЖЕЕ наблюдение; акция исполняется против него, и если policy по нему
более не пропускает акцию — акция не исполняется вовсе.
"""
import asyncio

from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState,
                                              TaskMode, TaskState)
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def click(**kw):
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"), **kw)


def complete(**kw):
    kw.setdefault("expected", ExpectedState(contains_text="ok"))
    return ComputerAction.make(ActionKind.COMPLETE, **kw)


# ------------------------------------------------------------------ AT-01

async def test_bare_complete_is_never_a_result(tmp_path):
    """AT-01: эффектная цель ('создай файл'), планировщик сразу отвечает
    COMPLETE без постусловия — итог не COMPLETED."""
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState())]),
                       FakeObserver(summary="desktop"), adapter=adapter)
    t = mgr.create_task(r"создай файл C:\tmp\result.txt")
    state = await mgr.run(t.id)
    assert state is TaskState.FAILED
    z = mgr.store.get(t.id)
    assert z.state is not TaskState.COMPLETED and z.terminal
    assert z.replans_used >= 1                # менеджер потребовал постусловие
    assert adapter.executed == []


async def test_complete_with_unmet_postcondition_is_not_completed(tmp_path):
    """AT-01: COMPLETE ссылается на результат, которого нет на экране."""
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState(contains_text="result.txt saved"))]),
                       FakeObserver(summary="empty desktop"), adapter=adapter)
    t = mgr.create_task("создай файл result.txt")
    state = await mgr.run(t.id)
    assert state is TaskState.FAILED
    z = mgr.store.get(t.id)
    assert z.state is not TaskState.COMPLETED and z.terminal
    assert z.replans_used >= 1
    assert adapter.executed == []


async def test_complete_is_verified_against_the_actual_observation(tmp_path):
    """AT-01: ложное COMPLETE не проходит, если постусловие противоречит
    фактическому состоянию экрана."""
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState(contains_text="invoice paid"))]),
                       FakeObserver(summary="invoice OVERDUE"), adapter=adapter)
    t = mgr.create_task("оплатить счёт")
    assert await mgr.run(t.id) is TaskState.FAILED
    assert adapter.executed == []


async def test_verified_complete_completes(tmp_path):
    """AT-01 положительный контроль: постусловие подтверждено — COMPLETED."""
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([click(), complete()]),
                       FakeObserver(summary="ok"), adapter=FakeAdapter())
    t = mgr.create_task("one click then done")
    assert await mgr.run(t.id) is TaskState.COMPLETED


async def test_informational_complete_with_observable_postcondition_completes(tmp_path):
    """AT-01 контроль цели без внешнего эффекта: проверяемое постусловие
    наблюдаемо — задача завершается без единого действия."""
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState(contains_text="answer"))]),
                       FakeObserver(summary="answer on screen"), adapter=FakeAdapter())
    t = mgr.create_task("найди ответ на экране")
    assert await mgr.run(t.id) is TaskState.COMPLETED


# ------------------------------------------------------------------ AT-03

class ShiftingObserver(FakeObserver):
    """После N-го наблюдения экран меняется (попап/фокус/вкладка)."""

    def __init__(self, change_on_call=2, **kw):
        super().__init__(**kw)
        self.change_on_call = change_on_call
        self.returned = []

    async def observe(self, *, generation):
        if len(self.returned) + 1 >= self.change_on_call:
            self.summary = "changed screen ok"
            self.foreground = {"app": "other.exe", "title": "changed"}
        obs = await super().observe(generation=generation)
        self.returned.append(obs)
        return obs


class RecordingAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.obs_seen = None

    async def execute(self, a, o):
        self.obs_seen = o
        return await super().execute(a, o)


async def _approval_manager(tmp_path, observer, adapter, wait_hook=None, actions=None):
    created = asyncio.Event()

    async def create(kind, preview, tool=None, payload=None):
        created.set()
        return 1

    async def wait(approval_id, timeout_s=None):
        if wait_hook is not None:
            await wait_hook()
        return {"status": "approved", "id": approval_id}

    planner = FakePlanner(actions if actions is not None else [click(args={"semantic": "pay"}), complete()])
    mgr = make_manager(tmp_path / "t.json", planner,
                       observer, adapter=adapter, approval_create=create, approval_wait=wait)
    return mgr, created


async def test_action_after_approval_executes_on_a_fresh_observation(tmp_path):
    """AT-03: экран сменился за время одобрения — акция исполняется против
    свежего наблюдения, а не против снимка до ожидания."""
    observer = ShiftingObserver(change_on_call=2, summary="old screen")
    adapter = RecordingAdapter()
    mgr, created = await _approval_manager(tmp_path, observer, adapter)
    t = mgr.create_task("pay invoice")
    rt = asyncio.create_task(mgr.run(t.id))
    await asyncio.wait_for(created.wait(), 5)
    state = await asyncio.wait_for(rt, 5)
    assert state is TaskState.COMPLETED
    # исполнение получило СВЕЖЕЕ наблюдение (второе), а не снимок до ожидания
    assert adapter.obs_seen is observer.returned[1]
    assert adapter.obs_seen is not observer.returned[0]
    assert adapter.obs_seen.summary == "changed screen ok"
    assert len(observer.returned) >= 3        # re-observe + пост-наблюдение


async def test_action_after_approval_denied_when_policy_no_longer_allows(tmp_path):
    """AT-03: если по свежему наблюдению policy более не пропускает акцию
    (режим сменился на OBSERVE_ONLY за время ожидания) — акция НЕ исполняется."""
    observer = ShiftingObserver(change_on_call=99, summary="ok")
    adapter = RecordingAdapter()
    holder = {}

    async def switch_mode():
        t = mgr.store.get(holder["task_id"])
        t.mode = TaskMode.OBSERVE_ONLY
        mgr.store.save(t)

    mgr, created = await _approval_manager(tmp_path, observer, adapter, wait_hook=switch_mode,
                                           actions=[click(args={"semantic": "pay"})])
    t = mgr.create_task("pay invoice")
    holder["task_id"] = t.id
    rt = asyncio.create_task(mgr.run(t.id))
    await asyncio.wait_for(created.wait(), 5)
    state = await asyncio.wait_for(rt, 5)
    assert state is TaskState.FAILED
    assert adapter.executed == []             # одобрение не пробило свежий отказ
    assert len(observer.returned) >= 2        # отказ случился после re-observe
    z = mgr.store.get(t.id)
    assert z.state is not TaskState.COMPLETED and z.terminal
