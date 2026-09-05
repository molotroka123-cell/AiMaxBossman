"""Bossman Organization Layer (V3) — КТО делает работу.

Стек: владелец → Organization Layer → (Executive OS) → V3 Foundation → V2
action engine → реальные инструменты. Этот пакет управляет отделами, ролями,
командами, делегированием, бюджетами, качеством, обучением и состоянием
организации. Исполнение и доказательства — ниже; организация наследует их
истину и не может её ослабить.

Флаг: BOSSMAN_V3_ENABLED + BOSSMAN_V3_ORGANIZATION (см. feature_flags).
Пакет импортируем и тестируем без флага; флаг решает, включает ли его
хост-процесс.
"""
from .planner import NO_EXECUTABLE_STEPS, DeterministicPlanner, PlannerPort
from .bridges import (ExecutionBridge, HumanReviewPort, MissionReporter, MissionStatus,
                      RecordingHumanReview, RecordingReporter, V3ExecutionBridge,
                      contracts_from_company_plan, step_from_dict, step_to_dict)
from .contracts import (DelegationContract, EscalationPolicy, EvidenceRequirement,
                        TRUSTED_EVIDENCE_SOURCES, consensus)
from .control_plane import OrganizationSnapshot, snapshot
from .events import EventIntake, EventOutcome, Reaction, event_key
from .learning import OrganizationalLearning, OutcomeStats
from .marketplace import CapabilityMarketplace, RouteDecision
from .memory_scope import ExportBlocked, Fact, KnowledgePort, ScopedKnowledge
from .models import (AUDITOR, EXECUTOR, KNOWN_ROLES, LEAD, QA, RESEARCHER, REVIEWER, RISK,
                     TIER_LADDER, VERIFYING_ROLES, AgentProfile, Department, Evidence,
                     MissionState, Resources, ReviewVerdict, RiskTier, TaskState, WorkResult)
from .runtime import ContractReviewer, OrganizationRuntime, ReviewerPort
from .store import OrganizationStore
from .teams import AdaptiveTeamFormer, MissionTeam, required_roles
from .treasury import Envelope, PartitionViolation, ResourceTreasury, TreasuryDecision

__all__ = [
    "AUDITOR", "EXECUTOR", "KNOWN_ROLES", "LEAD", "QA", "RESEARCHER", "REVIEWER", "RISK",
    "TIER_LADDER", "TRUSTED_EVIDENCE_SOURCES", "VERIFYING_ROLES",
    "AdaptiveTeamFormer", "AgentProfile", "CapabilityMarketplace", "ContractReviewer",
    "DelegationContract", "Department", "Envelope", "EscalationPolicy", "EventIntake", "EventOutcome",
    "Evidence", "EvidenceRequirement", "ExecutionBridge", "ExportBlocked", "Fact", "HumanReviewPort",
    "KnowledgePort", "MissionReporter", "MissionState", "MissionStatus", "MissionTeam", "OrganizationRuntime",
    "OrganizationSnapshot", "OrganizationStore", "OrganizationalLearning", "OutcomeStats", "Reaction",
    "RecordingHumanReview", "RecordingReporter", "Resources", "ResourceTreasury", "PartitionViolation", "ReviewVerdict",
    "ReviewerPort", "RiskTier", "RouteDecision", "ScopedKnowledge", "TaskState", "TreasuryDecision",
    "V3ExecutionBridge", "WorkResult", "consensus", "contracts_from_company_plan", "event_key",
    "DeterministicPlanner", "PlannerPort", "NO_EXECUTABLE_STEPS",
    "required_roles", "snapshot", "step_from_dict", "step_to_dict",
]
