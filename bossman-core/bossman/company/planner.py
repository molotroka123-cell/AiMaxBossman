"""Детерминированный планировщик: цель → отделы/роли/потоки/DAG задач.

Без вызовов моделей: декомпозиция ведётся небольшой таблицей правил по
`objective.domain`. Тот же objective → тот же план (воспроизводимо, тестируемо).

Перепланирование (`replan`) — тоже правило, а не «попросить модель ещё раз»:
повторяются только FAILED-задачи в пределах `max_attempts` и SKIPPED-задачи,
чьи зависимости стали DONE. DENIED (отказ гейта) и BUDGET_EXCEEDED не
пересматриваются — отказ одобрения не обходится повторным запросом, бюджет не
растягивается планировщиком.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import (AgentRole, ApprovalRequirement, BudgetEnvelope, CompanyObjective, CompanyPlan,
                    CompanyRunState, CompanyTask, Department, EvidenceRequirement, TaskDependency,
                    Workstream)


@dataclass(frozen=True, slots=True)
class TaskRule:
    id: str
    title: str
    action: str
    role: str
    workstream: str
    kind: str = "read"
    depends_on: tuple[str, ...] = ()
    approvals: tuple[ApprovalRequirement, ...] = ()
    evidence: tuple[EvidenceRequirement, ...] = ()
    estimated_cost: float = 1.0


@dataclass(frozen=True, slots=True)
class DomainRules:
    departments: tuple[Department, ...]
    roles: tuple[AgentRole, ...]
    workstreams: tuple[Workstream, ...]
    tasks: tuple[TaskRule, ...]


_SITE_NONEMPTY = {"all_nonempty": True}

SEO_RULES = DomainRules(
    departments=(
        Department("growth", "Органический трафик, контент, SEO-готовность"),
        Department("engineering", "Правки шаблонов и разметки сайта"),
        Department("compliance", "Релизы и публикация — только по внешнему одобрению"),
    ),
    roles=(
        AgentRole("seo_analyst", "growth", ("seo.audit", "seo.rescore"),
                  "Аудит и подсчёт SEO-готовности (только чтение)"),
        AgentRole("content_editor", "growth", ("seo.fix_titles", "seo.fix_meta"),
                  "Заголовки и meta-описания"),
        AgentRole("web_engineer", "engineering", ("seo.fix_alt",), "Alt-тексты изображений"),
        AgentRole("release_manager", "compliance", ("seo.publish",),
                  "Готовит публикацию; права публиковать НЕ имеет — решает approval_gate"),
    ),
    workstreams=(
        Workstream("ws-audit", "SEO audit", "growth"),
        Workstream("ws-fix", "On-page fixes", "engineering"),
        Workstream("ws-release", "Release", "compliance"),
    ),
    tasks=(
        TaskRule("seo-audit", "Audit SEO readiness", "seo.audit", "seo_analyst", "ws-audit",
                 evidence=(EvidenceRequirement("site", "score", {"observed": True}),)),
        TaskRule("seo-fix-titles", "Fill missing page titles", "seo.fix_titles", "content_editor",
                 "ws-fix", kind="write", depends_on=("seo-audit",), estimated_cost=2.0,
                 evidence=(EvidenceRequirement("site", "pages.title", _SITE_NONEMPTY),)),
        TaskRule("seo-fix-meta", "Fill missing meta descriptions", "seo.fix_meta", "content_editor",
                 "ws-fix", kind="write", depends_on=("seo-audit",), estimated_cost=2.0,
                 evidence=(EvidenceRequirement("site", "pages.meta", _SITE_NONEMPTY),)),
        TaskRule("seo-fix-alt", "Fill missing image alt text", "seo.fix_alt", "web_engineer",
                 "ws-fix", kind="write", depends_on=("seo-audit",), estimated_cost=2.0,
                 evidence=(EvidenceRequirement("site", "images.alt", _SITE_NONEMPTY),)),
        TaskRule("seo-rescore", "Re-score SEO readiness", "seo.rescore", "seo_analyst", "ws-audit",
                 depends_on=("seo-fix-titles", "seo-fix-meta", "seo-fix-alt"),
                 evidence=(EvidenceRequirement("site", "score", {"min_score": 90.0}),)),
        TaskRule("seo-publish", "Publish fixed pages", "seo.publish", "release_manager",
                 "ws-release", kind="publish", depends_on=("seo-rescore",),
                 approvals=(ApprovalRequirement("publish", "publishing changes the live site"),),
                 evidence=(EvidenceRequirement("site", "published", {"equals": True}),)),
    ),
)

GENERIC_RULES = DomainRules(
    departments=(Department("operations", "Общий поток: разведка → исполнение → ревью"),),
    roles=(
        AgentRole("analyst", "operations", ("generic.discover",)),
        AgentRole("operator", "operations", ("generic.execute",)),
        AgentRole("reviewer", "operations", ("generic.review",)),
    ),
    workstreams=(Workstream("ws-main", "Main", "operations"),),
    tasks=(
        TaskRule("discover", "Discover current state", "generic.discover", "analyst", "ws-main"),
        TaskRule("execute", "Execute the change", "generic.execute", "operator", "ws-main",
                 kind="write", depends_on=("discover",), estimated_cost=2.0),
        TaskRule("review", "Review outcome", "generic.review", "reviewer", "ws-main",
                 depends_on=("execute",)),
    ),
)

RULES: dict[str, DomainRules] = {"seo": SEO_RULES, "generic": GENERIC_RULES}


def plan_objective(objective: CompanyObjective, budget: BudgetEnvelope, *,
                   rules: dict[str, DomainRules] | None = None) -> CompanyPlan:
    """Цель → CompanyPlan по таблице правил. ValueError, если домен неизвестен
    (никакого «придумать план» без правил)."""
    table = rules if rules is not None else RULES
    if objective.domain not in table:
        raise ValueError(f"no planning rules for domain {objective.domain!r}; known: {sorted(table)}")
    dr = table[objective.domain]
    tasks = tuple(
        CompanyTask(
            id=r.id, workstream_id=r.workstream, title=r.title, action=r.action, role=r.role,
            kind=r.kind, dependencies=tuple(TaskDependency(d) for d in r.depends_on),
            requires_approval=r.approvals, evidence_requirements=r.evidence,
            estimated_cost=r.estimated_cost, params={"objective_id": objective.id},
        )
        for r in dr.tasks
    )
    plan = CompanyPlan(objective=objective, budget=budget, departments=dr.departments,
                       roles=dr.roles, workstreams=dr.workstreams, tasks=tasks)
    plan.validate()
    return plan


def over_budget(plan: CompanyPlan) -> list[str]:
    """Задачи, чья оценка уже не помещается в конверт по одиночке (диагностика
    до запуска; рантайм повторяет проверку с учётом фактических трат)."""
    out = []
    for t in plan.tasks:
        ok, _ = plan.budget.allows(t.estimated_cost, 0.0, 0)
        if not ok:
            out.append(t.id)
    return out


def replan(plan: CompanyPlan, state: CompanyRunState) -> tuple[str, ...]:
    """Какие задачи выполнять в следующем раунде (в топологическом порядке).
    Пустой кортеж — план исчерпан."""
    todo: list[str] = []
    for t in plan.ordered():
        o = state.outcome(t.id)
        if o.state == "PENDING":
            todo.append(t.id)
        elif o.state == "FAILED" and o.attempts < t.max_attempts:
            todo.append(t.id)
        elif o.state == "SKIPPED":
            ups = [state.outcome(u).state for u in t.depends_on]
            # Зависимость может стать DONE в этом же раунде (повтор upstream).
            if all(s == "DONE" or u in todo for s, u in zip(ups, t.depends_on)):
                todo.append(t.id)
    return tuple(todo)
