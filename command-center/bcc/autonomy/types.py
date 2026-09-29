"""Shared autonomy types (docs/autonomy/AUTONOMY_CONTRACT.md).

Line A defines them; Line B codes against them. Field names and order are the
contract: change them only together with the contract document and the JSON
Schemas under ``schemas/autonomy/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

GoalState = Literal["PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING",
                    "USER_APPROVAL", "DEPLOYED", "MONITORING", "COMPLETE", "ROLLED_BACK", "BLOCKED"]
RiskTier = Literal["docs_tests", "prompts_models", "memory_keys_telegram_services", "critical_runtime"]
Requester = Literal["claude", "codex", "jev", "jeff"]
RiskClass = Literal["low", "medium", "high"]
Reviewer = Literal["claude", "codex"]
Verdict = Literal["APPROVE", "REQUEST_CHANGES", "REJECT"]

GOAL_STATES: tuple[str, ...] = get_args(GoalState)
RISK_TIERS: tuple[str, ...] = get_args(RiskTier)
REQUESTERS: tuple[str, ...] = get_args(Requester)
RISK_CLASSES: tuple[str, ...] = get_args(RiskClass)
REVIEWERS: tuple[str, ...] = get_args(Reviewer)
VERDICTS: tuple[str, ...] = get_args(Verdict)


@dataclass(frozen=True)
class Budget:            # a goal without a budget is refused
    max_minutes: int
    max_agent_turns: int
    max_cost_usd: float = 0.0


@dataclass(frozen=True)
class Goal:
    goal_id: str                 # "JEFF-0042"
    problem: str
    desired_result: str
    constraints: tuple[str, ...]
    acceptance_tests: tuple[str, ...]   # each must be an executable check reference or a measurable statement
    budget: Budget
    risk_tier: RiskTier
    target_metric: str           # e.g. "identity_redteam.leaks"
    protected_metrics: tuple[str, ...]


@dataclass(frozen=True)
class HandRequest:               # the structured hand protocol from the plan
    goal_id: str
    requested_by: Requester
    action: str
    target: str
    arguments: dict
    expected_evidence: tuple[str, ...]
    risk_class: RiskClass
    timeout_s: int               # hard wall-clock limit for this one action
    rollback: str                # how to undo it ("none: read-only" for reads)


@dataclass(frozen=True)
class HandResult:
    request_hash: str
    ok: bool
    exit_code: int | None
    started_at: str
    finished_at: str
    artifacts: dict[str, str]    # name -> sha256
    refused_reason: str = ""


@dataclass(frozen=True)
class Review:
    goal_id: str
    reviewer: Reviewer
    sha: str
    diff_sha256: str
    verdict: Verdict
    notes: str


__all__ = ["GoalState", "RiskTier", "Requester", "RiskClass", "Reviewer", "Verdict", "GOAL_STATES", "RISK_TIERS",
           "REQUESTERS", "RISK_CLASSES", "REVIEWERS", "VERDICTS", "Budget", "Goal", "HandRequest", "HandResult",
           "Review"]
