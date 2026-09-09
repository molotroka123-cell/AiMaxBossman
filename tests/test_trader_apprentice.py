from decimal import Decimal

import pytest

import learning.trader_apprentice

from learning.trader_apprentice import (
    Direction,
    Incompatibility,
    LevelMap,
    Regime,
    SeriesId,
    Snapshot,
    Stance,
    accepted_above,
    analyze,
    classify_regime,
    long_return_pct,
    series_compatibility,
    sweep_and_reclaim,
    weighted_average_entry,
)


# --------------------------------------------------------------- identity fixtures
# A compatible pair, stated once.  The matrix tests below assert the SAME regime
# conclusions they always did; supplying identity is the precondition the module
# now enforces, not a change to what the matrix means.

CVD_ID = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                  aggregation="1m", normalization="usd", version="v1")
OI_ID = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                 aggregation="1m", normalization="contracts", version="v1")


def snap(price, cvd=None, oi=None, *, ts="2026-09-08T12:00:00Z",
         source="binance", instrument="BTCUSDT", cvd_series=CVD_ID, oi_series=OI_ID,
         **extra):
    """A snapshot that states who measured it, on what, and when."""
    return Snapshot(price=price, cvd=cvd, open_interest=oi, source=source,
                    instrument=instrument, timestamp=ts,
                    cvd_series=cvd_series if cvd is not None else None,
                    oi_series=oi_series if oi is not None else None, **extra)


def pair(prev_args, cur_args, **common):
    """(previous, current) one minute apart — time advances by construction."""
    return (snap(*prev_args, ts="2026-09-08T12:00:00Z", **common),
            snap(*cur_args, ts="2026-09-08T12:01:00Z", **common))


def test_weighted_average_equal_tranches_matches_september_case():
    avg = weighted_average_entry([(79_800, 0.15), (79_350, 0.15), (78_900, 0.15)])
    assert avg == 79_350


def test_long_return_pct():
    assert round(long_return_pct(80_000, 79_350), 4) == round((650 / 79_350) * 100, 4)


def test_price_down_cvd_down_oi_down_is_deleveraging():
    prev, cur = pair((80_000, 67.0, 19.0), (79_200, 65.0, 18.5))
    result = analyze(prev, cur)
    assert result.regime is Regime.DELEVERAGING_SELL_OFF
    assert result.stance is Stance.WATCH


def test_price_down_cvd_down_oi_up_is_risk_off():
    prev, cur = pair((79_000, 62.0, 18.50), (78_400, 61.1, 18.63))
    result = analyze(prev, cur)
    assert result.regime is Regime.BEARISH_LEVERAGE_EXPANSION
    assert result.stance is Stance.RISK_OFF


def test_price_up_cvd_up_oi_up_is_long_candidate():
    prev, cur = pair((78_420, 61.10, 18.63), (78_520, 61.29, 18.78))
    result = analyze(prev, cur)
    assert result.regime is Regime.BULLISH_LEVERAGE_EXPANSION
    assert result.stance is Stance.LONG_CANDIDATE


def test_level_context_reports_reclaim():
    prev, cur = pair((78_500, 61.1, 18.6), (78_600, 61.3, 18.7))
    levels = LevelMap(dval=78_430, dpoc=78_565, dvah=78_710, dopen=78_795)
    result = analyze(prev, cur, levels)
    assert "dpoc" in result.reclaimed_levels
    assert result.nearest_resistances[0][0] == "dvah"


def test_acceptance_requires_multiple_observations():
    history = [
        snap(78_550, ts="2026-09-08T12:00:00Z"),
        snap(78_580, ts="2026-09-08T12:01:00Z"),
        snap(78_590, ts="2026-09-08T12:02:00Z"),
    ]
    assert accepted_above(history, 78_565, observations=2)
    assert not accepted_above(history[:2], 78_565, observations=2)


def test_sweep_and_reclaim():
    history = [
        snap(78_500, ts="2026-09-08T12:00:00Z"),
        snap(78_390, ts="2026-09-08T12:01:00Z"),
        snap(78_580, ts="2026-09-08T12:02:00Z"),
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
    # `datetime` is date arithmetic and a clock read — no socket, no file, no
    # broker.  It is in the allowlist because timestamp ordering is now part of
    # the compatibility proof; the guard still bounds the set, and the
    # forbidden-name check below is unchanged.
    assert imported <= {"__future__", "dataclasses", "datetime", "decimal",
                        "enum", "typing"}, imported

    public = {n for n in dir(learning.trader_apprentice) if not n.startswith("_")}
    forbidden = {n for n in public
                 if any(v in n.lower() for v in ("place", "submit", "execute", "order",
                                                 "buy", "sell", "trade", "withdraw"))}
    assert forbidden == set(), forbidden


def test_missing_market_data_stays_unknown_and_is_never_invented():
    """Отсутствующее значение — UNKNOWN, а не ноль и не «вероятно, как вчера»."""
    prev, cur = pair((79_000,), (79_100,))
    result = analyze(prev, cur, LevelMap())
    assert result.cvd_direction is Direction.UNKNOWN
    assert result.oi_direction is Direction.UNKNOWN
    assert result.regime is Regime.UNKNOWN
    assert result.stance in (Stance.NO_TRADE, Stance.WATCH)


# ------------------------------------------------- §25: metric-series identity
# The module's own rule — "never compare absolute CVD/OI values across different
# providers/settings" — used to live only in the docstring.  A Snapshot carried
# `source`/`instrument`/`timestamp` and nothing read them, so two rows from two
# venues were subtracted as happily as two rows from one.  These tests are the
# enforcement: each one is a pair that a human would immediately call
# incomparable, and the classifier must refuse rather than produce a direction.


def test_compatible_series_are_classified_normally():
    """Legitimate case: identity stated, identity matches, time advances."""
    prev, cur = pair((79_000, 62.0, 18.50), (78_400, 61.1, 18.63))
    incompatibility, why = series_compatibility(prev, cur)
    assert incompatibility is None and why == []
    result = analyze(prev, cur)
    assert result.regime is Regime.BEARISH_LEVERAGE_EXPANSION
    assert result.cvd_direction is Direction.DOWN


def test_source_mismatch_refuses_to_classify():
    """Binance CVD minus Bybit CVD is not a Binance direction."""
    prev = snap(79_000, 62.0, 18.50, source="binance", ts="2026-09-08T12:00:00Z")
    cur = snap(78_400, 61.1, 18.63, source="bybit", ts="2026-09-08T12:01:00Z")
    incompatibility, why = series_compatibility(prev, cur)
    assert incompatibility is Incompatibility.SOURCE_MISMATCH
    assert any("source differs" in reason for reason in why)
    result = analyze(prev, cur)
    assert result.regime is Regime.UNKNOWN
    assert result.stance is Stance.NO_TRADE
    # Not merely "regime unknown": no direction is published either.
    assert result.price_direction is Direction.UNKNOWN
    assert result.cvd_direction is Direction.UNKNOWN
    assert result.oi_direction is Direction.UNKNOWN


def test_instrument_mismatch_refuses_to_classify():
    """A spot price and a perp price are two instruments, not one series."""
    prev = snap(79_000, 62.0, 18.50, instrument="BTCUSDT", ts="2026-09-08T12:00:00Z")
    cur = snap(78_400, 61.1, 18.63, instrument="ETHUSDT", ts="2026-09-08T12:01:00Z")
    incompatibility, _ = series_compatibility(prev, cur)
    assert incompatibility is Incompatibility.INSTRUMENT_MISMATCH
    assert analyze(prev, cur).regime is Regime.UNKNOWN


def test_cvd_series_mismatch_refuses_even_when_source_matches():
    """The trap this exists for: same venue, same instrument, DIFFERENT CVD
    normalization.  Contracts and USD are both "62.0" and neither is the other."""
    other = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                     aggregation="1m", normalization="contracts", version="v1")
    prev = snap(79_000, 62.0, 18.50, ts="2026-09-08T12:00:00Z")
    cur = snap(78_400, 61.1, 18.63, ts="2026-09-08T12:01:00Z", cvd_series=other)
    incompatibility, why = series_compatibility(prev, cur)
    assert incompatibility is Incompatibility.CVD_SERIES_MISMATCH
    assert any("CVD series differs" in reason for reason in why)
    assert analyze(prev, cur).regime is Regime.UNKNOWN


def test_oi_series_mismatch_refuses():
    """Open interest aggregated over 1m is not open interest aggregated over 1h."""
    other = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                     aggregation="1h", normalization="contracts", version="v1")
    prev = snap(79_000, 62.0, 18.50, ts="2026-09-08T12:00:00Z")
    cur = snap(78_400, 61.1, 18.63, ts="2026-09-08T12:01:00Z", oi_series=other)
    incompatibility, _ = series_compatibility(prev, cur)
    assert incompatibility is Incompatibility.OI_SERIES_MISMATCH
    assert analyze(prev, cur).regime is Regime.UNKNOWN


def test_missing_identity_is_refused_not_treated_as_a_match():
    """Two snapshots that both say "unknown" are the DANGEROUS case.

    Before this rule, the default `source="unknown"` on both rows compared equal,
    so two unlabelled feeds were silently declared one series.  Absence of a
    stated identity is never evidence of a shared identity.
    """
    # (a) the row-level identity is a placeholder on both sides
    prev, cur = Snapshot(price=79_000, cvd=62.0, open_interest=18.5), \
        Snapshot(price=78_400, cvd=61.1, open_interest=18.6)
    assert prev.source == cur.source == "unknown"      # they "match" as strings
    incompatibility, _ = series_compatibility(prev, cur)
    assert incompatibility is Incompatibility.IDENTITY_MISSING
    assert analyze(prev, cur).regime is Regime.UNKNOWN

    # (b) row identity stated, but a PRESENT metric carries no series identity
    bare_prev = Snapshot(price=79_000, cvd=62.0, open_interest=18.5, source="binance",
                         instrument="BTCUSDT", timestamp="2026-09-08T12:00:00Z",
                         cvd_series=None, oi_series=OI_ID)
    bare_cur = Snapshot(price=78_400, cvd=61.1, open_interest=18.6, source="binance",
                        instrument="BTCUSDT", timestamp="2026-09-08T12:01:00Z",
                        cvd_series=None, oi_series=OI_ID)
    assert series_compatibility(bare_prev, bare_cur)[0] is Incompatibility.IDENTITY_MISSING

    # (c) identity object present but incomplete — a half-named series is unnamed
    partial = SeriesId(provider="binance", instrument="BTCUSDT")   # no market/aggregation
    p2 = snap(79_000, 62.0, 18.5, ts="2026-09-08T12:00:00Z", cvd_series=partial)
    c2 = snap(78_400, 61.1, 18.6, ts="2026-09-08T12:01:00Z", cvd_series=partial)
    assert not partial.is_complete()
    assert "market" in partial.missing_fields()
    assert series_compatibility(p2, c2)[0] is Incompatibility.IDENTITY_MISSING


def test_reversed_or_equal_timestamps_refuse_to_produce_a_direction():
    """A direction needs a forward step.  Equal timestamps are the same instant
    compared with itself; a reversed pair is the move backwards."""
    a = snap(79_000, 62.0, 18.50, ts="2026-09-08T12:01:00Z")
    b = snap(78_400, 61.1, 18.63, ts="2026-09-08T12:00:00Z")

    reversed_incompat, why = series_compatibility(a, b)          # previous is later
    assert reversed_incompat is Incompatibility.TIMESTAMP_NOT_ADVANCING
    assert any("not before" in reason for reason in why)
    assert analyze(a, b).regime is Regime.UNKNOWN

    same = snap(78_400, 61.1, 18.63, ts="2026-09-08T12:01:00Z")
    assert series_compatibility(a, same)[0] is Incompatibility.TIMESTAMP_NOT_ADVANCING

    # An unparseable timestamp is missing identity, not epoch zero.
    broken = snap(78_400, 61.1, 18.63, ts="last tuesday")
    assert series_compatibility(a, broken)[0] is Incompatibility.IDENTITY_MISSING


def test_timezone_forms_of_the_same_instant_do_not_fake_an_advance():
    """12:01Z and 14:01+02:00 are ONE instant.  Reading the second as naive local
    time would invent a two-hour step and, with it, a direction."""
    a = snap(79_000, 62.0, 18.50, ts="2026-09-08T12:00:00Z")
    same_instant_other_zone = snap(78_400, 61.1, 18.63, ts="2026-09-08T14:00:00+02:00")
    assert series_compatibility(a, same_instant_other_zone)[0] is \
        Incompatibility.TIMESTAMP_NOT_ADVANCING


def test_identity_comparison_ignores_case_and_padding_but_not_meaning():
    """Spurious mismatches are as wrong as spurious matches: "Binance" and
    "binance " are one venue, while "binance-futures" is not."""
    prev = snap(79_000, 62.0, 18.50, source="Binance ", ts="2026-09-08T12:00:00Z")
    cur = snap(78_400, 61.1, 18.63, source="binance", ts="2026-09-08T12:01:00Z")
    assert series_compatibility(prev, cur)[0] is None

    other_venue = snap(78_400, 61.1, 18.63, source="binance-futures",
                       ts="2026-09-08T12:01:00Z")
    assert series_compatibility(prev, other_venue)[0] is Incompatibility.SOURCE_MISMATCH


def test_series_identity_is_immutable_and_hashable():
    """Provenance that can be edited after the fact is not provenance."""
    ident = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                     aggregation="1m", normalization="usd", version="v1")
    with pytest.raises(Exception):
        ident.provider = "bybit"           # type: ignore[misc]
    twin = SeriesId(provider="binance", instrument="BTCUSDT", market="perp",
                    aggregation="1m", normalization="usd", version="v1")
    assert len({ident, twin}) == 1                 # value identity, hashable
    assert ident.series_id == "binance|btcusdt|perp|1m|usd|v1"


def test_absent_metric_needs_no_identity_and_stays_unknown():
    """Legitimate case, and the boundary of the rule: a metric that is ABSENT is
    already UNKNOWN downstream, so demanding a series identity for it would
    refuse pairs that are perfectly comparable on the metrics they do have."""
    prev = Snapshot(price=79_000, source="binance", instrument="BTCUSDT",
                    timestamp="2026-09-08T12:00:00Z")
    cur = Snapshot(price=79_100, source="binance", instrument="BTCUSDT",
                   timestamp="2026-09-08T12:01:00Z")
    assert series_compatibility(prev, cur)[0] is None
    result = analyze(prev, cur)
    assert result.price_direction is Direction.UP          # price IS comparable
    assert result.cvd_direction is Direction.UNKNOWN
    assert result.regime is Regime.UNKNOWN                 # matrix needs CVD+OI


def test_classification_remains_analysis_only_after_the_gate():
    """The gate must not have introduced a stance that acts."""
    prev, cur = pair((78_420, 61.10, 18.63), (78_520, 61.29, 18.78))
    _, _, _, _, stance, _ = classify_regime(prev, cur)
    assert stance in set(Stance)
    assert not hasattr(analyze(prev, cur), "order")


@pytest.mark.parametrize("bad", [None, 3, True, {}, []])
def test_malformed_identity_fails_closed(bad):
    from dataclasses import replace
    previous, current = pair((100, 10, 20), (110, 11, 22))
    for malformed in (replace(current, source=bad),
                      replace(current, cvd_series=bad),
                      replace(current, cvd_series=replace(CVD_ID, aggregation=bad))):
        result = analyze(previous, malformed)
        assert result.regime is Regime.UNKNOWN
        assert result.stance is Stance.NO_TRADE


def test_incompatible_identity_cannot_reclaim_or_lose_levels():
    from dataclasses import replace
    previous, current = pair((100, 10, 20), (110, 11, 22))
    levels = LevelMap(dpoc=105)
    assert analyze(previous, current, levels).reclaimed_levels == ("dpoc",)
    refused = analyze(previous, replace(current, instrument="ETHUSDT"), levels)
    assert refused.reclaimed_levels == refused.lost_levels == ()
    assert not any("Reclaimed" in reason for reason in refused.reasons)


@pytest.mark.parametrize("helper", [accepted_above, sweep_and_reclaim])
@pytest.mark.parametrize("change", ["instrument", "source", "timestamp"])
def test_history_helpers_require_one_ordered_series(helper, change):
    from dataclasses import replace
    prices = (110, 115) if helper is accepted_above else (95, 115)
    previous, current = pair((prices[0],), (prices[1],))
    assert helper([previous, current], 100)
    values = {"instrument": "ETHUSDT", "source": "other", "timestamp": previous.timestamp}
    assert not helper([previous, replace(current, **{change: values[change]})], 100)
