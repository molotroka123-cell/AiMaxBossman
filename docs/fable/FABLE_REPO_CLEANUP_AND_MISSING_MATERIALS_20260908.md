# Fable — residual repository cleanup + missing-material publication

Date: 2026-09-08
Branch: `claude/fable-system-hardening-lpqq9r`
Starting HEAD: `b7c692cceacfab4019a195a514a4e9f2e6f9e6e0`

## Mission

Fable must stop adding architecture and leave its lane clean, reviewable, reproducible and easy to integrate. This is a repository-hygiene + evidence/publication pass, not a new feature epoch.

## 1. First reconcile current truth

Before deleting or publishing anything:

1. fetch current default branch, PR #58/convergence, current V4/V5 closure ledger and this Fable branch;
2. compare this branch against current integrated/convergence code;
3. classify every Fable-only file/commit as one of:
   - already integrated/superseded;
   - still uniquely valuable runtime change;
   - test/evidence only;
   - historical documentation;
   - generated/transient junk;
4. never merge a stale whole branch merely to preserve one useful file.

## 2. Cleanup rules

Remove from the Fable branch if tracked and not required by an executable test/release contract:

- root-level ZIP/drop-in/transport archives after their contents are materialized;
- duplicate handoff packs and V1/V2/V3 copies superseded by tracked plaintext source;
- screenshots/reference binaries that have no current documentation reference;
- generated caches, local state, temporary acceptance outputs, pytest caches, logs, build outputs, editor temp files;
- stale delivery/materializer scaffolding whose runtime payload is already committed;
- duplicate audit copies where one canonical historical copy already exists;
- obsolete scorecard snapshots that can be replaced by a pointer to the canonical ledger.

Do NOT delete:

- source code that is still unique to Fable;
- hostile/regression tests expressing a still-valid invariant;
- exact-SHA evidence needed to understand a release claim;
- current schemas/migrations;
- red-team fixtures that still reproduce a boundary;
- legally/security-relevant provenance needed to explain why a guard exists.

Never rewrite history or force-push merely to shrink old blobs. Clean the current tree. History rewriting is a separate owner-approved operation.

## 3. Materials Fable must publish before this lane is considered complete

Create/update one canonical `docs/fable/FABLE_FINAL_HANDOFF_20260908.md` containing:

- `FABLE_HEAD_SHA` and tested SHA(s);
- exact list of Fable runtime files still NOT present in current convergence/default branch;
- exact list of Fable tests still NOT present there;
- exact list of Fable changes already integrated elsewhere, with destination SHA/PR when known;
- explicit superseded items that must NOT be merged;
- commands actually run and passed/failed/skipped counts;
- CI workflow run IDs for the final Fable SHA if available;
- Windows/local-model/browser/Fleet evidence marked `NOT_RUN` when not actually executed;
- remaining V4/V5 release blockers separated from Fable-specific blockers;
- rollback notes for every still-unique runtime change.

Also publish machine-readable `docs/fable/fable_final_handoff_20260908.json` with at least:

```json
{
  "fable_head_sha": "...",
  "tested_sha": "...",
  "unique_runtime_paths": [],
  "unique_test_paths": [],
  "already_integrated": [],
  "superseded_do_not_merge": [],
  "open_fable_blockers": [],
  "external_not_run": [],
  "test_commands": [],
  "workflow_runs": []
}
```

The JSON must agree with the Markdown; add a test/validator if practical.

## 4. Important materials that must not be forgotten

Fable's final handoff must explicitly account for:

- journal anti-rollback / anchor work;
- approval identity and current dispatch authority;
- AT-01/AT-03 related tests, while recognizing later convergence authority and not reopening them from stale Fable status;
- executor bookkeeping != post-state evidence;
- context byte-digest/cache migration work;
- recovery/owner parking and no-duplicate-effect regressions;
- Video Studio real FFmpeg/CFR evidence and any still-unique media tests;
- Fleet RPC experiments, clearly labelled EXPERIMENTAL if still not production-certified;
- exact skip-registry state and reasons;
- intelligence-preservation evidence status (never manufacture a PASS);
- owner Windows/local-model acceptance status;
- canary/N8 status from the current authority, not an old Fable snapshot;
- any unique red-team/security tests that convergence does not yet contain.

## 5. Cross-lane additions from 2026-09-08

Do NOT broadly merge these into Fable. Record them in the handoff as parallel work so future integrators do not accidentally overwrite them:

- Trader Apprentice / deterministic BTC order-flow corpus and API on the default branch;
- Visual V3 UX/performance lane;
- Adaptive Token Shunt / local-first cheap-worker routing lane;
- current V7/convergence Reality/OpenHands/provider-health work.

If Fable has an overlapping file, perform a semantic comparison and report the conflict; do not choose Fable merely because its branch is older.

## 6. Root repository hygiene expectations

The default branch cleanup on 2026-09-08 removes legacy root ZIP/drop-in packs and a stray root screenshot. Fable should follow the same policy in its own tree:

- plaintext tracked source is canonical;
- large/generated packs belong in release artifacts, not repo root;
- docs belong under `docs/`;
- handoffs belong under `handoffs/` only when still needed;
- generated test/build evidence belongs in CI artifacts or a bounded evidence directory, not scattered at root.

Add/verify `.gitignore` patterns for ZIPs, caches, local state and generated outputs, but do not ignore intentional source fixtures by overly broad patterns.

## 7. Validation before push

After cleanup:

- `git diff --check`;
- ensure no required imports/docs links point to deleted files;
- regenerate skip registry if test line changes require it;
- run Fable-specific hostile/regression suites;
- run the smallest relevant Core/Command Center/root tripwires for touched paths;
- secret scan;
- list remaining root files and justify every nonstandard binary/archive;
- prove no runtime semantic file changed unless required to fix a reproduced cleanup regression.

## 8. Final output

Push one or more small commits to this branch with clear messages. End with:

`FABLE_FINAL_HEAD=`
`FILES_REMOVED=`
`BYTES_REMOVED_CURRENT_TREE=`
`UNIQUE_RUNTIME_PATHS=`
`UNIQUE_TEST_PATHS=`
`ALREADY_INTEGRATED=`
`SUPERSEDED_DO_NOT_MERGE=`
`TESTS=`
`CI=`
`WINDOWS_OWNER=`
`LOCAL_MODEL=`
`INTELLIGENCE_RETENTION=`
`CANARY_N8=`
`OPEN_FABLE_BLOCKERS=`
`OPEN_RELEASE_BLOCKERS=`
`READY_FOR_ARCHIVE=`

`READY_FOR_ARCHIVE=YES` means this Fable branch contains no forgotten unique material and no active work should continue here. It does NOT mean the Bossman product is release-ready.
