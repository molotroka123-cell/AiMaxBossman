from __future__ import annotations

import pytest

from bcc.features.economy_swarm import (
    GLM_MODEL,
    LING_MODEL,
    NEMOTRON_MODEL,
    RouteIn,
    allowed_actions,
    choose,
    paid_eligible,
)


class FakeJev:
    def __init__(self, action: str, *, confidence: float = 0.93):
        self.action = action
        self.confidence = confidence
        self.questions = None
        self.state = None

    def ask(self, state, questions, *, force=False):
        self.state = state
        self.questions = questions
        options = list(questions["economy_action"]["criteria"])
        probabilities = {x: 0.0 for x in options}
        if self.action in probabilities:
            probabilities[self.action] = 1.0
        else:
            # Intentionally malformed/unoffered choice: strict validation must reject it.
            probabilities = {x: 1.0 / len(options) for x in options}
        return {
            "model": "jev-test",
            "answers": {"economy_action": {
                "choice": self.action,
                "confidence": self.confidence,
                "probabilities": probabilities,
            }},
            "usage": {},
        }


def test_model_contract_is_three_free_workers_then_free_ling_then_paid_glm():
    assert NEMOTRON_MODEL == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert LING_MODEL == "inclusionai/ling-3.0-flash-fin:free"
    assert NEMOTRON_MODEL.endswith(":free") and LING_MODEL.endswith(":free")
    assert GLM_MODEL == "z-ai/glm-5.3-flash"
    assert not GLM_MODEL.endswith(":free")


def test_paid_glm_is_not_even_offered_before_ling_failure_and_budget():
    req = RouteIn(stage="repair", ling_attempts=0, ling_verdict="NOT_RUN",
                  unresolved_blockers=3, allow_paid=True, paid_budget_usd=1.0)
    assert not paid_eligible(req)
    assert "glm_finalize" not in allowed_actions(req)

    req = RouteIn(stage="repair", ling_attempts=1, ling_verdict="FAIL",
                  unresolved_blockers=3, allow_paid=False, paid_budget_usd=1.0)
    assert not paid_eligible(req)
    assert "glm_finalize" not in allowed_actions(req)

    req = RouteIn(stage="repair", ling_attempts=1, ling_verdict="FAIL",
                  unresolved_blockers=3, allow_paid=True, paid_budget_usd=0.0)
    assert not paid_eligible(req)
    assert "glm_finalize" not in allowed_actions(req)


def test_jev_can_choose_glm_only_from_the_hard_allowed_set():
    req = RouteIn(stage="repair", ling_attempts=1, ling_verdict="FAIL",
                  unresolved_blockers=2, allow_paid=True, paid_budget_usd=0.25)
    out = choose(req, FakeJev("glm_finalize"))
    assert out.action == "glm_finalize"
    assert out.source == "jev"
    assert out.remaining_paid_usd == pytest.approx(0.25)

    # Same Jev answer is rejected when the deterministic gate removes GLM.
    blocked = RouteIn(stage="repair", ling_attempts=0, ling_verdict="FAIL",
                      unresolved_blockers=2, allow_paid=True, paid_budget_usd=0.25)
    out2 = choose(blocked, FakeJev("glm_finalize"))
    assert out2.action == "stop_blocked"
    assert out2.source == "jev_blocked"
    assert "glm_finalize" not in out2.allowed


def test_ling_pass_goes_to_aster_without_paid_finalizer():
    req = RouteIn(stage="repair", ling_attempts=1, ling_verdict="PASS",
                  unresolved_blockers=0, allow_paid=True, paid_budget_usd=5)
    assert allowed_actions(req) == ("aster_audit", "stop_blocked")
    out = choose(req, FakeJev("aster_audit"))
    assert out.action == "aster_audit"
    assert "glm_finalize" not in out.allowed


def test_explicit_non_jev_fallback_is_free_first_and_never_paid():
    class Down:
        def ask(self, *_a, **_kw):
            from bcc.jev.client import JevUnavailable
            raise JevUnavailable("down")

    req = RouteIn(stage="repair", ling_attempts=1, ling_verdict="FAIL",
                  unresolved_blockers=1, allow_paid=True, paid_budget_usd=1,
                  require_jev=False)
    out = choose(req, Down())
    assert out.action == "ling_repair"
    assert out.source == "deterministic_fallback"


def test_paid_budget_and_call_count_are_fail_closed():
    exhausted = RouteIn(stage="final", ling_attempts=1, ling_verdict="FAIL",
                        unresolved_blockers=1, allow_paid=True,
                        paid_spent_usd=0.25, paid_budget_usd=0.25)
    assert allowed_actions(exhausted) == ("stop_blocked",)

    already_used = RouteIn(stage="final", ling_attempts=1, ling_verdict="FAIL",
                           unresolved_blockers=1, allow_paid=True,
                           paid_budget_usd=1.0, glm_calls=1)
    assert allowed_actions(already_used) == ("stop_blocked",)
