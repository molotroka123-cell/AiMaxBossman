# Current scorecard (rendered from current-scorecard.json)

| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 9.1/10 | VERIFIED | HIGH | Browser download B4 now persists and verifies real artifacts instead of returning false success; Approval restart matrix is durable and task-bound effects remain exactly-once across hard restart |
| 2 | Security | 9.0/10 | VERIFIED | HIGH | CU-APPROVAL no longer trusts a model-supplied semantic field as proof of approval; CU-VERIFY fails closed on unknown or mistyped expectations; approval and anti-replay boundaries remain enforced |
| 3 | Tooling / OS Integration | 8.6/10 | INTEGRATED | HIGH | Owner hardware live run exercised Computer Use on Windows with 12/12 checks including focus, Cyrillic typing, STOP/resume and coordinate fallback; Local MAIN/FAST models, Chromium, FFmpeg and browser download were exercised on the Ryzen AI Max+ 395 target system |
| 4 | Organization Layer | 7.6/10 | INTEGRATED | MEDIUM | Mission/task orchestration and durable task state remain integrated; Planner/worker/verifier owner scenario still needs the final installed 1.0-RC re-run |
| 5 | Fleet & Resources | 7.2/10 | INTEGRATED | MEDIUM | Lease/fence/resource controls remain in place and owner repair used explicit desktop/backend/GPU leases; Media workers are bounded child processes rather than permanently resident generation daemons |
| 6 | Memory / Context | 7.2/10 | INTEGRATED | MEDIUM | Controlled audit disproved a general memory-loss P1; unique memory writes and restart continuity passed; Coaching/frontier learning design is documented, but holdout transfer is not yet measured |
| 7 | Testing / CI | 8.6/10 | VERIFIED | MEDIUM | Repair pass added targeted regressions for B4, AP-ALL, TEL-001, Computer Use safety, media hashing/cancel and UX settle; Full-suite triage found no confirmed product regression after harness/environment fixes; current exact-SHA mandatory CI is still pending |
| 8 | Observability / CEO Control | 7.9/10 | INTEGRATED | HIGH | Flight Recorder and installed-product telemetry were live on owner hardware; TEL-001 now reports model generation throughput from native/upstream timing rather than short-prompt wall time |
| 9 | Treasury / Cost | 6.9/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain fail-closed and unknown pricing is not treated as free; Frontier-audit/training and media cost-per-verified-result is not yet measured |
| 10 | Mission UX / Command Center | 7.8/10 | INTEGRATED | MEDIUM | Computer Use is wired into the owner product and real Windows interaction has owner evidence; F-17 page-settle race was fixed with a product-owned rendered-page signal |

- **Current bottleneck:** The repair pass is materially ahead of the 2026-09-07 scorecard, but the final repaired exact-SHA Windows artifact still needs mandatory CI, clean-install owner re-run, Video Studio product-path acceptance, coaching/holdout and an independent red-team.
- **Next highest-value fix:** Finish mandatory exact-SHA CI, build one clean Windows 1.0-RC artifact, then run the full owner acceptance and independent red-team on those exact bytes.
- **Last evidence SHA:** `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · **Current HEAD SHA:** `1b7cf6d924fd` · **Evidence freshness:** PARTIALLY_STALE
- **Last scorecard update:** 2026-09-22
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 8.0/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._

## Execution Truth — 9.1 · VERIFIED

- evidence: Browser download B4 now persists and verifies real artifacts instead of returning false success
- evidence: Approval restart matrix is durable and task-bound effects remain exactly-once across hard restart
- blocker: Final repaired Windows artifact has not yet completed the full owner re-run
- tests: bossman-core/tests/test_operator_at01_at03_regression.py
- tests: command-center/tests/test_golden_missions.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Security — 9.0 · VERIFIED

- evidence: CU-APPROVAL no longer trusts a model-supplied semantic field as proof of approval
- evidence: CU-VERIFY fails closed on unknown or mistyped expectations; approval and anti-replay boundaries remain enforced
- blocker: Independent post-repair red-team on the final installed artifact is still pending
- tests: tests/test_ci_secret_scan.py
- tests: command-center/tests/test_policy_algebra.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Tooling / OS Integration — 8.6 · INTEGRATED

- evidence: Owner hardware live run exercised Computer Use on Windows with 12/12 checks including focus, Cyrillic typing, STOP/resume and coordinate fallback
- evidence: Local MAIN/FAST models, Chromium, FFmpeg and browser download were exercised on the Ryzen AI Max+ 395 target system
- blocker: Current repaired exact-SHA Windows package still needs clean-install owner acceptance
- tests: command-center/tests/test_apps_control.py
- tests: command-center/tests/test_testing_period.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Organization Layer — 7.6 · INTEGRATED

- evidence: Mission/task orchestration and durable task state remain integrated
- evidence: Planner/worker/verifier owner scenario still needs the final installed 1.0-RC re-run
- blocker: Final installed multi-agent owner scenario is pending
- tests: command-center/tests/test_feat_missions.py
- tests: bossman-core/tests/test_v3_organization_e2e.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Fleet & Resources — 7.2 · INTEGRATED

- evidence: Lease/fence/resource controls remain in place and owner repair used explicit desktop/backend/GPU leases
- evidence: Media workers are bounded child processes rather than permanently resident generation daemons
- blocker: Final owner restart/resource/rollback acceptance remains pending
- tests: bossman-core/tests/test_v3_fleet_e2e.py
- tests: command-center/tests/test_fence_fl01.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Memory / Context — 7.2 · INTEGRATED

- evidence: Controlled audit disproved a general memory-loss P1; unique memory writes and restart continuity passed
- evidence: Coaching/frontier learning design is documented, but holdout transfer is not yet measured
- blocker: Coaching holdout transfer and long-session re-test are pending
- tests: bossman-core/tests/test_v3_memory_kernel.py
- tests: tests/test_context_slice.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Testing / CI — 8.6 · VERIFIED

- evidence: Repair pass added targeted regressions for B4, AP-ALL, TEL-001, Computer Use safety, media hashing/cancel and UX settle
- evidence: Full-suite triage found no confirmed product regression after harness/environment fixes; current exact-SHA mandatory CI is still pending
- blocker: Mandatory exact-SHA workflows for the current tip are queued/pending
- tests: .github/workflows/bossman-core-ci.yml
- tests: .github/workflows/command-center-ci.yml
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Observability / CEO Control — 7.9 · INTEGRATED

- evidence: Flight Recorder and installed-product telemetry were live on owner hardware
- evidence: TEL-001 now reports model generation throughput from native/upstream timing rather than short-prompt wall time
- blocker: Final owner-level latency distributions on the repaired artifact are incomplete
- tests: tests/test_operator_step_profile.py
- tests: command-center/tests/test_testing_period.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Treasury / Cost — 6.9 · IMPLEMENTED

- evidence: Budget/cost gates remain fail-closed and unknown pricing is not treated as free
- evidence: Frontier-audit/training and media cost-per-verified-result is not yet measured
- blocker: Real cost-per-verified-result for future frontier auditing/training is unavailable
- tests: tests/test_fable_budget_pricing.py
- tests: command-center/tests/conftest.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0

## Mission UX / Command Center — 7.8 · INTEGRATED

- evidence: Computer Use is wired into the owner product and real Windows interaction has owner evidence
- evidence: F-17 page-settle race was fixed with a product-owned rendered-page signal
- blocker: Video Studio product-path generation, Web Designer and long-session final acceptance remain pending
- tests: command-center/tests/test_v6_lazy_pages.py
- tests: command-center/tests/test_ux2_pages_sweep.py
- last_verified_sha: `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · last_verified_at: 2026-09-22 · live_attestation: PENDING · regression_delta: +0.0
