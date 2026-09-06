"""Governed self-improvement for V5: promotion is earned, never self-declared.

Bossman may propose route, context and skill changes and promote them behind a
canary. It may never rewrite the trust kernel, widen a permission, or promote
itself on its own say-so. This module is the decision gate for that.

Invariants
  * TRUST_CRITICAL_KINDS (policy kernel, evidence signer, finalizer,
    authorization, admission, treasury, objective store) are NEVER auto-promoted
    — refused regardless of how good the evidence looks.
  * A candidate that touches the trust kernel or widens permissions is refused
    structurally, before any measurement is even read.
  * The pipeline is data: observe -> hypothesis -> sandbox -> A/B -> red-team ->
    intelligence preservation -> canary -> monitor -> rollback. Stages must be
    completed in order; a skipped stage is a refusal naming that stage.
  * Intelligence retention must clear CORE_INTELLIGENCE_RETENTION (0.98) AND be
    bound to a paired-measurement evidence reference. A bare float a caller
    asserted is not proof and is refused.
  * `may_promote` returns (bool, machine-readable reason) — reasons are stable
    identifiers for dashboards and evidence, not prose.

Non-goals (deliberate)
  * No measuring. This gate consumes a completed measurement; the lane protocol
    (RAW -> SYSTEM -> CONTEXT -> FULL BOSSMAN) and its statistics live in
    tools/intelligence_preservation_gate.py.
  * No promotion. A True here means "eligible for a controlled canary", which is
    still a separate, owner-visible act.
  * No IO, no clock, no randomness.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ordered pipeline. Membership AND position are both contractual.
PIPELINE_STAGES: tuple[str, ...] = (
    "observe",
    "hypothesis",
    "sandbox",
    "ab_test",
    "red_team",
    "intelligence_preservation",
    "canary",
    "monitor",
    "rollback",
)
# Stages that must be complete BEFORE a promotion decision. canary/monitor/
# rollback happen after it, so they are prepared, not completed, at this point.
REQUIRED_STAGES: tuple[str, ...] = PIPELINE_STAGES[:PIPELINE_STAGES.index("canary")]

TRUST_CRITICAL_KINDS = frozenset({
    "policy_kernel", "evidence_signer", "finalizer", "authorization",
    "admission", "treasury", "objective_store",
})
# The only surfaces learning is allowed to move at all.
PROMOTABLE_KINDS = frozenset({"route", "context", "skill"})

CORE_INTELLIGENCE_RETENTION = 0.98
MIN_SAMPLE_COUNT = 20

# A retention figure is only evidence if it points at a stored paired
# measurement: lane protocol id + the sha256 of the report it came from.
RETENTION_EVIDENCE_REF = re.compile(r"\Aintelligence_preservation/paired/[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class CandidateImprovement:
    """A proposed change. Proposal is never authorization."""

    kind: str
    current_version: str
    candidate_version: str
    hypothesis: str
    completed_stages: tuple[str, ...] = ()
    # Declared structurally so the refusal does not depend on reading a diff.
    touches_trust_kernel: bool = False
    widens_permissions: bool = False


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """Completed measurement. `promotion_authorized` can never be set here."""

    baseline_score: float
    candidate_score: float
    intelligence_retention: float
    retention_evidence_ref: str
    security_pass: bool
    rollback_available: bool
    sample_count: int
    promotion_authorized: bool = field(default=False, init=False)


def missing_stage(completed: tuple[str, ...]) -> str | None:
    """First required stage that is absent or out of order, else None."""
    if type(completed) not in (tuple, list):
        return REQUIRED_STAGES[0]
    seen = [stage for stage in completed if stage in PIPELINE_STAGES]
    position = -1
    for stage in REQUIRED_STAGES:
        if stage not in seen:
            return stage
        index = seen.index(stage)
        if index <= position:  # present, but out of pipeline order
            return stage
        position = index
    return None


def _finite(value: object) -> bool:
    return type(value) in (int, float) and type(value) is not bool and value == value and abs(value) != float("inf")


def may_promote(candidate: CandidateImprovement,
                evidence: PromotionEvidence) -> tuple[bool, str]:
    """Decide eligibility for a controlled canary. (ok, machine-readable reason)."""
    if type(candidate) is not CandidateImprovement or type(evidence) is not PromotionEvidence:
        return False, "malformed_promotion_request"
    # Structural refusals first: no amount of evidence can buy these.
    if candidate.kind in TRUST_CRITICAL_KINDS:
        return False, "trust_critical_kind_never_auto_promoted"
    if candidate.kind not in PROMOTABLE_KINDS:
        return False, "unpromotable_kind"
    if candidate.touches_trust_kernel:
        return False, "trust_kernel_rewrite_refused"
    if candidate.widens_permissions:
        return False, "permission_widening_refused"
    if candidate.candidate_version == candidate.current_version:
        return False, "candidate_is_not_a_change"
    absent = missing_stage(candidate.completed_stages)
    if absent is not None:
        return False, f"pipeline_stage_skipped:{absent}"
    # Then measurement, cheapest disqualifier first.
    if type(evidence.sample_count) is not int or evidence.sample_count < MIN_SAMPLE_COUNT:
        return False, "insufficient_samples"
    if type(evidence.retention_evidence_ref) is not str or not RETENTION_EVIDENCE_REF.fullmatch(evidence.retention_evidence_ref):
        return False, "retention_not_bound_to_paired_measurement"
    if not _finite(evidence.intelligence_retention):
        return False, "retention_not_a_measurement"
    if evidence.intelligence_retention < CORE_INTELLIGENCE_RETENTION:
        return False, "intelligence_regression"
    if evidence.security_pass is not True:
        return False, "red_team_failed"
    if evidence.rollback_available is not True:
        return False, "rollback_unavailable"
    if not (_finite(evidence.baseline_score) and _finite(evidence.candidate_score)):
        return False, "scores_not_measured"
    if evidence.candidate_score <= evidence.baseline_score:
        return False, "no_measured_improvement"
    return True, "eligible_for_controlled_canary"
