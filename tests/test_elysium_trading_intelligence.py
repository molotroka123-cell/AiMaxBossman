"""Unit tests for Elysium Trading Intelligence, Discovery, Strategies & Paper-Trading Engine."""
from __future__ import annotations

import pytest

from learning.bullshot_earnings_ledger import build_canonical_bullshot_ledger
from learning.elysium_trading_intelligence import (
    ELYSIUM_CONFIG,
    ElysiumExecutionGovernor,
    LookAheadBiasError,
    OHLCVCandle,
    TokenMarketSnapshot,
    assert_no_lookahead_bias,
    calculate_shannon_entropy_bits,
    classify_discovery_signals,
    evaluate_all_strategies,
    evaluate_strategy_a_early_momentum,
    evaluate_strategy_b_pullback_entry,
    evaluate_strategy_c_post_launch_migration,
    evaluate_token_risk,
    generate_200_elysium_scenarios,
    record_elysium_session_to_ledger,
    run_forward_paper_trading_session,
)


def _make_clean_snapshot(**overrides) -> TokenMarketSnapshot:
    defaults = dict(
        token_address="0x8a2201953af29e8be7700bcb53da3ce5cad18726",
        symbol="KALOS",
        decision_timestamp=1794168600,
        chain_id=99801,
        age_minutes=25.0,
        is_verified_factory=True,
        bonding_curve_pct=100.0,
        curve_locked=False,
        is_graduated_dex=True,
        migration_age_minutes=15.0,
        volume_5m_hype=45.0,
        volume_prev_5m_hype=22.0,
        reserve_whype=135.0,
        pool_fee_bps=500,
        effective_spread_bps=35.0,
        creator_holding_pct=2.5,
        creator_prior_rugs=0,
        top10_non_pool_pct=16.0,
        sybil_cluster_share_pct=3.5,
        unique_buyers_5m=28,
        taker_buy_ratio_5m=0.64,
        net_inflow_5m_hype=12.5,
        smart_money_net_buys=2,
        smart_money_dumping=False,
        eth_call_buy_ok=True,
        eth_call_sell_ok=True,
        round_trip_tax_bps=25.0,
        has_mutable_tax_or_blacklist=False,
        trade_interval_entropy_bits=4.35,
        price_change_5m_pct=8.5,
        pullback_from_local_high_pct=-5.0,
        vwap_distance_pct=0.8,
        candles=(
            OHLCVCandle(
                timestamp=1794168540,
                open=1.0,
                high=1.05,
                low=0.99,
                close=1.04,
                volume_hype=10.0,
                buy_volume_hype=6.5,
                trade_count=15,
            ),
            OHLCVCandle(
                timestamp=1794168600,
                open=1.04,
                high=1.09,
                low=1.03,
                close=1.08,
                volume_hype=12.0,
                buy_volume_hype=8.0,
                trade_count=18,
            ),
        ),
    )
    defaults.update(overrides)
    return TokenMarketSnapshot(**defaults)


def test_network_config_and_chain_id_validation() -> None:
    assert ELYSIUM_CONFIG.validate_chain_id(99901) is True
    assert ELYSIUM_CONFIG.validate_chain_id(99801) is True
    assert ELYSIUM_CONFIG.validate_chain_id(8453) is False
    # Never confuse Kinetiq Elysium L2 (99901/99801) with Vulcan Forged Elysium (1339)
    with pytest.raises(ValueError, match="Vulcan Forged"):
        ELYSIUM_CONFIG.validate_chain_id(1339)

    assert ELYSIUM_CONFIG.recommended_fcfs_max_fee_wei(100_000_000) == 105_000_000


def test_lookahead_bias_guard_rejects_future_candles() -> None:
    future_candle = OHLCVCandle(
        timestamp=1794168660,  # 60s after decision_timestamp
        open=1.08,
        high=1.20,
        low=1.07,
        close=1.18,
        volume_hype=20.0,
        buy_volume_hype=15.0,
        trade_count=25,
    )
    with pytest.raises(LookAheadBiasError, match="Look-ahead bias violation"):
        assert_no_lookahead_bias([future_candle], decision_timestamp=1794168600)

    snap_with_future = _make_clean_snapshot(candles=(future_candle,))
    with pytest.raises(LookAheadBiasError):
        evaluate_token_risk(snap_with_future)


def test_shannon_entropy_distinguishes_organic_vs_wash_bot() -> None:
    # Deterministic timer bot trading every 1.0s
    wash_intervals = [1.0] * 32
    assert calculate_shannon_entropy_bits(wash_intervals) == 0.0

    # Diverse multi-wallet inter-arrival times across all 16 bins
    organic_intervals = [
        0.08, 0.18, 0.4, 0.8, 1.5, 2.8, 4.2, 6.1,
        8.9, 12.4, 18.0, 25.0, 38.0, 52.0, 85.0, 140.0,
    ] * 2
    entropy = calculate_shannon_entropy_bits(organic_intervals)
    assert entropy >= 3.8


def test_phase2_signal_classification_and_risk_vetoes() -> None:
    classes = classify_discovery_signals()
    assert len(classes["predictive"]) >= 4
    assert len(classes["unverified"]) >= 2
    assert len(classes["correlational"]) >= 2

    clean_snap = _make_clean_snapshot()
    clean_risk = evaluate_token_risk(clean_snap)
    assert clean_risk.is_tradeable is True
    assert clean_risk.risk_tier == "LOW"
    assert clean_risk.risk_score < 20

    # Honeypot revert must trigger CRITICAL_REJECT
    hp_snap = _make_clean_snapshot(eth_call_sell_ok=False)
    hp_risk = evaluate_token_risk(hp_snap)
    assert hp_risk.is_tradeable is False
    assert hp_risk.risk_tier == "CRITICAL_REJECT"
    assert any("HEX_REVERT_HONEYPOT" in r for r in hp_risk.hard_veto_reasons)

    # Curve graduation lock (99.6%) must trigger CRITICAL_REJECT
    lock_snap = _make_clean_snapshot(bonding_curve_pct=99.6, is_graduated_dex=False, curve_locked=True)
    lock_risk = evaluate_token_risk(lock_snap)
    assert lock_risk.is_tradeable is False
    assert any("CURVE_GRADUATION_LOCK" in r for r in lock_risk.hard_veto_reasons)


def test_strategies_a_b_c_entry_and_skip_logic() -> None:
    # Strategy A: Early Momentum
    snap_a = _make_clean_snapshot(
        is_graduated_dex=False,
        bonding_curve_pct=60.0,
        migration_age_minutes=0.0,
        age_minutes=12.0,
        reserve_whype=70.0,
        volume_5m_hype=50.0,
        volume_prev_5m_hype=24.0,
        taker_buy_ratio_5m=0.66,
        net_inflow_5m_hype=14.0,
        price_change_5m_pct=12.0,
        pullback_from_local_high_pct=-4.0,
    )
    risk_a = evaluate_token_risk(snap_a)
    dec_a = evaluate_strategy_a_early_momentum(snap_a, risk_a, portfolio_hype=10.0)
    assert dec_a.action == "ENTER_LONG"
    assert dec_a.pool_impact_pct <= 1.8
    assert dec_a.expected_net_edge_bps >= 60.0

    # Strategy B: Pullback Entry
    snap_b = _make_clean_snapshot(
        is_graduated_dex=False,
        bonding_curve_pct=75.0,
        migration_age_minutes=0.0,
        age_minutes=32.0,
        reserve_whype=90.0,
        pullback_from_local_high_pct=-24.0,
        vwap_distance_pct=-1.2,
        taker_buy_ratio_5m=0.58,
        net_inflow_5m_hype=5.5,
    )
    risk_b = evaluate_token_risk(snap_b)
    dec_b = evaluate_strategy_b_pullback_entry(snap_b, risk_b, portfolio_hype=10.0)
    assert dec_b.action == "ENTER_LONG"
    assert dec_b.expected_net_edge_bps >= 55.0

    # Strategy C: Post-Launch / Migration
    snap_c = _make_clean_snapshot()
    risk_c, dec_c = evaluate_all_strategies(snap_c, portfolio_hype=10.0)
    assert risk_c.is_tradeable is True
    assert dec_c.strategy_id == "STRATEGY_C_POST_MIGRATION"
    assert dec_c.action == "ENTER_LONG"


def test_execution_governor_safety_gates_and_circuit_breakers() -> None:
    gov = ElysiumExecutionGovernor(
        max_trade_hype=0.25,
        max_daily_loss_hype=0.50,
        max_drawdown_pct=3.0,
        peak_equity_hype=10.0,
        current_equity_hype=10.0,
    )
    # Block unlimited approval
    ok, reason = gov.check_pre_trade_safety(
        requested_size_hype=0.10,
        approval_amount_tokens=1_000_000.0,
        exact_trade_amount_tokens=100.0,
        base_fee_gwei=0.10,
        rpc_block_lag=0,
        eth_call_round_trip_ok=True,
    )
    assert ok is False
    assert "UNLIMITED_APPROVAL_BLOCKED" in reason

    # Trigger drawdown circuit breaker (>3.0%)
    gov.record_trade_outcome(-0.35, execution_succeeded=True)
    ok2, reason2 = gov.check_pre_trade_safety(
        requested_size_hype=0.10,
        approval_amount_tokens=100.0,
        exact_trade_amount_tokens=100.0,
        base_fee_gwei=0.10,
        rpc_block_lag=0,
        eth_call_round_trip_ok=True,
    )
    assert ok2 is False
    assert gov.halted is True
    assert "MAX_DRAWDOWN_BREACHED" in reason2


def test_200_scenarios_paper_trading_and_strict_simulated_ledger_integration() -> None:
    scenarios = generate_200_elysium_scenarios()
    assert len(scenarios) == 200

    # Verify all 14 categories are covered and zero look-ahead bias holds on all 200
    cats = {s.category for s in scenarios}
    assert len(cats) == 14
    for s in scenarios:
        assert_no_lookahead_bias(s.snapshot.candles, s.decision_timestamp)
        assert set(s.steps.keys()) == {
            "1_OBSERVE",
            "2_ANALYZE",
            "3_DECIDE",
            "4_SIMULATE",
            "5_VERIFY",
            "6_SCORE",
            "7_REMEMBER",
        }

    paper = run_forward_paper_trading_session(scenarios, initial_capital_hype=10.0)
    assert paper["accounting_classification"] == "SIMULATED_PAPER_TRADING"
    assert paper["confirmed_realized_income_usd"] == 0.0
    assert paper["scenarios_evaluated"] == 200
    assert paper["trades_executed"] > 20
    assert paper["net_simulated_pnl_hype"] > 0.0
    assert paper["profit_factor"] > 1.5
    assert paper["max_drawdown_pct"] < 3.0

    # Verify integration with BullshotEarningsLedger preserves strict SIMULATED ($0.00) accounting
    ledger = build_canonical_bullshot_ledger()
    entry = record_elysium_session_to_ledger(ledger, paper)
    assert entry.status == "SIMULATED"
    assert entry.cash_out_proceeds_usd == 0.0
    assert entry.net_realized_pnl_usd == 0.0
    summary = ledger.summary()
    assert summary["status_counts"]["SIMULATED"] == 1
    assert summary["overall_verdict"] == "NOT_PROVEN"
