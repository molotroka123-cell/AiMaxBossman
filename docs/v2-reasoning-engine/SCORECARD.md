# V2 — Model Scorecard

## Purpose

The router selects model profiles based on **observed performance on real tasks**,
not on static benchmarks or marketing claims.

Every production run contributes to the scorecard. No production model
is permanently trusted — trust is earned and maintained by data.

---

## Tracked metrics

For each `(model_profile, task_class, reasoning_level)` combination:

| Metric | Description |
|---|---|
| `request_count` | Total calls made to this profile/class/level |
| `success_rate` | Fraction ending in DONE (not aborted) |
| `verifier_pass_rate` | Fraction where verifier returned `pass` on first attempt |
| `coding_test_pass_rate` | For `coding` tasks: fraction of test suites passing |
| `tool_success_rate` | Fraction of tool calls with no error |
| `schema_valid_rate` | Fraction of outputs passing JSON Schema on first generation |
| `retry_rate` | Fraction of calls requiring at least one retry |
| `escalation_rate` | Fraction escalated to a stronger profile |
| `replan_rate` | Fraction requiring at least one replan |
| `latency_p50_ms` | Median end-to-end latency |
| `latency_p95_ms` | 95th-percentile latency |
| `tokens_per_request` | Average input + output tokens |
| `cost_per_request_usd` | Average cost in USD |
| `safety_violations` | Count of safety/policy blocks |

---

## Routing thresholds

Default floors for a profile to remain as primary for a task class:

| Metric | Minimum |
|---|---:|
| `verifier_pass_rate` | 0.70 |
| `schema_valid_rate` | 0.90 |
| `success_rate` | 0.75 |
| `safety_violations` (rolling 7d) | 0 |
| `request_count` (to be eligible) | 50 |

If a profile falls below any floor → automatically demoted to fallback.
If a profile falls below floor on safety → immediately suspended.

---

## Shadow / canary mode

New model profiles start in `shadow` mode:
- Receives no real user tasks
- Evaluated against a fixed benchmark + holdout suite
- Promoted to `canary` (1–5% of eligible traffic) only after meeting thresholds
- Promoted to `primary` after 50+ canary samples with no regressions
- All promotions logged with a `DecisionRecord` and the scorecard snapshot used

---

## Holdout suite (immutable)

A permanently separate set of benchmark tasks is maintained for each task class.
Holdout tasks must NOT be used as training data for prompts or adapters.

Holdout properties:
- At least 20 tasks per task class
- Cover representative sub-tasks, edge cases, and failure modes
- Versioned — tasks can be added but not modified
- Run offline, never in production

---

## Scorecard update frequency

| Scope | Frequency |
|---|---|
| Real-time metrics (latency, error, cost) | Per request |
| Rate metrics (success/verifier/schema) | Rolling 7-day window |
| Routing threshold evaluation | Every 6 hours |
| Holdout suite evaluation (full) | Weekly + on any router change |
| Router policy update | Requires holdout pass + human review |

---

## Scorecard API

```
GET /api/v2/scorecard                        # All profiles summary
GET /api/v2/scorecard/{profile}              # One profile, all task classes
GET /api/v2/scorecard/{profile}/{task_class} # Full metrics history
GET /api/v2/scorecard/compare/{a}/{b}        # Side-by-side two profiles
GET /api/v2/scorecard/holdout/latest         # Latest holdout run results
```
