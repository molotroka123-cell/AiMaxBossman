# Delta audit for Opus — 6 September 2026

## Scope, not a new full execution certificate

Remote main is still `22e2ea304bcc2f4f51b24af41c881f0f9353d827`.
PR33 head inspected: `24d1141ba5afba9f23f290db812a64d640458a75`.
PR32 head observed: `f97b8c18dcf3b25a7655f53cbdeb944150603af2`.
Both remain open drafts at inspection; their changes are not yet main.
This is a read-only code/CI delta plus publication of historical audits.
No new full suite, local-model benchmark or owner UI run executed in this pass.

## Real progress since 22e2ea

PR33 contains the root dependency-boundary correction (shared Ollama normalizer,
Core profiler test moved to its package, regression for root-only dependencies),
the stale stop-status assertion correction, owner interrupt latch and phase
instrumentation. Root PR workflow 34045629760 is now SUCCESS; its API head SHA
is 24d1141. This is a PR-triggered run: actual checkout may be a merge ref, so
it is not automatically standalone exact-head certification. ASTRA, media/Fleet,
Solana, auto-repair and internal benchmark PR workflows were also successful.
Core run34045629748 still had rest-py3.12 and coverage running on final inspection;
Command Center run34045629741 was still running. Intelligence run34045629727
remains failed. Missing measured evidence is not measured cognitive regression.

PR33's 0.46ms Stop p95 is AUTHOR-REPORTED FRAMEWORK-ONLY, not independently
reproduced here and not UI/Windows/local-model latency. Its 2.2/5.3ms-per-step
figures must not be treated as p95 samples just because they occur in a p95
requirements table. Raw per-event samples and timer boundaries are necessary.

PR32 reports journal rollback anchors, stronger automatic workload telemetry,
startup/doctor and owner acceptance tooling. Changed-path inventory confirms
work on journal/compound/telemetry/startup. These are real candidate changes,
not justification to reimplement them in PR33; their full behavior is not
independently reproduced in this fast pass. Journal+anchor simultaneous rollback
remains outside the claimed purely local protection.

## Residuals confirmed by current code inspection

The prior audit reproduced six negative scenarios at 22e2ea. On PR33:

| Finding | Inspection at 24d1141 | Required evidence before closure |
|---|---|---|
| AT-01 false desktop COMPLETE | manager still directly writes COMPLETED on planner COMPLETE | Effectful goal without required file cannot complete; verified positive control can |
| AT-02 paused-state recovery | recover_all still rewrites every nonterminal state to RECOVERING and clears interrupt | PAUSED/USER_CONTROL/WAITING_APPROVAL survive real process restart; only explicit owner resume dispatches |
| AT-03 stale reused screen | _reuse still gates only task generation and age <=0.75s | Popup/focus/tab/layout/value change invalidates at effect boundary, including change during planner/approval |
| AT-04a/b/c spent evidence replay | evidence_ledger blob unchanged: ed9e130e1a7a87c1695ebaab58307fb868a20516 | No replay after eviction/corruption/restart; atomic single-use across processes |
| AT-05 root imports | corrected in candidate; root PR workflow successful | Preserve root-only dependency gate; verify final actual checkout SHA |

These are six historically reproduced cases in four residual root-cause groups,
not six newly executed failures on PR33. New current-code inspection must not be
mislabelled as a new dynamic run. Historical test specifications are in
`history/2026-09-06/reproductions/` and FINDINGS.json.

## Focused closure order

First preserve PR33's useful Stop/phase/import changes and PR32's independent
journal/telemetry/startup work. Reconcile shared manager/package-init files
semantically. Do not blindly merge the old telemetry PR30 over newer PR32 code.
Then close AT-01/02/03/04 with genuine regressions and process tests. Observe/plan
cancellation needs tests for underlying thread work that does not stop when an
async wrapper is cancelled; in-flight effects stay reconciled, not replayed.

Speed is constrained by correctness. Timer freshness alone cannot authorize
reuse. Current policy, scopes, approvals and independent post-state remain in
the measured path. Input feedback, observation, planning, dispatch, verification,
persistence and remote-generation wait need separate measured distributions.
UI feedback p95 <=100ms, Stop acknowledgment <=200ms and verified-cycle p50<=1s /
p95<=2s are engineering targets, not a generic claim about human reaction time.
No paired owner/agent workflow on target hardware means HUMAN_COMPARISON=NOT_RUN.

After safety: close remaining repository-local V4 M0-M11 and V5 N0-N8 gaps per
current plans/acceptance matrix, using canonical components. Missing real-model,
Windows, egress, soak, canary or rollback proof stays NOT_RUN/INSUFFICIENT_EVIDENCE.
No unattended runtime activation, no skipped tests masquerading as success,
no lowering coverage or evidence thresholds. A 24-hour soak requires 24 hours.

## Source locators

- PR33: https://github.com/molotroka123-cell/AiMaxBossman/pull/33
- PR32: https://github.com/molotroka123-cell/AiMaxBossman/pull/32
- Root PR run: https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34045629760
- Core PR run: https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34045629748
- Intelligence PR run: https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34045629727
- PR33 manager: bossman-core/bossman/computer_operator/manager.py
- PR33 ledger: bossman-core/bossman/learning_guard/evidence_ledger.py

VERDICT=IMPLEMENTATION_ADVANCING_RELEASE_NOT_ACCEPTED
V4=NOT_COMPLETE
V5=NOT_COMPLETE
HUMAN_SPEED=NOT_PROVEN
