# Exact-SHA CI plan for `feat/bossman-1.9-final-freeze` (agent C, 2026-09-29)

Base measured: `9f4fca9a` (`feat/bossman-1.9-final-freeze` == `feat/bossman-1.9-freeze-20260929`).
Source of truth: `.github/workflows/*.yml` `on:` blocks, `tools/exact_sha_certify.py` `DEFAULT_REQUIRED`,
`tools/owner_facing_branches.json`, `docs/owner/RC19_RELEASE_PROCEDURE.md` section 1. `gh` is NOT installed here.

## Finding (P1, process): the branch name itself starves 6 of 11 mandatory workflows

`feat/**` matches only the generic `push: '**'` workflows. Branch filters of the rest:

| Mandatory workflow (name in `DEFAULT_REQUIRED`) | Runs on push to `feat/bossman-1.9-final-freeze`? | Filter |
|---|---|---|
| root-ci | yes | `**` |
| Bossman Core CI | yes | `**` |
| Command Center CI | yes | `**` |
| ASTRA acceptance | yes | push + PR |
| Solana safety gates | yes | push + PR |
| Bossman V2 Auto-Repair | **NO** | `claude/**`, `night/**`, `release/**`, `integrate/bossman-1.7-unified-20260925` |
| Fable media and Fleet acceptance | **NO** | listed branches, `release/**`, `claude/**`, `night/**`, integrate |
| One-download Windows application | **NO** | same four patterns + path filter (74 paths, incl. `tools/release_candidate.json`) |
| Windows owner run (light/medium/super-long) | **NO** | same + 4 paths |
| Windows 100 real checks | **NO** | same + 6 paths |
| Owner scenarios | **NO** | same + 10 paths |

None of these has a `pull_request` trigger, `workflow_dispatch` answers 403 for agent tokens, so a PR from
the freeze branch cannot start them either. Result today: `exact_sha_certify.py` = NOT_CERTIFIED with 6 missing
workflows, for any SHA that only ever lived on `feat/**`.

## Plan (no force-push, no workflow edit, no touch of `release/bossman-owner`)

1. Freeze content first. Every later commit makes all exact-SHA evidence stale.
2. From the final freeze HEAD create a NEW branch whose name matches `claude/**`, e.g.
   `claude/bossman-1.9-final-freeze-cert` (`git switch -c ... feat/bossman-1.9-final-freeze`).
3. Make ONE last commit on that branch that edits `tools/release_candidate.json`
   (`candidate_label`, `declared_for` = the branch). That file is in the path filters of the Windows chain,
   owner-scenarios and installed-product. Nothing else may follow.
4. `git push origin claude/bossman-1.9-final-freeze-cert` (a new ref: normal push, no force). The commit is new
   to GitHub, so path filters see `tools/release_candidate.json` and all 11 workflows start on that push.
   Do not push the pre-declaration SHA to a `claude/**` branch first; a push that adds no new commit can skip
   path-filtered workflows.
5. Fast-forward `feat/bossman-1.9-final-freeze` to the same SHA (`git push origin <sha>:refs/heads/feat/bossman-1.9-final-freeze`,
   a plain fast-forward). Same SHA, same certificate; this only keeps the freeze branch name pointing at it.
6. Wait for the Windows chain (about 60-75 min). Then certify the exact SHA without `gh`:
   ```powershell
   $env:GITHUB_TOKEN = '<fine-grained token, actions:read>'   # gh is not installed; a token from
                                                              # Credential Manager also works:  "protocol=https`nhost=github.com`n" | git credential fill
   python tools/exact_sha_certify.py --sha <40hex> --fetch --repo molotroka123-cell/AiMaxBossman --output exact-sha-certification.json
   ```
   Offline alternative: save `GET /repos/molotroka123-cell/AiMaxBossman/actions/runs?head_sha=<sha>&per_page=100`
   from a browser and pass `--runs-json`. Expect CERTIFIED (exit 0); NOT_FINAL = runs still going.
7. Artifacts: `astra6-freeze-<SHA>` (`ASTRA6-FREEZE.json` `status` must be `FROZEN`; the job badge lies because the
   publish gate is `continue-on-error`) and `bossman-windows-<SHA>` (zip; verify SHA-256 with
   `tools/rc19_side_by_side.ps1 -Action Install -ArchiveSha256 ...`).

Alternative (not recommended): add `feat/bossman-1.9-**` to the six workflow filters and to
`tools/owner_facing_branches.json`; it changes the SHA and touches release workflows.

## Known non-CI gates (unchanged)

* Intelligence Preservation: BLOCKED until the owner-authored evidence comment exists on the exact SHA; not in
  `DEFAULT_REQUIRED`, needs about 189+ paired items per core metric and 13-33 h of GPU. OWNER_REQUIRED.
* Local `verify_windows_bundle.py` fails on this PC only under Smart App Control (opentimelineio `.pyd`); CI passes.
* Chromium is downloaded at build time without a byte digest (only the revision directory is checked). P2.
