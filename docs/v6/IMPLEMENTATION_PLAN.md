# Epoch 6 — Implementation Plan

## Phase 0 — Measurement only

**Goal:** establish truth before optimization.

Deliverables:

- startup phase timestamps;
- process-tree CPU/RAM/GPU sampling;
- UI/API latency probes;
- model load/reload counters;
- Computer Use phase timing;
- Video preview/export timing;
- exact-SHA baseline artifact.

No behavioral optimization in this phase.

## Phase 1 — Low-risk quick wins

Implement only hot paths proven by Phase 0:

- lazy optional subsystem init after `UI_READY`;
- adaptive idle polling/backoff;
- remove duplicate refresh/re-render/model/observation work;
- reuse existing safe HTTP clients/connections;
- bounded model keep-alive tuned to measured headroom;
- bounded media admission/priority;
- route-specific heavy UI/module load.

Every patch gets before/after micro + end-to-end benchmark.

## Phase 2 — Medium-risk changes

- narrow event/delta state updates;
- DB query/index improvements from query plans;
- context/skill/tool selection;
- non-authoritative telemetry batching;
- history/journal write-amplification changes with restart/tamper regressions;
- better model routing based on residency and workload class.

## Phase 3 — Architectural performance layer

Only after successful Phase 1/2:

- unified resource scheduler;
- event-driven state propagation + reconciliation;
- CPU/GPU/unified-memory/media admission envelopes;
- residency-aware model router objective;
- cross-app background work prioritization.

## Priority model

### P0-perf

Performance defect that effectively blocks product use: startup hangs, UI owner control unavailable, resource exhaustion/OOM, export freezes whole control plane, pathological model reload loop.

### P1-perf

Major owner-visible latency/jank or repeated waste with low-to-medium implementation risk.

### P2-perf

Efficiency work with meaningful aggregate benefit but no immediate usability block.

## Suggested first implementation order after freeze

1. baseline harness;
2. startup/readiness profile;
3. Command Center polling/render profile;
4. process-tree RAM/VRAM accounting;
5. model residency/reload measurement;
6. Video export contention;
7. Computer Use phase timing;
8. execute only top 3 measured ROI fixes;
9. rebaseline;
10. decide whether Phase 2 is still necessary.

## Commit discipline

Small perf commits, one measured hypothesis per change. Commit message should name metric affected and no broader claim than evidence supports. Broad refactors without baseline are rejected.