"""Tests for the Long-Term Pump.fun Project Launchpad, 5-Wallet Genesis Simulation,
Volume & Creator-Fee Control, and Bossman Computer-Use Harness Case.
"""
from __future__ import annotations

import json
import pytest

from bossman.trading_learning.pump_project_launchpad import (
    analyze_twitter_trends,
    build_computer_use_harness_case,
    build_project_launch_plan,
    build_twitter_marketing_campaign,
    calculate_creator_fee_projections,
    launchpad_overview,
    simulate_five_wallet_genesis_launch,
    simulate_volume_and_fee_control,
)
from bossman.trading_learning.routes import pipeline_status


def test_twitter_trends_scores_organic_and_rejects_scam_and_bots():
    signals = [
        {
            "topic": "Autonomous AI Copilots on Solana",
            "hashtags": ["#AIAgents", "#Solana"],
            "mentions_24h": 52000,
            "velocity_pct_6h": 145.0,
            "organic_author_ratio": 0.86,
            "builder_engagement_score": 0.92,
            "utility_fit": "Open-source AI agent control plane",
            "suggested_name": "Bossman Sentinel",
            "suggested_ticker": "BSENT",
        },
        {
            "topic": "1000x guaranteed rug pump and dump coin",
            "hashtags": ["#Scam"],
            "mentions_24h": 95000,
            "velocity_pct_6h": 500.0,
            "organic_author_ratio": 0.90,
            "builder_engagement_score": 0.90,
        },
        {
            "topic": "Sybil Bot Farm Narrative",
            "hashtags": ["#Bots"],
            "mentions_24h": 40000,
            "velocity_pct_6h": 190.0,
            "organic_author_ratio": 0.25,
            "builder_engagement_score": 0.80,
        },
    ]
    res = analyze_twitter_trends(signals, min_organic_ratio=0.65)
    assert res["evaluated_count"] == 3
    assert res["accepted_count"] == 1
    assert res["rejected_count"] == 2
    assert res["winner"]["suggested_ticker"] == "BSENT"
    assert res["winner"]["anti_scam_passed"] is True


def test_five_wallet_genesis_simulation_respects_120bps_liquidity_gate():
    rep = simulate_five_wallet_genesis_launch(
        project_name="Bossman Neural Sentinel",
        ticker="BSENT",
        total_sol_budget=3.0,
    )
    assert rep["execution_mode"] == "OFF"
    assert rep["paper_trading_only"] is True
    assert rep["wallets_count"] == 5
    assert rep["all_tranches_within_120bps_gate"] is True
    assert rep["max_tranche_impact_bps"] <= 120.0
    assert all(t["execution_allowed_live"] is False for t in rep["tranches"])
    assert all(w["disclosed_in_manifest"] is True for w in rep["wallets"])
    assert sum(w["allocated_sol"] for w in rep["wallets"]) == pytest.approx(3.0, abs=1e-4)
    assert rep["holder_monitor_check"]["creator_sold"] is False


def test_volume_and_fee_control_and_creator_fee_tables():
    vol = simulate_volume_and_fee_control(ticker="BSENT", sol_price_usd=180.0)
    assert len(vol["phases"]) == 4
    assert vol["volume_control_policy"]["wash_trading_allowed"] is False
    assert vol["cumulative_creator_fee_usd"] > 0.0
    for phase in vol["phases"]:
        routing = phase["treasury_routing_usd"]
        total_routed = (
            routing["buyback_and_lock_40pct"]
            + routing["product_rd_35pct"]
            + routing["twitter_marketing_25pct"]
        )
        assert total_routed == pytest.approx(phase["phase_creator_fee_usd"], abs=0.05)

    fees = calculate_creator_fee_projections(
        daily_curve_volume_usd=30_000.0,
        daily_pumpswap_volume_usd=150_000.0,
        market_cap_usd=90_000.0,
        sol_price_usd=180.0,
    )
    assert fees["rates_bps"]["bonding_curve_creator_bps"] == 30.0
    assert fees["rates_bps"]["pumpswap_creator_bps"] > 0.0
    assert fees["anti_scam_economics"]["organic_project_daily_net_usd"] > 0.0
    assert fees["anti_scam_economics"]["wash_trading_same_volume_daily_net_usd"] < 0.0


def test_twitter_campaign_requires_owner_approval_and_computer_use_case():
    campaign = build_twitter_marketing_campaign(
        project_name="Bossman Neural Sentinel",
        ticker="BSENT",
        thesis="AI agent telemetry and holder safety vault",
        twitter_handle="@owner_project",
    )
    assert campaign["publish_mode"] == "OWNER_APPROVAL_GATE_ONLY"
    assert campaign["auto_post_without_approval"] is False
    assert campaign["posts_count"] == 4
    assert all(d["status"] == "DRAFT_AWAITING_OWNER_APPROVAL" for d in campaign["drafts"])

    case = build_computer_use_harness_case(
        project_name="Bossman Neural Sentinel",
        ticker="BSENT",
        twitter_handle="@owner_project",
        total_sol_budget=3.0,
    )
    assert case["available"] is True
    assert case["badge"] == "PAPER"
    assert case["safety"]["trading_execution"] == "OFF"
    assert len(case["computer_use_steps"]) == 5
    assert "<svg" in case["token_generator"]["svg_emblem"]
    assert len(case["token_generator"]["plan_sha256"]) == 64

    overview = launchpad_overview()
    assert overview["available"] is True
    step_names = {s["step"] for s in pipeline_status()["steps"]}
    assert "pump_project_launchpad" in step_names


def test_cli_pump_launchpad_outputs_valid_json(capsys):
    from bossman.trading_learning.cli import EXIT_OK, main

    rc = main(["pump_launchpad", "--ticker", "BSENT", "--sol-budget", "3.0"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["token_generator"]["ticker"] == "BSENT"
    assert payload["five_wallet_genesis"]["wallets_count"] == 5


def test_codex_invariants_lamport_conservation_partial_and_na():
    # 1. Full 15 SOL allocation completes with exact lamport conservation
    full_plan = build_project_launch_plan(
        project_name="Bossman Sentinel",
        ticker="BSENT",
        thesis="Long-term utility copilot",
        treasury_sol=15.0,
    )["genesis_bundle"]
    assert full_plan["allocation_status"] == "COMPLETE"
    assert full_plan["requested_lamports"] == 15_000_000_000
    assert full_plan["allocated_lamports"] == 15_000_000_000
    assert full_plan["unallocated_lamports"] == 0
    assert sum(s["amount_lamports"] for s in full_plan["slices"]) == full_plan["allocated_lamports"]
    assert full_plan["plan_verification_status"] == "UNVERIFIED_OFFCHAIN_PLAN_ONLY"

    # 2. Capped max_slices=5 on 15 SOL yields PARTIAL with exact lamport conservation and curve progress on allocated only
    partial_plan = build_project_launch_plan(
        project_name="Bossman Sentinel",
        ticker="BSENT",
        thesis="Long-term utility copilot",
        treasury_sol=15.0,
        max_slices=5,
    )["genesis_bundle"]
    assert partial_plan["allocation_status"] == "PARTIAL"
    assert partial_plan["unallocated_lamports"] > 0
    assert (
        partial_plan["allocated_lamports"] + partial_plan["unallocated_lamports"]
        == partial_plan["requested_lamports"]
    )
    assert partial_plan["curve_progress_after_genesis_pct"] < full_plan["curve_progress_after_genesis_pct"]

    # 3. Trends without demo fixtures or with empty input return honest N/A
    na_trends = analyze_twitter_trends(None, use_demo_fixtures=False)
    assert na_trends["status"] == "N/A"
    assert na_trends["winner"] is None
    empty_trends = analyze_twitter_trends([])
    assert empty_trends["status"] == "N/A"
    demo_trends = analyze_twitter_trends()
    assert demo_trends["status"] == "DEMO"
    assert demo_trends["data_source"] == "DEMO_FIXTURE"

    # 4. Zero-volume & N/A fee projections + accounting ledger separation
    zero_fees = calculate_creator_fee_projections(
        daily_curve_volume_usd=0.0,
        daily_pumpswap_volume_usd=0.0,
        market_cap_usd=50_000.0,
        sol_price_usd=180.0,
        daily_expenses_usd=15.0,
    )
    assert zero_fees["accounting_ledger"]["estimated_gross_usd"] == 0.0
    assert zero_fees["accounting_ledger"]["estimated_net_model_usd"] == -15.0
    assert zero_fees["accounting_ledger"]["realized_net_usd"] == "N/A"
    assert zero_fees["accounting_ledger"]["accrued_usd"] == "N/A"

    na_fees = calculate_creator_fee_projections(market_cap_usd=None)
    assert na_fees["status"] == "N/A"


def test_codex_five_wallet_lamport_conservation_and_partial_curve_progress():
    full_5w = simulate_five_wallet_genesis_launch(
        project_name="Bossman Sentinel",
        ticker="BSENT",
        total_sol_budget=15.0,
        max_tranches=60,
    )
    assert full_5w["allocation_status"] == "COMPLETE"
    assert full_5w["requested_lamports"] == 15_000_000_000
    assert full_5w["allocated_lamports"] == 15_000_000_000
    assert full_5w["unallocated_lamports"] == 0
    assert sum(t["amount_lamports"] for t in full_5w["tranches"]) == full_5w["allocated_lamports"]
    assert sum(w["requested_lamports"] for w in full_5w["wallets"]) == full_5w["requested_lamports"]
    assert sum(w["allocated_lamports"] for w in full_5w["wallets"]) == full_5w["allocated_lamports"]
    assert full_5w["plan_verification_status"] == "UNVERIFIED_OFFCHAIN_PLAN_ONLY"

    partial_5w = simulate_five_wallet_genesis_launch(
        project_name="Bossman Sentinel",
        ticker="BSENT",
        total_sol_budget=15.0,
        max_tranches=5,
    )
    assert partial_5w["allocation_status"] == "PARTIAL"
    assert partial_5w["tranches_count"] == 5
    assert partial_5w["unallocated_lamports"] > 0
    assert (
        partial_5w["allocated_lamports"] + partial_5w["unallocated_lamports"]
        == partial_5w["requested_lamports"]
    )
    expected_progress = round((partial_5w["allocated_lamports"] / (85.0 * 1_000_000_000)) * 100.0, 2)
    assert partial_5w["bonding_curve_progress_pct"] == expected_progress
    assert partial_5w["bonding_curve_progress_pct"] < full_5w["bonding_curve_progress_pct"]


def test_codex_cli_flag_passthrough_and_honest_offchain_labels(capsys):
    from bossman.trading_learning.cli import EXIT_OK, main

    rc = main([
        "pump_launchpad",
        "--project-name", "Custom Utility Token",
        "--ticker", "CUST",
        "--utility-thesis", "Custom verifiable utility thesis for CLI passthrough test",
        "--sol-budget", "4.5",
        "--daily-volume-usd", "50000",
        "--market-cap-usd", "95000",
        "--bonding-curve-only",
    ])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["token_generator"]["metadata_manifest"]["description"] == (
        "Custom verifiable utility thesis for CLI passthrough test"
    )
    assert payload["five_wallet_genesis"]["requested_lamports"] == 4_500_000_000
    fees = payload["creator_fee_projections"]
    assert fees["status"] == "SIMULATED"
    assert fees["data_source"] == "USER_SCENARIO"
    assert fees["inputs"]["daily_curve_volume_usd"] == 50_000.0
    assert fees["inputs"]["daily_pumpswap_volume_usd"] == 0.0
    assert fees["inputs"]["market_cap_usd"] == 95_000.0
    assert fees["daily_breakdown_usd"]["pumpswap_fee_usd"] == 0.0

    # Verify honest off-chain wording in Twitter drafts
    campaign = payload["twitter_marketing"]
    assert campaign["plan_verification_status"] == "UNVERIFIED_OFFCHAIN_PLAN_ONLY"
    for draft in campaign["drafts"]:
        assert "заблокирована по графику вестинга" not in draft["text"]

    # Verify --no-demo returns N/A when no explicit scenario is given
    rc_nodemo = main(["pump_launchpad", "--no-demo"])
    assert rc_nodemo == EXIT_OK
    payload_nodemo = json.loads(capsys.readouterr().out)
    assert payload_nodemo["trend_analysis"]["status"] == "N/A"
    assert payload_nodemo["creator_fee_projections"]["status"] == "N/A"


def test_codex_fee_sharing_multi_pool_and_negative_controls():
    # 50% creator share (5000 bps)
    shared = calculate_creator_fee_projections(
        daily_curve_volume_usd=20_000.0,
        daily_pumpswap_volume_usd=80_000.0,
        market_cap_usd=90_000.0,
        sol_price_usd=180.0,
        daily_expenses_usd=50.0,
        creator_share_bps=5_000,
    )
    gross = shared["accounting_ledger"]["estimated_gross_usd"]
    share = shared["accounting_ledger"]["estimated_creator_share_usd"]
    assert share == pytest.approx(round(gross * 0.5, 2), abs=0.01)
    assert shared["accounting_ledger"]["estimated_net_model_usd"] == pytest.approx(round(share - 50.0, 2), abs=0.01)

    # Unknown recipient share (None -> "N/A")
    unknown_share = calculate_creator_fee_projections(
        daily_curve_volume_usd=20_000.0,
        daily_pumpswap_volume_usd=80_000.0,
        market_cap_usd=90_000.0,
        sol_price_usd=180.0,
        creator_share_bps=None,
    )
    assert unknown_share["accounting_ledger"]["estimated_creator_share_usd"] == "N/A"
    assert unknown_share["accounting_ledger"]["estimated_net_model_usd"] == "N/A"

    # Multi-pool / tariff schedule change within the same day
    multi = calculate_creator_fee_projections(
        market_cap_usd=90_000.0,
        sol_price_usd=180.0,
        pools=[
            {"label": "bonding_curve_morning", "curve_vol_usd": 30_000.0, "swap_vol_usd": 0.0, "mcap_usd": 45_000.0},
            {"label": "pumpswap_Canonical_afternoon", "curve_vol_usd": 0.0, "swap_vol_usd": 100_000.0, "mcap_usd": 110_000.0},
        ],
    )
    assert len(multi["pools_breakdown"]) == 2
    assert multi["accounting_ledger"]["estimated_gross_usd"] == pytest.approx(
        sum(p["total_usd"] for p in multi["pools_breakdown"]), abs=0.02
    )

    # Negative controls: invalid inputs must raise ValueError
    with pytest.raises(ValueError):
        calculate_creator_fee_projections(daily_curve_volume_usd=-1.0)
    with pytest.raises(ValueError):
        calculate_creator_fee_projections(sol_price_usd=0.0)
    with pytest.raises(ValueError):
        calculate_creator_fee_projections(market_cap_usd=float("nan"))
    with pytest.raises(ValueError):
        calculate_creator_fee_projections(creator_share_bps=12_000)
    with pytest.raises(ValueError):
        simulate_five_wallet_genesis_launch(total_sol_budget=0.0)
    with pytest.raises(ValueError):
        simulate_five_wallet_genesis_launch(total_sol_budget=25.0)
    with pytest.raises(ValueError):
        simulate_five_wallet_genesis_launch(total_sol_budget=3.0, max_tranches=0)


