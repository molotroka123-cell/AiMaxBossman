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
    """AT-03: экран НЕ менялся — акция всё равно исполняется против свежего
    наблюдения, снятого после ожидания, а не против снимка до него."""
    observer = ShiftingObserver(change_on_call=99, summary="ok")
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
    assert len(observer.returned) >= 3        # re-observe + пост-наблюдение
    assert mgr.stale_boundaries == 0


async def test_approval_does_not_travel_to_a_changed_screen(tmp_path):
    """AT-03, граница эффекта: владелец одобрил ЭТО действие ПРОТИВ ЭТОГО экрана.

    Пока он думал, передний план сменился. Свежего наблюдения мало: клик
    «оплатить» был нацелен в конкретное окно, и переносить на другое окно уже
    выданное разрешение нельзя. TTL и generation здесь ничего не сказали бы —
    задача та же и не прерывалась. Правильный исход — не исполнять, а
    перепланировать (новое действие получит новое одобрение).
    """
    observer = ShiftingObserver(change_on_call=2, summary="old screen")
    adapter = RecordingAdapter()
    mgr, created = await _approval_manager(tmp_path, observer, adapter)
    t = mgr.create_task("pay invoice")
    rt = asyncio.create_task(mgr.run(t.id))
    await asyncio.wait_for(created.wait(), 5)
    state = await asyncio.wait_for(rt, 5)
    assert adapter.executed == []             # эффекта не произошло вовсе
    assert state is not TaskState.COMPLETED
    assert mgr.store.get(t.id).terminal
    assert mgr.stale_boundaries == 1
    step = mgr.store.get(t.id).history[0]
    assert step.verified is None and "not dispatched" in (step.error or "")


async def test_ui_change_during_planning_prevents_dispatch(tmp_path):
    """AT-03, вторая граница: между наблюдением и намерением прошёл вызов модели.

    Попап/смена фокуса за время планирования не меняет ни generation, ни TTL
    наблюдения, поэтому одних этих признаков недостаточно — экран спрашивают
    заново прямо перед эффектом.
    """
    adapter = FakeAdapter()
    observer = FakeObserver(summary="ok", foreground={"app": "notepad.exe", "title": "Untitled"},
                            probes=[{"foreground": {"app": "notepad.exe", "title": "Save as"}}])
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]),
                       observer, adapter=adapter)
    t = mgr.create_task("click the button")
    state = await mgr.run(t.id)
    assert adapter.executed == []             # по устаревшему экрану не действуем
    assert state is not TaskState.COMPLETED
    assert mgr.stale_boundaries == 1
    assert observer.probe_calls >= 1


async def test_unchanged_screen_during_planning_still_dispatches(tmp_path):
    """Положительный контроль к предыдущему: экран тот же — действие исполняется,
    и проверка применимости обслуживается дешёвым probe, а не вторым полным
    наблюдением."""
    adapter = FakeAdapter()
    observer = FakeObserver(summary="ok", foreground={"app": "notepad.exe", "title": "Untitled"})
    mgr = make_manager(tmp_path / "t.json", FakePlanner([click(), complete()]),
                       observer, adapter=adapter)
    t = mgr.create_task("click the button")
    assert await mgr.run(t.id) is TaskState.COMPLETED
    assert [a.kind for a in adapter.executed] == [ActionKind.CLICK]
    assert mgr.stale_boundaries == 0
    assert mgr.boundary_probes == 1 and observer.probe_calls == 1


# ------------------------------------------------- AT-01, вторая половина улики

async def test_true_postcondition_the_planner_could_already_see_is_not_a_result(tmp_path):
    """AT-01: постусловие пишет сам планировщик по экрану, который сам и прочитал.

    «создай файл» закрывается фразой «desktop» — и она ПРАВДИВА. Одной проверки
    постусловия мало: для цели, обещающей внешний результат, нужен хотя бы один
    подтверждённый изменяющий шаг.
    """
    adapter = FakeAdapter()
    # Планировщик стоит на своём весь бюджет: терминальная причина обязана быть
    # отказом улики, а не «планировщик кончился».
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState(contains_text="desktop"))
                                    for _ in range(25)]),
                       FakeObserver(summary="empty desktop"), adapter=adapter)
    t = mgr.create_task(r"создай файл C:\tmp\result.txt")
    state = await mgr.run(t.id)
    assert state is TaskState.FAILED
    assert adapter.executed == []
    assert mgr.completions_refused >= 1
    # Причина отказа обязана дожить до владельца: исчерпание бюджета раньше
    # затирало её собой, и «почему не закрылось» приходилось угадывать.
    failure = mgr.store.get(t.id).last_error
    assert failure.startswith("completion evidence budget")
    assert "completion refused" in failure, failure


async def test_observation_goal_needs_no_effect_evidence(tmp_path):
    """AT-01, отдельный контроль для целей без внешнего эффекта: наблюдательная
    цель ничего снаружи не обещала и закрывается одним проверенным постусловием,
    без единого действия."""
    adapter = FakeAdapter()
    mgr = make_manager(tmp_path / "t.json",
                       FakePlanner([complete(expected=ExpectedState(contains_text="ready"))]),
                       FakeObserver(summary="the pane says ready"), adapter=adapter)
    t = mgr.create_task("describe what the screen shows right now")
    assert await mgr.run(t.id) is TaskState.COMPLETED
    assert adapter.executed == []


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
