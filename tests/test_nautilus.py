"""Comprehensive unit and edge-case tests for the `nautilus` analytics & Telegram Pult package."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import httpx
import pytest
from PIL import Image

from nautilus import (
    PerformanceMetrics,
    build_operational_state,
    compute_beta,
    compute_cagr,
    compute_calmar,
    compute_dollar_becomes,
    compute_max_drawdown,
    compute_multi_window_report,
    compute_performance_metrics,
    compute_sharpe,
    compute_sortino,
    compute_volatility,
    compute_winning_weeks_streak,
    format_nautilus_telegram_message,
    generate_trade_ticket,
    load_sample_fixture,
    render_nautilus_card,
    run_stress_audit,
    save_nautilus_card,
    send_nautilus_report_to_pult,
)
from nautilus.card import BG_COLOR, ORANGE_ACCENT_COLOR, TILE_BG_COLOR


def test_core_metrics_formulas_and_edge_cases():
    # Empty series edge cases
    assert compute_dollar_becomes([]) == 1.0
    assert compute_cagr([]) == 0.0
    assert compute_volatility([]) == 0.0
    assert compute_sharpe([]) == 0.0
    assert compute_sortino([]) == 0.0
    assert compute_max_drawdown([]) == 0.0
    assert compute_calmar(42.19, 0.0) == 0.0
    assert compute_beta([], []) == 0.0
    assert compute_winning_weeks_streak([]) == 0

    # Single element edge cases
    assert compute_dollar_becomes([0.05]) == pytest.approx(1.05)
    assert compute_volatility([0.05]) == 0.0
    assert compute_sharpe([0.05]) == 0.0
    assert compute_sortino([0.05]) == 0.0
    assert compute_beta([0.05], [0.02]) == 0.0

    # Total wipeout (-100% return) edge case
    assert compute_dollar_becomes([0.10, -1.0, 0.20]) == 0.0
    assert compute_cagr([0.10, -1.0, 0.20]) == -100.0

    # Non-finite input raises ValueError across all metric functions
    for bad in ([0.01, float("nan")], [0.01, float("inf")], [0.01, float("-inf")]):
        with pytest.raises(ValueError, match="non-finite"):
            compute_dollar_becomes(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_cagr(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_volatility(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_sharpe(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_sortino(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_max_drawdown(bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_beta(bad, [0.01, 0.02])
        with pytest.raises(ValueError, match="non-finite"):
            compute_beta([0.01, 0.02], bad)
        with pytest.raises(ValueError, match="non-finite"):
            compute_winning_weeks_streak(bad)

    with pytest.raises(ValueError, match="finite"):
        compute_calmar(float("nan"), -10.0)

    # Known series test
    returns = [0.01, 0.02, -0.01, 0.015, 0.005, -0.005, 0.02]
    bench = [0.005, 0.01, -0.008, 0.006, 0.002, -0.004, 0.01]
    perf = compute_performance_metrics(returns, bench, periods_per_year=252.0)
    assert perf.dollar_becomes > 1.05
    assert perf.cagr_pct > 0.0
    assert perf.sharpe > 0.0
    assert perf.sortino > perf.sharpe
    assert perf.max_drawdown_pct < 0.0
    assert perf.calmar > 0.0
    assert perf.volatility_pct > 0.0
    assert perf.beta > 0.0


def test_winning_weeks_streak_tail_calculation():
    weekly = [-0.02, 0.01, -0.005] + [0.012] * 14
    assert compute_winning_weeks_streak(weekly) == 14
    assert compute_winning_weeks_streak([0.01, 0.02, -0.001]) == 0
    assert compute_winning_weeks_streak([0.01, 0.02, 0.0]) == 0


def test_sample_fixture_multi_window_exact_values():
    data = load_sample_fixture()
    report = compute_multi_window_report(data)

    # 1. 6-tile dashboard card values
    assert report.card_strategy_label == "NAUTILUS"
    assert report.card_benchmark_label == "SPY"
    assert report.card_strategy.cagr_pct == pytest.approx(42.19)
    assert report.card_benchmark.cagr_pct == pytest.approx(11.49)
    assert report.card_strategy.sharpe == pytest.approx(1.62)
    assert report.card_benchmark.sharpe == pytest.approx(0.69)
    assert report.card_strategy.sortino == pytest.approx(2.41)
    assert report.card_benchmark.sortino == pytest.approx(0.94)
    assert report.card_strategy.max_drawdown_pct == pytest.approx(-23.62)
    assert report.card_benchmark.max_drawdown_pct == pytest.approx(-33.72)
    assert report.card_strategy.calmar == pytest.approx(1.79)
    assert report.card_benchmark.calmar == pytest.approx(0.34)
    assert report.card_strategy.dollar_becomes == pytest.approx(13.34)
    assert report.card_benchmark.dollar_becomes == pytest.approx(2.21)

    # 2. YTD (NAUTILUS_BTC vs BTC)
    ytd = report.ytd_btc
    assert ytd.strategy_return_pct == pytest.approx(49.27)
    assert ytd.benchmark_return_pct == pytest.approx(-6.89)
    assert ytd.strategy_sharpe == pytest.approx(2.18)
    assert ytd.benchmark_sharpe == pytest.approx(0.03)
    assert ytd.strategy_max_dd_pct == pytest.approx(-11.36)
    assert ytd.benchmark_max_dd_pct == pytest.approx(-39.53)
    assert ytd.exposure_delta_note == "30% -> 100% long BTC"

    # 3. Since Inception (NAUTILUS vs SPY)
    inc = report.since_inception
    assert inc.strategy_return_pct == pytest.approx(22.41)
    assert inc.benchmark_return_pct == pytest.approx(14.43)
    assert inc.excess_return_pct == pytest.approx(7.98)
    assert inc.strategy_sharpe == pytest.approx(2.86)
    assert inc.strategy_volatility_pct == pytest.approx(14.16)
    assert inc.strategy_max_dd_pct == pytest.approx(-6.75)
    assert inc.beta == pytest.approx(0.293)

    # 4. Last 26 Trading Sessions
    l26 = report.last_26_sessions
    assert l26.strategy_sharpe == pytest.approx(4.79)
    assert l26.benchmark_sharpe == pytest.approx(1.06)
    assert l26.strategy_volatility_pct == pytest.approx(8.48)
    assert l26.benchmark_volatility_pct == pytest.approx(9.66)
    assert l26.strategy_max_dd_pct == pytest.approx(-1.18)
    assert l26.benchmark_max_dd_pct == pytest.approx(-2.1)
    assert l26.beta == pytest.approx(0.013)

    # 5. Winning weeks streak
    assert report.winning_weeks_streak == 14
    assert report.winning_weeks_label == "13+ weeks in a row"


def test_five_operational_blocks_and_safety_gate():
    data = load_sample_fixture()
    ops = build_operational_state(data)

    # Eternal Prohibition #1 safety gate
    assert ops.safety_gate["mode"] == "READ_ONLY_SHADOW_ANALYTICS"
    assert ops.safety_gate["live_order_execution"] == "DISABLED"
    assert ops.safety_gate["money_transfers"] == "PROHIBITED"
    assert ops.safety_gate["private_keys_loaded"] is False

    # Block 1: market_regime
    assert ops.market_regime.regime_code == "BULL_EXPANSION_HEDGED"
    assert ops.market_regime.btc_exposure_transition == "30% -> 100% long BTC"

    # Block 2: leverage_exposure
    assert ops.leverage_exposure.gross_leverage == pytest.approx(1.25)
    assert ops.leverage_exposure.net_exposure == pytest.approx(0.75)
    assert ops.leverage_exposure.long_pct == pytest.approx(100.0)
    assert ops.leverage_exposure.short_pct == pytest.approx(25.0)

    # Block 3: trade_ticket
    by_sym = {item.symbol: item for item in ops.trade_ticket}
    assert by_sym["BTC-SPOT"].side == "LONG"
    assert by_sym["BTC-SPOT"].old_weight_pct == pytest.approx(30.0)
    assert by_sym["BTC-SPOT"].new_weight_pct == pytest.approx(45.0)
    assert by_sym["BTC-SPOT"].action == "INCREASE"

    assert by_sym["APEX-SHORT-BASKET"].side == "SHORT"
    assert by_sym["APEX-SHORT-BASKET"].old_weight_pct == pytest.approx(-20.0)
    assert by_sym["APEX-SHORT-BASKET"].new_weight_pct == pytest.approx(-25.0)
    assert by_sym["APEX-SHORT-BASKET"].action == "INCREASE"

    assert by_sym["CASH-USD"].action == "CLOSE"
    assert by_sym["NVDA"].action == "HOLD"

    # Block 4: portfolio_comparison (including Silent-Killer comparison)
    sk = ops.portfolio_comparison.silent_killer_comparison
    assert "Silent-Killer" in sk["profile_name"]
    assert "APEX" in ops.portfolio_comparison.short_hedge_description_ru

    # Block 5: stress_audit
    sa = ops.stress_audit
    assert sa.base_cagr_pct == pytest.approx(42.19)
    assert sa.net_cagr_after_cost_pct == pytest.approx(39.19)
    assert sa.removed_best_n_days == 5
    assert sa.live_sample_days == 26
    assert "26" in sa.sample_warning_ru


def test_stress_audit_from_raw_return_series():
    daily = [0.002] * 240 + [0.045, 0.038, 0.032, 0.029, 0.025] + [-0.015] * 7
    audit = run_stress_audit(
        daily,
        fee_slippage_bps_per_turn=20.0,
        annual_turnover_turns=10.0,
        remove_best_n_days=5,
    )
    assert audit.base_cagr_pct > audit.cagr_without_best_n_days_pct
    assert audit.net_cagr_after_cost_pct == pytest.approx(audit.base_cagr_pct - 2.0, abs=0.05)


def test_render_and_save_nautilus_card_png(tmp_path: Path):
    data = load_sample_fixture()
    report = compute_multi_window_report(data)
    out_file = tmp_path / "nautilus_card.png"

    saved_path = save_nautilus_card(
        out_file,
        report.card_strategy,
        report.card_benchmark,
        strategy_label=report.card_strategy_label,
        benchmark_label=report.card_benchmark_label,
        width=1200,
        height=680,
    )
    assert saved_path == out_file
    assert out_file.is_file()
    raw = out_file.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")

    with Image.open(io.BytesIO(raw)) as img:
        assert img.size == (1200, 680)
        assert img.mode == "RGB"
        # Verify corner is dark background
        assert img.getpixel((5, 5)) == BG_COLOR
        # Verify orange accent pixels are rendered inside the card
        pixels = list(getattr(img, "get_flattened_data", img.getdata)())
        orange_Hits = sum(
            1
            for r, g, b in pixels
            if abs(r - ORANGE_ACCENT_COLOR[0]) < 25 and abs(g - ORANGE_ACCENT_COLOR[1]) < 25 and b < 50
        )
        tile_Hits = sum(1 for px in pixels if px == TILE_BG_COLOR)
        assert orange_Hits > 500
        assert tile_Hits > 100_000

    # Extreme 6-digit values at minimum resolution 360x240 must fit inside tiles without overflowing borders
    extreme_strat = PerformanceMetrics(999999.99, 999999.99, 999.99, 999.99, -99.99, 999.99, 999999.99)
    min_png = render_nautilus_card(
        extreme_strat,
        report.card_benchmark,
        strategy_label="NAUTILUS_EXTREME_LONG_STRATEGY_LABEL",
        width=360,
        height=240,
    )
    with Image.open(io.BytesIO(min_png)) as min_img:
        assert min_img.size == (360, 240)
        # Left margin outside tiles (x=2) must remain untouched BG_COLOR across the entire height
        for y in range(240):
            assert min_img.getpixel((2, y)) == BG_COLOR

    with pytest.raises(ValueError, match="at least 360x240"):
        render_nautilus_card(report.card_strategy, report.card_benchmark, width=200, height=100)


def test_edge_cases_reverse_ticket_cash_leverage_and_html_escaping():
    # 1. Sign-flip trade tickets must be classified as REVERSE (not DECREASE)
    tickets = {
        t.symbol: t
        for t in generate_trade_ticket(
            {"FLIP_TO_SHORT": 20.0, "FLIP_TO_LONG": -20.0, "DEEPEN_SHORT": -10.0},
            {"FLIP_TO_SHORT": -30.0, "FLIP_TO_LONG": 15.0, "DEEPEN_SHORT": -25.0},
        )
    }
    assert tickets["FLIP_TO_SHORT"].side == "SHORT"
    assert tickets["FLIP_TO_SHORT"].action == "REVERSE"
    assert tickets["FLIP_TO_LONG"].side == "LONG"
    assert tickets["FLIP_TO_LONG"].action == "REVERSE"
    assert tickets["DEEPEN_SHORT"].side == "SHORT"
    assert tickets["DEEPEN_SHORT"].action == "INCREASE"

    # 2. Cash keys must not inflate risky long_pct or gross_leverage when leverage_exposure is computed from weights
    ops_with_cash = build_operational_state(
        {
            "portfolio": {
                "current_portfolio": {
                    "BTC-SPOT": 30.0,
                    "NVDA": 25.0,
                    "MSTR": 15.0,
                    "PLTR": 15.0,
                    "CASH-USD": 15.0,
                    "APEX-SHORT-BASKET": -20.0,
                }
            }
        }
    )
    assert ops_with_cash.leverage_exposure.long_pct == pytest.approx(85.0)
    assert ops_with_cash.leverage_exposure.short_pct == pytest.approx(20.0)
    assert ops_with_cash.leverage_exposure.cash_buffer_pct == pytest.approx(15.0)
    assert ops_with_cash.leverage_exposure.gross_leverage == pytest.approx(1.05)
    assert ops_with_cash.leverage_exposure.net_exposure == pytest.approx(0.65)

    # 3. Raw return series in multi-window payload + HTML escaping of '<', '>', '&'
    raw_payload = {
        "dashboard_card": {
            "strategy_cagr_pct": 42.19,
            "strategy_max_drawdown_pct": -23.62,
            "strategy_dollar_becomes": 13.34,
            "benchmark_cagr_pct": 11.49,
            "benchmark_max_drawdown_pct": -33.72,
            "benchmark_dollar_becomes": 2.21,
        },
        "ytd_btc": {
            "strategy_returns": [0.02, 0.03, -0.01, 0.04],
            "benchmark_returns": [0.01, -0.02, 0.005, -0.01],
            "exposure_delta_note": "30% -> 100% & <fast>",
        },
    }
    rep = compute_multi_window_report(raw_payload)
    ops_default = build_operational_state(raw_payload)
    assert rep.card_strategy.total_return_pct == pytest.approx(1234.0)
    assert rep.card_strategy.calmar == pytest.approx(42.19 / 23.62, abs=0.01)

    html_msg = format_nautilus_telegram_message(rep, ops_default)
    assert "30% -&gt; 100% &amp; &lt;fast&gt;" in html_msg
    assert "Max DD &lt; 8%" in html_msg


@pytest.mark.asyncio
async def test_format_and_send_nautilus_report_to_pult_mock_transport(tmp_path: Path, monkeypatch):
    data = load_sample_fixture()
    report = compute_multi_window_report(data)
    ops = build_operational_state(data)
    msg = format_nautilus_telegram_message(report, ops)

    # Check all required sections and metrics appear in the formatted Telegram HTML report
    for expected in (
        "NAUTILUS / NAUTILUS_BTC",
        "42.19%",
        "11.49%",
        "$13.34",
        "$2.21",
        "+49.27%",
        "-6.89%",
        "30% -&gt; 100% long BTC",
        "+22.41%",
        "+14.43%",
        "+7.98%",
        "0.293",
        "4.79",
        "1.06",
        "8.48%",
        "9.66%",
        "0.013",
        "13+ weeks in a row",
        "BULL_EXPANSION_HEDGED",
        "1.25x",
        "APEX-SHORT-BASKET",
        "Silent-Killer",
        "stress_audit",
    ):
        assert expected in msg

    # Mock _load_pult_target and httpx transport
    monkeypatch.setattr(
        "nautilus.telegram_report._load_pult_target",
        lambda config_path=None: ("123456789:TEST_FAKE_TOKEN_FOR_UNIT_TEST", 987654321, ""),
    )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/sendPhoto"):
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 701}})
        if request.url.path.endswith("/sendMessage"):
            body = json.loads(request.content.decode("utf-8"))
            assert body["chat_id"] == 987654321
            assert body["parse_mode"] == "HTML"
            assert "NAUTILUS" in body["text"]
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 702}})
        return httpx.Response(404, json={"ok": False})

    card_path = tmp_path / "pult_card.png"
    res = await send_nautilus_report_to_pult(
        data,
        card_output_path=card_path,
        transport=httpx.MockTransport(handler),
    )
    assert res["ok"] is True
    assert res["photo_message_id"] == 701
    assert res["text_message_id"] == 702
    assert card_path.is_file()
    assert len(calls) == 2

