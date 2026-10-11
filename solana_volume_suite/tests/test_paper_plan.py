"""Paper-plan maths and static negative controls for the H4 removal (2026-10-11)."""
import ast
import dataclasses
import math
import sys
from pathlib import Path

import pytest

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from core.paper_plan import (  # noqa: E402
    MAX_PLAN_SLICES,
    MAX_SLIPPAGE_BPS,
    MigrationThresholdExceededException,
    PaperBuyPlan,
    PlanRejected,
    check_quoted_impact,
    estimate_price_impact_pct,
    max_input_lamports,
    min_output_lamports,
    slice_order,
    validate_curve_safety,
)
from stages.amm_paper_plan import plan_amm_swap  # noqa: E402
from stages.pumpfun_engine import PumpFunPaperPlanner  # noqa: E402

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # ci-secret-scan: allow -- public SPL token mint address


# ---- curve safety --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pct", [94.99, 0.0, 50.0])
def test_buy_allowed_below_cutoff(pct):
    validate_curve_safety(pct, "BUY")


@pytest.mark.parametrize("pct", [95.0, 95.01, 100.0])
def test_buy_blocked_at_or_above_cutoff(pct):
    with pytest.raises(MigrationThresholdExceededException):
        validate_curve_safety(pct, "buy")


def test_sell_not_blocked_by_cutoff_and_nan_rejected():
    validate_curve_safety(99.0, "SELL")
    with pytest.raises(PlanRejected):
        validate_curve_safety(float("nan"), "BUY")


# ---- slicing / impact ----------------------------------------------------------------------------------------------
def test_slices_respect_impact_and_sum_to_request():
    slices = slice_order(1.0, 10.0, max_impact_pct=1.2)
    assert len(slices) >= 4
    assert abs(sum(slices) - 1.0) < 0.01
    assert all(estimate_price_impact_pct(s, 10.0) <= 1.25 for s in slices)


def test_unbounded_or_degenerate_orders_are_rejected():
    for bad in (0, -1, float("inf"), float("nan"), 1e20, True, "1"):
        with pytest.raises(PlanRejected):
            slice_order(bad, 30.0)
    with pytest.raises(PlanRejected):
        slice_order(100.0, 0.5)  # would need far more than MAX_PLAN_SLICES
    assert MAX_PLAN_SLICES == 100


def test_buy_plan_slices_and_curve_moves_between_slices():
    plan = PumpFunPaperPlanner().plan_buy(MINT, 1.0, 20.0, virtual_sol_reserve=10.0)
    assert isinstance(plan, PaperBuyPlan) and len(plan.slices) >= 4
    assert all(s.impact_pct <= 1.25 for s in plan.slices)
    # later slices get fewer tokens per SOL because the paper curve advanced
    per_sol = [s.est_tokens / s.sol for s in plan.slices]
    assert per_sol == sorted(per_sol, reverse=True)
    assert plan.live_executable is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.mint = "x"  # type: ignore[misc]


# ---- slippage cap maths --------------------------------------------------------------------------------------------
def test_slippage_caps_use_integer_lamports():
    assert max_input_lamports(1_000_000_000, 500) == 1_050_000_000
    assert max_input_lamports(1, 100) == 2                  # ceil: never under-caps
    assert min_output_lamports(1_000_000_000, 500) == 950_000_000
    assert min_output_lamports(1, 100) == 0                 # floor: never over-promises
    assert max_input_lamports(10, 0) == 10 and min_output_lamports(10, 0) == 10


@pytest.mark.parametrize("bps", [-1, MAX_SLIPPAGE_BPS + 1, 1.5, True, None])
def test_slippage_out_of_bounds_rejected(bps):
    with pytest.raises(PlanRejected):
        max_input_lamports(1000, bps)
    with pytest.raises(PlanRejected):
        min_output_lamports(1000, bps)


def test_sell_plan_min_out_and_rent_item():
    plan = PumpFunPaperPlanner().plan_sell(MINT, 5_000_000.0, slippage_bps=100)
    expected = int(round(plan.expected_sol * 1e9))
    assert plan.min_sol_lamports == expected * 9900 // 10000
    assert plan.rent_reclaim.estimated_lamports == 2_039_280
    assert plan.live_executable is False


# ---- AMM plan replaces the Jupiter quote check ----------------------------------------------------------------------
def test_amm_plan_over_caller_supplied_reserves_and_impact_gate():
    plan = plan_amm_swap("SOL", MINT, 10**8, 650 * 10**9, 10**15)
    assert plan.est_out > plan.min_out > 0
    assert plan.impact_pct < 1.5 and plan.live_executable is False
    with pytest.raises(PlanRejected):
        plan_amm_swap("SOL", MINT, 100 * 10**9, 650 * 10**9, 10**15)   # ~13% impact
    with pytest.raises(PlanRejected):
        plan_amm_swap("SOL", MINT, 0, 650 * 10**9, 10**15)
    assert check_quoted_impact(1.5) == 1.5
    with pytest.raises(PlanRejected):
        check_quoted_impact(1.51)
    with pytest.raises(PlanRejected):
        check_quoted_impact(math.nan)


# ---- static negative controls over the whole package ---------------------------------------------------------------
def _package_sources():
    for path in sorted(SUITE_ROOT.rglob("*.py")):
        rel = path.relative_to(SUITE_ROOT).as_posix()
        if rel.startswith(("tests/", "scripts/")) or "__pycache__" in rel:
            continue
        yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, None
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                yield node.module or "", a.name


# Key custody only: the vault stores/derives keys and the zero-knowledge wrapper annotates its return type.
KEYPAIR_CUSTODY = {"core/key_vault/vault.py", "core/key_vault/hd_wallet.py", "core/ai_interface.py"}
# Reads that are NOT engine code: LLM chat (ai_orchestrator) and the DexScreener reserve read (liquidity_gate).
HTTPX_ALLOWED = {"core/ai_orchestrator.py", "core/liquidity_gate.py"}
TX_BUILDING = {"solders.transaction", "solders.message", "solders.instruction", "solders.system_program",
               "solders.signature", "solders.hash", "spl.token.instructions", "spl.token.constants"}


def test_keypair_is_only_imported_for_key_custody():
    offenders = [rel for rel, tree in _package_sources()
                 for mod, _ in _imports(tree) if mod == "solders.keypair" and rel not in KEYPAIR_CUSTODY]
    assert offenders == []


def test_no_transaction_building_primitives_in_package():
    offenders = [(rel, mod) for rel, tree in _package_sources() for mod, _ in _imports(tree) if mod in TX_BUILDING]
    assert offenders == []


def test_no_signing_calls_anywhere_in_package():
    bad_attrs = {"sign", "sign_message", "partial_sign", "sign_transaction"}
    offenders = []
    for rel, tree in _package_sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in bad_attrs:
                offenders.append((rel, node.attr))
            if isinstance(node, ast.Name) and node.id in {"VersionedTransaction", "MessageV0", "Transaction"}:
                offenders.append((rel, node.id))
    assert offenders == []


def test_no_httpx_or_network_client_in_stage_and_engine_code():
    net = {"httpx", "requests", "aiohttp", "urllib", "socket", "websockets"}
    offenders = [(rel, mod) for rel, tree in _package_sources() for mod, _ in _imports(tree)
                 if mod.split(".")[0] in net and rel not in HTTPX_ALLOWED and rel != "dashboard/safety_app.py"]
    assert offenders == []
    # explicit list, so a rename cannot silently drop the engine/plan modules from the check
    for rel in ("stages/pumpfun_engine.py", "stages/amm_paper_plan.py", "core/paper_plan.py", "core/jito_client.py",
                "core/treasury_guard.py", "core/price_impact_calculator.py", "core/paper_guard.py"):
        assert (SUITE_ROOT / rel).exists(), rel
    stage_files = sorted(p.name for p in (SUITE_ROOT / "stages").glob("*.py"))
    assert stage_files == ["__init__.py", "amm_paper_plan.py", "pumpfun_engine.py"]
