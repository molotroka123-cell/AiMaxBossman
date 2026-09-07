# Independent Frontier Model — V7 Audit Prompt

```text
BOSSMAN V7 — INDEPENDENT FRONTIER MODEL AUDIT + VISION

Repository:
molotroka123-cell/AiMaxBossman

Canonical branch:
v7/audit-convergence-20260907

Work in THIS SAME branch.
Do not create a competing V7 branch unless a hard Git conflict makes it unavoidable.
Never force-push.

FIRST
1. git fetch origin
2. git checkout v7/audit-convergence-20260907
3. git pull --rebase origin v7/audit-convergence-20260907
4. record exact starting HEAD + tree SHA
5. read docs/v7/README.md
6. record your exact exposed model/provider identity; if version is unknown, write UNKNOWN_VERSION

ANTI-ANCHORING RULE
Do NOT read another model's RAW_AUDIT before you commit your own first-pass RAW_AUDIT when practical.
You are not here to agree with Fable. You are here to independently inspect the same current code and find what Fable missed, overstated, or got right.

YOUR NAMESPACE
Create:
docs/v7/audits/<your-real-sanitized-model-id>/

Required files:
- IDENTITY.md
- RAW_AUDIT.md
- VISION.md
- FINDINGS.json
- TEST_EVIDENCE.md

AUDIT FROM FIRST PRINCIPLES
Cover:
1. execution truth / postconditions / effect obligations / crash-resume;
2. agent routing / no-agent / multi-agent handoffs / retries / budgets;
3. tool calling / structured output / schema and argument correctness;
4. local models / capability preflight / modality mismatch / fallback;
5. Windows/packaging/installable UI/runtime truth;
6. browser/computer use / stale observations / post-action verification;
7. Video Studio/image/media import-edit-render-export-persistence;
8. memory/context continuity / stale evidence / cross-mission leakage;
9. fleet leases/fencing/ownership/idempotency/concurrency;
10. filesystem/network/archive/SSRF/DNS/symlink/security boundaries;
11. resource admission / unified-memory truth / no invented hardware values;
12. telemetry/session isolation / false PASS / false dead-click / evidence integrity;
13. UX refusal/error/reconnect/blocked-state clarity;
14. release truth: repo-green vs owner Windows/local-model/real-provider evidence.

REQUIRED FINDING FORMAT
ID | severity P0/P1/P2/P3 | confidence | component | exact evidence | reproducer/test | proposed fix | regression risk | owner-hardware-required

FINDINGS.json fields:
id, title, severity, confidence, status, category, affected_paths, evidence, reproducer, proposed_fix, regression_risk, owner_hardware_required, conflicts_with_known_work

VISION.md
Give YOUR V7 architecture vision.
Explicitly assess these hypotheses rather than blindly accepting them:
- Reality Compiler / Mission IR
- world-state graph with freshness/provenance
- strategy search / expected utility
- adaptive model routing / Local Cognitive Fabric
- temporary dynamic agent teams
- self-improving skills with shadow/replay/canary/rollback
- counterfactual pre-effect simulation
- attention/QoS scheduler
- goal-first Mission/Reality UX

For each: KEEP / MODIFY / REJECT / DEFER, with evidence and implementation cost.

CODE CHANGES
You may fix issues in the SAME branch only if:
- current HEAD reproduces the defect;
- patch is small and reversible;
- regression test is added;
- existing safety/canary/budget/evidence invariants are not weakened;
- focused and relevant broader tests pass.

Do not perform speculative mass rewrites.
Do not resurrect old AT-03, Video CFR, PR26/PR36, or historical dead-click findings without a new current-HEAD reproducer.
Do not fabricate owner Windows/GPU/local-model/media/provider evidence.

After your RAW audit is committed, read peer model audits and create:
RESPONSE_TO_OTHER_AUDITS.md

For each peer claim mark:
CONFIRM / PARTIAL / REBUT / NEEDS_EVIDENCE / DUPLICATE / NEW_INTERACTION

Resolve disagreement with code/tests/current evidence, not voting.

Before every push:
git pull --rebase origin v7/audit-convergence-20260907
rerun materially affected tests
push to the SAME branch

FINAL RESPONSE
Return exact starting SHA, final SHA, model identity, files/commits created, tests, open P0/P1/P2/P3 counts, top 10 findings, top 5 V7 architecture recommendations, and top 5 peer claims you dispute or confirm.
```
