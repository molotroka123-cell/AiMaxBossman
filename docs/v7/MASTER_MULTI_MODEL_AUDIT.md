# V7 MASTER MULTI-MODEL AUDIT — Synthesis Contract

Status: **WAITING FOR INDEPENDENT AUDIT INPUTS**

This document is the convergence point for V7 architecture audits. It must not erase disagreement.

## Source audits

Current required sources:

- `docs/v7/SOL_AUDIT.md` — GPT-5.6 Sol
- `docs/v7/audits/<MODEL>_AUDIT.md` — each additional independent model
- relevant historical Astra / Fable / Opus / Perplexity / Reality Compiler audits where they materially constrain V7

## Mandatory synthesis rules

1. Every finding keeps its source model.
2. Consensus is not truth merely because several models repeat the same idea.
3. Repository evidence beats model opinion.
4. Measured evidence beats speculative performance claims.
5. A safety concern is not averaged away.
6. Contradictions must remain explicit until resolved by code/evidence/tests.
7. Duplicate subsystem proposals should default to reuse/adaptation, not parallel implementation.
8. No V7 production code begins until the V6 gate in `README.md` is satisfied.

## Synthesis table

For each material proposal/finding record:

| ID | Topic | Models supporting | Models opposing | Repository evidence | Risk | Decision | Required proof |
|---|---|---|---|---|---|---|---|

Decision enum:
- `ACCEPT`
- `ACCEPT_WITH_CHANGES`
- `REJECT`
- `DEFER`
- `NEEDS_EVIDENCE`

## Required master sections

### 1. Current V6 truth
Exact current V6 source/tree/test status and remaining external gaps.

### 2. Consensus V7 thesis
Only ideas that survive evidence/reconciliation.

### 3. Disagreements
Preserve model-vs-model differences verbatim enough to understand the architectural choice.

### 4. Existing authority crosswalk

| Existing subsystem | Current authority | V7 proposal | Reuse/adapt/replace | Migration risk | Required tests |
|---|---|---|---|---|---|

Must cover at least:
- Reality Compiler;
- task/mission lifecycle;
- effect obligations;
- approvals/auth;
- evidence;
- memory/context;
- Fleet;
- resource brain;
- model routing;
- recovery/journals;
- learning/canary;
- Command Center state.

### 5. Final V7 architecture
One architecture, not several competing implementations.

### 6. Rejected ideas
Document why apparently attractive ideas were rejected.

### 7. Implementation phases
Order by dependency, safety and measured ROI.

### 8. Hostile test matrix
Every new authority or adaptive mechanism needs negative controls.

### 9. Performance contract
Define measurable owner-visible targets without fake human-level claims.

### 10. Freeze contract
Exact exit criteria for each V7 phase and final release.

## Current preliminary synthesis from GPT-5.6 Sol

Strongly recommended:
- one canonical mission/effect authority;
- typed World State with provenance/freshness;
- strategy search with uncertainty, not fake decimal precision;
- smallest capable execution path before multi-agent expansion;
- one resource truth for model routing/admission;
- strategy-switch recovery without duplicate effects;
- learned skills below the authorization boundary;
- uncertainty visible in owner UX.

Primary unresolved architectural question:

**How much of `codex/reality-compiler-v010` and the current integrated mission/evidence stack should be adapted versus replaced?**

No implementation decision should be made until independent models inspect this overlap.
