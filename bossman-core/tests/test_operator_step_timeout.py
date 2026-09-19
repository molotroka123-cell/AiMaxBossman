"""A3-04: ни одна фаза шага рабочего стола не ждёт бесконечно.

Находка живая, а не теоретическая: зависшее приложение (или застрявшее
UIA/COM-чтение, или не отвечающий браузер) держало `await` внутри шага вечно.
Цикл не возвращался наверх, авторитетную строку задачи никто не перечитывал, и
«Стоп» владельца оставался ЗАПИСАННЫМ, но не исполненным — снаружи это выглядело
как «Bossman не реагирует».

Проверяется здесь ровно то, что отличает исправление от косметики:

* потолок существует и по нему цикл действительно выходит;
* «Стоп» во время зависания исполняется, и быстрее потолка;
* таймаут ОТПРАВЛЕННОГО ввода не превращается в повтор — исход неизвестен,
  и задача уходит на сверку к владельцу, а не действует второй раз;
* поток, доживший до конца ПОСЛЕ таймаута, ввода больше не делает;
* перезапуск после таймаута не переигрывает шаг;
* штатный шаг ничего из этого не задевает (положительный контроль).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from bossman.computer_operator.manager import (ACT_TIMEOUT_S, OBSERVE_TIMEOUT_S, OWNER_POLL_S,
                                               PLAN_TIMEOUT_S, StepTimeout)
from bossman.computer_operator.models import (ActionKind, ComputerAction, ExpectedState, TaskState)
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager

FAST = {"observe_timeout_s": .25, "plan_timeout_s": .25, "act_timeout_s": .25}


def click(**kw):
    return ComputerAction.make(ActionKind.CLICK, expected=ExpectedState(contains_text="ok"),
                               target="кнопка", **kw)


def screenshot():
    return ComputerAction.make(ActionKind.TAKE_SCREENSHOT)


class HangingObserver(FakeObserver):
    """Наблюдение, которое не возвращается: зависшее UIA-чтение."""

    def __init__(self, *, hang_from_call=1, **kw):
        super().__init__(**kw)
        self.hang_from_call = hang_from_call
        self.entered = asyncio.Event()

    async def observe(self, *, generation):
        if len(self.generations) + 1 >= self.hang_from_call:
            self.generations.append(generation)
            self.entered.set()
            await asyncio.Event().wait()          # никогда
        return await super().observe(generation=generation)


class HangingAdapter(FakeAdapter):
    """Ввод отправлен, ответа нет — и поток честно доживает СВОЙ срок.

    Поток настоящий: `asyncio.to_thread` неотменяем, и именно это утверждение
    («после таймаута он больше ничего не вводит») требует проверки.
    """

    def __init__(self, *, hold_s=10.0, **kw):
        super().__init__(**kw)
        self.hold_s = hold_s
        self.entered = asyncio.Event()
        self.interrupt = None
        self.inputs_after_timeout = []
        self.finished = threading.Event()

    def set_interrupt(self, event):
        self.interrupt = event

    async def execute(self, a, o):
        self.executed.append(a)
        self.entered.set()
        await asyncio.to_thread(self._work, a)
        return self.name

    def _work(self, a):
        deadline = time.monotonic() + self.hold_s
        while time.monotonic() < deadline:
            if self.interrupt is not None and self.interrupt.is_set():
                break
            # То, что делал бы настоящий бэкенд: ещё одно нажатие. После
            # прерывания этот список обязан перестать расти.
            if self.interrupt is not None and self.interrupt.is_set():
                self.inputs_after_timeout.append(a.kind)
            time.sleep(.01)
        self.finished.set()


def events():
    seen = []
    return seen, (lambda topic, **kw: seen.append(kw))


# ------------------------------------------------------------- потолок есть

def test_the_shipped_defaults_bound_every_phase():
    """Значения по умолчанию — не None: «безлимитно» не бывает по умолчанию."""
    for value in (OBSERVE_TIMEOUT_S, PLAN_TIMEOUT_S, ACT_TIMEOUT_S):
        assert 0 < value <= 300
    assert OWNER_POLL_S <= 1.0, "«Стоп» не может ждать дольше секунды на опрос"


@pytest.mark.asyncio
async def test_an_observer_that_never_returns_does_not_hang_the_task(tmp_path):
    """Раньше это была ровно та ситуация: `run()` не возвращался никогда."""
    seen, emit = events()
    m = make_manager(tmp_path / "t.json", FakePlanner([click()]),
                     HangingObserver(), event_emit=emit, **FAST)
    t = m.create_task("нажать кнопку")
    state = await asyncio.wait_for(m.run(t.id), timeout=15)
    assert state is TaskState.FAILED
    row = m.store.get(t.id)
    assert "timeout" in (row.last_error or "").lower()
    assert "observe" in (row.last_error or "")
    assert any(e.get("event") == "step_timeout" for e in seen)
    assert m.step_timeouts >= 1
    # Ничего не отправлялось — значит и парковать нечего.
    assert m.unknown_effects_parked == 0


@pytest.mark.asyncio
async def test_a_planner_that_never_answers_does_not_hang_the_task(tmp_path):
    m = make_manager(tmp_path / "t.json", FakePlanner([click()], gate=asyncio.Event()),
                     FakeObserver(), **FAST)
    t = m.create_task("нажать кнопку")
    state = await asyncio.wait_for(m.run(t.id), timeout=15)
    assert state is TaskState.FAILED
    assert "plan" in (m.store.get(t.id).last_error or "")


# --------------------------------------- отправленный ввод != повод повторить

@pytest.mark.asyncio
async def test_a_hung_action_parks_for_reconciliation_and_is_never_retried(tmp_path):
    """Самое важное свойство: таймаут не превращает неизвестный исход в повтор.

    Клик мог дойти. Повторить его — значит совершить второй, уже точно лишний
    эффект. FAILED тоже была бы ложью: он утверждал бы, что эффекта не было.
    """
    seen, emit = events()
    adapter = HangingAdapter(hold_s=10.0)
    m = make_manager(tmp_path / "t.json", FakePlanner([click(), click(), click()]),
                     FakeObserver(), adapter=adapter, event_emit=emit, **FAST)
    t = m.create_task("оплатить счёт")
    state = await asyncio.wait_for(m.run(t.id), timeout=15)

    assert state is TaskState.PAUSED, "неизвестный исход обязан ждать владельца"
    assert len(adapter.executed) == 1, "повтор уже отправленного эффекта"
    row = m.store.get(t.id)
    assert "unknown outcome" in (row.last_error or "")
    assert row.pending_action is None
    assert any(e.get("event") == "unknown_effect_parked" for e in seen)
    assert m.unknown_effects_parked == 1


@pytest.mark.asyncio
async def test_the_backend_thread_that_outlives_the_timeout_stops_typing(tmp_path):
    """Зомби-поток: цикл освобождён, поток ещё жив — ввода больше быть не должно."""
    adapter = HangingAdapter(hold_s=10.0)
    m = make_manager(tmp_path / "t.json", FakePlanner([click()]),
                     FakeObserver(), adapter=adapter, **FAST)
    t = m.create_task("оплатить счёт")
    await asyncio.wait_for(m.run(t.id), timeout=15)

    assert adapter.interrupt is not None, "бэкенду вообще не дали флаг отмены"
    assert adapter.interrupt.is_set(), "поток не получил команды остановиться"
    assert await asyncio.to_thread(adapter.finished.wait, 5.0), "поток не завершился"
    assert adapter.inputs_after_timeout == [], "поток вводил уже после таймаута"


@pytest.mark.asyncio
async def test_a_post_action_observation_timeout_parks_instead_of_replanning(tmp_path):
    """Действие ИСПОЛНЕНО, прочитать экран нечем: подтвердить эффект невозможно."""
    m = make_manager(tmp_path / "t.json", FakePlanner([click(), click()]),
                     HangingObserver(hang_from_call=2), **FAST)
    t = m.create_task("оплатить счёт")
    state = await asyncio.wait_for(m.run(t.id), timeout=15)
    assert state is TaskState.PAUSED
    assert "could not be re-read" in (m.store.get(t.id).last_error or "")


@pytest.mark.asyncio
async def test_a_harmless_action_may_be_retried_after_a_timeout(tmp_path):
    """Отрицательный контроль парковки: скриншот ничего в мире не меняет,
    поэтому его таймаут — обычная трата replan-бюджета, а не сверка."""
    adapter = HangingAdapter(hold_s=10.0)
    m = make_manager(tmp_path / "t.json", FakePlanner([screenshot()] * 12),
                     FakeObserver(), adapter=adapter, **FAST)
    t = m.create_task("посмотреть экран")
    state = await asyncio.wait_for(m.run(t.id), timeout=25)
    assert state is TaskState.FAILED
    assert m.unknown_effects_parked == 0
    assert m.store.get(t.id).replans_used > 1


# ------------------------------------------------- «Стоп» во время зависания

@pytest.mark.asyncio
async def test_stop_during_a_hang_is_obeyed_and_faster_than_the_timeout(tmp_path):
    """Отзывчивость «Стопа» определяется опросом строки, а не потолком фазы."""
    observer = HangingObserver()
    m = make_manager(tmp_path / "t.json", FakePlanner([click()]), observer,
                     observe_timeout_s=30.0, plan_timeout_s=30.0, act_timeout_s=30.0)
    t = m.create_task("нажать кнопку")
    running = asyncio.create_task(m.run(t.id))
    await asyncio.wait_for(observer.entered.wait(), timeout=5)

    began = time.monotonic()
    m.stop(t.id)
    state = await asyncio.wait_for(running, timeout=10)
    waited = time.monotonic() - began

    assert state is TaskState.CANCELLED
    assert waited < 5.0, f"«Стоп» ждал {waited:.1f}s при потолке фазы 30s"
    assert m.step_timeouts == 0, "выход по «Стопу», а не по потолку"


@pytest.mark.asyncio
async def test_pause_during_a_hang_leaves_the_task_resumable(tmp_path):
    observer = HangingObserver()
    m = make_manager(tmp_path / "t.json", FakePlanner([click()]), observer,
                     observe_timeout_s=30.0, plan_timeout_s=30.0, act_timeout_s=30.0)
    t = m.create_task("нажать кнопку")
    running = asyncio.create_task(m.run(t.id))
    await asyncio.wait_for(observer.entered.wait(), timeout=5)
    m.pause(t.id)
    assert await asyncio.wait_for(running, timeout=10) is TaskState.PAUSED
    assert m.store.get(t.id).state is TaskState.PAUSED


# ------------------------------------------------------- перезапуск и повтор

@pytest.mark.asyncio
async def test_a_restart_after_a_parked_timeout_does_not_replay_the_action(tmp_path):
    """Припаркованная задача не переигрывается сама: эффект остаётся одним."""
    path = tmp_path / "t.json"
    adapter = HangingAdapter(hold_s=1.0)
    m = make_manager(path, FakePlanner([click(), click()]), FakeObserver(),
                     adapter=adapter, **FAST)
    t = m.create_task("оплатить счёт")
    assert await asyncio.wait_for(m.run(t.id), timeout=15) is TaskState.PAUSED

    again = make_manager(path, FakePlanner([click()]), FakeObserver(),
                         adapter=HangingAdapter(hold_s=1.0), **FAST)
    assert await asyncio.wait_for(again.run(t.id), timeout=15) is TaskState.PAUSED
    assert again.store.get(t.id).steps_used == 0
    assert len(adapter.executed) == 1, "перезапуск отправил эффект второй раз"


# ------------------------------------------------------ положительный контроль

@pytest.mark.asyncio
async def test_an_ordinary_step_is_untouched_by_the_bound(tmp_path):
    """Без этого «все тесты зелёные» означало бы лишь, что всё падает по таймауту."""
    complete = ComputerAction.make(ActionKind.COMPLETE,
                                   expected=ExpectedState(contains_text="ok"))
    m = make_manager(tmp_path / "t.json", FakePlanner([click(), complete]),
                     FakeObserver(summary="ok"), **FAST)
    t = m.create_task("посмотреть экран")
    assert await asyncio.wait_for(m.run(t.id), timeout=15) is TaskState.COMPLETED
    assert m.step_timeouts == 0 and m.unknown_effects_parked == 0
