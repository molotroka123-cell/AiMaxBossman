"""P0 (spending) 31.10: an unresolved `billing_unknown` charge must block paid cloud.ask calls until the owner reconciles it.

Audit finding: `tools_cloud.budget_state` only looked at TODAY's rows, so an unknown charge written at 23:59Z stopped
blocking new paid calls at 00:00Z. Second finding (reproduced here): the budget was checked before the call and spend was
written only after it, so N parallel paid calls all saw the same remaining budget and together overspent it.

Synthetic only: fake catalog, fake transport, tmp data dir. No network, no real provider, no backend.
"""
from __future__ import annotations

import asyncio
import calendar
import json
import os
import subprocess
import sys
import time as _time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features import tools_cloud as tc
from bcc.tools import ToolContext

BUDGET = 2.0                                     # DEFAULT_SESSION_BUDGET_USD
PAID = {"id": "paid-test", "tier_id": "worker:paid-test", "tier": 4, "model": "vendor/paid-test", "label": "Paid Test",
        "provider": "Vendor", "endpoint": "https://api.vendor.example/v1", "paid": True, "ready": True,
        "price_in_1m": 0.0, "price_out_1m": 500.0}          # quote = 1200 answer tokens * 500/1M = 0.6 USD per call
FREE = {**PAID, "id": "free-test", "tier_id": "worker:free-test", "tier": 3, "model": "vendor/free-test", "paid": False,
        "price_in_1m": 0.0, "price_out_1m": 0.0}


class FrozenTime:
    """Stands in for the `time` module inside tools_cloud: wall clock frozen, monotonic real."""

    def __init__(self, iso: str) -> None:
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


def _svc(tmp_path: Path):
    return SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))


def _budget(svc):
    return asyncio.run(tc.budget_state(svc, None))


def _rows(tmp_path: Path) -> list[dict]:
    path = tmp_path / "cloud_ask" / "audit.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def clock(monkeypatch):
    fake = FrozenTime("2026-10-30T23:59:30Z")
    monkeypatch.setattr(tc, "time", fake)
    return fake


# ------------------------------------------------------------------------------------- 1. UTC rollover (the P0 repro)

@pytest.mark.parametrize("unknown_cost", [0.0, None])     # 0.0 = what the handler wrote before this fix; None = now
def test_unknown_charge_still_blocks_after_utc_midnight(tmp_path, clock, unknown_cost):
    svc = _svc(tmp_path)
    asyncio.run(tc.audit(svc, model="m-paid", provider="p", worker="w", cost_usd=unknown_cost, quoted_cost_usd=0.6,
                         status="billing_unknown", error="TimeoutError", chars_out=10, chars_in=0))
    before = _budget(svc)
    assert before["remaining_usd"] == 0 and before.get("billing_unknown") is True
    clock.now += 60                                                   # 2026-10-31T00:00:30Z: a new UTC day
    assert tc._today() == "2026-10-31"
    after = _budget(svc)
    assert after.get("billing_unknown") is True, after
    assert after["remaining_usd"] == 0, after
    clock.now += 7 * 86400                                            # a week later: still unknown, still blocked
    assert _budget(svc)["remaining_usd"] == 0


def test_rollover_unknown_blocks_paid_choice_but_free_model_still_answers(tmp_path, clock):
    svc = _svc(tmp_path)
    asyncio.run(tc.audit(svc, model="m-paid", provider="p", worker="w", cost_usd=None, status="billing_unknown"))
    clock.now += 3600
    remaining = _budget(svc)["remaining_usd"]
    cand, denied = tc.choose([PAID], requested="paid-test", tokens_in=10, remaining_usd=remaining)
    assert cand is None and denied.startswith("CLOUD_ASK_NO_BUDGET")
    cand, denied = tc.choose([PAID, FREE], requested=None, tokens_in=10, remaining_usd=remaining)
    assert cand is FREE and denied is None                            # blocks PAID calls only


def test_legacy_unknown_row_with_zero_cost_is_not_spend_zero_after_rollover(tmp_path, clock):
    """Rows written before this fix carry cost_usd=0.0 on billing_unknown; that zero is not a known cost."""
    svc = _svc(tmp_path)
    path = tmp_path / "cloud_ask" / "audit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"ts": "2026-10-30T12:00:00Z", "status": "billing_unknown", "cost_usd": 0.0}) + "\n",
                    encoding="utf-8")
    clock.now += 86400
    assert _budget(svc)["remaining_usd"] == 0


# ----------------------------------------------------------------------------------- 2. owner reconciliation unblocks

def test_reconciliation_after_rollover_unblocks_and_counts_the_confirmed_amount(tmp_path, clock):
    svc = _svc(tmp_path)
    asyncio.run(tc.audit(svc, model="m-paid", provider="p", worker="w", cost_usd=None, quoted_cost_usd=0.6,
                         status="billing_unknown", error="CancelledError"))
    clock.now += 120                                                  # reconciled on the next UTC day
    [unknown] = tc.unresolved_unknown_spend(tmp_path)
    row = tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=unknown["sha256"], actual_cost_usd=0.45,
                                              owner_id="owner", evidence_note="provider usage page")
    assert row["cost_usd"] == 0.45 and row["status"] == "billing_reconciled"
    state = _budget(svc)
    assert not state.get("billing_unknown")
    assert state["spent_usd"] == pytest.approx(0.45)                  # counted in spend, not dropped as "yesterday"
    assert state["remaining_usd"] == pytest.approx(BUDGET - 0.45)
    assert tc.unresolved_unknown_spend(tmp_path) == []


def test_only_the_reconciled_row_is_cleared(tmp_path, clock):
    svc = _svc(tmp_path)
    for i in range(2):
        asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=None, status="billing_unknown", n=i))
    clock.now += 86400
    first = tc.unresolved_unknown_spend(tmp_path)[0]["sha256"]
    tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=first, actual_cost_usd=0.1, owner_id="owner",
                                        evidence_note="usage")
    assert _budget(svc)["remaining_usd"] == 0                         # the second one still blocks
    assert len(tc.unresolved_unknown_spend(tmp_path)) == 1


def test_quoted_price_and_actual_spend_are_distinct_fields(tmp_path, clock):
    svc = _svc(tmp_path)
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=0.2, quoted_cost_usd=0.6, status="ok"))
    asyncio.run(tc.audit(svc, model="m", provider="p", worker="w", cost_usd=None, quoted_cost_usd=0.6,
                         status="billing_unknown"))
    ok, unknown = _rows(tmp_path)
    assert ok["cost_usd"] == 0.2 and ok["quoted_cost_usd"] == 0.6
    assert unknown["cost_usd"] is None and unknown["quoted_cost_usd"] == 0.6   # unknown is not 0, quote is not spend
    sha = tc.unresolved_unknown_spend(tmp_path)[0]["sha256"]
    tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=sha, actual_cost_usd=0.3, owner_id="owner",
                                        evidence_note="usage")
    assert _budget(svc)["spent_usd"] == pytest.approx(0.5)            # 0.2 actual + 0.3 confirmed; the 0.6 quote is not spend


# --------------------------------------------------------------------------------------- 3. corrupt ledger fails closed

@pytest.mark.parametrize("bad", [
    "{broken",
    json.dumps({"ts": "2026-10-30T10:00:00Z", "status": "ok", "cost_usd": None}),          # actual spend missing on a settled row
    json.dumps({"ts": "2026-10-30T10:00:00Z", "status": "reserved", "cost_usd": None}),    # reservation without a quote
    json.dumps({"ts": "2026-10-30T10:00:00Z", "status": "ok", "cost_usd": 0.1, "quoted_cost_usd": -1}),
])
def test_corrupt_ledger_fails_closed_after_rollover(tmp_path, clock, bad):
    svc = _svc(tmp_path)
    path = tmp_path / "cloud_ask" / "audit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(bad + "\n", encoding="utf-8")
    clock.now += 86400
    state = _budget(svc)
    assert state["remaining_usd"] == 0 and state.get("audit_unavailable") is True


def test_unreadable_ledger_fails_closed_and_reservation_refused(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / "cloud_ask" / "audit.jsonl").mkdir(parents=True)     # a directory where the ledger should be
    assert _budget(svc)["remaining_usd"] == 0
    res, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.1, budget_usd=BUDGET, scope="day")
    assert res is None and denied.startswith("CLOUD_ASK_NO_BUDGET")


# ------------------------------------------------------------------------------------------ 4. parallel paid requests

def _paid_env(monkeypatch, tmp_path, calls: list):
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))

    async def fake_catalog(_svc):
        return [dict(PAID)]

    async def fake_transport(_svc, _route, _messages, *, max_tokens, timeout):
        calls.append(1)
        await asyncio.sleep(0.05)                                     # every request is in flight at the same time
        return SimpleNamespace(text="ok", tokens_in=10, tokens_out=1200)   # actual = 1200 * 500/1M = 0.6 USD

    monkeypatch.setattr(tc, "cloud_catalog", fake_catalog)
    monkeypatch.setattr(tc, "_governance_refusal", lambda c: "")
    monkeypatch.setattr(tc, "stop_reason", lambda svc: "")
    monkeypatch.setattr(tc, "transport", fake_transport)
    monkeypatch.setattr(tc._STATE, "svc", svc)
    return svc


@pytest.mark.parametrize("n", [4, 8, 16])
def test_parallel_paid_requests_cannot_exceed_the_budget(tmp_path, monkeypatch, n):
    calls: list = []
    svc = _paid_env(monkeypatch, tmp_path, calls)

    async def one(i: int):
        args = {"prompt": f"Вопрос номер {i}: что такое идемпотентность?", "model": "paid-test"}
        denied = await tc.bind_route(args)
        if denied:                                                    # refused before approval: also never reaches the provider
            return SimpleNamespace(error=True, content=denied)
        return await tc._t_ask(args, ToolContext(svc=svc, task={"id": None}, run_id=i, agent={}, approval_id=100 + i))

    async def run_all():
        return await asyncio.gather(*(one(i) for i in range(n)))

    results = asyncio.run(run_all())
    ok = [r for r in results if not r.error]
    refused = [r for r in results if r.error]
    spent = sum(float(r.get("cost_usd") or 0) for r in _rows(tmp_path) if r.get("status") in ("ok", "empty", "error"))
    assert len(calls) <= int(BUDGET / 0.6 + 1e-9), (len(calls), [r.content for r in results])
    assert spent <= BUDGET + 1e-9, spent
    assert len(ok) == len(calls) == 3                                 # positive control: the budget is used, not refused wholesale
    assert all("CLOUD_ASK_NO_BUDGET" in r.content for r in refused)
    assert _budget(svc)["remaining_usd"] == pytest.approx(BUDGET - 3 * 0.6)
    assert not [r for r in _rows(tmp_path) if r.get("status") == "reserved" and not tc._settled(_rows(tmp_path), r)]


def test_paid_call_failure_leaves_billing_unknown_and_blocks_next_paid_call(tmp_path, monkeypatch):
    calls: list = []
    svc = _paid_env(monkeypatch, tmp_path, calls)

    async def broken(_svc, _route, _messages, *, max_tokens, timeout):
        calls.append(1)
        raise TimeoutError("provider timed out after sending")

    monkeypatch.setattr(tc, "transport", broken)

    async def ask(i):
        args = {"prompt": f"Вопрос {i}", "model": "paid-test"}
        denied = await tc.bind_route(args)
        if denied:
            return denied
        return await tc._t_ask(args, ToolContext(svc=svc, task={"id": None}, run_id=i, agent={}, approval_id=i))

    first = asyncio.run(ask(1))
    assert first.error and "CLOUD_ASK_FAILED" in first.content
    unknown = [r for r in _rows(tmp_path) if r.get("status") == "billing_unknown"]
    assert len(unknown) == 1 and unknown[0]["cost_usd"] is None and unknown[0]["quoted_cost_usd"] == pytest.approx(0.6)
    second = asyncio.run(ask(2))
    assert isinstance(second, str) and second.startswith("CLOUD_ASK_NO_BUDGET")
    assert len(calls) == 1


def test_stale_open_reservation_is_unknown_spend_and_blocks_across_days(tmp_path, clock):
    """A process that died between reserving and settling: whether the provider billed is unknown."""
    svc = _svc(tmp_path)
    res, denied = tc.reserve_paid_call_sync(tmp_path, quoted_usd=0.6, budget_usd=BUDGET, scope="day")
    assert denied is None and res["status"] == "reserved"
    fresh = _budget(svc)
    assert fresh["remaining_usd"] == pytest.approx(BUDGET - 0.6) and not fresh.get("billing_unknown")  # in flight: quote held
    clock.now += tc.RESERVATION_STALE_S + 1                           # crosses UTC midnight too
    stale = _budget(svc)
    assert stale["remaining_usd"] == 0 and stale.get("billing_unknown") is True
    [unknown] = tc.unresolved_unknown_spend(tmp_path)
    tc.reconcile_unknown_spend_by_owner(tmp_path, target_record_sha256=unknown["sha256"], actual_cost_usd=0.6,
                                        owner_id="owner", evidence_note="usage page")
    after = _budget(svc)
    assert not after.get("billing_unknown") and after["spent_usd"] == pytest.approx(0.6)


_CHILD = r"""
import sys, time, json
from pathlib import Path
from bcc.features import tools_cloud as tc
data, go, n = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
while not go.exists():
    time.sleep(0.002)
won = 0
for _ in range(n):
    res, denied = tc.reserve_paid_call_sync(data, quoted_usd=0.6, budget_usd=2.0, scope="day")
    won += res is not None
print(json.dumps({"won": won}))
"""


def test_two_processes_cannot_jointly_overspend(tmp_path):
    """Cross-process: the reservation is a ledger row written under an OS file lock, so it holds across processes."""
    go = tmp_path / "go"
    cc = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(cc) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, str(tmp_path), str(go), "5"], cwd=str(cc), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    _time.sleep(1.5)                                                  # both children imported and waiting
    go.write_text("1", encoding="utf-8")
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    won = sum(json.loads(o[0].strip().splitlines()[-1])["won"] for o in outs)
    assert won == 3, outs                                             # 3 * 0.6 <= 2.0 < 4 * 0.6
    reserved = [r for r in _rows(tmp_path) if r.get("status") == "reserved"]
    assert len(reserved) == 3 and sum(r["quoted_cost_usd"] for r in reserved) <= 2.0
