"""The live gate says NO unless every measured criterion is met."""
from __future__ import annotations

from dataclasses import replace

from bossman.trading_learning import safety
from bossman.trading_learning.live_gate import Evidence, evaluate_gate


def _all_good() -> Evidence:
    return Evidence(oos_trades=150, independent_periods=4, ci_low_net_return=0.001,
                    net_return_at_2x_costs=0.0005, ci_low_vs_best_benchmark=0.0004,
                    max_drawdown_fraction=0.12, forward_paper_days=20,
                    slippage_divergence=0.1, kill_switch_drill_passed=True,
                    kill_switch_seconds=2.0, restart_drill_passed=True,
                    duplicate_orders_after_restart=0, api_key_trade_only=True,
                    withdrawal_disabled=True, ip_allowlist=True, leverage_cap_set=True,
                    position_fraction=0.03, total_exposure_fraction=0.1,
                    daily_loss_stop_fraction=0.02, monitoring_alerts_live=True,
                    owner_confirmation_per_action=True, keys_outside_repo_and_env=True)


def test_empty_evidence_is_no_go_and_everything_fails():
    r = evaluate_gate(Evidence())
    assert r.verdict == "NO_GO"
    assert r.passed == [] or all("G" in x for x in r.passed)
    assert len(r.failed) >= 15


def test_current_known_evidence_is_no_go():
    # arena 10.10: 2 trades, new_pair -16.7% / -21.7%, no CI, no drills, no limits
    r = evaluate_gate(Evidence(oos_trades=2, forward_paper_days=1))
    assert r.verdict == "NO_GO"
    assert any(x.startswith("G2") for x in r.failed)


def test_all_good_is_only_pending_owner_confirmation():
    r = evaluate_gate(_all_good())
    assert r.failed == []
    assert r.verdict == "ALL_CRITERIA_MET_PENDING_OWNER_CONFIRMATION"
    # the gate never flips the safety switch
    assert safety.TRADING_EXECUTION == "OFF"
    assert safety.execution_mode() == "OFF"


def test_any_single_failure_blocks():
    good = _all_good()
    for change in ({"ci_low_net_return": 0.0}, {"oos_trades": 99},
                   {"withdrawal_disabled": False}, {"kill_switch_seconds": 9.0},
                   {"duplicate_orders_after_restart": 1}, {"position_fraction": 0.2},
                   {"daily_loss_stop_fraction": None}, {"net_return_at_2x_costs": -0.001}):
        assert evaluate_gate(replace(good, **change)).verdict == "NO_GO", change
