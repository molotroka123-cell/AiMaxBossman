# BOSSMAN Multi-Agent Full-Surface Convergence

Date: 2026-09-22

## Goal

Improve the entire originally planned Bossman surface, not only the minimum 1.0 acceptance path, while keeping one canonical release line and preventing agents from editing the same files blindly.

Canonical destination: `release/bossman-owner`.

Working integration branch: `feat/video-duration-presets` until it is merged back.

Audit lanes:
- `audit/codex-full-surface-20260922`
- `audit/aster6-full-surface-20260922`

Claude remains the final integrator/teacher. Codex is a parallel engineer-auditor. Aster 6 is an economical independent auditor.

## Non-interference rules

1. Each agent uses its own branch/worktree.
2. Never force-push shared branches.
3. Never rewrite another agent's commits.
4. No agent edits `release/bossman-owner` directly during active audit.
5. Claude integrates only reviewed deltas.
6. Agents communicate through small checkpoint files and commit SHAs, not by mutating each other's worktrees.
7. GPU/desktop/model-server operations must use the existing lease/ownership mechanism. One owner at a time.
8. When evidence conflicts, keep both records and reproduce; do not overwrite history.
9. Any product fix must include reproduction + regression + neighboring tests.
10. Old green CI on another SHA is not inherited.

## Cost-aware routing

Use deterministic checks first:
- git diff / search / static validation;
- targeted unit/regression tests;
- local FAST model for log triage;
- local MAIN Qwen for normal coding;
- Codex for bounded implementation/review where it adds value;
- Aster 6 for independent adversarial review and concise checkpointing;
- Claude for architecture/security conflicts, teaching, difficult root causes and final integration.

Do not send full repositories or giant logs to frontier models by default. Build compact audit packets:
task, invariant, diff, relevant files, failing log excerpt, tests, evidence SHA, remaining question.

Aster 6 should not repeat an already-proven full scan unless:
- the code changed in that subsystem;
- Codex/Claude evidence conflicts;
- a release gate requires an independent pass.

## Full-surface inventory

Audit and improve every originally intended subsystem, not only today's headline features:

1. Models/Gateway/provider health/routing/circuit breakers/cost caps.
2. Tasks/missions/planner-worker-verifier/restart/resume/exactly-once effects.
3. Memory/context/facts/LearningStore/automatic retrieval/backup-restore.
4. Browser/downloads/navigation/sessions.
5. Computer Use/STOP/Resume/focus/fresh observation/post-state verification.
6. Files/File Intelligence/project isolation/recovery.
7. Web Designer/preview/save/rollback/creative brief path.
8. Image Studio/local generation/edit/export.
9. Video Studio/local generation/I2V/chaining/cancel/restart/resource control/export.
10. Music Studio/generation workflow and file provenance.
11. Telegram/mobile owner control/approvals/idempotency.
12. Fleet/resource leasing/remote execution safety.
13. OpenCode/coding tools/agent handoff.
14. Security/permissions/secrets/prompt-injection boundaries.
15. Packaging/install/update/rollback/Windows bundle.
16. Observability/Flight Recorder/diagnostics.
17. Treasury/budgets/unknown-price fail-closed behavior.
18. UX/Command Center/mobile.
19. Trading Lab remains READ-ONLY/PAPER unless explicitly changed by owner policy.
20. Tests, CI, exact-SHA certification and owner-hardware evidence.

Each subsystem gets one status:
PASS / PARTIAL / FAIL / NOT_RUN / OWNER_REQUIRED / EXTERNAL_BLOCKER.

## Claude lane

Claude:
- owns integration and final release decisions;
- teaches Qwen/Codex on difficult cases;
- resolves cross-subsystem architecture conflicts;
- reviews security-sensitive patches;
- merges accepted deltas into the working integration line;
- keeps `repair-ledger.md` authoritative and append-only;
- does not silently rewrite Codex/Aster evidence.

## Codex lane

Codex works on `audit/codex-full-surface-20260922`.

Priority:
1. Compare current working tip to the original feature inventory.
2. Find implemented-but-broken, half-wired, fake-success, dead-UI, stale-doc and missing-regression cases.
3. Prefer fixing bounded product defects that do not collide with active Claude files.
4. For each fix, commit separately with tests.
5. Publish `docs/agents/checkpoints/CODEX_FULL_SURFACE.md` with:
   - SHA;
   - subsystem;
   - finding;
   - severity P0/P1/P2/P3;
   - reproduced?;
   - fix commit if any;
   - tests/evidence;
   - files touched;
   - collision risk with Claude;
   - integration recommendation.
6. If Claude is actively touching the same subsystem, stop coding there and switch to audit-only.

## Aster 6 lane

Aster 6 works on `audit/aster6-full-surface-20260922`.

It is independent and cost-aware:
- review deltas since its last checkpoint;
- sample high-risk unchanged subsystems instead of rescanning everything;
- focus on fake PASS, restart/replay, state drift, owner approvals, file/data loss, model/runtime truth, packaging and resource leaks;
- verify Codex/Claude claims with targeted tests or source evidence;
- do not patch unless explicitly required; prefer finding + minimal reproduction.

Publish only meaningful new checkpoints in:
`docs/agents/checkpoints/ASTER6_FULL_SURFACE.md`.

Checkpoint fields:
SHA, changed scope, PASS/FAIL, new P0/P1/P2, performance finding, blocker, evidence paths, whether Claude/Codex already covers it.

If nothing material changed, do not produce noise.

## Merge gate

Claude may integrate a Codex patch when:
- root cause reproduced;
- regression fails before/fixes after;
- neighboring tests pass;
- no policy weakened;
- Aster 6 has no contradictory P0/P1 evidence, or conflict is resolved;
- change is rebased/ported onto current working tip by meaning.

Release merge into `release/bossman-owner` only after:
- software P0=0;
- release-critical software P1=0;
- exact-SHA mandatory CI green on the final SHA;
- one Windows artifact is built and hashed;
- affected owner-hardware scenarios are rerun;
- unresolved experimental features are clearly marked, not hidden.

## Continuous learning

For repair tasks:
Qwen/local model attempts first where practical -> tests -> Claude/Codex audit -> correction -> independent verification -> verified lesson -> restart -> analogous holdout.

Store reusable strategy in the existing LearningStore and canonical memory architecture.
Do not create another memory database.
Do not store hidden chain-of-thought, secrets or raw private chats.

A lesson is useful only when retrieval works after restart and the learner applies it on a new analogous task without the teacher supplying the answer.

## Final objective

Bossman 1.0 is the stable base, not the end of scope.
Continue closing the full original product surface after 1.0 without sacrificing release truth.
One canonical release line, many isolated audit lanes, evidence-driven convergence.
