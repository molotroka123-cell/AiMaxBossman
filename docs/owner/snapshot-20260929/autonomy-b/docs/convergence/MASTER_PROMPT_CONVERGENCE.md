# BOSSMAN — FINAL OSS CONVERGENCE + OPENCONTEXT INTEGRATION

## CRITICAL BRANCH OVERRIDE — CONTINUE CURRENT CLAUDE CONVERGENCE

Repository:
https://github.com/molotroka123-cell/AiMaxBossman

ACTIVE WORKING BRANCH:
claude/bossman-final-convergence-hu2702

THIS IS THE CURRENT WORKING CONVERGENCE BRANCH.

DO NOT switch development back to:
claude/bossman-control-v03-43igbk
and DO NOT switch development to:
claude/bossman-final-completion-kymr05

Those branches are SOURCE / REFERENCE lines only.

Continue all implementation, fixes, OSS integration, tests and final convergence on:
claude/bossman-final-convergence-hu2702

At prompt creation time the observed HEAD was:
fd3da99344c8afa2264baf67717fe89023bb3dbe

BUT DO NOT assume this SHA is still current.

FIRST:
git fetch --all --prune
git checkout claude/bossman-final-convergence-hu2702
git pull --ff-only
git status
git rev-parse HEAD
git log --oneline --decorate -30

Record:
ACTIVE_BRANCH
START_HEAD_SHA
START_RUNTIME_SHA
WORKTREE_STATUS

Preserve ALL work already written by Claude on this branch.
Do not restart the convergence from an older prompt or older SHA.

---

## SOURCE BRANCH ROLES

Use:
claude/bossman-control-v03-43igbk
for newer canonical/runtime/trading/research/security work that may need to be preserved.

Use:
claude/bossman-final-completion-kymr05
for product work that historically contained:
* hybrid capability ports;
* runtime registry;
* sidecar lifecycle manager;
* Windows-MCP adapter;
* File Intelligence;
* OpenHands integration;
* third-party integration decisions;
* packaging/product closure work.

But:
DO NOT BLIND-MERGE EITHER BRANCH INTO THE ACTIVE CONVERGENCE BRANCH.

For every missing subsystem:
inspect -> compare semantics -> identify stronger/newer implementation -> port/cherry-pick narrowly -> reconcile with current code -> regression-test -> commit

The destination and future single product line is:
claude/bossman-final-convergence-hu2702

---

## IMPORTANT CURRENT TEST FACT

The latest convergence run discovered that running Bossman Core in the background together with root tests can generate false failures through resource contention.

An isolated Core run produced:
3206 passed, 41 skipped, 0 failed

Therefore:
Do NOT classify a failure as a product regression until it is reproduced in an isolated appropriate test environment.
Do NOT dismiss failures as "resource contention" without reproducing them.

Required procedure:
parallel failure -> record failure -> reproduce isolated -> reproduce again if timing-sensitive -> classify PRODUCT / TEST / ENVIRONMENT / CONTENTION -> fix the actual cause.

Do not weaken thresholds merely to make CI green.

---

## CONTINUATION RULE

You are already in the middle of the final convergence.
Do not redo completed phases.

Before starting each phase from the master prompt:
1. inspect current branch;
2. determine whether that phase has already been implemented;
3. run its acceptance tests;
4. if already correct, mark PRESERVED and continue;
5. if partial, finish it;
6. if broken, fix it;
7. never create a duplicate subsystem.

The remaining mission stays:
CURRENT CONVERGENCE -> RECOVER MISSING STRONG WORK -> OPENCONTEXT -> AI FILE SORTER -> WINDOWS-MCP -> GRAPESJS -> VIDEO-SHOTCRAFT -> CROSS-ENGINE FLOWS -> CLEAN INSTALL -> FULL REGRESSION -> EXACT FINAL SHA.

All final commits go to:
claude/bossman-final-convergence-hu2702

---

## YOUR ROLE

You are the final convergence engineer for BOSSMan.
This is NOT a greenfield rewrite.

Your objective is:
AUDIT CURRENT REMOTE TRUTH -> RECOVER ALREADY-BUILT PRODUCT WORK -> CONVERGE THE STRONGEST BOSSMAN LINE -> INTEGRATE THE USEFUL OSS CAPABILITIES -> TEST REAL OWNER FLOWS -> PACKAGE -> FREEZE ONE EXACT SHA.

Do not spend the run producing another impressive architecture document while leaving the product fragmented.
Write code. Test it. Preserve proven functionality. Do not duplicate subsystems that already exist on another Bossman branch.

---

## 0. REMOTE TRUTH FIRST — ABSOLUTE REQUIREMENT

Before editing anything:
git fetch --all --prune
git status
git branch -a
git log --all --graph --decorate --oneline -n 150

Resolve and record:
CANONICAL_BRANCH
CANONICAL_HEAD_SHA
CANONICAL_RUNTIME_SHA
PRODUCT_BRANCH
PRODUCT_HEAD_SHA
MERGE_BASE_SHA
WORKTREE_STATUS

Canonical line: claude/bossman-control-v03-43igbk
Product line: claude/bossman-final-completion-kymr05
Historical merge base: 1bb39bf8dc656cf21a9bd03f85f6751d87d725c7

DO NOT:
* blindly merge the complete product branch;
* reset canonical to the product branch;
* overwrite newer trading/research/runtime work;
* rewrite integrations already implemented elsewhere;
* trust generated build/, caches or stale worktree files when checking whether code exists.

Create `docs/convergence/REMOTE_TRUTH.md` before large implementation work.

---

## 1. RECOVER EXISTING BOSSMAN WORK BEFORE ADDING OSS

Inspect the product branch for at least:
- command-center/bcc/hybrid/capabilities.py
- command-center/bcc/hybrid/registry.py
- command-center/bcc/hybrid/sidecar.py
- command-center/bcc/hybrid/evidence.py
- command-center/bcc/hybrid/adapters/windows_mcp.py
- command-center/bcc/file_intelligence/
- bossman-core/bossman/apprentice/openhands_*
- docs/hybrid/THIRD_PARTY_DECISIONS.md

DO NOT build a second hybrid framework. Recover, adapt and test against CURRENT canonical architecture.

---

## 2. BOSSMAN REMAINS THE AUTHORITY

No OSS project becomes the Bossman brain. BOSSMan remains authoritative for intent, IR, compiler, policy, permissions, approvals, budgets, evidence ledger, and completion gates.

Pipeline: external engine result -> Observation -> Bossman independent verification -> EvidenceCandidate -> Evidence Ledger -> Effect Obligation satisfied? -> Completion Gate -> COMPLETED.

---

## 3. PRESERVE CURRENT SECURITY + CORRECTNESS INVARIANTS

Preserve effect-obligation gates, authorization at effect time, owner Take Control / Stop, protected paths, and durable unreplayable evidence.

---

## 4. THE FIVE PRIMARY OSS TARGETS

1. OSS-1: OpenContext (github.com/0xranx/OpenContext) -> ContextStoreRuntime (SHADOW/AUGMENTATION mode, Bossman memory remains authoritative).
2. OSS-2: AI File Sorter (github.com/hyperfield/ai-file-sorter) -> AGPL preflight, optional external sidecar behind FileIntelligenceRuntime.
3. OSS-3: Windows-MCP (github.com/CursorTouch/Windows-MCP) -> DesktopRuntime adapter, verify effect at execution time.
4. OSS-4: GrapesJS (github.com/GrapesJS/grapesjs) -> WebEditorRuntime canvas backend.
5. OSS-5: video-shotcraft (github.com/Vincentwei1021/video-shotcraft) -> VideoPlanningSkill (recipes/storyboarding), not replacing native FFmpeg engine.

Reference/Deferred:
- OpenHands: Recover existing code from product branch.
- browser-use: REFERENCE_ONLY.
- Weave: REFERENCE_ONLY.
- LocalAI: REJECT / REFERENCE_ONLY.
- Face Recognition: REFERENCE_ONLY / flag OFF by default.

---

## 5. SIDECAR & RUNTIME ARCHITECTURE

- One Bossman Sidecar Supervisor (lifecycle, health, timeout, PID tracking, orphan cleanup).
- Runtimes: DesktopRuntime, BrowserRuntime, WebEditorRuntime, VideoCompositionRuntime, LocalModelRuntime, ContextStoreRuntime, FileIntelligenceRuntime.

---

## 6. COMMIT STRATEGY & OWNER FLOWS

Phased commits (C0 to C10).
Prove Flows A through E (coding, website, desktop, files, video).
Packaged clean install verification.
Final report at `docs/final/BOSSMAN_OSS_CONVERGENCE_FINAL.md` with explicit verdict.
