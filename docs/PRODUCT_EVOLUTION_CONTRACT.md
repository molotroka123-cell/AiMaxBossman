# Bossman — cross-epoch product quality contract

Status: ACCEPTANCE TARGETS, NOT MEASURED RESULTS. Owner scope: 2026-09-06.
Canonical architecture: [Continuity](v4/EPOCH_4_PLAN.md) and
[Steward](v5/EPOCH_5_PLAN.md). This document adds product gates; it does not
waive either epoch's correctness, permission or release requirements.

## The owner experience

One workspace presents the goal, current state, next meaningful action,
verified deliverable and spent/remaining budget. A task becoming unknown or
blocked is visible immediately; a polished progress animation never invents
completion. Advanced inspection is available from the same mission, without
forcing the owner to understand agents, journals or model routing.

V4's primary journey: describe goal → review scope if needed → observe progress
→ inspect verified output → resume or revise. V5 adds: define desired condition
→ preview monitoring and response budget → enable → inspect deviations and
changes → pause/revoke. Every stage has loading, empty, offline, denied,
partial-result, failed and recovered states. No dead control is rendered as
available; unavailable hardware/model capabilities have a concrete explanation.

## Acceptance matrix

Targets are preregistered before measuring. Freeze hardware, OS/browser,
display scale, dataset, model, network profile and workload. Report cold/warm
separately. Use at least 100 interactions for UI/API p95 and five independent
sessions; use the stricter epoch trial protocol for performance gain claims.

| Area | V4 requirement | V5 extension | Evidence |
|---|---|---|---|
| Throughput and cost | V4's 3x verified throughput, <=1/3 cost and avoidable intervention targets | Retain V4 gates; measure monitoring costs and useful intervention separately | Paired frozen workload, failed attempts included, confidence bounds |
| Responsiveness | Local click/key acknowledgement p95 <=100 ms; cached mission view usable <=1 s; local status update visible <=1 s after committed event | Same limits with 100 standing objectives and 10,000 retained timeline entries | Instrumented application/browser timings; model/network delay separately |
| Startup | Warm app ready <=3 s; cold launch <=8 s excluding explicitly displayed first model load | No synchronous scanning of all objectives during startup | Ten launches in each mode on supported target host |
| API | Read-only local mission/status API p95 <=250 ms at 10 concurrent clients, 1,000 missions | Paginated projections; no per-request observer or policy writes | Load profile, request counts, DB query counts |
| Resources | One CPU/GPU memory envelope; bounded queues/caches; V4 soak caps apply | Observer overhead <=5% of one CPU core mean when idle, <=100 MiB incremental RSS for 100 dormant objectives; zero model calls while truly idle | 8-hour measured idle trace; explicit fixture event rate |
| Reliability | Resume without lost verified result or duplicate irreversible effect; unknown state blocks unsafe replay | No oscillating repair loops or starvation of urgent owner missions | Crash matrix, restart/partition fixtures, 24-hour bounded soak |
| Output quality | Independent required-effect verification and per-family success reporting | Fresh condition verification after each intervention; distinguish temporary fix from sustained result | Fixture oracles and held-out owner workflows |
| Context and memory | Provenance, freshness, scopes, invalidation and configured retention | Objective-scoped history with explicit expiry and deletion | Retrieval/invalidation tests and owner-visible controls |
| Models and fleet | Hard privacy/capability/resource checks precede ranking; unknown footprint defers | Priority and deadline scheduling preserves finite resource and money budgets | Exhaustion, fallback and fairness trials |
| UX clarity | Goal, reason for blocker, next action, budget and result in the primary view; evidence one action away | Desired/current state, last observation, next check, change preview and pause in the primary view | Scripted first-use and recovery sessions |
| Accessibility | Every primary journey keyboard operable; visible focus; accessible names; no color-only state; 200% zoom usable; reduced-motion mode | Same requirements for timeline, diff, schedule and objective controls | Automated checks plus manual keyboard/screen-reader acceptance on supported platform |
| Installation | Signed/versioned release manifest where supported; clean install, upgrade, offline missing-model explanation, health diagnosis | Disabled-by-default objective migration; no implicit monitoring enrollment | Two clean-install/upgrade/rollback rehearsals with populated state |
| Extensibility | Versioned adapter contract, OSS license/provenance record, bounded capabilities | Versioned condition templates; recipes remain subject to current grants | Contract conformance and revoked-version tests |

Latency thresholds refer to the UI shell/control plane; external application
completion time is reported separately and still included in mission throughput.
Unsupported platforms cannot satisfy a gate through a mock screenshot.

## Usability qualification

### Human-level computer reaction: qualification, not a label

Owner amendment, 2026-09-06: prioritize implemented computer-control quality.
Separate local reaction from model reasoning: a trusted local interrupt or
focus-change event must invalidate pending input immediately; no model call
is needed to stop. Coalesce superseded observations, preserve only the newest
valid state, and reject expired actions at dispatch. After manual takeover,
explicit resume and a fresh observation are required; old queued clicks cannot
resume. This primitive never grants policy permission or proves task success.

Target local controller event-to-invalidation p95 <=50 ms and p99 <=100 ms on
the supported host under the registered workload. OS hooks and executor checks
must be integrated and measured before this is an end-to-end stop guarantee.
For usable interaction, test at least 20 repeatable browser/editor/file-manager
tasks with matched human and Bossman runs on the same machine: target >=95%
correct task completion and median completed-task time <=1.5x the human median.
Report first-action delay, target errors, corrections, takeover latency and
per-task outcomes. Model delay and external application waits stay in task
time. A small controller microbenchmark cannot establish human-level ability.
Human comparison data and Windows execution remain UNVERIFIED until collected.

Recruit at least five first-time users when available. Each performs six fixed
tasks: start a mission, find its verified output, understand a blocker, approve
only a named change, resume after restart, and stop/revoke. V5 adds configuring
and pausing a standing objective. Require >=90% unassisted completion across
attempts, zero mistaken approvals caused by ambiguous UI, and median time to
find the blocker or stop control <=15 seconds. Record participant count and
failures; developer walkthroughs are not a substitute for first-time-user data.
If participants or target hardware are unavailable, mark this gate UNVERIFIED.

## Implementation and usage economy

Lead owns shared contracts and integration. Existing specialist workstreams
own bounded slices; spawn only when an independent implementation/review task
exists. Reuse recorded audit findings; inspect only new upstream diffs. Run
affected tests after each coherent change and the full required suites at a
release candidate. Do not repeatedly rebuild or run paid model evaluations
while M0 is blocked. Offline deterministic fixtures come first; record actual
model/tool cost separately. Unknown task cost stops admission at the configured
cap rather than assuming free execution.

Implement in order: correctness and evidence → bounded resource use → measured
bottlenecks → coherent navigation and recovery → accessibility/onboarding →
visual polish. Performance work requires a profile, a measurable hypothesis
and an affected regression test. Avoid indiscriminate rewrites, extra services,
new dependency frameworks or dashboards without an owner journey.

## Release and rollback

Every changed product gate needs linked results at the release SHA. A failure
blocks the capability it qualifies and prevents declaring the epoch complete.
Rollout is opt-in with component flags. Rollback restores the prior compatible
UI/strategy while preserving canonical tasks, approvals, evidence and uncertain
effect states. V5 observers are paused before rollback; no pending objective
is silently replayed as a legacy task.
