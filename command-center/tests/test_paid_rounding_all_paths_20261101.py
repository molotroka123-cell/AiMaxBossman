"""The 01.11 rounding-to-zero class on every OTHER paid / budget path (cloud.ask: test_paid_reservation_complete_20261101).

Rule (bcc.paid_money): a positive estimate, reservation or known actual charge is rounded UP to the micro-unit;
a paid call never reserves 0; what is left of a cap is rounded DOWN; unknown cost stays unknown.
These are Bossman's LOCAL ledgers against the owner's caps, not a limit on any provider's bill.
Synthetic data only; no provider is called.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from bcc import long_run, paid_money
from bcc.autonomy.budget import DailyBudget
from bcc import engine
from bcc.features import spend_meter
from bcc.provider_governance import GovernedAdapter
from bcc.providers import ChatResult, ProviderError

MICRO = 0.000001
OR = "https://openrouter.ai/api/v1"


# ------------------------------------------------------------------------------------------ paid_money itself

@pytest.mark.parametrize("bad", [0, 0.0, -1e-9, None, float("nan"), float("inf"), True, "0.1", [1]])
def test_paid_quote_refuses_zero_and_garbage(bad):
    with pytest.raises(ValueError):
        paid_money.paid_quote(bad)


def test_paid_money_rounding_directions():
    assert paid_money.paid_quote(1e-7) == MICRO
    assert paid_money.ceil_micro(0) == 0.0
    assert paid_money.floor_micro(0.0000019) == MICRO
    assert paid_money.floor_micro(-0.0000001) == -MICRO               # an overdraft stays visible, rounded away from room
    assert paid_money.ceil_micro_or_none(None) is None
    assert paid_money.ceil_micro_or_none(float("nan")) is None
    with pytest.raises(ValueError):
        paid_money.ceil_micro(-1.0)


# ---------------------------------------------------- long_run ledger (GovernedAdapter paid families: GLM/Haiku/...)

@pytest.fixture
def lr_root(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_ALLOW_PAID_CLOUD", raising=False)
    monkeypatch.delenv("BOSSMAN_LOCAL_ONLY", raising=False)
    root = tmp_path / "autonomy"
    monkeypatch.setenv("BOSSMAN_AUTONOMY_ROOT", str(root))
    long_run.write_profile(enable=True, caps={"glm": 0.000005}, root=root)
    return root


def test_long_run_twenty_submicro_reservations_cannot_pass_a_micro_cap(lr_root):
    ledger = long_run.LongRunLedger(lr_root)
    won = 0
    for _ in range(20):
        try:
            ledger.reserve("glm", 0.0000001, model="z-ai/glm-5.3-flash")
            won += 1
        except long_run.LongRunRefused:
            pass
    assert won == 5                                                    # was 20: each row stored round(1e-7, 6) == 0
    reserves = [r for r in ledger._rows() if r.get("kind") == "reserve"]
    assert all(r["eur"] == MICRO for r in reserves)


def test_long_run_threads_cannot_exceed_the_cap(lr_root):
    ledger = long_run.LongRunLedger(lr_root)
    barrier = threading.Barrier(12)

    def one(_):
        barrier.wait()
        try:
            return ledger.reserve("glm", 0.0000001)
        except long_run.LongRunRefused:
            return None

    with ThreadPoolExecutor(12) as pool:
        assert sum(r is not None for r in pool.map(one, range(12))) == 5


@pytest.mark.parametrize("worst", [0, 0.0, -0.1])
def test_long_run_paid_call_never_reserves_zero(lr_root, worst):
    with pytest.raises(long_run.LongRunRefused, match="never reserves 0"):
        long_run.LongRunLedger(lr_root).reserve("glm", worst)


def test_long_run_tiny_actual_is_booked_and_unknown_actual_keeps_the_worst_case(lr_root):
    ledger = long_run.LongRunLedger(lr_root)
    rid = ledger.reserve("glm", 0.000003)
    ledger.commit(rid, 0.00000001)
    assert ledger.spent_today()["glm"] == MICRO                        # not 0
    rid2 = ledger.reserve("glm", 0.000002)
    ledger.commit(rid2, float("nan"))                                  # unknown: no settle row, worst case stays
    assert ledger.spent_today()["glm"] == pytest.approx(MICRO + 0.000002)


class _Inner:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def chat(self, *a, **kw):
        self.calls += 1
        return self.result


def _glm(pin=0.0001, pout=0.0001):
    return {"kind": "cloud", "name": "z-ai/glm-5.3-flash", "alias": "glm", "pricing_known": True,
            "price_in": pin, "price_out": pout}


async def test_governed_adapter_tiny_paid_calls_stop_at_the_cap(lr_root):
    inner = _Inner(ChatResult(text="ok", tokens_in=1, tokens_out=1, provider_meta={}))
    adapter = GovernedAdapter(inner, {"kind": "openai_compat", "base_url": OR, "name": "or"}, _glm())
    ok = refused = 0
    for _ in range(12):
        try:
            await adapter.chat("z-ai/glm-5.3-flash", [{"role": "user", "content": "hi"}], max_tokens=8)
            ok += 1
        except ProviderError as exc:
            assert exc.kind == "budget"
            refused += 1
    assert inner.calls == ok == 5 and refused == 7                  # was 12: every worst case was stored as 0


# ------------------------------------------------------------------------------- engine run cost / spend meter

def test_engine_run_cost_is_never_rounded_to_zero():
    assert engine._run_usd(0.0000002) == MICRO                                # was round(2e-7, 6) == 0.0
    assert engine._run_usd(0.0) == 0.0 and engine._run_usd(None) == 0.0
    assert engine._run_usd(0.6) == 0.6


def test_spend_meter_counts_tiny_spend_and_floors_limits():
    assert spend_meter._usd(0.0000002) == MICRO
    assert spend_meter._usd(0) == 0.0
    assert spend_meter._limit_usd(0.0000015) == MICRO                 # a cap never gains headroom by rounding
    ledger = spend_meter.Ledger()
    for _ in range(20):
        ledger.add(None, "m", "2026-11-01", spend_meter._usd(0.0000001))
    assert ledger.day_usd("2026-11-01") == pytest.approx(20 * MICRO)  # was 0.0


# --------------------------------------------------------------------------------------- autonomy daily budget

def test_autonomy_daily_budget_tiny_charges_reach_the_cap(tmp_path):
    (tmp_path / "budget.json").write_text(json.dumps({"usd_per_day": 0.000005}), encoding="utf-8")
    budget = DailyBudget(tmp_path)
    for _ in range(6):
        budget.charge("g1", usd=0.0000001)
    assert budget.usage().usd == pytest.approx(6 * MICRO)             # was 0.0
    assert "usd/day cap reached" in budget.exhausted()
    budget.charge("g1", usd=float("nan"))
    assert budget.usage().usd == float("inf")                         # non-finite fails closed, not 0


# ------------------------------------------------------------------------- Fable hard-cap ledger (bossman_shared)

def test_fable_ledger_tiny_reservations_and_commits(tmp_path):
    from bossman_shared.fable_budget import BudgetExhausted, DirectApiBudget
    budget = DirectApiBudget(tmp_path / "fable.json", total_usd=0.000003)
    rids = []
    for _ in range(10):
        try:
            rids.append(budget.reserve(0.0000001))
        except BudgetExhausted:
            pass
    assert len(rids) == 3                                             # 3 * 1e-6 = cap
    budget.commit(rids[0], 0.00000001)
    rec = next(r for r in budget._records if r["reservation_id"] == rids[0])
    assert rec["actual_usd"] == MICRO                                  # was round(1e-8, 6) == 0.0
    with pytest.raises(BudgetExhausted, match="positive"):
        budget.reserve(0.0)


def test_fable_tiny_overflow_is_booked(tmp_path):
    from bossman_shared.fable_budget import DirectApiBudget
    from bcc.fable_cap import _book_overflow
    budget = DirectApiBudget(tmp_path / "fable.json", total_usd=1.0)
    _book_overflow(budget, 0.0000002, "req-1")
    assert budget.remaining() == pytest.approx(1.0 - MICRO)            # was: overflow dropped, remaining 1.0


# ------------------------------------------------------------------------------------ Studio OpenRouter reservation

STUDIO_PAID = "openrouter:minimax/hailuo-3-max"            # a paid model in the shipped catalog


async def test_studio_paid_model_never_reserves_zero_even_with_free_only_off(env):
    from bcc.studio.governance import reserve, save_policy
    from bcc.studio.runtime import StudioError
    await save_policy(env.svc, {'enabled': True, 'free_only': False, 'cloud_budget_usd': 1, 'per_job_usd': 1,
                                'prices': {STUDIO_PAID: 0}})
    with pytest.raises(StudioError, match='zero reservation refused'):
        await reserve(env.svc, STUDIO_PAID, 1, 1, [])


async def test_studio_tiny_price_reserves_a_micro_dollar_not_zero(env):
    from bcc.studio.governance import reserve, save_policy
    await save_policy(env.svc, {'enabled': True, 'free_only': False, 'cloud_budget_usd': 1, 'per_job_usd': 1,
                                'prices': {STUDIO_PAID: 0.0000001}})
    got = await reserve(env.svc, STUDIO_PAID, 1, 1, [])
    assert got['upper_bound_usd'] == MICRO
