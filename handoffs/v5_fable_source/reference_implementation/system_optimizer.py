from dataclasses import dataclass

@dataclass(frozen=True)
class CandidateImprovement:
    kind: str
    current_version: str
    candidate_version: str
    hypothesis: str

@dataclass(frozen=True)
class PromotionEvidence:
    baseline_score: float
    candidate_score: float
    intelligence_retention: float
    security_pass: bool
    rollback_available: bool
    sample_count: int

def may_promote(candidate, evidence):
    if candidate.kind in {"policy_kernel","evidence_signer","finalizer","authorization"}:
        return False, "trust-critical change requires stronger owner/review gate"
    if evidence.sample_count < 20: return False, "insufficient samples"
    if evidence.intelligence_retention < 0.98: return False, "intelligence regression"
    if not evidence.security_pass: return False, "security red-team failed"
    if not evidence.rollback_available: return False, "rollback unavailable"
    if evidence.candidate_score <= evidence.baseline_score: return False, "no measured improvement"
    return True, "eligible for controlled canary"
