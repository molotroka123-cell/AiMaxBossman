"""Unit tests for Bossman Autonomous 7-Agent Trading Swarm, Fee-Aware Router & Cross-Chain Ledger."""
from __future__ import annotations

import pytest

from learning.bossman_trading_swarm import (
    BENCHMARKED_MODELS,
    CLOUD_BUDGET_LIMIT_USD,
    CLOUD_EXPENDITURE_TO_DATE_USD,
    BossmanTradingSwarm,
    CandidateOpportunity,
    STRESS_CATEGORIES_14,
    build_verified_cross_chain_ledger,
    calculate_dynamic_trade_economics,
    compare_execution_routes,
    generate_140_stress_scenarios,
    get_pumpswap_dynamic_fee_bps,
    run_champion_challenger_benchmark,
)


def test_phase1_model_catalog_and_cloud_budget_guard() -> None:
    assert CLOUD_EXPENDITURE_TO_DATE_USD < CLOUD_BUDGET_LIMIT_USD
    local_models = [m for m in BENCHMARKED_MODELS if m.provider == "local_ollama"]
    assert len(local_models) >= 4
    assert any(m.model_id == "ace-qwen3-17b:latest" and m.cost_per_1k_tokens_usd == 0.0 for m in local_models)
    assert any(m.model_id == "ace-decider-08b:latest" and m.measured_tokens_per_sec > 100.0 for m in local_models)


def test_phase3_and_phase4_pumpfun_and_pumpswap_fee_schedule() -> None:
    # PumpSwap dynamic tiers by market cap in SOL
    tier_low = get_pumpswap_dynamic_fee_bps(100.0)
    assert tier_low["total_single_swap_bps"] == 125  # 2 LP + 93 Protocol + 30 Creator

    tier_mid = get_pumpswap_dynamic_fee_bps(500.0)
    assert tier_mid["lp_bps"] == 20
    assert tier_mid["protocol_bps"] == 5
    assert tier_mid["creator_bps"] == 95
    assert tier_mid["total_single_swap_bps"] == 120

    tier_high = get_pumpswap_dynamic_fee_bps(100_000.0)
    assert tier_high["creator_bps"] == 5
    assert tier_high["total_single_swap_bps"] == 30

    # Compare DIRECT_PROGRAM_RPC vs WEB_APP_UI_ROUTER on Pump.fun bonding curve (1.25% = 125 bps per leg = 250 bps RT)
    comp = compare_execution_routes(
        venue="pumpfun_bonding_curve",
        notional_usd=10.0,
        entry_price=0.00010000,
        pool_liquidity_usd=5000.0,
        conservative_expected_gross_move_bps=420.0,
    )
    assert comp["recommended_route"] == "DIRECT_PROGRAM_RPC"
    assert comp["savings_vs_web_ui_bps"] == 150.0  # Saves 0.75% * 2 = 150 bps platform fee!

    direct_q = comp["recommended_quote"]
    assert direct_q["trading_fee_round_trip_bps"] == 250.0
    assert direct_q["platform_or_routing_fee_round_trip_bps"] == 0.0
    assert direct_q["minimum_profitable_exit_price"] > 0.00010000
    assert direct_q["is_trade_approved_after_fees"] is True

    # Reject trade when conservative expected gross move does not cover fees + risk premium
    weak_q = calculate_dynamic_trade_economics(
        venue="pumpfun_bonding_curve",
        route="DIRECT_PROGRAM_RPC",
        notional_usd=10.0,
        entry_price=1.0,
        pool_liquidity_usd=5000.0,
        conservative_expected_gross_move_bps=180.0,  # Less than 250 bps RT curve fee!
    )
    assert weak_q.is_trade_approved_after_fees is False
    assert "INSUFFICIENT_NET_EDGE" in weak_q.rejection_reason


def test_phase2_seven_agent_swarm_workflow_and_risk_blocks() -> None:
    swarm = BossmanTradingSwarm(bankroll_usd=150.0)
    scenarios = generate_140_stress_scenarios()
    assert len(scenarios) == 140
    assert len(STRESS_CATEGORIES_14) == 14

    # 1. Clean post-migration scenario (index 0 -> 01_price_discovery) should be approved & executed
    clean_cand = scenarios[0]
    res_clean = swarm.process_opportunity(clean_cand)
    assert res_clean["scout"]["promising"] is True
    assert res_clean["analyst"]["is_clean"] is True
    assert res_clean["researcher"]["selected_strategy"] == "STRATEGY_C_POST_MIGRATION"
    assert res_clean["risk_manager"]["approved"] is True
    assert res_clean["execution"]["executed"] is True
    assert res_clean["auditor"]["reconciled"] is True
    assert res_clean["learning"]["auto_deployed_unvalidated_weights"] is False

    # 2. Fake breakout / low entropy wash scenario (index 2 -> 03_fake_breakouts) must be blocked by Analyst
    wash_cand = scenarios[2]
    res_wash = swarm.process_opportunity(wash_cand)
    assert res_wash["analyst"]["is_clean"] is False
    assert any("WASH_TRADING_ENTROPY" in v for v in res_wash["analyst"]["vetoes"])
    assert res_wash["execution"]["executed"] is False

    # 3. Creator selling (index 4 -> 05_creator_selling) must be blocked
    dump_cand = scenarios[4]
    res_dump = swarm.process_opportunity(dump_cand)
    assert res_dump["analyst"]["is_clean"] is False
    assert "CREATOR_DUMPING_DETECTED" in res_dump["analyst"]["vetoes"]
    assert res_dump["execution"]["executed"] is False


def test_phase5_champion_challenger_benchmark_promotes_hybrid_swarm() -> None:
    bench = run_champion_challenger_benchmark()
    assert bench["total_stress_scenarios"] == 140
    assert bench["total_combined_curriculum_scenarios"] == 940
    assert set(bench["environment_breakdown"].keys()) == {"solana", "elysium", "base"}

    comp = bench["champion_challenger_comparison"]
    baseline = comp["A_EXISTING_BASELINE"]
    local_only = comp["B_LOCAL_SINGLE_MODEL"]
    cloud_only = comp["C_CLOUD_MULTI_AGENT"]
    hybrid = comp["D_HYBRID_LOCAL_FIRST_SWARM"]

    assert bench["promoted_champion"] == "D_HYBRID_LOCAL_FIRST_SWARM"
    assert hybrid["promoted"] is True
    assert hybrid["net_pnl_after_infra_usd"] > baseline["net_pnl_after_infra_usd"]
    assert hybrid["net_pnl_after_infra_usd"] > local_only["net_pnl_after_infra_usd"]
    assert hybrid["net_pnl_after_infra_usd"] > cloud_only["net_pnl_after_infra_usd"]
    assert hybrid["max_drawdown_pct"] < 2.0


def test_phase7_cross_chain_ledger_separates_deposits_unrealized_and_realized_pnl() -> None:
    ledger = build_verified_cross_chain_ledger()
    dash = ledger["owner_dashboard"]

    # Outside deposits ($171.38) are strictly separated from revenue
    assert dash["total_outside_deposits_usd"] > 170.0
    # Liquid capital ($150.51) + Unrealized inventory ($16.58) tracked separately
    assert dash["current_liquid_capital_usd"] == pytest.approx(150.5109, abs=0.01)
    assert dash["unrealized_inventory_and_rent_usd"] == pytest.approx(16.5787, abs=0.01)

    # Off-chain GEMS and Simulated Elysium paper trades must have $0.00 realized profit
    records_by_id = {r["record_id"]: r for r in ledger["records"]}
    assert records_by_id["LEDGER-03-BULL-GEMS-OFFCHAIN"]["net_realized_profit_usd"] == 0.0
    assert records_by_id["LEDGER-04-ELYSIUM-L2-SIMULATED"]["net_realized_profit_usd"] == 0.0

    # Realized creator fee rewards (+$2.2931) are recorded, while full-lifecycle net realized P&L is honestly negative (-$6.3070)
    assert dash["realized_creator_fee_rewards_usd"] == pytest.approx(2.2931, abs=0.01)
    assert dash["realized_net_pnl_usd"] < 0.0
    assert dash["independently_verified_full_lifecycle_profitable_trades"] == 0
