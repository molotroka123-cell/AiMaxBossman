# AI Company Mode — Foundation

Status: foundation only, feature-flagged OFF. No production path calls it.
Code: `bossman-core/bossman/company/` (`model.py`, `planner.py`, `runtime.py`, `synthetic_seo.py`).
Tests: `bossman-core/tests/test_company_mode.py`.

## Pipeline (spec → code)

```
CompanyObjective ─► plan_objective() ─► CompanyPlan (departments, roles, workstreams, task DAG, budget)
                                             │
                                   CompanyRuntime.run()
                                             │
        kpi_reader() before ─► rounds: replan() ─► per task:
                                  deps DONE? ─► approval_gate (gated kinds) ─► budget.allows()
                                  ─► executor(task) → WorkResult (self-report)
                                  ─► verifier(task, result) → VerificationOutcome (fresh observation)
                                  ─► DONE only if ok AND verifier != FAILED
        kpi_reader() after ─► aggregate_kpis ─► CompanyReport (+ trace, evidence, learning records)
```

## Feature flag

`AI_COMPANY_MODE_ENABLED` (env; `1|true|yes`). `company.enabled()` is False by default.
`CompanyRuntime.run()` raises `CompanyModeDisabled` unless the flag is on **or** the runtime was
constructed with `synthetic=True` (the deterministic in-memory demo). The executor is never called
in the refused case.

## Authority model — role names confer nothing

- `AgentRole` has no permission fields (`can_spend`, `can_publish`, …) and cannot acquire them: it is
  routing/competence metadata only. Finance ≠ spend, Legal ≠ commit, Growth ≠ publish, Admin ≠ secrets.
- Authority lives on the **task** (`CompanyTask.requires_approval: tuple[ApprovalRequirement]`, kinds
  `spend | publish | credentials | destructive`) and in the **injected** `approval_gate`.
- `CompanyPlan.validate()` rejects a task whose `kind` is a gated kind but declares no
  `requires_approval` — such a task cannot exist in a valid plan.
- The default gate is `deny_all_gate`. Anything other than `ApprovalDecision(approved=True)` — a bare
  `True`, `None`, a string, an exception from the gate — is treated as denial (fail closed).
- The gate is consulted **before** budget and **before** the executor; a denied task is `DENIED` and
  the executor is never invoked for it. `replan()` never retries `DENIED` (no "ask again" loops).
- No LLM has a say: the planner is a rule table, the runtime has no model calls, and the gate is
  whatever the host injects (human, policy engine). Approval records carry `approver` (e.g.
  `human:owner`, `policy:default-deny`), never a role name.

## Typed core (`model.py`)

`CompanyObjective`, `ObjectiveConstraint`, `KPI`, `Department`, `AgentRole`, `Workstream`,
`TaskDependency`, `CompanyTask`, `BudgetEnvelope`, `ApprovalRequirement`, `ApprovalDecision`,
`EvidenceRequirement`, `CompanyPlan` (`ordered()` = Kahn topological order, `ValueError` on cycle or
unknown dependency — mirrors `task_compiler.CompiledTask.ordered`), `CompanyRunState`, `WorkResult`,
`VerificationOutcome`, `TaskOutcome`, `CompanyReport`. Plan-level types are frozen; only run state
and outcomes are mutable.

Task states: `PENDING RUNNING DONE FAILED DENIED BUDGET_EXCEEDED SKIPPED`.
Verification statuses: `VERIFIED FAILED UNVERIFIED` (same vocabulary as `bcc.v2.verification`).

## Planner (`planner.py`)

Deterministic decomposition driven by `RULES[domain]` (`seo`, `generic`); unknown domain →
`ValueError` rather than an improvised plan. Same objective → identical plan (tested by equality).
`over_budget(plan)` lists tasks whose estimate cannot fit the envelope on its own.
`replan(plan, state)` is the only scheduling decision: PENDING tasks, FAILED tasks under
`max_attempts`, and SKIPPED tasks whose upstreams became DONE. DENIED and BUDGET_EXCEEDED are final.

## Runtime (`runtime.py`) guarantees (code, not prompt)

1. Dependencies not DONE → `SKIPPED` (blocked), executor not called.
2. Gated task → gate decision recorded; denial → `DENIED`, executor not called.
3. `BudgetEnvelope.allows(estimate, spent, executed)` before execution; failure → `BUDGET_EXCEEDED`,
   executor not called; actual `WorkResult.cost` is accumulated into `spent`.
4. Executor exception, wrong type, or a `WorkResult` for a different task id → `FAILED`.
5. Tasks with `evidence_requirements` are handed to the injected `verifier`; no verifier → `UNVERIFIED`;
   verifier exception or wrong return type → `UNVERIFIED` (never a pass); verifier `FAILED` overrides an
   `ok=True` self-report → task `FAILED`.
6. KPIs are read through `kpi_reader()` before round 1, after each round, and at the end — fresh reads,
   never from executor claims.
7. Report `status`: `FAILED` if any task failed; `VERIFIED` only if every DONE task with evidence
   requirements was verified (and there is at least one); else `UNVERIFIED`. `completion`
   is `COMPLETE`/`PARTIAL`.

## Learning records

One record per task plus one run-level record, shaped exactly like
`schemas/learning_fix_case.schema.json` and validated in tests with the root package `learning`
(`learning.validate`). Mapping:

| Task state | learning_status | outcome |
|---|---|---|
| DONE + verifier VERIFIED | VERIFIED (`verified_by=["verifier:<name>"]`) | FIXED |
| DENIED by gate | PARTIAL | ACCEPTED_RISK_REQUIRES_OWNER |
| BUDGET_EXCEEDED | PARTIAL | BLOCKED_ENV |
| SKIPPED | PARTIAL | PARTIAL |
| FAILED | UNVERIFIED | REJECTED |
| DONE without verification | UNVERIFIED | PARTIAL |

`VERIFIED` is emitted only when the verifier said VERIFIED; `verified_by` names the verifier callable,
which is independent from the recording agent (`company-runtime`) — the `learning` invariant against
self-certification holds. Records are not persisted by the runtime; the host decides where they go
(`LearningStore`), so nothing is written to `data/learning/` by this module.

## Synthetic deterministic E2E (`synthetic_seo.py`)

Objective "Improve a synthetic website's SEO readiness." over an in-memory `SyntheticSite`
(4 pages: title/meta/h1/images-with-alt). Score = passed checks / total checks; the default site scores
62.5 and reaches 100.0 after the three fix tasks. DAG:
`seo-audit → {seo-fix-titles, seo-fix-meta, seo-fix-alt} → seo-rescore → seo-publish (gated: publish)`.
`make_verifier(site)` re-reads the site and ignores the `WorkResult`; `make_executor(site, honest=False)`
claims success without writing and is caught (`FAILED`, report not VERIFIED, KPI unchanged).
`run_demo()` uses a counter clock so the whole report is deterministic (asserted by dict equality).

## Reused vs built

Reused: `learning` root package (schema + `validate`) for learning records; the verification vocabulary
and aggregation rule (FAILED > UNVERIFIED > VERIFIED, empty → UNVERIFIED) of
`command-center/bcc/v2/verification.py` via an injected verifier; `CompiledTask.ordered` algorithm.
Built: the typed model, rule-table planner/replanner, DAG runtime with gates/budget/verification hooks,
report + record emission, and the synthetic SEO harness.
Deliberately not built: gateway, policy, approval UI, memory, cost governor, scheduler — all are
injection points (`approval_gate`, `verifier`, `kpi_reader`, `executor`).

## Limitations / next steps

- No persistence: reports and learning records are returned, not stored; no DB/Redis wiring.
- Replanning is retry-only (no plan mutation, no new tasks); budgets are not renegotiated.
- Only two rule domains (`seo`, `generic`); real domains need rule tables and real verifiers built on
  `bcc.v2.verification` (file/db/browser expectations).
- Execution is sequential; no parallel workstreams or wall-clock budgets yet.
- `bcc.v2.verification` is not imported (it depends on sqlalchemy and lives in another package); the
  runtime mirrors its result shape instead. Wiring a real `verifier` adapter is a follow-up.
