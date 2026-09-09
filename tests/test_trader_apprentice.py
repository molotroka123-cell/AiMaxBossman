from learning.trader_apprentice import (
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


def test_price_up_cvd_down_oi_up_is_leveraged_sell_absorption():
    prev = Snapshot(price=78_450, cvd=61.91, open_interest=18.50)
    cur = Snapshot(price=79_400, cvd=57.02, open_interest=18.67)
    result = analyze(prev, cur)
    assert result.regime is Regime.LEVERAGED_SELL_ABSORPTION
    assert result.stance is Stance.LONG_CANDIDATE


def test_price_down_cvd_up_oi_down_is_buyer_failure_with_deleveraging():
    prev = Snapshot(price=78_775, cvd=61.78, open_interest=18.78)
    cur = Snapshot(price=78_450, cvd=61.91, open_interest=18.50)
    result = analyze(prev, cur)
    assert result.regime is Regime.BUYER_FAILURE_WITH_DELEVERAGING
    assert result.stance is Stance.WATCH


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
