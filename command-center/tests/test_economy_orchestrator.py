from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.economy_orchestrator import (
    BossmanOpenRouter, BudgetExceeded, EconomyOrchestrator, JevEconomyController, PaidViolation,
    SpendLedger, TrainingRound, ROLE_SPECS, model_policy,
)
from bcc.providers import ChatResult, ProviderError


def result(*, tin=1000, tout=100, cost=None):
    usage = {} if cost is None else {"cost": cost}
    return SimpleNamespace(tokens_in=tin, tokens_out=tout, provider_meta={"usage": usage})


def test_roles_are_bound_to_owner_requested_models_and_free_workers():
    assert ROLE_SPECS["nemotron_evidence"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert ROLE_SPECS["nemotron_strategy"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert ROLE_SPECS["nemotron_adversary"].model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert ROLE_SPECS["ling_coder"].model == "inclusionai/ling-3.0-flash-fin:free"
    assert ROLE_SPECS["glm_finalizer"].model == "z-ai/glm-5.3-flash"
    assert all(ROLE_SPECS[r].free for r in ("nemotron_evidence", "nemotron_strategy",
                                             "nemotron_adversary", "ling_coder"))
    assert not ROLE_SPECS["glm_finalizer"].free


def test_free_worker_non_zero_reported_cost_is_a_hard_failure():
    ledger = SpendLedger()
    with pytest.raises(PaidViolation):
        ledger.record(ROLE_SPECS["nemotron_evidence"], result(cost=0.001))


def test_glm_budget_is_reserved_before_call():
    ledger = SpendLedger(glm_budget_usd=0.000001)
    with pytest.raises(BudgetExceeded):
        ledger.reserve(ROLE_SPECS["glm_finalizer"], [{"role": "user", "content": "x" * 1000}])


def test_glm_actual_cost_cannot_cross_budget():
    ledger = SpendLedger(glm_budget_usd=0.01)
    spec = ROLE_SPECS["glm_finalizer"]
    ledger.record(spec, result(cost=0.005))
    with pytest.raises(BudgetExceeded):
        ledger.record(spec, result(cost=0.006))


class FakeJev:
    def ask(self, state, questions, force=False):
        options = questions["route"]["criteria"]
        choice = next(reversed(options))
        return {"model": "jev-test", "answers": {"route": {
            "choice": choice, "confidence": 0.9,
            "probabilities": {k: (1.0 if k == choice else 0.0) for k in options},
        }}, "usage": {}}


class BrokenJev:
    def ask(self, *a, **kw):
        raise RuntimeError("offline")


def test_jev_can_only_choose_from_bounded_options():
    ctl = JevEconomyController(FakeJev())
    got = ctl.choose(stage="x", summary="y", options={"free": "free", "paid": "paid"}, fallback="free")
    assert got == "paid"


def test_jev_failure_falls_back_without_expanding_scope():
    ctl = JevEconomyController(BrokenJev())
    got = ctl.choose(stage="x", summary="y", options={"free": "free", "paid": "paid"}, fallback="free")
    assert got == "free"
    assert ctl.records[-1]["choice"] == "free"


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.ledger = SimpleNamespace(spent_usd=0.0)

    async def chat(self, role, messages, tools=None, max_tokens=None):
        self.calls.append(role)
        return SimpleNamespace(text=role + "-answer", tokens_in=10, tokens_out=4,
                               finish="stop", provider_meta={"usage": {"cost": 0}})


class FirstJev:
    def __init__(self):
        self.records = []

    def choose(self, *, stage, summary, options, fallback):
        self.records.append({"stage": stage, "choice": fallback})
        return fallback


def test_video_learning_runs_three_independent_nemotron_roles_and_stays_unverified():
    gw = FakeGateway()
    orch = EconomyOrchestrator(gateway=gw, jev=FirstJev())
    item = TrainingRound("https://youtu.be/example", "example",
                         {"frame_sha256": "a" * 64, "cvd": 1, "oi": 2})
    out = asyncio.run(orch.learn_video(item))
    assert out["status"] == "UNVERIFIED"
    assert set(gw.calls) == {"nemotron_evidence", "nemotron_strategy", "nemotron_adversary"}
    assert len(gw.calls) == 3
    assert out["weights_changed"] is False and out["live_trading"] is False


def test_paid_finalizer_is_skipped_without_explicit_allow():
    gw = FakeGateway()
    orch = EconomyOrchestrator(gateway=gw, jev=FirstJev())
    out = asyncio.run(orch.finalize({"status": "NEEDS_REVIEW", "blockers": ["x"]}, allow_paid=False))
    assert out["status"] == "SKIPPED_FREE_PATH"
    assert "glm_finalizer" not in gw.calls


def test_policy_declares_jev_controller_and_no_live_trading():
    p = model_policy()
    assert p["controller"] == "jev"
    assert p["auditor"] == "aster_read_only_external"
    assert p["rules"]["three_independent_nemotron_agents"] is True
    assert p["rules"]["bossman_owns_repairs"] is True
    assert p["rules"]["aster_never_codes"] is True
    assert p["rules"]["live_trading"] is False
    assert p["external_auditor_policy"]["may_write_code"] is False
    assert p["external_auditor_policy"]["may_apply_patch"] is False
    assert p["external_auditor_policy"]["may_commit"] is False
    assert p["external_auditor_policy"]["may_run_tests"] is True



def test_free_worker_retries_transient_rate_limit_without_paid_fallback(monkeypatch):
    class Flaky:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise ProviderError("лимит запросов провайдера (429): попробуйте позже", kind="http")
            return ChatResult(text="ok", tokens_in=10, tokens_out=2,
                              provider_meta={"usage": {"cost": 0}})

    async def no_sleep(_seconds):
        return None

    adapter = Flaky()
    gw = BossmanOpenRouter(key="test")
    monkeypatch.setattr(gw, "_adapter", lambda _spec: adapter)
    monkeypatch.setattr("bcc.economy_orchestrator.asyncio.sleep", no_sleep)
    out = asyncio.run(gw.chat("nemotron_evidence", [{"role": "user", "content": "public evidence"}]))
    assert out.text == "ok"
    assert adapter.calls == 3
    assert len(gw.retry_events) == 2
    assert gw.ledger.spent_usd == 0


# ------------------------------------------------------------------ rc19 audit: ledger overrun

def test_an_overrun_is_booked_and_locks_the_ledger():
    """Regression: the over-budget charge raised BEFORE it was booked, so spent_usd
    stayed below the cap and every following paid call passed `reserve` again —
    5 calls of USD 0.02 against a USD 0.01 budget booked USD 0.00."""
    ledger = SpendLedger(glm_budget_usd=0.01)
    spec = ROLE_SPECS["glm_finalizer"]
    msgs = [{"role": "user", "content": "x" * 100}]
    est = ledger.reserve(spec, msgs)
    with pytest.raises(BudgetExceeded):
        ledger.record(spec, result(cost=0.02), reserved=est)
    assert ledger.spent_usd == pytest.approx(0.02)          # the money the provider took
    assert ledger.rows[-1]["cost_usd"] == pytest.approx(0.02)
    assert ledger.reserved_usd == pytest.approx(0.0)
    with pytest.raises(BudgetExceeded, match="locked"):
        ledger.reserve(spec, msgs)                            # no further paid call


def test_reservations_in_flight_count_against_the_budget():
    spec = ROLE_SPECS["glm_finalizer"]
    msgs = [{"role": "user", "content": "x"}]
    one = SpendLedger(glm_budget_usd=1.0).reserve(spec, msgs)
    ledger = SpendLedger(glm_budget_usd=one * 1.5)
    ledger.reserve(spec, msgs)                                # first concurrent call
    with pytest.raises(BudgetExceeded, match="in flight"):
        ledger.reserve(spec, msgs)                            # second sees the first


def test_reservation_covers_the_requested_max_tokens_and_tool_schemas():
    spec = ROLE_SPECS["glm_finalizer"]
    msgs = [{"role": "user", "content": "x"}]
    base = SpendLedger().reserve(spec, msgs)
    bigger = SpendLedger().reserve(spec, msgs, max_tokens=spec.max_tokens * 4)
    assert bigger > base
    tools = [{"type": "function", "function": {"name": "t", "parameters": {"d": "y" * 5000}}}]
    assert SpendLedger().reserve(spec, msgs, tools=tools) > base
    # Cyrillic is not undercounted as chars/4
    ru = [{"role": "user", "content": "привет " * 1000}]
    assert SpendLedger().reserve(spec, ru) >= 7000 * spec.price_in + spec.max_tokens * spec.price_out


def test_a_free_role_that_charged_is_booked_before_the_violation():
    ledger = SpendLedger()
    with pytest.raises(PaidViolation):
        ledger.record(ROLE_SPECS["nemotron_evidence"], result(cost=0.003))
    assert ledger.spent_usd == pytest.approx(0.003) and ledger.locked


def test_the_gateway_releases_the_reservation_it_booked(monkeypatch):
    class Paid:
        async def chat(self, *args, **kwargs):
            return ChatResult(text="ok", tokens_in=10, tokens_out=2,
                              provider_meta={"usage": {"cost": 0.0001}})

    gw = BossmanOpenRouter(key="test", ledger=SpendLedger(glm_budget_usd=0.25))
    monkeypatch.setattr(gw, "_adapter", lambda _spec: Paid())
    asyncio.run(gw.chat("glm_finalizer", [{"role": "user", "content": "bundle"}]))
    assert gw.ledger.reserved_usd == pytest.approx(0.0)
    assert gw.ledger.spent_usd == pytest.approx(0.0001)
