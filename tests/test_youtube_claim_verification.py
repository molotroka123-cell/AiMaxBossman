import math

from learning.claim_verification import (
    Claim,
    Market,
    align_from_chat,
    normalize_price_value,
    overall_status,
    summarize,
    value_supported_by_quote,
    verify_claim,
)

DAY = 1_787_184_000  # 2026-08-20 00:00:00 UTC (a session boundary)


def _klines(prices, start=DAY, tb_share=0.6, vol=10.0):
    rows = []
    for i, p in enumerate(prices):
        rows.append({"t": start + 60 * i, "o": p, "h": p + 5, "l": p - 5, "c": p,
                     "v": vol, "tb": vol * tb_share})
    return rows


def _claim(**kw):
    base = dict(claim_id="c1", video_id="vid", t_video_s=0.0, quote="q", metric="PRICE",
                kind="FORECAST", direction="UP", value=None, instrument="BTC")
    base.update(kw)
    return Claim(**base)


def test_align_from_chat_uses_in_stream_messages_only():
    pairs = [(0.0, 900.0), (10.0, 1010.5), (20.0, 1021.0), (30.0, 1030.0), (40.0, 5000.0)]
    out = align_from_chat(pairs)
    assert out["status"] == "ALIGNED"
    assert math.isclose(out["epoch_at_offset0"], 1000.5, abs_tol=0.6)
    assert out["n"] == 4


def test_align_from_chat_unknown_when_too_few():
    assert align_from_chat([(0.0, 1.0), (5.0, 7.0)])["status"] == "UNKNOWN"


def test_normalize_price_value_shorthand_and_rejects_out_of_scale():
    assert normalize_price_value(72.5, 72_600) == (72_500.0, "x1000")
    assert normalize_price_value(72_450, 72_600) == (72_450, None)
    assert normalize_price_value(500, 72_600) == (None, "out_of_btc_scale")


def test_value_must_appear_in_quote():
    assert value_supported_by_quote(72_500, "we are sitting at 72.5 right now")
    assert value_supported_by_quote(72_500, "target 72,500")
    assert not value_supported_by_quote(73_000, "we are sitting at 72.5 right now")


def test_forecast_up_verified_and_refuted_per_horizon():
    prices = [100_000 + 20 * i for i in range(300)]  # steady rise
    m = Market(_klines(prices))
    c = _claim(t_utc=DAY + 600)
    ev = verify_claim(c, m, horizons=(1, 15, 60))
    assert ev["15m"]["status"] == "VERIFIED"
    assert ev["60m"]["status"] == "VERIFIED"
    down = verify_claim(_claim(direction="DOWN", t_utc=DAY + 600), m, horizons=(60,))
    assert down["60m"]["status"] == "REFUTED"


def test_forecast_beyond_data_is_unknown_not_guessed():
    m = Market(_klines([100_000] * 30))
    ev = verify_claim(_claim(t_utc=DAY + 600), m, horizons=(240,))
    assert ev["240m"]["status"] == "UNKNOWN"


def test_flat_market_is_inconclusive():
    m = Market(_klines([100_000] * 120))
    ev = verify_claim(_claim(t_utc=DAY + 600), m, horizons=(15,))
    assert ev["15m"]["status"] == "INCONCLUSIVE"


def test_price_state_at_value_uses_past_close_only():
    prices = [72_500] * 20 + [80_000] * 20
    m = Market(_klines(prices))
    # at t=DAY+20*60 the candle opened at 19*60 is the last completed one (72,500)
    c = _claim(kind="STATE", direction="AT", value=72.5, quote="72.5", t_utc=DAY + 20 * 60)
    ev = verify_claim(c, m)
    assert ev["t0"]["status"] == "VERIFIED"
    assert ev["t0"]["normalized"] == "x1000"


def test_cvd_state_from_taker_volume():
    m = Market(_klines([100_000] * 60, tb_share=0.3))  # sellers dominate
    up = verify_claim(_claim(metric="CVD", kind="STATE", direction="UP", t_utc=DAY + 1800), m)
    down = verify_claim(_claim(metric="CVD", kind="STATE", direction="DOWN", t_utc=DAY + 1800), m)
    assert up["t0"]["status"] == "REFUTED"
    assert down["t0"]["status"] == "VERIFIED"


def test_oi_state_and_missing_oi_unknown():
    m = Market(_klines([100_000] * 90), oi=[{"t": DAY + 300 * i, "oi": 1000 + 10 * i} for i in range(18)])
    ev = verify_claim(_claim(metric="OI", kind="STATE", direction="UP", t_utc=DAY + 3600), m)
    assert ev["t0"]["status"] == "VERIFIED"
    no_oi = Market(_klines([100_000] * 90))
    assert verify_claim(_claim(metric="OI", kind="STATE", direction="UP", t_utc=DAY + 3600),
                        no_oi)["t0"]["status"] == "UNKNOWN"


def test_liquidations_and_unaligned_are_unknown():
    m = Market(_klines([100_000] * 90))
    liq = verify_claim(_claim(metric="LIQUIDATIONS", kind="STATE", direction="UP", t_utc=DAY + 600), m)
    assert liq["t0"]["status"] == "UNKNOWN"
    unaligned = verify_claim(_claim(t_utc=None), m, horizons=(5,))
    assert unaligned["5m"]["status"] == "UNKNOWN"
    eth = verify_claim(_claim(instrument="ETH", t_utc=DAY + 600), m, horizons=(5,))
    assert eth["5m"]["status"] == "UNKNOWN"


def test_developing_levels_and_level_claims():
    prices = [100_000] * 50 + [100_300] * 10
    m = Market(_klines(prices))
    lv = m.developing_levels(DAY + 60 * 60)
    assert lv["dOpen"] == 100_000
    assert abs(lv["dPOC"] - 100_000) <= 10
    assert lv["dVAL"] <= lv["dPOC"] <= lv["dVAH"]
    above = verify_claim(_claim(metric="dPOC", kind="STATE", direction="ABOVE", t_utc=DAY + 3600), m)
    assert above["t0"]["status"] == "VERIFIED"
    # levels need the session start candle; otherwise UNKNOWN
    partial = Market(_klines(prices, start=DAY + 600))
    assert verify_claim(_claim(metric="dPOC", kind="STATE", direction="ABOVE", t_utc=DAY + 3600),
                        partial)["t0"]["status"] == "UNKNOWN"


def test_touch_target_forecast():
    prices = [100_000 + 10 * i for i in range(120)]
    m = Market(_klines(prices))
    c = _claim(direction="TOUCH", value=100.5, quote="100.5", t_utc=DAY + 60)
    ev = verify_claim(c, m, horizons=(5, 60))
    assert ev["5m"]["status"] == "REFUTED"
    assert ev["60m"]["status"] == "VERIFIED"


def test_summary_counts_unknown_claims():
    m = Market(_klines([100_000 + 20 * i for i in range(300)]))
    claims = [_claim(t_utc=DAY + 600), _claim(claim_id="c2", metric="LIQUIDATIONS", t_utc=DAY + 600)]
    res = [verify_claim(c, m, horizons=(15,)) for c in claims]
    s = summarize(claims, res, horizons=(15,))
    assert s["claims"] == 2 and s["unknown_claims"] == 1
    assert s["per_horizon"]["15m"]["VERIFIED"] == 1
    assert overall_status(res[0]) == "VERIFIED"


def test_state_touch_checks_past_hour_only():
    prices = [100_000] * 30 + [100_600] * 5 + [100_000] * 60
    m = Market(_klines(prices))
    hit = _claim(kind="STATE", direction="TOUCH", value=100.6, quote="100.6", t_utc=DAY + 60 * 60)
    assert verify_claim(hit, m)["t0"]["status"] == "VERIFIED"
    miss = _claim(kind="STATE", direction="TOUCH", value=101.5, quote="101.5", t_utc=DAY + 60 * 60)
    assert verify_claim(miss, m)["t0"]["status"] == "REFUTED"


def test_history_long_term_and_off_scale_targets_are_unknown():
    m = Market(_klines([100_000 + 20 * i for i in range(300)]))
    hist = verify_claim(_claim(kind="HISTORY", t_utc=DAY + 600), m)
    assert list(hist) == ["t0"] and hist["t0"]["status"] == "UNKNOWN"
    lt = verify_claim(_claim(kind="LONG_TERM", t_utc=DAY + 600), m)
    assert lt["t0"]["status"] == "UNKNOWN"
    far = verify_claim(_claim(value=180_000, quote="180k this year", t_utc=DAY + 600), m, horizons=(15,))
    assert far["15m"]["status"] == "UNKNOWN"
