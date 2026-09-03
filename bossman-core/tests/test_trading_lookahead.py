"""Анти-lookahead: будущее не попадает в решение ни одним из известных путей."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bossman.trading_learning.models import (Candle, Claim, ClaimType, Episode,
                                             MarketRegime, Phase, VerificationStatus)
from bossman.trading_learning.replay import (ContextMismatch, LookaheadViolation,
                                             StaleObservation, assert_no_lookahead,
                                             build_decision_context, claims_for_phase,
                                             outcome_window, visible_candles)
from bossman.trading_learning.safety import utcnow

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def series(n: int, start: float = 70_000.0, drift: float = 50.0) -> list[Candle]:
    out, price = [], start
    for i in range(n):
        nxt = price + drift
        out.append(Candle(ts=T0 + timedelta(hours=i), open=price,
                          high=max(price, nxt) + 5, low=min(price, nxt) - 5,
                          close=nxt, volume=1.0, cvd=1000.0 + i * 10,
                          open_interest=5000.0 + i * 5))
        price = nxt
    return out


def claim(**kw) -> Claim:
    base = dict(claim_type=ClaimType.MARKET_OBSERVATION, source_id="s", video_hash="h",
                timestamp_start=0.0, timestamp_end=1.0, asset="BTCUSDT",
                venue="binance-futures", timeframe="1h", market_state="x",
                raw_quote_or_frame_ref="цена у уровня", confidence=0.5,
                extraction_model="rules/v1", created_at=utcnow(),
                collected_at=T0 + timedelta(hours=5))
    base.update(kw)
    return Claim(**base)


def episode(decision_index: int = 10, claims=None) -> Episode:
    candles = series(30)
    return Episode(episode_id="ep1", asset="BTCUSDT", venue="binance-futures",
                   timeframe="1h", decision_time=candles[decision_index].ts,
                   candles=candles, claims=list(claims or []))


def test_visible_candles_never_include_the_future():
    candles = series(20)
    cut = candles[9].ts
    seen = visible_candles(candles, cut)
    assert seen and all(c.ts <= cut for c in seen)
    assert len(seen) == 10


def test_assert_no_lookahead_catches_a_leak():
    candles = series(20)
    cut = candles[9].ts
    with pytest.raises(LookaheadViolation) as exc:
        assert_no_lookahead(candles, cut)
    assert "leaked into the decision" in str(exc.value)


def test_decision_context_has_no_future_candles():
    ctx = build_decision_context(episode())
    assert all(c.ts <= ctx.decision_time for c in ctx.candles)
    assert ctx.regime is not MarketRegime.UNKNOWN


def test_naive_decision_time_is_rejected():
    with pytest.raises(LookaheadViolation):
        visible_candles(series(3), datetime(2026, 1, 1))


def test_future_timestamped_claim_is_dropped_at_decision():
    """Наблюдение с меткой из будущего не участвует в решении."""
    future = claim(collected_at=T0 + timedelta(hours=25))
    ctx = build_decision_context(episode(claims=[future]))
    assert ctx.claims == ()


def test_retrospective_and_expected_outcome_are_never_visible_at_t1():
    """Ретроспектива и ожидаемый исход — это ответ, а не вход."""
    hindsight = claim(claim_type=ClaimType.RETROSPECTIVE_COMMENTARY,
                      raw_quote_or_frame_ref="как я и говорил, ушли на 82k")
    expected = claim(claim_type=ClaimType.EXPECTED_OUTCOME,
                     raw_quote_or_frame_ref="цель 82000")
    usable = claim()
    ctx = build_decision_context(episode(claims=[hindsight, expected, usable]))
    types = {c.claim_type for c in ctx.claims}
    assert ClaimType.RETROSPECTIVE_COMMENTARY not in types
    assert ClaimType.EXPECTED_OUTCOME not in types
    assert ClaimType.MARKET_OBSERVATION in types
    # А в фазе разбора они доступны — иначе разбирать нечего.
    t3 = claims_for_phase([hindsight, expected, usable], Phase.T3, T0 + timedelta(hours=10))
    assert len(t3) == 3


def test_stale_observation_is_dropped_and_can_fail_loudly():
    old = claim(collected_at=T0 - timedelta(days=400))
    ep = episode(claims=[old])
    assert build_decision_context(ep).claims == ()
    with pytest.raises(StaleObservation):
        build_decision_context(ep, strict_stale=True)


def test_quarantined_claim_never_reaches_the_decision():
    poisoned = claim(verification_status=VerificationStatus.QUARANTINED)
    assert build_decision_context(episode(claims=[poisoned])).claims == ()


@pytest.mark.parametrize("field,value", [("asset", "ETHUSDT"), ("timeframe", "15m"),
                                         ("venue", "bybit")])
def test_mixing_assets_timeframes_or_venues_is_refused(field, value):
    with pytest.raises(ContextMismatch):
        build_decision_context(episode(claims=[claim(**{field: value})]))


def test_outcome_window_is_strictly_the_future():
    ep = episode()
    window = outcome_window(ep, bars=5)
    assert window and all(c.ts > ep.decision_time for c in window)


def test_no_history_before_the_decision_is_an_error_not_an_empty_trade():
    candles = series(10)
    ep = Episode(episode_id="e", asset="BTCUSDT", venue="binance-futures", timeframe="1h",
                 decision_time=candles[0].ts - timedelta(hours=1), candles=candles)
    with pytest.raises(LookaheadViolation):
        build_decision_context(ep)


def test_claim_with_future_collected_at_cannot_be_constructed():
    with pytest.raises(Exception):
        claim(collected_at=utcnow() + timedelta(days=1))
