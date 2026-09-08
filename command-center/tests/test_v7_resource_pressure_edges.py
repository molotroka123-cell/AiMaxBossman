"""Границы отказа по памяти. Каждая — про то, как «нельзя» становится «можно».

Общий приём один: величина, которую нельзя сравнить, проходит любое сравнение.
`NaN > budget` — ложь, `NaN <= 0` — тоже ложь, поэтому незащищённая проверка
решает, что путь ПОМЕЩАЕТСЯ, и предлагает его. Отсюда правило модуля: то, что
не является числом, закрывает путь, а не открывает.
"""
from __future__ import annotations

import time

import pytest

from bcc.reality import strategy as st
from bcc.reality import world
from bossman_shared.objective_world_state import WorldFact

GB = 1024.0


def by_id(strategies):
    return {s.strategy_id: s for s in strategies}


def gen(**kwargs):
    kwargs.setdefault("deterministic_available", False)
    return st.generate_strategies(**kwargs)


# ------------------------------------------------------------ не-числа

@pytest.mark.parametrize("bogus", [float("nan"), float("inf"), float("-inf")])
def test_a_requirement_that_is_not_a_number_closes_the_path(bogus):
    """NaN сравнивается ложью со всем: незащищённая проверка сочла бы, что
    путь помещается, и предложила бы его."""
    pressure, reason = st._memory_verdict(st.MemoryReading(8 * GB, "observer"), bogus)
    assert pressure == 0.0
    assert reason, f"{bogus!r} прошло как измеримая потребность"


def test_a_negative_requirement_is_an_error_not_free_memory():
    pressure, reason = st._memory_verdict(st.MemoryReading(8 * GB, "observer"), -5.0)
    assert pressure == 0.0 and "отрицательна" in reason


@pytest.mark.parametrize("bogus", [float("nan"), float("inf")])
def test_a_reading_that_is_not_a_number_is_not_a_measurement(bogus):
    """Показание, пришедшее из деления на ноль выборок, — не измерение."""
    pressure, reason = st._memory_verdict(st.MemoryReading(bogus, "observer"), 1024.0)
    assert pressure == 0.0
    assert "не является числом" in reason


def test_zero_free_memory_does_not_fit_anything():
    pressure, reason = st._memory_verdict(st.MemoryReading(0.0, "observer"), 1.0)
    assert pressure == 0.0 and reason


def test_a_path_that_declares_nothing_is_not_refused():
    assert st._memory_verdict(st.MemoryReading(8 * GB, "observer"), 0.0) == (0.0, "")


# ------------------------------------------------------ ровно на границе

def test_exactly_at_the_reserve_boundary_the_path_still_fits():
    """15% остаются машине владельца. Ровно бюджет — это ещё «помещается»."""
    free = 10_000.0
    budget = free * st.MEMORY_HEADROOM
    pressure, reason = st._memory_verdict(st.MemoryReading(free, "observer"), budget)
    assert reason == "", "ровно бюджет обязан помещаться"
    assert pressure == pytest.approx(st.MEMORY_PRESSURE_WEIGHT)


def test_one_megabyte_past_the_reserve_does_not_fit():
    free = 10_000.0
    budget = free * st.MEMORY_HEADROOM
    _, reason = st._memory_verdict(st.MemoryReading(free, "observer"), budget + 1.0)
    assert reason, "перебор запаса обязан отказывать"


def test_the_reserve_is_left_to_the_machine_not_to_the_model():
    """Модель, влезающая во ВСЮ свободную память, не влезает."""
    free = 10_000.0
    _, reason = st._memory_verdict(st.MemoryReading(free, "observer"), free)
    assert reason


def test_seventy_gigabytes_do_not_fit_into_forty_two():
    terms = by_id(gen(memory=st.MemoryReading(42 * GB, "observer"),
                      model_memory_mb={"large_model": 70 * GB}))
    assert terms["large-model-tools"].available is False
    assert "не помещается" in terms["large-model-tools"].unavailable_reason


# ------------------------------------------- отказ ≠ разрешение

def test_a_refusal_never_becomes_authority_over_another_path():
    """Отказ по памяти закрывает СВОЙ путь и ничего не открывает у других."""
    terms = by_id(gen(memory=st.MemoryReading(4 * GB, "observer"),
                      model_memory_mb={"large_model": 70 * GB,
                                       "small_model": 512.0}))
    assert terms["large-model-tools"].available is False
    assert terms["small-model-tools"].available is True
    # И не выдаёт прав: у выжившего пути их столько же, сколько было.
    assert terms["small-model-tools"].required_permissions == ()


def test_a_cheaper_path_may_rank_but_carries_no_new_permission():
    """Запасной путь ранжируется — и на этом полномочия заканчиваются."""
    terms = gen(memory=st.MemoryReading(4 * GB, "observer"),
                model_memory_mb={"large_model": 70 * GB})
    decision = st.shadow_route(terms, permissions=[])
    assert decision.to_dict()["shadow_only"] is True
    assert decision.selected is None or decision.selected.required_permissions == ()
    assert "large-model-tools" not in {s.strategy_id for s in decision.ranked}


def test_an_unavailable_path_is_still_reported_so_the_refusal_is_readable():
    """«Почему этот путь не рассматривали» обязано иметь ответ."""
    terms = gen(memory=st.MemoryReading(1 * GB, "observer"),
                model_memory_mb={"large_model": 70 * GB})
    large = by_id(terms)["large-model-tools"]
    assert large.unavailable_reason
    assert large.strategy_id in {s.strategy_id for s in terms}


# ------------------------------------------------ мир состояния

def test_a_contested_memory_fact_is_not_a_measurement():
    """Два наблюдателя разошлись — спор не превращается ни в среднее, ни в
    свежее значение.

    Проекция отвечает `CONTESTED` без значения, а читатель
    (`features.reality.memory_reading`) отдаёт неизмеренное показание. Здесь
    проверяется вторая половина этой цепочки: что «не FRESH» действительно
    доезжает до отказа, а не оседает нулём.
    """
    for status in ("CONTESTED", "STALE", "MISSING"):
        reading = st.MemoryReading(None, f"process.host {status.lower()}")
        assert reading.measured is False, status
        _, why = st._memory_verdict(reading, 1024.0)
        assert status.lower() in why, why


def test_a_reading_carries_its_own_source_so_a_gap_is_nameable():
    unmeasured = st.MemoryReading()
    assert unmeasured.measured is False
    assert unmeasured.source == "unmeasured"
    _, reason = st._memory_verdict(unmeasured, 1024.0)
    assert "не измерена" in reason


def test_utility_arithmetic_cannot_refresh_a_missing_measurement():
    """Ранжирование не превращает отсутствие показания в показание.

    Путь без измерения памяти отсеивается ДО ранжирования и не может быть
    возвращён никаким счётом полезности.
    """
    terms = gen(memory=st.MemoryReading(),          # нет показания
                model_memory_mb={"large_model": 512.0})
    large = by_id(terms)["large-model-tools"]
    assert large.available is False

    decision = st.shadow_route(terms, permissions=[])
    assert decision.selected is None or decision.selected.strategy_id != "large-model-tools"
    assert "large-model-tools" not in {s.strategy_id for s in decision.ranked}, \
        "отсеян ДО ранжирования, а не проигран в нём"


def test_after_a_restart_memory_is_unknown_rather_than_the_old_value():
    """Перезапуск без нового наблюдения — это «неизвестно», а не «как было».

    Показание не хранится в модуле: оно приходит аргументом на каждый расчёт.
    Свежий процесс, который ничего не измерил, передаёт пустое показание, и
    путь закрывается — вместо того чтобы планировать по числу из прошлой жизни.
    """
    before = by_id(gen(memory=st.MemoryReading(64 * GB, "observer"),
                       model_memory_mb={"large_model": 8 * GB}))
    assert before["large-model-tools"].available is True

    after_restart = by_id(gen(memory=st.MemoryReading(),
                              model_memory_mb={"large_model": 8 * GB}))
    assert after_restart["large-model-tools"].available is False
    assert "не измерена" in after_restart["large-model-tools"].unavailable_reason
