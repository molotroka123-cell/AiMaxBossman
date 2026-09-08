from decimal import Decimal

import pytest

import learning.trader_apprentice

from learning.trader_apprentice import (
    Direction,
    LevelMap,
    Regime,
    Snapshot,
    Stance,
    accepted_above,
    analyze,
    long_return_pct,
    sweep_and_reclaim,
    weighted_average_entry,
)


def test_weighted_average_equal_tranches_matches_september_case():
    avg = weighted_average_entry([(79_800, 0.15), (79_350, 0.15), (78_900, 0.15)])
    assert avg == 79_350


def test_long_return_pct():
    assert round(long_return_pct(80_000, 79_350), 4) == round((650 / 79_350) * 100, 4)


def test_price_down_cvd_down_oi_down_is_deleveraging():
    prev = Snapshot(price=80_000, cvd=67.0, open_interest=19.0)
    cur = Snapshot(price=79_200, cvd=65.0, open_interest=18.5)
    result = analyze(prev, cur)
    assert result.regime is Regime.DELEVERAGING_SELL_OFF
    assert result.stance is Stance.WATCH


def test_price_down_cvd_down_oi_up_is_risk_off():
    prev = Snapshot(price=79_000, cvd=62.0, open_interest=18.50)
    cur = Snapshot(price=78_400, cvd=61.1, open_interest=18.63)
    result = analyze(prev, cur)
    assert result.regime is Regime.BEARISH_LEVERAGE_EXPANSION
    assert result.stance is Stance.RISK_OFF


def test_price_up_cvd_up_oi_up_is_long_candidate():
    prev = Snapshot(price=78_420, cvd=61.10, open_interest=18.63)
    cur = Snapshot(price=78_520, cvd=61.29, open_interest=18.78)
    result = analyze(prev, cur)
    assert result.regime is Regime.BULLISH_LEVERAGE_EXPANSION
    assert result.stance is Stance.LONG_CANDIDATE


def test_level_context_reports_reclaim():
    prev = Snapshot(price=78_500, cvd=61.1, open_interest=18.6)
    cur = Snapshot(price=78_600, cvd=61.3, open_interest=18.7)
    levels = LevelMap(dval=78_430, dpoc=78_565, dvah=78_710, dopen=78_795)
    result = analyze(prev, cur, levels)
    assert "dpoc" in result.reclaimed_levels
    assert result.nearest_resistances[0][0] == "dvah"


def test_acceptance_requires_multiple_observations():
    history = [
        Snapshot(price=78_550),
        Snapshot(price=78_580),
        Snapshot(price=78_590),
    ]
    assert accepted_above(history, 78_565, observations=2)
    assert not accepted_above(history[:2], 78_565, observations=2)


def test_sweep_and_reclaim():
    history = [
        Snapshot(price=78_500),
        Snapshot(price=78_390),
        Snapshot(price=78_580),
    ]
    assert sweep_and_reclaim(history, 78_430)


# ------------------------------------------------- числовой контракт цены входа

def test_equal_tranches_average_to_the_middle_price_exactly():
    """Двоичный float здесь даёт 79350.00000000001 и перестаёт быть ценой.

    Контракт не в тесте, а в функции: десятичная арифметика над десятичной
    записью входов. Проверка сравнивает ТОЧНО, потому что именно точность и
    является предметом договора — допуск в утверждении спрятал бы её.
    """
    assert weighted_average_entry([(79_800, 0.15), (79_350, 0.15), (78_900, 0.15)]) == 79_350.0


def test_a_price_is_carried_to_the_cent_and_no_further():
    """Треть от трёх разных цен — бесконечная десятичная дробь.

    Контракт говорит «до цента», и результат обязан быть ценой, а не хвостом
    из двадцати восьми значащих цифр.
    """
    avg = weighted_average_entry([(100.00, 1), (100.01, 1), (100.03, 1)])
    assert avg == 100.01
    assert Decimal(str(avg)).as_tuple().exponent >= -2


def test_unequal_weights_move_the_average_toward_the_heavier_tranche():
    """Округление до цента не должно съедать сам смысл взвешивания."""
    light = weighted_average_entry([(80_000, 1), (79_000, 1)])
    heavy = weighted_average_entry([(80_000, 3), (79_000, 1)])
    assert light == 79_500.0
    assert heavy == 79_750.0


def test_zero_and_negative_weights_are_refused_not_averaged():
    """Отрицательный вес — ошибка вызывающего, а не отрицательная позиция."""
    for entries in ([(80_000, 0)], [(80_000, -1)], []):
        with pytest.raises(ValueError, match="positive"):
            weighted_average_entry(entries)


def test_the_result_is_a_plain_float_for_callers():
    """Контракт возвращаемого типа не менялся: Decimal наружу не течёт."""
    assert type(weighted_average_entry([(79_350, 0.15)])) is float


# ------------------------------------------- полномочия: только анализ, и точка

def test_the_module_cannot_place_an_order_by_construction():
    """Не «не должен», а НЕ МОЖЕТ: у него нет ни сети, ни ввода-вывода.

    Владелец потребовал analysis-only по умолчанию и отсутствие автономного
    финансового исполнения. Формулировка в docstring это не гарантирует —
    гарантирует отсутствие импортов, через которые ордер вообще уходит.
    """
    import ast
    import pathlib

    src = pathlib.Path(learning.trader_apprentice.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "decimal", "enum", "typing"}, imported

    public = {n for n in dir(learning.trader_apprentice) if not n.startswith("_")}
    forbidden = {n for n in public
                 if any(v in n.lower() for v in ("place", "submit", "execute", "order",
                                                 "buy", "sell", "trade", "withdraw"))}
    assert forbidden == set(), forbidden


def test_missing_market_data_stays_unknown_and_is_never_invented():
    """Отсутствующее значение — UNKNOWN, а не ноль и не «вероятно, как вчера»."""
    result = analyze(Snapshot(price=79_000), Snapshot(price=79_100), LevelMap())
    assert result.cvd_direction is Direction.UNKNOWN
    assert result.oi_direction is Direction.UNKNOWN
    assert result.regime is Regime.UNKNOWN
    assert result.stance in (Stance.NO_TRADE, Stance.WATCH)
