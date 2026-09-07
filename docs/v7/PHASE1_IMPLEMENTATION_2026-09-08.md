# V7 Phase 1 — Reality Core

Base: V6 OpenHands completion `634fc6519a29887cc196873746bc5ea608730c8e`.

This implementation intentionally starts from the latest V6 source rather than merging the stale V7 documentation branch into runtime code. The existing V7 charter remains the design contract; Phase 1 adds the first runtime primitives without weakening V4-V6 safety gates.

## Implemented

- typed `MissionIR` with objective, desired state, effect/proof obligations, permissions, privacy, budgets, rollback, uncertainty and escalation fields
- structural fail-closed readiness checks for effectful missions
- provenance-aware `WorldStateGraph` with revision history and explicit fresh/stale/unknown semantics
- deterministic strategy candidates and evidence-shaped utility fields
- shadow-only utility router; it cannot authorize execution or replace production routing
- focused negative controls for missing proof/rollback, stale/unknown state and invalid budgets

## Deliberately not claimed

- no production router cutover
- no automatic permission grant
- no model-generated evidence accepted as external proof
- no full Reality Compiler yet
- no observation-adapter fleet yet
- no Skill Compiler/promotion yet
- no live V7 owner-machine acceptance yet

## Next implementation slice

1. Reality Compiler adapter: owner intent -> validated MissionIR
2. observation adapters: GitHub/files/process/browser/tool evidence -> WorldStateGraph
3. policy/budget integration against existing authoritative gates
4. shadow decision telemetry and replay corpus
5. strategy-changing recovery ladder
6. measured local-model capability/resource profiles

Run focused tests from `bossman-core`:

```bash
python -m pytest tests/reality/test_v7_phase1.py -q
```
