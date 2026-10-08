# AGENTS.md — Mandatory context for AI contributors

Before making product, release, training, memory, model-routing or self-improvement decisions, read:

- `BOSSMAN_1_5_START_HERE.md` and `docs/v1.5/README.md` — owner-authorized 1.5 specification and today's convergence handoff, 2026-09-23; documentation is not activation or certification;
- `docs/terminal/TERMINAL_RUN_1_2_MASTER.md` — latest owner clarification, 2026-09-23;
- `docs/evo/BOSSMAN_1_1_NORTH_STAR.md`;
- `CLAUDE_NEXT_ACTION.md`;
- current repair/audit checkpoints relevant to your work.
- `docs/trading/K1M6A_RESUME_AND_PROVE_LEARNING_20261008.md` — owner-authorized 08.10 continuation, independent ten-video audit and blind learning/transfer checks; read for K1m6a work.
- `docs/audits/2026-10-08-tree-audit-13-zones/README.md` — 08.10 checkpoint of the 13-zone capability-tree audit (what exists, what is missing, where to start) and `BRIEFS.md` (3 auditor roles per branch, 2 open-source candidates per leaf, selection by the existing G·P·D·S·V ≥ 9/10 rule). Status PARTIAL: agent waves failed on session limits; re-run per section 6 of that README before claiming any zone result.

## Non-negotiable direction

**NORTH STAR: Bossman = verified continuous self-improvement with retained, applicable experience and owner-approved commercially useful work.**

Latest owner clarification: **Terminal Run 1.2 is ONLY an additional control surface for the SAME Bossman**. CLI, dashboard and Telegram must use the same configured backend, project/files, models, tasks, memory, skills, permissions and evidence. No separate terminal product, data directory, model fleet, memory database or task engine. Candidate workspaces remain the existing safety mechanism; the learner still cannot directly rewrite installed stable.

Claude Code will teach and audit through structured CLI operations rather than repeatedly clicking the dashboard. Reduced orchestration overhead is a hypothesis to measure on identical tasks, not a guaranteed model speed/intelligence increase. Genuine browser/desktop operations still use their real tools and verification.

The owner's latest target is **4–6 days after the first accepted owner run** to demonstrate transferable learning and move toward first commercial results/revenue through ideas, workflows and the owner's hardware. This updates the earlier 4–7-day horizon only; it is not a profit guarantee or a new permission grant. Actual revenue, deliverable value and saved operating cost are separate metrics.

After release-critical safety/correctness blockers, this is the highest product priority. Terminal Run is an explicitly authorized scope addition to old freeze instructions, not permission to rewrite the architecture.

Do not claim learning from memory hits, teacher-written patches, repeated known tasks or weakened tests.

Use isolated candidate → tests → independent verification → review → promotion. Retain owner STOP, budgets, privacy and external-action approval boundaries across every interface.

Major handoffs/checkpoints must mention both the same-product Terminal Run contract and current progress against the North Star ladder:
SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT.

A specification, generated screenshot or new CLI entry point is not proof of an achieved level.

## Owner amendment — 1.5 specification and consolidation

Owner requested autonomous App/Skill acquisition, internet, an explicitly selected low-overrefusal local profile, own-voice/phone and shopping. Requirements live in `docs/v1.5/`. Reuse the existing backend and permission system; do not build four new cores. `LOCAL_UNRESTRICTED` controls model behavior, not authority over credentials, money or stable code.

## Owner amendment — capability tree is a map, not a certificate (2026-10-08)

The public tree site (`molotroka123-cell.github.io`, pages `ai/bossman-development.md` and `subtle-baklava-676893/`) is unreachable from cloud containers (network policy). Its data source in this repository is `command-center/bcc/capability_tree_seed.json`; per-leaf evidence levels live in `docs/architecture/tree-registry.json`. As of 08.10 that registry records **0 leaves at `ci` and 0 at `owner_pc`**; `reported`/`recorded`/`code` statuses are claims, not proof. Never change leaf statuses by hand — only via `tools/tree_registry_sync.py` / `tools/tree_apply_evidence.py` with evidence files. Owner 08.10: documentation-only work; do not write product code for the tree audit; reconcile site SHA, seed SHA, installed build SHA and registry head before any "green" claim.

## Owner amendment — canonical line (2026-10-08)

**Canonical integration and release destination is `main`, as explicitly directed by the owner on 2026-10-08.** This supersedes older instructions naming `release/bossman-owner` as canonical, including that destination in `docs/v1.5/CLAUDE_MERGE_MASTER.md`. `release/bossman-owner` is a historical release line; do not send new consolidation work there.

Use an existing isolated integration/owner/fix staging branch for candidate work, then integrate reviewed and verified changes into `main`. Do not create another final/canonical line, force-push, or automatically merge product changes into `main`. Required failures block product promotion. Explicit owner-authorized documentation corrections may be committed directly to `main`; they do not certify the product.

Release and owner-test handoffs must record the accepted source SHA, installed build SHA, and capability-tree source SHA. Do not claim one unified Bossman until these identities are reconciled and verification is recorded for the accepted revision. Naming `main` canonical does not certify its current CI, installed build, or autonomy. Document presence does not enable calls, purchases, voice enrollment or background spending.
