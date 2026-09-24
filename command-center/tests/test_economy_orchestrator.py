from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from bcc.economy_orchestrator import (
    BudgetExceeded, EconomyOrchestrator, JevEconomyController, PaidViolation,
    SpendLedger, TrainingRound, ROLE_SPECS, model_policy,
)


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
    assert p["auditor"] == "aster_external_only"
    assert p["rules"]["three_independent_nemotron_agents"] is True
    assert p["rules"]["live_trading"] is False
