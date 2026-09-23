# 23 September 2026 — Handoff Snapshot

This is a navigation snapshot, not a release certificate.

## Significant work completed during the day

- Convergence PR #74 merged the 1.0/1.1/1.2 work by meaning.
- Evolution 1.1 infrastructure includes verifier + bounded loop concepts with STOP/PAUSE/RESUME, budgets/leases and unknown-outcome handling.
- Terminal 1.2 uses the same Bossman API/state instead of a separate product.
- Multiple Windows/Linux/CI paths were exercised on the convergence candidate.
- Owner-line work continued on security, Computer Use, files/PDF truth, apply/streaming and related P0/P1 closure.
- Self-repair/lesson experiments produced an honest result: a coached repair cycle exists, but causal transfer improvement was not yet measured.
- Bossman 1.5 documentation was created on the release line.
- Game Studio specification was added on the release line.
- Model-stack refresh added Xing4.0-29B-A4B as a serious FAST/AGENT challenger.

## Branch truth at handoff creation

The old `main` runtime was still at historical freeze SHA:
`799fc3dd8e4327811be9d8f3e33cc43ce8168977`.

The product line has newer work in `release/bossman-owner`.

Therefore this package is stored in main for owner convenience but **must not be used to overwrite release runtime history**.

Always fetch current refs tomorrow.

## New direction added tonight

### WebDesigner specialist
Goal: domain-specialized site creation where local Bossman approaches strong frontier quality through:
- good design context;
- real rendering;
- visual critique;
- targeted repair;
- blind evaluation;
- selective fine-tuning.

### 8xH200
Use rented compute for PEFT specialization, not foundation pretraining.

Primary question:
"Can our specialist improve verified beautiful-site output on a frozen holdout enough to justify promotion?"

### Community reuse
Reviewed initial donors:
- Onlook;
- Open Design;
- Open CoDesign;
- Layout (AGPL boundary);
- Build Beautiful Sites (custom source-available boundary);
- WebSight dataset;
- Design2Code reference;
- Vision2Web evaluation reference.

## Tomorrow order

1. fetch and protect current release;
2. Model Fleet UX;
3. Qwen/Xing live comparison;
4. WebDesigner end-to-end runtime;
5. dataset + micro-holdout;
6. H200 training harness;
7. rent/train only after smoke gates;
8. integrate candidate only if blind gain exists.

## Honesty rule

Do not compress all of the above into "Bossman is finished".

Keep:
`IMPLEMENTED / LIVE_TESTED / BLOCKED / NOT_RUN / SPEC_ONLY`
separate.
