from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
from bossman_v3.contracts import TypedAction
from .reliability import reliability_lcb

class SkillStage(str, Enum):
    EXPERIMENTAL="EXPERIMENTAL"
    SHADOW="SHADOW"
    PRODUCTION="PRODUCTION"

@dataclass(frozen=True)
class TraceStep:
    action: TypedAction
    verified: bool

@dataclass
class SkillCandidate:
    name: str
    input_schema: Mapping[str, str]
    output_schema: Mapping[str, str]
    actions: tuple[TypedAction, ...]
    stage: SkillStage = SkillStage.EXPERIMENTAL
    successes: int = 0
    failures: int = 0
    measured: bool = False

    @property
    def status(self) -> str:
        return "MEASURED" if self.measured else "ESTIMATED"

    def reliability_lcb(self, q_low: float = 0.05) -> float:
        return reliability_lcb(self.successes, self.failures, q_low=q_low)

@dataclass(frozen=True)
class PromotionEvidence:
    trials: int
    verified_success: float
    baseline_verified_success: float
    security_failures: int
    baseline_security_failures: int
    benchmark_present: bool

class SkillFactory:
    RAW_SHELL = {"shell","exec","cmd","powershell","bash","sh","subprocess"}

    def from_verified_trace(self, name: str, trace: Sequence[TraceStep], *, input_schema: Mapping[str,str], output_schema: Mapping[str,str]) -> SkillCandidate:
        if not trace or not all(step.verified for step in trace):
            raise ValueError("skills may only be created from fully verified traces")
        for step in trace:
            if step.action.action_type.lower() in self.RAW_SHELL or step.action.action_type.lower().startswith("shell."):
                raise ValueError("raw shell cannot be learned as a skill")
        return SkillCandidate(name, dict(input_schema), dict(output_schema), tuple(s.action for s in trace))

    @staticmethod
    def typed_compatible(upstream: SkillCandidate, downstream: SkillCandidate) -> bool:
        return all(k in upstream.output_schema and upstream.output_schema[k] == t for k,t in downstream.input_schema.items())

    @staticmethod
    def record(candidate: SkillCandidate, success: bool) -> None:
        candidate.measured = True
        if success: candidate.successes += 1
        else: candidate.failures += 1

    def promote(self, candidate: SkillCandidate, evidence: PromotionEvidence, *, min_shadow_trials: int=20, min_lcb: float=0.80, max_quality_drop: float=0.01) -> SkillStage:
        if candidate.stage == SkillStage.EXPERIMENTAL:
            if evidence.benchmark_present and evidence.trials >= 1:
                candidate.stage = SkillStage.SHADOW
            return candidate.stage
        if candidate.stage == SkillStage.SHADOW:
            if not evidence.benchmark_present or evidence.trials < min_shadow_trials:
                return candidate.stage
            if evidence.security_failures > evidence.baseline_security_failures:
                return candidate.stage
            if evidence.baseline_verified_success - evidence.verified_success > max_quality_drop:
                return candidate.stage
            if candidate.reliability_lcb() < min_lcb:
                return candidate.stage
            candidate.stage = SkillStage.PRODUCTION
        return candidate.stage
