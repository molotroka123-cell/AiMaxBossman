"""AI Company Mode — foundation (feature-flagged, OFF by default).

Одна бизнес-цель → согласованная «рабочая сила» специалистов → DAG задач →
KPI → бюджетные конверты → одобрения → исполнение → свежие доказательства →
верификация → агрегация KPI → перепланирование → итоговый отчёт → learning
records.

Флаг: AI_COMPANY_MODE_ENABLED (env). При OFF `enabled()` == False, а
`CompanyRuntime.run()` отказывает (`CompanyModeDisabled`), если рантайм не
сконструирован явно как синтетический демо-прогон (`synthetic=True`).

Модель полномочий: название роли НЕ даёт полномочий. Finance ≠ право тратить,
Legal ≠ право коммитить, Growth ≠ право публиковать, Admin ≠ доступ к
секретам. Любая задача с флагом spend/publish/credentials/destructive проходит
через внедрённый `approval_gate` (по умолчанию — отказ). У модели нет власти
над этими решениями.

Модуль не дублирует существующие движки: верификация — внедрённый `verifier`
со свежим наблюдением (форма зеркалит bcc.v2.verification.VerificationResult),
learning records — dict в форме schemas/learning_fix_case.schema.json,
валидируемые корневым пакетом `learning`.
"""
from .model import (FLAG, AgentRole, ApprovalDecision, ApprovalRequirement, BudgetEnvelope,
                    CompanyModeDisabled, CompanyObjective, CompanyPlan, CompanyReport,
                    CompanyRunState, CompanyTask, Department, EvidenceRequirement, KPI,
                    ObjectiveConstraint, TaskDependency, TaskOutcome, VerificationOutcome,
                    WorkResult, Workstream, enabled)
from .planner import plan_objective, replan
from .runtime import CompanyRuntime, deny_all_gate

__all__ = [
    "FLAG", "enabled", "CompanyModeDisabled",
    "AgentRole", "ApprovalDecision", "ApprovalRequirement", "BudgetEnvelope",
    "CompanyObjective", "CompanyPlan", "CompanyReport", "CompanyRunState", "CompanyTask",
    "Department", "EvidenceRequirement", "KPI", "ObjectiveConstraint", "TaskDependency",
    "TaskOutcome", "VerificationOutcome", "WorkResult", "Workstream",
    "plan_objective", "replan", "CompanyRuntime", "deny_all_gate",
]
