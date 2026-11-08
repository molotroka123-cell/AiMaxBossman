"""Automated unit tests for learning.bullshot_earnings_ledger (Argon Phase 4 & 5)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from learning.bullshot_earnings_ledger import (
    BullshotEarningsLedger,
    EarningsLedgerEntry,
    build_canonical_bullshot_ledger,
    calculate_creator_fee_breakeven_volume_usd,
    calculate_expected_net_earnings,
    calculate_self_volume_cycle_loss_eth,
)


def test_untradeable_points_have_zero_realizable_usd() -> None:
    """Phase 1 rule: untradeable off-chain GEMS/points must have $0.00 realizable value."""
    calc = calculate_expected_net_earnings(
        method_id="M-GEM-MINING",
        method_name="Bull GEMS Cave Mining (Off-Chain)",
        is_onchain=False,
        is_monetizable_now=False,
        gross_reward_units=338.0,
        reward_unit_name="GEMS",
        unit_market_price_usd=0.05,  # Even if someone assumes a hypothetical price, it must be zeroed
        direct_costs_usd=0.0,
        transaction_fees_usd=0.0,
        execution_costs_usd=0.0,
    )
    assert calc.realizable_reward_usd == 0.0
    assert calc.expected_net_earnings_usd == 0.0


def test_monetizable_onchain_reward_calculates_exact_net_earnings() -> None:
    """When external traders generate 1.0 ETH swap volume, creator earns 0.007 ETH (70% of 1%)."""
    calc = calculate_expected_net_earnings(
        method_id="M-CREATOR-EXT-VOL",
        method_name="Base Creator LP Fee from 1.0 ETH External Swap Volume",
        is_onchain=True,
        is_monetizable_now=True,
        gross_reward_units=0.0070,
        reward_unit_name="ETH",
        unit_market_price_usd=2500.0,
        direct_costs_usd=0.125,       # 0.00005 ETH launch fee
        transaction_fees_usd=0.005,   # gas for launch + FeeCollector.collectAll
        execution_costs_usd=0.0,
    )
    assert calc.realizable_reward_usd == pytest.approx(17.50)
    assert calc.expected_net_earnings_usd == pytest.approx(17.37)


def test_creator_fee_breakeven_external_volume() -> None:
    """Break-even external volume at $2,230/ETH and 0.00006 ETH total launch+gas cost is ~$19.11-$19.43."""
    vol = calculate_creator_fee_breakeven_volume_usd(
        eth_price_usd=2266.83,
        creation_fee_eth=0.00005,
        total_gas_eth=0.00001,
    )
    assert 19.0 <= vol <= 19.6


def test_self_volume_cycle_loss_is_strictly_negative() -> None:
    """Rotating 0.003 ETH across 3 buy+sell cycles must lose 0.60% per cycle to protocol + gas."""
    res = calculate_self_volume_cycle_loss_eth(swap_amount_eth=0.003, cycles=3, gas_per_cycle_eth=0.0000017)
    assert res["total_volume_eth"] == pytest.approx(0.018)
    assert res["gross_pool_fee_eth"] == pytest.approx(0.00018)
    assert res["creator_rebate_eth"] == pytest.approx(0.000126)
    assert res["protocol_leakage_eth"] == pytest.approx(0.000054)
    assert res["net_pnl_eth"] < 0.0
    assert res["net_pnl_eth"] == pytest.approx(-(0.000054 + 0.0000051))


def test_ledger_rejects_false_verified_net_profit_without_tx_hash() -> None:
    with pytest.raises(ValueError, match="requires settled on-chain tx_hashes"):
        EarningsLedgerEntry(
            experiment_id="BAD-01",
            timestamp="2026-11-08T19:00:00Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=[],
            initial_balances={"ETH": 0.01},
            outside_deposits={},
            operations_performed=["Simulated trade"],
            rewards_received={"ETH": 0.001},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=2.50,
            direct_costs_usd=0.0,
            gas_and_protocol_fees_usd=0.01,
            execution_costs_usd=0.0,
            net_realized_pnl_usd=2.49,
            net_realized_pnl_eth=0.00099,
            independent_verification_method="None",
            status="VERIFIED_NET_PROFIT",
        ).validate()


def test_ledger_rejects_verified_net_profit_on_untradeable_gems() -> None:
    with pytest.raises(ValueError, match="untradeable off-chain"):
        EarningsLedgerEntry(
            experiment_id="BAD-02",
            timestamp="2026-11-08T19:00:00Z",
            chain="gemapi.bullshot.io",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=["0x1234"],
            initial_balances={"GEMS": 0.0},
            outside_deposits={},
            operations_performed=["Mine claim"],
            rewards_received={"GEMS": 1000.0},
            is_reward_monetizable_onchain=False,
            cash_out_proceeds_usd=10.0,
            direct_costs_usd=0.0,
            gas_and_protocol_fees_usd=0.0,
            execution_costs_usd=0.0,
            net_realized_pnl_usd=10.0,
            net_realized_pnl_eth=0.004,
            independent_verification_method="API",
            status="VERIFIED_NET_PROFIT",
        ).validate()


def test_ledger_rejects_verified_net_profit_when_net_pnl_is_negative() -> None:
    with pytest.raises(ValueError, match="requires positive net realized P&L"):
        EarningsLedgerEntry(
            experiment_id="BAD-03",
            timestamp="2026-11-08T19:00:00Z",
            chain="Base Mainnet (8453)",
            wallet_or_account="0x5171eDD56A53b9a7436E88050b17E45F2Db59be2",
            tx_hashes=["0xabc"],
            initial_balances={"ETH": 0.01},
            outside_deposits={},
            operations_performed=["Self swap"],
            rewards_received={"ETH": 0.0002},
            is_reward_monetizable_onchain=True,
            cash_out_proceeds_usd=0.50,
            direct_costs_usd=0.0,
            gas_and_protocol_fees_usd=1.50,
            execution_costs_usd=0.0,
            net_realized_pnl_usd=-1.00,
            net_realized_pnl_eth=-0.0004,
            independent_verification_method="RPC",
            status="VERIFIED_NET_PROFIT",
        ).validate()


def test_canonical_bullshot_ledger_verdict_and_serialization(tmp_path: Path) -> None:
    ledger = build_canonical_bullshot_ledger()
    summary = ledger.summary()
    assert summary["total_experiments"] == 6
    assert summary["status_counts"]["VERIFIED_REWARD"] == 5
    assert summary["status_counts"]["FAILED"] == 1
    assert summary["status_counts"]["VERIFIED_NET_PROFIT"] == 0
    assert summary["overall_verdict"] == "NOT_PROVEN"
    assert summary["blocker"] is not None
    assert "0.30%" in summary["blocker"]

    out_file = tmp_path / "ledger.json"
    ledger.save_json(out_file)
    loaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert loaded["summary"]["overall_verdict"] == "NOT_PROVEN"
    assert len(loaded["entries"]) == 6
