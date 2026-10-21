# Goal: live self-improvement capability tree

Branch: `goal/bossman-self-improvement-tree-20261005`.

## Purpose

When the existing bounded Evolution Loop becomes active, Command Center opens
`#/capability-tree` once for that campaign. The page shows the real campaign
status, current cycle, selected task and phase. It never starts a second learner
and never promotes a candidate into stable.

The owner can attach a durable note to an existing tree node. Notes are stored in
the configured Bossman data directory under
`evolution/capability-tree/owner-notes.json`; they do not overwrite evidence
status. The background feature mirrors changed runtime activity into
`activity-latest.json`.

## Deterministic discovery

`tools/bossman_capability_scan.py` walks every fetched `origin/*` tree and parses
the current checkout for argparse commands, API-like routes, ToolSpec entries,
plugin capabilities and UI pages. It calls no model. Presence of source is only
discovery evidence and can never become PASS.

Standalone:

```bash
python tools/bossman_capability_scan.py --repo . \
  --out capability-scan.json
```

From the authenticated Command Center page, **Проверить репозиторий без ИИ**
uses the same scanner after the normal code-root check. The second and later
runs show additions relative to the previous saved scan.

## API

- `GET /api/capability-tree` — seed tree + live evolution overlay + notes + latest scan.
- `POST /api/capability-tree/note` — note for an existing node only.
- `POST /api/capability-tree/scan` — deterministic scan of an allowed Git checkout.

## Evidence boundary

The bundled tree is reference-only. `code`, `recorded`, `reported`, `blocked`
and other source statuses are preserved. Owner notes, scanner results and model
claims cannot turn a node green. Promotion still requires exact-SHA tests,
independent verification, regression, restart/transfer evidence and the normal
review boundary.

Current North Star level is not advanced by this feature. It adds visibility and
durable bookkeeping around `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; it is not
`SELF_REPAIR_SINGLE_CYCLE_PASS` by itself.

## 2026-10-22 North Star skill-forge acceptance addendum

For the 2026-10-23 North Star session, one meaningful tree leaf can become verified **only after** a traceable end-to-end run, not simply because a `SKILL.md` file exists:

1. Observe a real recurring failure or user-requested reusable procedure, and propose a skill under `.agents/skills/<skill-id>/SKILL.md` (see `.agents/skills/skill-forge/SKILL.md`).
2. Freeze baseline + unseen acceptance set before generation. Writer may use scoped, permission-governed terminal in an isolated worktree. No host-wide permission bypass.
3. Test a previously absent behavior RED, create skill with portable manifest/provenance/permissions, test GREEN, run new unseen holdout; require separate verifier and measured improvement without regressions.
4. Record exact source SHA, worktree diff, command transcript (secrets redacted), test counts, model IDs, cost and immutable receipt. Await owner decision before installing/promoting.
5. Cold-restart Bossman and demonstrate a second different task that automatically selects and successfully executes the new skill with correct permission enforcement. Verify installed SHA and rollout/rollback.
6. Only after full CI + owner-PC evidence on the correct commit may the registry evidence tooling upgrade the leaf and publish the refreshed site.

Snapshot audit of the **existing** `docs/architecture/tree-registry.json`: 927 leaves; proven through code 358, tests 485, none 84; zero leaves carrying registered CI/owner-PC proof. Registry embedded head `a516eab8` is stale relative to audited `main` `5587809324`. The status of a 2026-10-18 independent self-improvement run was NOT_RUN. Later self-coding reports are not yet end-to-end skill invention evidence. Preserve their exact claims, but do not infer North Star graduation.

See `docs/architecture/TREE_UPDATE_20261011.md` (2026-10-22 reconciliation section). This addendum changes only docs; it does not color any capability leaf green.
