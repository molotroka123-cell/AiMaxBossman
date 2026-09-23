# END_OF_DAY_CONVERGENCE — Bossman 1.0, 2026-09-23

Read-only pass over `C:\Users\asd\Bossman\src` after `git fetch`. No merge, push or branch change was made.
Inputs: `../PRE_CONVERGENCE_HEADS.md`, `../REPORT_RU.md`, `../CONTINUE.md`.

## Heads (verified against remote refs)

| Ref | SHA | Verified |
|---|---|---|
| origin/fix/owner-run-20260923-p1 (candidate) | `f72af6a82569371365de7cc5fc983a842c13735b` | yes |
| origin/release/bossman-owner | `e0bf948dea18a02a5fc577a0c2de98fedbfdb8b6` | yes |
| origin/evidence/owner-run-20260923 | `06cd3d8ec9f610a5de32635f610cf86be02ceb07` | yes (docs/evidence only) |
| WIP controlled apply | `3fa3d33f` (local only, `C:\Users\asd\Bossman\wt-l3-apply`) | parent = `12612c81` (SHA4), **not** `f72af6a8`; on no remote branch |

## Ancestry

- `c5daa5e3` (security S1–S4, 2026-09-06) **is an ancestor** of `f72af6a8` (`git merge-base --is-ancestor`: true).
- `e0bf948d` (release) **is an ancestor** of `f72af6a8`, 263 commits behind → **fast-forward is possible**, and no rewrite is needed.
- WIP `3fa3d33f` against `f72af6a8`: no file overlap with `aa6bee3d..f72af6a8`, and `git merge-tree --write-tree f72af6a8 3fa3d33f` is **clean**. It still has to be rebased onto `f72af6a8` before it goes in.

## Today's commits on the candidate (`e0bf948d..f72af6a8`, committer date ≥ 2026-09-23 00:00 local, -0700)

Pitfall: `--since=2026-09-23` without a time returns **nothing**, because git reads it as "today at the current wall-clock time". Use `--since='2026-09-23 00:00:00'`. With that fix the window holds 24 commits (23 plus merge #74).

| Commit | Class | Purpose | Regression test(s) in the commit (`git show --name-status`) | Needed for candidate? | Already included? |
|---|---|---|---|---|---|
| `f72af6a8` | P1-fix | CODING-SNAPSHOT-32MB: streaming sha256 of every file, bytes only for changed files (32 MB budget on changes, limit not raised) | A `bossman-core/tests/apprentice/test_openhands_snapshot_bounds.py` | yes (P1) | yes (HEAD) |
| `c0a7e039` | P1-fix | DOWNLOAD-FALSE-SUCCESS: download-by-URL is an action contract, and a missing file means failed | A `command-center/tests/test_download_false_success.py` | yes (P1) | yes |
| `aa6bee3d` | P1-fix (security-adjacent) | APP-CONTRACT-OVERRIDES-AGENT-TOOLS: a routing grant extends the agent's tools instead of replacing them (computer.act still `waiting_approval`) | A `command-center/tests/test_contract_keeps_agent_tools.py` | yes (P1) | yes |
| `12612c81` | feature (owner-requested; SHA4) | CLI /compact /context /cost /export /doctor | A `command-center/tests/test_terminal_chat_claude_parity.py` | yes (SHA4 = verified baseline) | yes |
| `cdb4b09d` | owner-fix | sidecar NO_PROGRESS: flag and stop repeated observations that make no progress | M `bossman-core/tests/apprentice/test_local_sidecar.py` | yes | yes |
| `d53f3b12` | owner-fix | sidecar: a truncated tool result carries a truncation marker | M `bossman-core/tests/apprentice/test_local_sidecar.py` | yes | yes |
| `ea866084` | owner-fix (P1) | chat: an earlier turn is context, not the current request | A `command-center/tests/test_chat_context_not_a_request.py` | yes | yes |
| `7439917f` | owner-fix (P1) | coding: a CRLF-committed blob is not a change under autocrlf=true | M `bossman-core/tests/apprentice/test_openhands_evidence_independence.py` | yes | yes |
| `bd2fe23d` | merge (#74) | convergence 1.0 + 1.1 evolution + 1.2 terminal (254 files) | touches `.github/workflows/{root-ci,bossman-core-ci,command-center-ci,windows-bundle}.yml`, `tests/conftest.py`, `tests/test_astra_security_gate.py`, mvcr fixtures, and more | yes (baseline SHA1) | yes |
| `3707d6ab` | CI | project_host echo up to 30 s on a loaded Windows runner | M `command-center/tests/test_feat_terminal_map.py` | yes (CI stability) | yes |
| `60b8254e` | CI | retry a pip-audit run that dies before writing a report | M `tests/test_astra_security_gate.py` (+ `tools/astra_security_gate.py`) | yes | yes |
| `f9070fba` | docs | regenerate the skips registry | `docs/testing/SKIPS_REGISTRY.md` (checked by `test_skips_registry`) | yes (registry gate) | yes |
| `bb395b4a` | CI | byte-exact verifier diffs for CRLF fixtures on Windows | M `tests/test_evolution_verifier.py` | yes | yes |
| `2e3fefb7` | CI | wait for task.finalized before checking the event cursor | M `command-center/tests/test_terminal_backend_events.py` | yes | yes |
| `827be286` | CI | show the verifier's reasons on a wrong holdout verdict | M `tests/test_evolution_verifier.py` | yes (diagnostic) | yes |
| `c40843d0` | CI | pace the fake bench adapter (timer noise) | M `command-center/tests/test_feat_bench_opencode.py` | yes (equivalent of `0c1241f8`) | yes |
| `68dded39` | owner-fix (race) | images: a cancel racing the last asset no longer leaves a cancelled job holding it | **none in the commit** (only `command-center/bcc/features/images.py`), a test gap | yes | yes |
| `994f3851` | owner-fix (Windows) | sidecar guard NUL device literal was a newline | M `bossman-core/tests/apprentice/test_local_sidecar.py` | yes | yes |
| `2f24dfab` | CI | show guarded pytest output on a failed pytest-runner verdict | M `tests/test_evolution_verifier.py` | yes (diagnostic) | yes |
| `05a93bfc` | owner-fix (Windows) | parse unittest verdict lines ending in CRLF | M `tests/test_evolution_verifier.py` | yes | yes |
| `cfc3bf7a` | CI | let the task flip to waiting_approval after the approval row | M `command-center/tests/test_terminal_cli_e2e.py` | yes | yes |
| `bfab2da4` | CI | wait for the completed event (emitted after the record is written) | M `command-center/tests/test_coding_tasks.py` | yes | yes |
| `3014bc62` | owner-fix (Windows) | pipe patches to `git apply` as bytes (live_workspace, verifier, self_improve_lab) | **none in the commit**, covered only indirectly by the evolution/lab suites | yes | yes |
| `2a148396` | CI / Windows fixtures | decode git output as UTF-8 in evolution/lab fixtures | M `tests/test_evolution_loop.py`, `tests/test_evolution_verifier.py`, `tests/test_self_improve_lab.py`, `tests/test_self_improve_lab_observers.py` | yes | yes |

Also on the candidate: 61 more commits have 2026-09-23 dates in UTC but were committed before local midnight. They arrived through merge #74. These include the security fixes `e60f4c78` (bandit B324), `540ca3d3` (bandit B613 HIGH) and `4c2197cd` (four red-team OPEN defects). All of them are ancestors of `f72af6a8`.

No security commit was authored in today's local window. The security baseline `c5daa5e3` is included.

## Today's commits on remote branches that are NOT in `f72af6a8`

Query: `git log --remotes --since='2026-09-22 17:00:00' --not f72af6a8`. This covers the whole 2026-09-23 UTC day.

| Commit | Branch | Purpose | Needed for candidate? | Reason |
|---|---|---|---|---|
| `06cd3d8e` | evidence/owner-run-20260923 | evidence: shutdown stop, 3 P1 fixes, WIP patch, SHA4 regression, C1–C3 | no | evidence/docs only, never product code |
| `6225965b` | evidence/owner-run-20260923 | PRE_CONVERGENCE_HEADS | no | evidence only |
| `a579d266` | evidence/owner-run-20260923 | owner-run final reports and screens | no | evidence only |
| `c181f97b` | evidence/owner-run-20260923 | checkpoint 4 evidence | no | evidence only |
| `d1d144f6` | evidence/owner-run-20260923 | checkpoint 3 evidence | no | evidence only |
| `3757305b` | evidence/owner-run-20260923 | docs: JEV_AND_STACK_ACTIVATION.md | no | owner planning doc, no code |
| `15ec9b91` | evidence/owner-run-20260923 | checkpoint 2 evidence | no | evidence only |
| `3cbeebd7` | evidence/owner-run-20260923 | checkpoint 1 evidence | no | evidence only |
| `d6b20cdb` | claude/bossman-1-0-rc-owner-ready-cfesui | CDP-attach hang diagnostic test | no | test-only diagnostic; no product change |
| `0c1241f8` | claude/bossman-1-0-rc-owner-ready-cfesui | deterministic bench recommendations | no | equivalent fix already in as `c40843d0` |
| `db891bb5` | claude/bossman-1-0-rc-owner-ready-cfesui | merge of docs tail `e0bf948` | no | `e0bf948d` is already an ancestor of the candidate |
| `5b5ccff5` | claude/bossman-1-0-rc-owner-ready-cfesui | scorecard markdown regen | no | generated docs |
| `b6736092` | claude/bossman-1-0-rc-owner-ready-cfesui | merge of docs tail `7590cc7` | no | docs only |
| `2ccc66ac` | codex/bossfield-amd-30s | Seedance 2.5 / AMD Wan 30 s prep | no | new feature and new models (post-1.0) |
| `037cd140` | codex/bossfield-amd-30s | bossfield continuity anchor | no | belongs to the feature above (post-1.0) |
| `3fa3d33f` | none (local `wt-l3-apply`) | WIP controlled apply | **yes, later** | P1 gap (apply to canonical project). Unverified: neighbour suites have not been run, and the parent is SHA4, so it needs a rebase |

The default-branch `windows-stress-100.yml` (from 2026-09-19, outside today's window) is a print loop. It is not taken into the candidate (see PRE_CONVERGENCE_HEADS).

## Decision

**BEST_CURRENT_CANDIDATE_SHA = `f72af6a82569371365de7cc5fc983a842c13735b`**

No remote commit from today is missing from it that belongs in 1.0. It contains SHA4, the three P1 fixes, `c5daa5e3`, and release `e0bf948d`, so a fast-forward is possible.

It is still **not RC0** and **not certified**. Open blockers (from REPORT_RU):
- C1–C3 are unreproduced. C1 is a P0 candidate: `kind` path traversal in `POST /api/snapshots`.
- The three P1 fixes have not been verified independently.
- Full regression is incomplete: SHA4 has 6 root and 7 core failures plus 2 core errors, all unclassified, and the command-center run stopped at about 20%.
- Windows-100 does not exist yet.
- Exact-SHA CI is NOT_RUN.

Test gaps to close during verification:
- `68dded39` (images cancel race) has no test in its commit.
- `3014bc62` (`git apply` as bytes) has no test in its commit.

## Merge order for tomorrow (all on `fix/owner-run-20260923-p1`, fast-forward and new commits only, no force-push)

1. **Controlled apply WIP `3fa3d33f`.**
   - Rebase onto `f72af6a8`. merge-tree is clean.
   - Run the neighbour suites: `command-center/tests/test_coding*.py test_approval_*.py test_secrem_f013_approval_identity.py test_terminal_cli_unit.py test_terminal_cli_e2e.py`, plus bossman-core `tests/apprentice`.
   - Rewrite the `wip(...)` message into a real `fix(coding): …` message.
   - Commit only after everything is green.
   - Independent verification of `aa6bee3d` / `c0a7e039` / `f72af6a8` can run in parallel with this step.
2. **Fixes for C1–C3**, each as: reproduce with TestClient → failing test → minimal fix → neighbour suites.
   - C1: `POST /api/snapshots {"kind":"/../../../x"}`, the snapshot-kind traversal (P0 candidate).
   - C2: `POST /api/opencode/sessions {"worktree_name":"../x"}` runs `git worktree add` before the containment check.
   - C3: `POST /api/terminal/roots` accepts roots without validation or approval.
   - C4 and C5 are optional P2.
   - After this step, classify the SHA4 full-regression failures as PRODUCT, ENVIRONMENT or HARNESS.
3. **Windows-100.**
   - Add a real `scripts/windows_stress_100.py` that installs the product from `git clone`, not `git archive`.
   - Add a workflow under a distinct name (not the fake `windows-stress-100.yml` display name).
   - Add it to `tools/exact_sha_certify.DEFAULT_REQUIRED`. `root-ci.yml` stays the root gate.
4. **`tools/release_candidate.json` declaration** of the resulting head as RC0.
   - Push the same SHA to a `claude/**` CI trigger ref.
   - Run `tools/exact_sha_certify.py --sha <RC0> --fetch`.
   - Then run RC0 live, FREEZE, build the ZIP and SHA-256, and have the final auditor sign off.
   - Only then fast-forward `release/bossman-owner` from `e0bf948d` to RC0 and check that the tree matches.
