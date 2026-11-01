"""Paid reservations never round to zero (owner master prompt 01.11, item 2 «ЗАКРОЙ РАСХОДЫ»).

Codex reproduction 01.11: a paid quote below half a micro-dollar was rounded to 0, so 20 reservations of
$0.0000001 passed a total budget of $0.000001. The rule fixed here, for every paid path: a positive estimate,
reservation or known actual charge is rounded UP to the micro-dollar; a paid call never reserves 0; unknown
price is not free; unknown final cost stays unknown (null) and blocks.

These are Bossman's LOCAL reservations against the owner's caps. They are not a limit on the provider's bill.
Synthetic data only: no provider is called, no owner data is read.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features import tools_cloud as tc
from bcc.tools import ToolContext


class FrozenTime:
    """Stands in for `time` inside tools_cloud: wall clock frozen (UTC), monotonic real."""

    def __init__(self, iso: str) -> None:
        import calendar
        self.now = float(calendar.timegm(_time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))

    def time(self) -> float:
        return self.now

    def gmtime(self, secs: float | None = None):
        return _time.gmtime(self.now if secs is None else secs)

    def strftime(self, fmt, t=None):
        return _time.strftime(fmt, t if t is not None else self.gmtime())

    def monotonic(self) -> float:
        return _time.monotonic()

    def sleep(self, s: float) -> None:
        _time.sleep(s)


MICRO = 0.000001
#: price chosen so one worst-case quote (16+ input tokens, 1200 answer tokens) is ~1.2e-7 USD - below half a micro-dollar
TINY_PAID = {"id": "tiny-paid", "tier_id": "worker:tiny-paid", "tier": 4, "model": "vendor/tiny-paid", "label": "Tiny",
             "provider": "Vendor", "endpoint": "https://api.vendor.example/v1", "paid": True, "ready": True,
             "price_in_1m": 0.0001, "price_out_1m": 0.0001}


def _rows(tmp_path: Path) -> list[dict]:
    path = tmp_path / "cloud_ask" / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _budget(tmp_path: Path) -> dict:
    return asyncio.run(tc.budget_state(SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path)), None))


# ------------------------------------------------------------------------------------- 1. the reproduction (Codex 01.11)

def test_twenty_submicro_reservations_cannot_pass_a_micro_budget(tmp_path):
    won = [tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=MICRO, scope="day")[0] is not None
           for _ in range(20)]
    assert sum(won) == 1, won                                    # was 20 before the fix
    [row] = [r for r in _rows(tmp_path) if r.get("status") == "reserved"]
    assert row["quoted_cost_usd"] == MICRO                       # rounded UP, never written as 0


def test_submicro_estimate_rounds_up_not_to_zero():
    assert tc.estimate_cost(TINY_PAID, 16) == MICRO              # raw ~1.2e-7
    assert tc.estimate_cost(TINY_PAID, 16) > 0


@pytest.mark.parametrize("raw,expected", [
    (0.6, 0.6),                    # exact micro amounts are not inflated
    (0.1 + 0.2, 0.3),              # float noise (0.30000000000000004) is not a new micro-dollar
    (0.0000011, 0.000002),
    (1e-12, MICRO),
    (2.000001, 2.000001),
])
def test_ceil_micro_is_conservative_but_exact_on_micro_amounts(raw, expected):
    assert tc.ceil_micro_usd(raw) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("bad", [0, 0.0, -0.000001, None, float("nan"), float("inf"), True, "0.1"])
def test_paid_reservation_of_zero_or_invalid_quote_is_refused(tmp_path, bad):
    row, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=bad, budget_usd=2.0, scope="day")
    assert row is None and denied.startswith("CLOUD_ASK_NO_BUDGET")
    assert _rows(tmp_path) == []                                 # nothing reserved


def test_positive_control_one_micro_quote_fits_one_micro_budget(tmp_path):
    row, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=MICRO, budget_usd=MICRO, scope="day")
    assert denied is None and row["quoted_cost_usd"] == MICRO


def test_remaining_budget_is_floored_not_rounded_up(tmp_path):
    """budget 1.5 micro: a 2-micro quote must not fit because round(1.5e-6, 6) went UP to 2e-6."""
    row, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.000002, budget_usd=0.0000015, scope="day")
    assert row is None and denied.startswith("CLOUD_ASK_NO_BUDGET")
    row, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=MICRO, budget_usd=0.0000015, scope="day")
    assert denied is None


def test_session_scope_submicro_reservations_are_held(tmp_path):
    won = sum(tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=2.0, scope="session",
                                        session_id="s1", session_remaining_usd=0.000003)[0] is not None
              for _ in range(10))
    assert won == 3


# ---------------------------------------------------------------------- 2. zero price vs unknown price in route choice

def test_unknown_price_is_not_free():
    cand = {**TINY_PAID, "price_in_1m": None, "price_out_1m": None}
    assert tc.estimate_cost(cand, 100) is None
    got, denied = tc.choose([cand], requested="tiny-paid", tokens_in=100, remaining_usd=2.0)
    assert got is None and "цена модели неизвестна" in denied


def test_paid_model_with_zero_price_is_refused_not_reserved_at_zero(monkeypatch):
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    cand = {**TINY_PAID, "price_in_1m": 0.0, "price_out_1m": 0.0}
    got, denied = tc.choose([cand], requested="tiny-paid", tokens_in=100, remaining_usd=2.0)
    assert got is None and denied.startswith("CLOUD_ASK_NO_MODEL") and "нулевая цена" in denied


def test_free_model_with_zero_price_still_routes(monkeypatch):
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    free = {**TINY_PAID, "id": "free", "paid": False, "price_in_1m": 0.0, "price_out_1m": 0.0}
    got, denied = tc.choose([free], requested="free", tokens_in=100, remaining_usd=0.0)
    assert got is free and denied is None
    assert tc.estimate_cost(free, 100) == 0.0                    # free stays exactly 0, it is not reserved


# --------------------------------------------------------------------------------- 3. concurrent tiny paid calls

def _env(monkeypatch, tmp_path, calls: list, *, result=None, budget=0.000005):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))

    async def fake_catalog(_svc):
        return [dict(TINY_PAID)]

    async def fake_transport(_svc, _route, _messages, *, max_tokens, timeout):
        calls.append(1)
        await asyncio.sleep(0.02)
        return result if result is not None else SimpleNamespace(text="ok", tokens_in=20, tokens_out=50)

    monkeypatch.setattr(tc, "DEFAULT_SESSION_BUDGET_USD", budget)
    monkeypatch.setattr(tc, "cloud_catalog", fake_catalog)
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "")
    monkeypatch.setattr(tc, "transport", fake_transport)
    monkeypatch.setattr(tc._STATE, "svc", svc)
    return svc


async def _ask(svc, i: int):
    args = {"prompt": f"Вопрос {i}: что такое идемпотентность?", "model": "tiny-paid"}
    denied = await tc.bind_route(args)
    if denied:
        return SimpleNamespace(error=True, content=denied)
    return await tc._t_ask(args, ToolContext(svc=svc, task={"id": None}, run_id=i, agent={}, approval_id=500 + i))


def test_gathered_tiny_paid_calls_cannot_exceed_a_micro_cap(tmp_path, monkeypatch):
    calls: list = []
    svc = _env(monkeypatch, tmp_path, calls, budget=0.000005)

    async def run_all():
        return await asyncio.gather(*(_ask(svc, i) for i in range(20)))

    results = asyncio.run(run_all())
    reserved = [r for r in _rows(tmp_path) if r.get("status") == "reserved"]
    assert len(calls) == 5, (len(calls), [r.content for r in results if r.error][:3])   # 5 * 1e-6 = cap; was 20
    assert all(r["quoted_cost_usd"] == MICRO for r in reserved)
    settled = [r for r in _rows(tmp_path) if r.get("status") == "ok"]
    assert len(settled) == 5 and all(r["cost_usd"] >= MICRO for r in settled)   # tiny actual charge is not booked as 0
    assert sum(r["cost_usd"] for r in settled) <= 0.000005 + 1e-12
    assert all("CLOUD_ASK_NO_BUDGET" in r.content for r in results if r.error)
    assert _budget(tmp_path)["remaining_usd"] == 0


def test_threads_reserving_tiny_quotes_cannot_exceed_cap(tmp_path):
    barrier = threading.Barrier(16)

    def one(_):
        barrier.wait()
        return tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=0.000004, scope="day")[0]

    with ThreadPoolExecutor(16) as pool:
        won = [r for r in pool.map(one, range(16)) if r is not None]
    assert len(won) == 4
    assert math.isclose(sum(r["quoted_cost_usd"] for r in _rows(tmp_path)), 0.000004, abs_tol=1e-12)


_CHILD = r"""
import sys, time, json
from pathlib import Path
from bcc.features import tools_cloud as tc
data, go = Path(sys.argv[1]), Path(sys.argv[2])
while not go.exists():
    time.sleep(0.002)
won = sum(tc.reserve_paid_call_sync(data, quoted_usd=0.0000001, budget_usd=0.000003, scope="day")[0] is not None
          for _ in range(5))
print(json.dumps({"won": won}))
"""


def test_two_processes_cannot_jointly_overspend_with_tiny_quotes(tmp_path):
    go = tmp_path / "go"
    cc = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(cc) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, str(tmp_path), str(go)], cwd=str(cc), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    _time.sleep(1.5)
    go.write_text("1", encoding="utf-8")
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    assert sum(json.loads(o[0].strip().splitlines()[-1])["won"] for o in outs) == 3, outs


# ------------------------------------------------------------------- 4. UTC day change and unknown final charge

@pytest.fixture
def clock(monkeypatch):
    fake = FrozenTime("2026-10-31T23:59:59Z")
    monkeypatch.setattr(tc, "time", fake)
    return fake


def test_reservation_before_midnight_settled_after_counts_on_the_new_day(tmp_path, clock):
    res, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=0.000002, scope="day")
    assert denied is None and res["quoted_cost_usd"] == MICRO
    clock.now += 2                                                    # 2026-11-01T00:00:01Z
    assert tc._today() == "2026-11-01"
    held = _budget(tmp_path)
    assert held["reserved_usd"] == MICRO and not held.get("billing_unknown")   # still in flight across midnight
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=tc.ceil_micro_usd(0.00000005),
                         quoted_cost_usd=res["quoted_cost_usd"], reservation_id=res["reservation_id"], status="ok"))
    after = _budget(tmp_path)
    assert after["spent_usd"] == MICRO and after["reserved_usd"] == 0   # counted, not 0, on the settle day


def test_unknown_final_charge_from_previous_day_still_blocks(tmp_path, clock):
    res, _ = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=2.0, scope="day")
    clock.now += 2
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=None, quoted_cost_usd=res["quoted_cost_usd"],
                         reservation_id=res["reservation_id"], status="billing_unknown", error="TimeoutError"))
    clock.now += 86400
    row, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.0000001, budget_usd=2.0, scope="day")
    assert row is None and "billing_unknown" in denied
    assert _budget(tmp_path)["remaining_usd"] == 0


def test_owner_reconciliation_of_tiny_amount_is_not_booked_as_zero(tmp_path, clock):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=None, quoted_cost_usd=MICRO,
                         status="billing_unknown"))
    [unknown] = tc.unresolved_unknown_spend(tmp_path)
    row = tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=unknown["sha256"],
                                              actual_cost_usd=0.0000002, owner_id="owner", evidence_note="usage page")
    assert row["cost_usd"] == MICRO
    zero = tc.reconcile_unknown_spend_by_owner                       # an owner-stated 0 stays 0 (not free by rounding)
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=None, quoted_cost_usd=MICRO,
                         status="billing_unknown", n=2))
    [second] = tc.unresolved_unknown_spend(tmp_path)
    assert zero(tmp_path, target_record_sha256=second["sha256"], actual_cost_usd=0.0, owner_id="owner",
                evidence_note="provider refunded")["cost_usd"] == 0.0


# --------------------------------------------------------- 5. provider returns no usage / unknown final cost

def test_paid_answer_without_usage_stays_unknown_and_blocks(tmp_path, monkeypatch):
    calls: list = []
    svc = _env(monkeypatch, tmp_path, calls, result=SimpleNamespace(text="ok"), budget=2.0)   # no tokens, no cost
    first = asyncio.run(_ask(svc, 1))
    assert not first.error and first.data["cost_usd"] is None
    [settle] = [r for r in _rows(tmp_path) if r.get("status") not in ("reserved",)]
    assert settle["status"] == "billing_unknown" and settle["cost_usd"] is None   # never silently 0
    second = asyncio.run(_ask(svc, 2))
    assert second.error and "CLOUD_ASK_NO_BUDGET" in second.content
    assert len(calls) == 1


def test_paid_answer_with_tiny_usage_is_booked_at_least_one_micro(tmp_path, monkeypatch):
    calls: list = []
    svc = _env(monkeypatch, tmp_path, calls, result=SimpleNamespace(text="ok", tokens_in=1, tokens_out=1), budget=2.0)
    res = asyncio.run(_ask(svc, 1))
    assert not res.error and res.data["cost_usd"] == MICRO       # raw 2e-10 USD
    assert tc._actual_cost({"paid": True, "price_in_1m": 0.0001, "price_out_1m": 0.0001},
                           SimpleNamespace(provider_meta={"usage": {"cost": 0.00000001}})) == MICRO
    assert tc._actual_cost({"paid": False}, SimpleNamespace()) == 0.0
