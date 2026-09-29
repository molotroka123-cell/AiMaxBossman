"""Bossman 1.6 coding limit saver.

This module is a deterministic policy layer. It does not execute model calls.
Its job is to keep ordinary code generation on local/free workers, allow a
bounded GLM-5.3-Flash escalation, and keep Aster strictly audit-only.

Authority/spend stays outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import Feature

GLM_MODEL = "z-ai/glm-5.3-flash"

WriterClass = Literal["LOCAL", "FREE", "GLM53_FLASH"]
Complexity = Literal["low", "medium", "high", "critical"]

# Aster is deliberately not present.
CODE_WRITER_CLASSES: tuple[WriterClass, ...] = ("LOCAL", "FREE", "GLM53_FLASH")
AUDIT_ONLY_ACTORS = frozenset({"ASTER"})

DEFAULT_MAX_GLM_CALLS_PER_TASK = 2
DEFAULT_FREE_ATTEMPTS_BEFORE_GLM = 2
DEFAULT_CONTEXT_UTILIZATION = 0.30
MAX_CONTEXT_UTILIZATION = 0.50


class CandidateIn(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    writer_class: WriterClass
    healthy: bool = True
    capabilities: set[str] = Field(default_factory=set)
    verified_success: float = Field(default=0.50, ge=0.0, le=1.0)
    latency_s: float = Field(default=1.0, gt=0.0, le=3600.0)
    remaining_quota: float | None = Field(default=None, ge=0.0)
    context_window: int = Field(default=32768, ge=1024)


class CodingRouteIn(BaseModel):
    task_id: str = Field(min_length=1, max_length=256)
    task_fingerprint: str = Field(min_length=1, max_length=1024)
    required_capabilities: set[str] = Field(default_factory=lambda: {"coding"})
    privacy_class: str = Field(default="OWNER_LOCAL", max_length=64)
    complexity: Complexity = "medium"
    free_attempts: int = Field(default=0, ge=0, le=20)
    failed_writer_ids: set[str] = Field(default_factory=set)
    glm_calls: int = Field(default=0, ge=0, le=20)
    allow_glm: bool = True
    max_glm_calls: int = Field(default=DEFAULT_MAX_GLM_CALLS_PER_TASK, ge=0, le=10)
    free_attempts_before_glm: int = Field(default=DEFAULT_FREE_ATTEMPTS_BEFORE_GLM, ge=0, le=10)
    requested_context_tokens: int = Field(default=4096, ge=1)
    candidates: list[CandidateIn] = Field(default_factory=list)


@dataclass(frozen=True)
class CodingRouteDecision:
    action: str
    writer_id: str | None
    writer_class: str | None
    model: str | None
    cache_key: str
    context_token_cap: int
    reason: str
    aster_mode: str = "AUDIT_ONLY"

    def public(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "writer_id": self.writer_id,
            "writer_class": self.writer_class,
            "model": self.model,
            "cache_key": self.cache_key,
            "context_token_cap": self.context_token_cap,
            "reason": self.reason,
            "aster_mode": self.aster_mode,
            "code_writer_classes": list(CODE_WRITER_CLASSES),
        }


def request_cache_key(req: CodingRouteIn) -> str:
    """Stable key for reusing an identical verified coding request/result."""
    payload = "|".join([
        req.task_fingerprint,
        ",".join(sorted(req.required_capabilities)),
        req.privacy_class,
        req.complexity,
    ])
    return sha256(payload.encode("utf-8")).hexdigest()


def _is_external(writer_class: WriterClass) -> bool:
    return writer_class in {"FREE", "GLM53_FLASH"}


def _privacy_allows(candidate: CandidateIn, privacy_class: str) -> bool:
    # LOCAL_ONLY/SECRET never leaves the owner machine.
    if privacy_class in {"LOCAL_ONLY", "SECRET"} and _is_external(candidate.writer_class):
        return False
    return True


def _quota_available(candidate: CandidateIn) -> bool:
    return candidate.remaining_quota is None or candidate.remaining_quota > 0


def eligible_candidates(req: CodingRouteIn) -> list[CandidateIn]:
    return [
        c for c in req.candidates
        if c.healthy
        and c.id not in req.failed_writer_ids
        and req.required_capabilities <= c.capabilities
        and _quota_available(c)
        and _privacy_allows(c, req.privacy_class)
    ]


def context_cap(candidate: CandidateIn, requested: int) -> int:
    """Progressive-context cap: do not fill a large window just because it exists."""
    fraction = min(MAX_CONTEXT_UTILIZATION, DEFAULT_CONTEXT_UTILIZATION)
    physical_cap = max(1024, int(candidate.context_window * fraction))
    return min(requested, physical_cap)


def _rank_free_local(candidate: CandidateIn) -> tuple[float, float, int, str]:
    # Better verified success first, then lower latency, then LOCAL before FREE.
    locality = 0 if candidate.writer_class == "LOCAL" else 1
    return (-candidate.verified_success, candidate.latency_s, locality, candidate.id)


def choose_writer(req: CodingRouteIn) -> CodingRouteDecision:
    """Choose a code writer without ever selecting Aster.

    Policy:
      1) healthy eligible LOCAL/FREE worker;
      2) bounded GLM-5.3-Flash escalation after cheap attempts, or immediately
         for critical tasks when explicitly allowed;
      3) honest BLOCKED.

    This function cannot authorize payment and cannot create a new provider.
    """
    key = request_cache_key(req)
    eligible = eligible_candidates(req)

    cheap = sorted(
        [c for c in eligible if c.writer_class in {"LOCAL", "FREE"}],
        key=_rank_free_local,
    )
    glm = sorted(
        [c for c in eligible if c.writer_class == "GLM53_FLASH"],
        key=lambda c: (-c.verified_success, c.latency_s, c.id),
    )

    should_try_cheap = (
        bool(cheap)
        and req.complexity != "critical"
        and req.free_attempts < req.free_attempts_before_glm
    )
    if should_try_cheap:
        c = cheap[0]
        return CodingRouteDecision(
            action="WRITE_CODE",
            writer_id=c.id,
            writer_class=c.writer_class,
            model=None,
            cache_key=key,
            context_token_cap=context_cap(c, req.requested_context_tokens),
            reason="Cheapest verified eligible local/free writer is used before GLM escalation.",
        )

    # If cheap attempts remain possible but threshold was reached, GLM may take
    # the hard blocker. If GLM is unavailable/forbidden, fall back to another
    # cheap attempt instead of silently choosing a premium teacher.
    glm_allowed = req.allow_glm and req.glm_calls < req.max_glm_calls and bool(glm)
    if glm_allowed and (req.free_attempts >= req.free_attempts_before_glm or req.complexity == "critical" or not cheap):
        c = glm[0]
        return CodingRouteDecision(
            action="WRITE_CODE",
            writer_id=c.id,
            writer_class=c.writer_class,
            model=GLM_MODEL,
            cache_key=key,
            context_token_cap=context_cap(c, req.requested_context_tokens),
            reason="Bounded GLM-5.3-Flash escalation after cheap path was exhausted or task is critical.",
        )

    if cheap:
        c = cheap[0]
        return CodingRouteDecision(
            action="WRITE_CODE",
            writer_id=c.id,
            writer_class=c.writer_class,
            model=None,
            cache_key=key,
            context_token_cap=context_cap(c, req.requested_context_tokens),
            reason="GLM is unavailable/not allowed; continue with an eligible cheap writer rather than spend elsewhere.",
        )

    return CodingRouteDecision(
        action="BLOCKED",
        writer_id=None,
        writer_class=None,
        model=None,
        cache_key=key,
        context_token_cap=0,
        reason="No eligible LOCAL/FREE/GLM-5.3-Flash code writer. Aster cannot be promoted into a coder.",
    )


router = APIRouter()


@router.get("/coding-limit/status")
async def coding_limit_status():
    return {
        "policy": "LOCAL_FREE_THEN_GLM53_FLASH",
        "code_writer_classes": list(CODE_WRITER_CLASSES),
        "aster": "AUDIT_ONLY",
        "glm_model": GLM_MODEL,
        "default_max_glm_calls_per_task": DEFAULT_MAX_GLM_CALLS_PER_TASK,
        "default_free_attempts_before_glm": DEFAULT_FREE_ATTEMPTS_BEFORE_GLM,
        "default_context_utilization": DEFAULT_CONTEXT_UTILIZATION,
        "cache": "TASK_FINGERPRINT_REUSE_REQUIRED_BY_CALLER",
    }


@router.post("/coding-limit/route")
async def coding_limit_route(body: CodingRouteIn):
    return choose_writer(body).public()


FEATURE = Feature(name="coding_limit_saver_v16", router=router)
