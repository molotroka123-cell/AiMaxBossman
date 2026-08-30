# V2 — Rollout Plan

## Phases

### Phase 0 — Observe Only

Goal: establish baseline, no routing changes.

- Deploy telemetry layer: emit `DecisionRecord` for every classification
  and routing recommendation (shadow only, no actual routing change).
- Collect 500+ real task traces across all task classes.
- Measure current latency, cost, verifier pass rate, escalation rate.
- Identify top-3 highest-volume task classes for Phase 1.

Exit criteria:
- Telemetry covers ≥ 90% of production runs.
- Baseline scorecard has ≥ 50 samples per top-3 task class.

---

### Phase 1 — Shadow Routing

Goal: validate classifier and router recommendations against production.

- `TaskClassifier` runs in shadow: labels every task but does not change routing.
- Compare shadow recommendation vs. current routing on outcome metrics.
- Calibrate confidence thresholds using holdout.
- Identify misclassifications and calibration gaps.

Exit criteria:
- Shadow classifier agreement with human labels ≥ 85% on sampled tasks.
- No systematic misclassification of `security_review` or `critical` risk tasks.

---

### Phase 2 — Local Fast-Mode on Low-Risk Tasks

Goal: demonstrate cost/latency reduction without quality regression.

- Enable `local-fast` profile for `classification` and `json` task classes only.
- Verifier and schema validation required on every output.
- Escalation to fallback active from day 1.
- Feature flag: `v2.fast_local_routing = true`.

Exit criteria:
- `local-fast` verifier pass rate ≥ 0.85 on these task classes.
- No increase in safety incidents.
- Latency improvement ≥ 20% vs. baseline on same tasks.

---

### Phase 3 — Standard Mode with Verifier-Gated Execution

Goal: add Planner → Executor → Verifier pipeline for `coding` and `tool_use`.

- Enable `standard` reasoning level for `coding` and `tool_use`.
- Execution-grounded verification required (tests + diff for code,
  fresh observation for tools).
- Repair policy active: up to 2 targeted repairs before escalation.
- Feature flag: `v2.standard_verifier = true`.

Exit criteria:
- Coding verifier pass rate ≥ 0.75.
- Repair loop does not exceed budget in ≥ 95% of runs.
- No increase in production incidents vs. Phase 2 baseline.

---

### Phase 4 — Deep Reasoning and Planner/Critic Split

Goal: activate `deep` mode for `planning`, `long_context`, and `security_review`.

- Planner/Critic/Verifier roles active.
- Multi-candidate enabled for `risk_level >= high`.
- Security review always routes to `security-model` + independent second model.
- Human approval gate active for `critical` and `security_review`.
- Feature flag: `v2.deep_reasoning = true`.

Exit criteria:
- Critic identifies at least 1 risk in ≥ 30% of `high` risk tasks (sanity check).
- No `security_review` task completes without human approval.
- Cost increase for `deep` mode ≤ 2x `standard` mode on same tasks.

---

### Phase 5 — Scorecard-Driven Adaptive Routing

Goal: router automatically adjusts based on real scorecard data.

- Enable data-driven threshold evaluation (every 6 hours).
- New model profiles can enter canary via holdout.
- Speculative local-first execution enabled for eligible task classes.
- Conditional debate enabled for `critical` / `security_review` only.
- Feature flag: `v2.adaptive_routing = true`.

Exit criteria:
- Router makes at least one autonomous profile demotion based on scorecard.
- No model promoted to primary without 50+ canary samples.
- End-to-end cost per task ≤ 60% of Phase 0 baseline on equivalent task mix.

---

## Feature flags

| Flag | Default | Enables |
|---|---|---|
| `v2.fast_local_routing` | `false` | Phase 2 |
| `v2.standard_verifier` | `false` | Phase 3 |
| `v2.deep_reasoning` | `false` | Phase 4 |
| `v2.speculative_local` | `false` | Phase 5 |
| `v2.adaptive_routing` | `false` | Phase 5 |
| `v2.conditional_debate` | `false` | Phase 4–5 |
| `v2.scorecard_routing` | `false` | Phase 5 |

All flags are per-environment. Production requires explicit enable
after exit criteria are met and holdout is green.

---

## Definition of done

- Every runtime decision emits a typed `DecisionRecord`.
- No action reaches DONE without fresh `ObservationResult`.
- Router logs why each model was selected and rejected alternatives.
- Invalid JSON cannot reach the Executor.
- Repair loops are bounded and observable.
- A failed Verifier cannot silently produce DONE.
- Model scorecards are queryable by profile and task class.
- Local-first path has a validated escalation safety net.
- All new behavior is behind feature flags.
- Benchmark + holdout suites are green before each phase rollout.
