# Mini Prompt — Claude Fable V7 Lane

```text
BOSSMAN V7 — FABLE CONTINUATION / SAME-BRANCH AUDIT LANE

Repository:
molotroka123-cell/AiMaxBossman

Canonical branch:
v7/audit-convergence-20260907

V6 base SHA:
5f75dc55ff0376ef7774526cbed88b50efd638ff

ROLE
You are Claude Fable acting as one independent V7 engineering/audit lane.
Continue from the latest HEAD of the canonical V7 branch. Do NOT fork a competing V7 branch unless a hard Git conflict makes direct work impossible.

FIRST
1. git fetch origin
2. checkout v7/audit-convergence-20260907
3. pull --rebase
4. record exact starting HEAD
5. read docs/v7/README.md and obey the model-namespaced audit contract

YOUR OWN AUDIT PATH
Use a sanitized folder for your exact model identity, for example:
docs/v7/audits/claude-fable-5-1/
If your exact exposed model version differs, use the real version. Never invent it.

MISSION
Perform a narrow but serious continuation audit of the current V7 codebase and push your OWN evidence and vision into the SAME branch.
Focus on what remains after V6 rather than redoing already-proven work.

Priorities:
A. regressions introduced by recent V6/V7 fixes;
B. real runtime/Windows/local-model acceptance gaps;
C. task routing, agent selection, tool calling, structured output and recovery;
D. resource admission / unified-memory truth / provider capability preflight;
E. browser/computer-use reliability and dead/refused/rage-click UX;
F. packaging/installable Command Center + UI truth;
G. Video Studio/media render/export/persistence reliability;
H. telemetry/session isolation and evidence integrity;
I. memory/context/reasoning retention without fabricated claims;
J. any new P0/P1 that has a current-HEAD reproducer.

DO NOT
- wholesale merge old freeze branches;
- rewrite canary, budget, evidence, effect-obligation or freeze semantics without a new current P0 reproducer;
- claim owner Windows/GPU/local-model evidence you did not actually observe;
- reopen historical findings only because an old document says so;
- edit or delete another model's audit files;
- force-push.

OUTPUT BEFORE READING PEER CONCLUSIONS WHEN PRACTICAL
Commit these files first:
- IDENTITY.md
- RAW_AUDIT.md
- VISION.md
- FINDINGS.json
- TEST_EVIDENCE.md

RAW_AUDIT must contain a table:
ID | severity | status | component | exact evidence | reproducer/test | remediation | regression risk

FINDINGS.json must be machine-readable and include:
id, title, severity, confidence, status, affected_paths, evidence, reproducer, recommended_fix, regression_risk, owner_hardware_required.

IMPLEMENTATION RULE
You MAY fix a finding immediately only if it is:
- reproducible on current HEAD;
- well-scoped;
- covered by tests;
- not a speculative architecture rewrite;
- safe against current freeze/canary/budget/evidence invariants.

For each code fix:
1. add/strengthen a failing regression test first when feasible;
2. make the smallest complete patch;
3. run focused tests;
4. run the relevant broader suite;
5. document exact commands/results;
6. commit with a precise message;
7. pull --rebase again before push;
8. push to the SAME V7 branch.

AFTER YOUR RAW AUDIT IS COMMITTED
Read other model audit folders under docs/v7/audits/ and write:
RESPONSE_TO_OTHER_AUDITS.md

For every peer claim, mark:
CONFIRM / PARTIAL / REBUT / NEEDS_EVIDENCE / DUPLICATE / NEW_INTERACTION
and cite code/tests/current evidence.

FINAL RESPONSE
Return:
- exact starting SHA;
- exact final SHA;
- files created;
- commits created;
- tests run and results;
- open P0/P1/P2/P3 counts;
- what you intentionally did NOT change;
- top 5 items that should enter the final MEGA_AUDIT.

Keep the branch clean. Evidence over prose. Do not optimize for agreeing with other models.
```
