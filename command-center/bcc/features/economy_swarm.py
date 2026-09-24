"""Bossman 1.5 economy swarm: Jev-managed, free-first stage routing.

This feature does not execute model work itself. It is the typed routing gate
between stages of the owner learning/coding workflow:

    3x Nemotron free -> Ling free verify/repair -> GLM paid finalizer -> Aster audit

Jev chooses only from actions that the deterministic policy makes available.
It can make the workflow cheaper/stricter; it can never authorize paid work,
increase a budget, bypass approval, or turn audit into code execution.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..jev import config as jev_config
from ..jev.client import JevClient, JevError, validate_choice
from . import Feature

NEMOTRON_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
LING_MODEL = "inclusionai/ling-3.0-flash-fin:free"
GLM_MODEL = "z-ai/glm-5.3-flash"

FREE_ACTIONS = frozenset({"nemotron_parallel", "ling_verify", "ling_repair", "aster_audit", "stop_blocked"})
PAID_ACTION = "glm_finalize"
ALL_ACTIONS = FREE_ACTIONS | {PAID_ACTION}
DEFAULT_GLM_BUDGET_USD = 0.25
MAX_GLM_CALLS = 1

ACTION_TEXT = {
    "nemotron_parallel": "Run three independent free Nemotron workers in parallel on bounded public evidence.",
    "ling_verify": "Use the free Ling verifier/tester on the combined candidate.",
    "ling_repair": "Give the free Ling coder one bounded repair attempt, then verify again.",
    "glm_finalize": "Use the paid GLM 5.3 Flash finalizer once, only inside the remaining explicit budget.",
    "aster_audit": "Hand evidence to Aster for audit/control only; Aster does not write product code.",
    "stop_blocked": "Stop honestly with a named blocker; do not spend or fabricate a pass.",
}

RULES = (
    "Choose the cheapest action that can make measurable progress. Free workers come before paid finalization. "
    "Aster audits and coordinates but does not write code. A paid action is valid only if it is offered; "
    "never invent another action. Evidence and verifier results outrank model self-reports."
)


class RouteIn(BaseModel):
    stage: str = Field(pattern=r"^(fanout|verify|repair|final|audit)$")
    free_failures: int = Field(default=0, ge=0, le=100)
    ling_attempts: int = Field(default=0, ge=0, le=10)
    ling_verdict: str = Field(default="NOT_RUN", pattern=r"^(NOT_RUN|PASS|FAIL|BLOCKED)$")
    unresolved_blockers: int = Field(default=0, ge=0, le=1000)
    allow_paid: bool = False
    paid_spent_usd: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    paid_budget_usd: float = Field(default=DEFAULT_GLM_BUDGET_USD, ge=0, le=25, allow_inf_nan=False)
    glm_calls: int = Field(default=0, ge=0, le=MAX_GLM_CALLS)
    require_jev: bool = True


@dataclass(frozen=True)
class RouteDecision:
    action: str
    source: str
    allowed: tuple[str, ...]
    remaining_paid_usd: float
    reason: str
    jev_model: str | None = None
    confidence: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "source": self.source,
            "allowed": list(self.allowed),
            "remaining_paid_usd": round(self.remaining_paid_usd, 6),
            "reason": self.reason,
            "jev_model": self.jev_model,
            "confidence": self.confidence,
            "models": {
                "bulk_workers": NEMOTRON_MODEL,
                "free_verifier_coder": LING_MODEL,
                "paid_finalizer": GLM_MODEL,
                "aster": "AUDIT_ONLY",
            },
        }


def remaining_paid(req: RouteIn) -> float:
    return max(0.0, float(req.paid_budget_usd) - float(req.paid_spent_usd))


def paid_eligible(req: RouteIn) -> bool:
    """Paid GLM is absent from Jev's action space until every hard gate is true."""
    return (
        bool(req.allow_paid)
        and remaining_paid(req) > 0
        and req.glm_calls < MAX_GLM_CALLS
        and req.ling_attempts >= 1
        and req.ling_verdict in ("FAIL", "BLOCKED")
        and req.unresolved_blockers > 0
    )


def allowed_actions(req: RouteIn) -> tuple[str, ...]:
    if req.stage == "fanout":
        return ("nemotron_parallel", "stop_blocked")
    if req.stage == "verify":
        return ("ling_verify", "stop_blocked")
    if req.stage == "repair":
        if req.ling_verdict == "PASS" and req.unresolved_blockers == 0:
            return ("aster_audit", "stop_blocked")
        actions = ["ling_repair"]
        if paid_eligible(req):
            actions.append(PAID_ACTION)
        actions.append("stop_blocked")
        return tuple(actions)
    if req.stage == "final":
        if req.unresolved_blockers == 0:
            return ("aster_audit", "stop_blocked")
        return ((PAID_ACTION, "stop_blocked") if paid_eligible(req) else ("stop_blocked",))
    return ("aster_audit", "stop_blocked")


def deterministic_free_first(req: RouteIn, allowed: tuple[str, ...]) -> str:
    """Offline fallback for tests/manual recovery; never invents a paid action."""
    for action in ("nemotron_parallel", "ling_verify", "ling_repair", "aster_audit", "stop_blocked"):
        if action in allowed:
            return action
    return "stop_blocked"


def _questions(allowed: tuple[str, ...]) -> dict:
    return {
        "economy_action": {
            "type": "choice",
            "criteria": {a: ACTION_TEXT[a] for a in allowed},
            "instructions": {"rules": RULES, "decision": "economy_action"},
        }
    }


def jev_choose(req: RouteIn, client: JevClient, *, force: bool = False) -> RouteDecision:
    allowed = allowed_actions(req)
    state = {
        "workflow": "bossman-1.5-economy-learning",
        "stage": req.stage,
        "free_failures": req.free_failures,
        "ling_attempts": req.ling_attempts,
        "ling_verdict": req.ling_verdict,
        "unresolved_blockers": req.unresolved_blockers,
        "paid": {
            "eligible": paid_eligible(req),
            "remaining_usd": round(remaining_paid(req), 6),
            "glm_calls_remaining": MAX_GLM_CALLS - req.glm_calls,
        },
    }
    envelope = client.ask(state, _questions(allowed), force=force)
    answer = validate_choice(envelope["answers"]["economy_action"], {a: ACTION_TEXT[a] for a in allowed})
    action = answer["choice"]
    if action not in allowed or (action == PAID_ACTION and not paid_eligible(req)):
        action = "stop_blocked"  # belt-and-braces fail closed
    return RouteDecision(
        action=action,
        source="jev",
        allowed=allowed,
        remaining_paid_usd=remaining_paid(req),
        reason="Jev chose from a deterministic allow-set; spend authority stayed outside Jev.",
        jev_model=str(envelope.get("model") or ""),
        confidence=float(answer["confidence"]),
    )


def choose(req: RouteIn, client: JevClient | None = None) -> RouteDecision:
    allowed = allowed_actions(req)
    if client is None:
        client = JevClient(jev_config.load())
    try:
        return jev_choose(req, client)
    except JevError as exc:
        if req.require_jev:
            return RouteDecision(
                action="stop_blocked", source="jev_blocked", allowed=allowed,
                remaining_paid_usd=remaining_paid(req),
                reason=f"Jev unavailable ({exc.reason}); required routing does not silently fall back.",
            )
        action = deterministic_free_first(req, allowed)
        return RouteDecision(
            action=action, source="deterministic_fallback", allowed=allowed,
            remaining_paid_usd=remaining_paid(req),
            reason="Jev unavailable; explicit fallback mode stays free-first and cannot select paid GLM.",
        )


router = APIRouter()


@router.get("/economy/status")
async def economy_status(request: Request):
    svc = request.app.state.svc
    state = getattr(svc, "jev", None)
    return {
        "workflow": "bossman-1.5-economy-learning",
        "policy": "FREE_FIRST",
        "models": {
            "nemotron_workers": [NEMOTRON_MODEL] * 3,
            "ling": LING_MODEL,
            "glm_finalizer": GLM_MODEL,
            "aster": "AUDIT_ONLY",
        },
        "hard_limits": {"glm_calls": MAX_GLM_CALLS, "default_glm_budget_usd": DEFAULT_GLM_BUDGET_USD},
        "jev_wired": state is not None,
        "jev": jev_config.load().public(),
    }


@router.post("/economy/route")
async def economy_route(body: RouteIn, request: Request):
    svc = request.app.state.svc
    state = getattr(svc, "jev", None)
    client = state.provider.client if state is not None else JevClient(jev_config.load())
    decision = await asyncio.to_thread(choose, body, client)
    await svc.bus.emit(
        "economy.route",
        stage=body.stage,
        action=decision.action,
        source=decision.source,
        paid_eligible=paid_eligible(body),
        remaining_paid_usd=decision.remaining_paid_usd,
    )
    return decision.public()


FEATURE = Feature(name="economy_swarm", router=router)
