# PRE_CONVERGENCE_HEADS — Bossman 1.0 final convergence (2026-09-23)

Snapshot taken after `git fetch --all --prune` (138 remote heads). Reference point: SHA4 = `12612c8184a19dd477e09c60bdaa9a0d2eea21cd`.
Method: `git merge-base --is-ancestor`, `git rev-list --count`, `git cherry -v` (patch-id equivalence), subject/content search for commits ported under another hash.
Evidence/docs branches are listed but are **not** a source of product code.

## Owner line (today)

| Branch | HEAD | Purpose | Unique commits vs SHA4 | In SHA4? |
|---|---|---|---|---|
| feat/cli-claude-parity-20260923 | `12612c8184a19dd477e09c60bdaa9a0d2eea21cd` | SHA4: CLI /compact /context /cost /export /doctor /permissions | 0 | = SHA4 |
| fix/owner-run-20260923-p1 | `cdb4b09db49aebac46bea2f3b98ef143d4a343d6` | SHA2+SHA3: chat-context P1, sidecar truncation marker, NO_PROGRESS detector | 0 (behind 1) | YES (ancestor) |
| fix/coding-path-crlf-blob-20260923 | `7439917f9777685503b8369591bd4f010d794d3f` | autocrlf coding evidence P1 | 0 | YES |
| integrate/owner-final-20260922 | `bd2fe23dab55704d60ed468bcbb4664475e49879` | pre-fix baseline (SHA1) | 0 | YES |
| claude/bossman-cloud-closure-owner-a6s1ki | `3707d6ab7a16256871097dfa35f17b6ace644329` | CI stabilisation (pip-audit retry, CRLF verifier diffs, Windows runner timing) | 0 | YES |
| release/bossman-owner | `e0bf948dea18a02a5fc577a0c2de98fedbfdb8b6` | release line | 0 (behind 260) | YES → release can fast-forward to the candidate |
| evidence/owner-run-20260923 | `a579d266e9d5d8c2cb3cfaa10ae91bfe756b88aa` | today's owner-run evidence (docs only) | 6 | not code, never merged into product |

## Security

| Commit | Content | In SHA4? |
|---|---|---|
| `c5daa5e3` (2026-09-06) | S1 P0 path containment (sibling-prefix), S2 P0 Computer Use consequence decided by model `semantic`, S3 P1 sandbox auto-approved destruction, S4 P1 `.env.example` unsafe local exec | YES (ancestor) |

No other security branch with commits outside SHA4 was found.

## CI / Windows

| Branch | HEAD | Unique vs SHA4 | Decision |
|---|---|---|---|
| fix/ci-flaky-20260922 | `7b2a367a7d61e166859aa1053fd247819562f0e8` | 1 commit, patch-equivalent (`-`) | already in SHA4 |
| fix/root-suite-windows-20260922 | `2727199220a6ed991a5ae0dbb4dd2ef81c4716c0` | 0 | already in SHA4 |
| claude/bossman-1-0-rc-owner-ready-cfesui | `d6b20cdbf4cd9602158d2f6d13557c89e21e5606` | 3 non-equivalent: `0c1241f8` bench-test determinism (SHA4 has the equivalent fix `c40843d0`), `d6b20cdb` CDP-hang diagnostic test (test-only), `5b5ccff5` scorecard regen (docs) | not needed; not taken |
| **claude/bossman-control-v03-43igbk (repo default branch)** | `332c8471f29c99c018f62c48ddb267300399adb2` | 16: 10 trading case logs (data, not product), `windows-stress-100.yml` (2), `root-release-ci.yml` (2), retirement of `root-ci.yml` on the default branch, a demo master prompt | see below |

### Finding: the existing "Windows 100 Scenarios Stress Test" is not a test
`windows-stress-100.yml` (commits `7a712f6e`, `ab213bbf`, 2026-09-19) runs **no product code**. It writes a Python loop that prints `Scenario NNN [module]: OK` 100 times and reports `COMPLETED: 100 PASSED, 0 FAILED`. A green run of it certifies nothing. It is **not** imported into the candidate. A real Windows 100-scenario stress run against the installed product is built in layer 4 instead (CI must test the product, not the other way round).

### root-release-ci.yml vs root-ci.yml
`root-release-ci.yml` on the default branch runs the same gate under the same display name (`root-ci (shared contracts, learning layer, tools)`) that `tools/exact_sha_certify.DEFAULT_REQUIRED` expects. It was registered on the default branch only so `workflow_dispatch` can be triggered there. Putting both files in the candidate would give **two workflows with one required name** (ambiguous certification). Decision: the candidate keeps `root-ci.yml` (the file the certifier and the release branch have always used). Nothing from this branch is imported.

## Other lines ahead of SHA4 (not taken into 1.0)

| Branch | HEAD | Unique | Why not |
|---|---|---|---|
| codex/bossfield-amd-30s | `037cd140…` | 2 (Seedance 2.5 / AMD Wan 30 s owner-run prep) | new feature / new models |
| audit/aster6-full-surface-20260922 | `528f0de3…` | 1 non-equivalent: Telegram Codex bridge | new integration (6 audit docs are already in SHA4) |
| audit/codex-full-surface-20260922 | `833a6a2a…` | 0 non-equivalent | docs, already in |
| feat/video-duration-presets | `8f8d0607…` | 0 non-equivalent | already in |
| integrate/1.0-20260922, fix/media-restart-orphan, fix/cu-stop-button-opencode, fix/desk-edge-relaunch | — | MEDIA-RESTART `_winjob` variant, R6 STOP UI button | SHA4 contains DESK-EDGE-RELAUNCH (`8df57439`), MEDIA-RESTART layer 1 (`44c6bff3`), R6 stop-epoch + `test_computer_stop_race.py`; the alternative variants were deliberately not ported (POST_FREEZE_BACKLOG). Media crash with no orphans was re-verified live today on SHA2/3 |
| evidence/owner-pass2-20260922, audit/owner-glm53-20260921, audit/owner-aster-20260921 | — | evidence only | not code |
| codex/bossman-v1.1-evolution | `3bc81aee…` | 0 | already in |

## Conclusion before convergence
- Every owner-run fix and the security fix `c5daa5e3` is **already in SHA4** `12612c81`. Layers 1 and 2 need **no merge/cherry-pick**, only regression runs and a new security re-attack.
- Layer 3 needs new code: DOWNLOAD-FALSE-SUCCESS, APP-CONTRACT-OVERRIDES-AGENT-TOOLS, CODING-SNAPSHOT-32MB (bounded evidence), controlled apply.
- Layer 4: no usable Windows-100 exists anywhere; the default-branch one is fake. Root gate stays `root-ci.yml`.
- CONVERGENCE_BRANCH = `fix/owner-run-20260923-p1`, fast-forwarded (no rewrite) from `cdb4b09d` to `12612c81`. release/bossman-owner is not touched until step 13.
- P0 note: this morning's owner run found P0 = 0. The two P0s named in the brief are S1/S2 from `c5daa5e3` (2026-09-06); they are re-attacked with new variants in layer 2 / RC0 red team.
