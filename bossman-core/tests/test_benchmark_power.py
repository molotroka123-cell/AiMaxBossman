"""Бенчмарк обязан отличать «регрессии нет» от «я её не увижу».

Замерено на этой ветке: tier `smoke` даёт n=10 и доверительный интервал
[0.722, 1.000]; tier `pr` — n=21 и [0.845, 1.000]. То есть при идеальном
результате 1.0 прогон не отличает «безупречно» от «каждый седьмой случай
падает». Методика сама по себе честная (Уилсон, классы улик,
INSUFFICIENT_EVIDENCE вместо нуля), но МОЩНОСТИ у неё мало.

До этой правки сравнение с базой было таким:

    if baseline and metrics["VerifiedSuccessRate"] < baseline[...]:
        reasons.append("VerifiedSuccessRate regressed from baseline")

Голые точечные оценки. Два дефекта сразу: один перевернувшийся случай из
двадцати одного (−4.8 п.п., внутри интервала) объявляется регрессией, а
молчание этой строки читается как доказательство её ОТСУТСТВИЯ.

Взято из практики открытых проектов: google/benchmark сравнивает прогоны
критерием Манна — Уитни, а для ДВОИЧНОГО исхода на ОДНИХ И ТЕХ ЖЕ случаях
правильный парный аналог — точный критерий Макнемара по расходящимся парам.
Он не требует SciPy: это биномиальное распределение, считается через
`math.comb`.
"""
from __future__ import annotations

import pytest

from bossman.benchmark.engine import (
    compare_to_baseline,
    mcnemar_exact,
    minimum_detectable_effect,
)


# --------------------------------------------------------- критерий Макнемара

def test_mcnemar_is_symmetric_and_bounded():
    assert mcnemar_exact(5, 5) == pytest.approx(1.0)
    assert mcnemar_exact(3, 7) == pytest.approx(mcnemar_exact(7, 3))
    for b, c in ((0, 0), (1, 0), (10, 4), (30, 30)):
        assert 0.0 <= mcnemar_exact(b, c) <= 1.0


def test_no_discordant_pairs_proves_nothing():
    """Ни одна пара не разошлась — это отсутствие данных, а не доказательство."""
    assert mcnemar_exact(0, 0) == pytest.approx(1.0)


def test_a_one_case_flip_is_not_significant():
    """Ровно тот случай, который прежняя строка объявляла регрессией."""
    assert mcnemar_exact(1, 0) > 0.05


def test_a_lopsided_split_is_significant():
    """А вот двенадцать падений против нуля — уже не шум."""
    assert mcnemar_exact(12, 0) < 0.05


# ------------------------------------------- наименьшая различимая просадка

def test_the_minimum_detectable_effect_shrinks_as_the_sample_grows():
    small = minimum_detectable_effect(10)
    medium = minimum_detectable_effect(21)
    large = minimum_detectable_effect(400)
    assert small > medium > large
    assert 0.0 < large < 0.10, large


def test_the_measured_tiers_are_honestly_underpowered():
    """Числа замерены на этой ветке, а не предположены."""
    assert minimum_detectable_effect(10) > 0.20, "smoke не видит и пятой части"
    assert minimum_detectable_effect(21) > 0.10, "pr не видит и десятой"


def test_no_samples_has_no_detectable_effect_at_all():
    assert minimum_detectable_effect(0) is None


# --------------------------------------------------------------- вердикт

def _cases(passed: list[bool], prefix: str = "c") -> list[dict]:
    return [{"case_id": f"{prefix}{i}", "passed": ok} for i, ok in enumerate(passed)]


def test_a_real_regression_is_named():
    base = _cases([True] * 40)
    now = _cases([True] * 25 + [False] * 15)
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] == "REGRESSION", verdict
    assert verdict["p_value"] < 0.05
    assert verdict["worse"] == 15 and verdict["better"] == 0


def test_an_identical_run_is_never_called_a_regression():
    """Пара к предыдущему: без неё «REGRESSION» не отличить от постоянного крика.

    Заодно — иллюстрация того, ради чего правка. Прогон из сорока случаев,
    совпавший с базой идеально, НЕ получает «изменений нет»: при n=40 порог
    различимости 15.5 п.п., и честный ответ здесь `UNDERPOWERED`. Я написал
    этот тест с ожиданием `NO_CHANGE_DETECTED`, и механизм меня поправил.
    """
    base = _cases([True] * 30 + [False] * 10)
    now = _cases([True] * 30 + [False] * 10)
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] != "REGRESSION", verdict
    assert verdict["verdict"] == "UNDERPOWERED", verdict
    assert verdict["worse"] == 0 and verdict["better"] == 0
    assert verdict["minimum_detectable_effect"] == pytest.approx(0.155, abs=0.005)


def test_a_single_flip_in_a_small_run_is_reported_as_underpowered_not_as_clean():
    """Главное утверждение: молчание не выдаётся за отсутствие регрессии."""
    base = _cases([True] * 21)
    now = _cases([True] * 20 + [False])
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] == "UNDERPOWERED", verdict
    assert verdict["p_value"] > 0.05
    assert verdict["minimum_detectable_effect"] > 0.10
    assert "не" in verdict["means"].lower()


def test_a_large_clean_run_may_state_that_no_regression_was_found():
    """Когда мощности хватает, «не найдено» — законное утверждение."""
    base = _cases([True] * 400)
    now = _cases([True] * 400)
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] == "NO_CHANGE_DETECTED", verdict


def test_an_improvement_is_not_called_a_regression():
    base = _cases([False] * 20 + [True] * 20)
    now = _cases([True] * 40)
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] == "IMPROVEMENT", verdict
    assert verdict["better"] == 20 and verdict["worse"] == 0


def test_only_shared_case_ids_are_paired():
    """Парный критерий на непарных данных — не критерий."""
    base = _cases([True, True, True], prefix="a")
    now = _cases([False, False, False], prefix="b")
    verdict = compare_to_baseline(now, base)
    assert verdict["verdict"] == "INSUFFICIENT_EVIDENCE", verdict
    assert verdict["paired"] == 0


def test_a_missing_baseline_is_insufficient_evidence_not_success():
    assert compare_to_baseline(_cases([True] * 10), None)["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert compare_to_baseline(_cases([True] * 10), [])["verdict"] == "INSUFFICIENT_EVIDENCE"
