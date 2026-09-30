# Epoch 6 — Opus Implementation Handoff

Use this only **after V4/V5 freeze is honestly reached**.

```text
BOSSMAN — EPOCH 6 / VELOCITY PERFORMANCE OPTIMIZATION

Repository: molotroka123-cell/AiMaxBossman
Role: Opus is the single integration lead.

FIRST:
1. fetch current canonical branch and record START_SHA + tree SHA;
2. confirm V4/V5 freeze candidate and repo-local P0/P1 release blockers are closed;
3. read docs/v6/README.md and every document linked from it;
4. read docs/optimization/* and current multi-model audit index;
5. never treat historical audit numbers as baseline facts.

MISSION:
Measure → rank hot paths → implement smallest proven optimizations → test → A/B → integrate.
Do not add features or parallel kernels.

PHASE 0:
Create exact-SHA baseline for cold/warm startup, UI/API p50/p95, TTFR,
Computer Use phase latency, observations/action, verified actions/min, idle CPU,
process-tree RAM HWM, GPU/unified-memory use, model reloads, Video preview/export,
Web Studio save/reopen, concurrency and recovery.

PHASE 1:
Choose only the top measured low-risk wins. Candidates may include lazy optional
startup, adaptive polling, narrow UI updates, duplicate-work removal, model
residency tuning and media admission. Current code wins over old audit prose.

PHASE 2/3:
Only if baseline still shows need: DB/query work, context/skill selection,
observational telemetry batching, event/delta architecture and unified resource scheduler.

NON-NEGOTIABLE:
- execution truth, evidence, owner control, permissions and recovery cannot weaken;
- no blind retry of ambiguous effects;
- no lossy authoritative journal/evidence path;
- no timeout increase instead of root cause;
- no baseline/candidate model/config mismatch hidden from the report;
- no claimed RAM/VRAM or Strix Halo performance without measurement.

FOR EACH PATCH:
REPRO/HOTPATH=
BASELINE_METRIC=
CHANGE=
CANDIDATE_METRIC=
DELTA=
NEGATIVE_REGRESSIONS=
SAFETY/TRUTH_RESULT=
ROLLBACK=

FINAL:
START_SHA=
FINAL_SHA=
BASELINE_ARTIFACT=
CANDIDATE_ARTIFACT=
COLD_START_DELTA=
WARM_START_DELTA=
UI_P95_DELTA=
TTFR_DELTA=
VERIFIED_ACTION_DELTA=
RAM_HWM_DELTA=
MODEL_RELOAD_DELTA=
VIDEO_RESPONSIVENESS_DELTA=
RECOVERY_DELTA=
P0_OPEN=
P1_OPEN=
EPOCH6_VERDICT=
CLAIMS_NOT_PROVEN=
```

Do not finish with a plan only. Once entry gate is satisfied, implement and validate. If entry gate is not satisfied, stop wide optimization and report the exact freeze blocker instead.