"""Focused availability tests; importing these does not start the dashboard."""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from core.ai_orchestrator import VolumeDecision
from core.liquidity_gate import MAX_PAPER_ORDER_SOL, MAX_PAPER_SLICES, LiquidityGate


def _decision(amount_sol):
    return {
        "action": "BUY",
        "wallet_index": 0,
        "amount_sol": amount_sol,
        "delay_sec": 5,
        "mode_tag": "test",
        "reason": "synthetic decision",
    }


@pytest.mark.parametrize("amount_sol", [float("nan"), float("inf"), -float("inf"), 1e20])
def test_volume_decision_rejects_nonfinite_or_oversized_paper_order(amount_sol):
    with pytest.raises(ValidationError):
        VolumeDecision.model_validate(_decision(amount_sol))


@pytest.mark.parametrize("amount_sol", [float("nan"), float("inf"), -1.0, 1e20])
def test_liquidity_gate_blocks_invalid_order_without_slicing(amount_sol):
    gate = LiquidityGate()

    result = gate.validate_and_slice_order(amount_sol)

    assert result["status"] == "BLOCK"
    assert result["execution_allowed"] is False
    assert result["slices_sol"] == []
    assert gate.last_status == "BLOCK"


def test_liquidity_gate_bounds_slice_count_and_keeps_normal_slicing():
    gate = LiquidityGate(max_impact_bps=26)

    result = gate.validate_and_slice_order(MAX_PAPER_ORDER_SOL)

    assert result["status"] == "BLOCK"
    assert result["reason"] == "ORDER_REQUIRES_TOO_MANY_SLICES"
    assert result["slices_sol"] == []
    assert MAX_PAPER_SLICES == 100

    normal = LiquidityGate().validate_and_slice_order(50.0)
    assert normal["status"] == "SLICED_PASS"
    assert len(normal["slices_sol"]) <= MAX_PAPER_SLICES
    assert all(slice_sol > 0 for slice_sol in normal["slices_sol"])
