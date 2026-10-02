# Bossman 1.9 RC — release procedure (freeze / CI / Windows packaging)

Measured on 2026-09-28 against `762e96d2c46bdf3ede17045a46114fbd3edf2c7b` (PR #84 head).
Evidence (outside Git): `%USERPROFILE%\Bossman\evidence\rc19\a\`.
Everything below is copy-pasteable PowerShell. Replace `$SHA` with the FINAL release SHA.

```powershell
$SHA  = '762e96d2c46bdf3ede17045a46114fbd3edf2c7b'   # <- the FINAL SHA, 40 hex
$REPO = 'molotroka123-cell/AiMaxBossman'
```

## 0. Verdict on 762e96d2 (why it is not releasable yet)

| Gate | State on 762e96d2 | What closes it |
|---|---|---|
| Exact-SHA certification (`tools/exact_sha_certify.py`, 11 required workflows) | **NOT_CERTIFIED**: `Windows owner run — light, medium and super-long task` has **no run** on this SHA (path filter) | the final push must touch `tools/release_candidate.json` (section 1) |
| ASTRA 6 freeze (`windows-bundle.yml` → `freeze` job, artifact `astra6-freeze-<SHA>`) | **BLOCKED**, `ui-sweep.json:not_passed` — the job badge is green because the publish gate is `continue-on-error`; read `status`, not the badge | fix the one UI-sweep error (section 5) and rebuild |
| Intelligence Preservation / measured intelligence retention | **FAIL / BLOCKED** — no owner evidence exists, and the corpus is too small for the unchanged gate to ever PASS | section 2 — owner work, multi-hour, not possible by tomorrow |
| One-download Windows application (CI) | PASS (archive `66d6917b…2f4b`, 789,029,236 bytes) | — |
| Windows 100 real checks | PASS, real: `WINDOWS_100_REAL: PASS — passed 100/100 real tests (missing 0, not passed 0, extra 0)`, negative controls caught | — |
| Local locked rebuild + side-by-side install / start / stop / restart / rollback | PASS (section 3) | — |
| Secret scan (tree + all history) | PASS: no real secret found (section 6) | — |

## 1. Release CI — required workflows and how to make all of them run on ONE SHA

`tools/exact_sha_certify.py` `DEFAULT_REQUIRED` (names must match `name:`):

| Workflow (file) | Trigger | Required | 762e96d2 |
|---|---|---|---|
| root-ci (`root-ci.yml`) | push `**` + pull_request | yes | PR ✅ [36367227365](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227365), push ✅ [36367224192](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224192) |
| Bossman Core CI (`bossman-core-ci.yml`) | push `**` + pull_request | yes | PR ✅ [36367227395](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227395), push ✅ [36367224193](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224193) |
| Command Center CI (`command-center-ci.yml`) | push `**` + pull_request | yes | PR ✅ [36367227490](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227490), push ✅ [36367224183](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224183) |
| Bossman V2 Auto-Repair (`bossman-v2-repair.yml`) | push `claude/**`, `night/**`, `release/**`, integrate | yes | push ✅ [36367224167](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224167) |
| ASTRA acceptance (`astra-acceptance.yml`) | push + pull_request + schedule | yes | PR ✅ [36367227383](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227383), push ✅ [36367224233](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224233) |
| Solana safety gates (`solana-safety-ci.yml`) | push + pull_request | yes | PR ✅ [36367227388](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227388), push ✅ [36367224219](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224219) |
| Fable media and Fleet acceptance (`fable-media-fleet.yml`) | push (listed branches) + pull_request | yes | PR ✅ [36367227387](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227387), push ✅ [36367224177](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224177) |
| One-download Windows application (`windows-bundle.yml`) | **push only**, `claude/**`/`night/**`/`release/**`/integrate, path filter incl. `tools/release_candidate.json` | yes | push ✅ [36367224171](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224171) (freeze verdict BLOCKED, see §0) |
| Windows owner run — light, medium and super-long task (`windows-owner-tasks.yml`) | **push only**, same branches, paths: `scripts/windows_owner_tasks.py`, `scripts/verify_clean_install.py`, its own yml, `tools/release_candidate.json` | yes | **NO RUN** → certification fails |
| Windows 100 real checks (`windows-stress-100.yml`) | **push only**, same branches, paths incl. `command-center/**`, `bossman-core/**`, `tools/release_candidate.json` | yes | push ✅ [36367224185](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224185) |
| Owner scenarios (`owner-scenarios.yml`) | **push only**, same branches, path filter incl. `tools/release_candidate.json` | yes | push ✅ [36367224271](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367224271) |
| Intelligence Preservation (`intelligence-preservation.yml`) | pull_request + push (3 named branches, `release/**`) | **not** in DEFAULT_REQUIRED (separate SHA-bound axis), but red on the PR | PR ❌ [36367227464](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/36367227464) |
| Also green on 762e96d2 (not required): internal benchmark, Editors user safety, Installed product chain, Local bundle, PostgreSQL run contracts, Shipped app contracts, 1.5 Economy CI, 1.7 PIT foundation | | no | ✅ |

`pull_request` runs do NOT start the four Windows/owner workflows; only a **push** to `claude/**`, `night/**`,
`release/**` or the integrate branch does, and only when the push touches their paths.
`workflow_dispatch` answers 403 from agent tokens. Therefore the final candidate commit must:

1. be the LAST commit (every later commit makes all exact-SHA evidence stale, including section 2);
2. edit `tools/release_candidate.json` (new `candidate_label`, `declared_for` = the branch) — that file is in
   the path filter of `windows-bundle.yml`, `windows-owner-tasks.yml`, `windows-stress-100.yml`,
   `owner-scenarios.yml`, `installed-product.yml`;
3. be pushed to a `claude/**` or `release/**` branch (the PR branch qualifies).

Then, after every run finished (≈ 60–75 min for the Windows chain):

```powershell
# read-only; needs a token with actions:read
$env:GITHUB_TOKEN = (gh auth token)
python tools/exact_sha_certify.py --sha $SHA --fetch --repo $REPO --output exact-sha-certification.json
# must print CERTIFIED (exit 0). NOT_FINAL = come back later; NOT_CERTIFIED names the missing workflow.
gh run download --repo $REPO -n "astra6-freeze-$SHA" -D astra6
Get-Content astra6\ASTRA6-FREEZE.json   # "status" must be "FROZEN"; BLOCKED lists its blockers
```

## 2. Intelligence Preservation — BLOCKED (owner action, not a code fix)

Exact failure on 762e96d2 (job 108755930095, step "Fetch owner-attested redacted evidence for this commit"):

```
INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE: no owner-authored evidence comment on this exact commit
##[error]Process completed with exit code 2.
```

The workflow accepts ONLY a commit comment written by the repository owner account on the exact SHA,
starting with `BOSSMAN_INTELLIGENCE_EVIDENCE_V1`, whose redacted summary the UNCHANGED gate evaluates as PASS
(`--core-retention-min 0.98 --tool-retention-min 1.0 --min-samples 20`, 95 % confidence). No agent can or may
produce that comment, and a comment for 762e96d2 would be stale for any later SHA.

### Minimum sample size under the unchanged gate (computed by running the gate itself)

With paired reporting and a perfect result (0 lost, 0 gained), the full/raw core-retention lower bound is
`1 - (z²/(n+z²)) / mean_raw_core`, z = 1.95996. Probe: `evidence\rc19\a\ip_min_n.py` (synthetic payloads,
never evidence), results in `ip_min_n.json`:

| raw core accuracy (per core metric) | min paired items per core metric |
|---|---|
| 1.00 (every item right) | **189** |
| 0.90 | 210 |
| 0.80 | 236 |
| 0.70 | 271 |
| 0.60 | 316 |
| 1.00 with 0.5 % items lost | 280 |
| 0.90 with 0.5 % lost | 401 |
| 0.90 with 1 % lost | 946 |

Current corpus `docs/benchmark/intelligence_tasks.json` (`bossman-retention-v2`): **220 tasks = 20 per metric**
for all 11 metrics. Best possible bound at n = 20 is 0.8389 < 0.98, so even a perfect run is
`INSUFFICIENT_EVIDENCE`; the runner refuses before any model call (verified:
`tools/intelligence_preservation_run.py --preflight-only` → "corpus has only 20 items in its smallest metric;
best possible paired core bound is 0.838875 … need 189 each").

### Feasibility by tomorrow: NO

* The corpus does not exist: ≥ 169 new, independent, reviewed items per core metric (×4 = 676+) at the floor,
  realistically 250–300 per core metric (≈ 920–1,120 new items). Duplicates are refused by the runner.
* Measured model speed on this machine (`bossman-fast-qwen36-35b-a3b-q5:latest`, raw lane, same payload as
  the runner, thinking on): cold load 24 s; warm 4.9 / 6.6 / 10.9 s per call at ≈ 48 tok/s
  (`evidence\rc19\a\ip_latency.jsonl`). Four lanes per item, FULL lane multi-step (≤ 6 steps, tool schemas,
  larger prompt) ⇒ ≈ 35–90 s per item. Floor corpus (4×189 + 7×20 = 896 items) ≈ 9–22 h; recommended
  (4×300 + 140 = 1,340) ≈ 13–33 h of exclusive GPU time, on the FINAL SHA, after all other commits.

### What the owner must do (when there is time; not for tomorrow)

```powershell
# 0) Only after the FINAL commit exists and nothing else will be committed.
git clone --no-checkout https://github.com/$REPO ip-src; cd ip-src
git -c core.autocrlf=false checkout --detach $SHA
# 1) An independently reviewed corpus OUTSIDE the repo: >= 300 per core metric
#    (reasoning, coding, structured output, unknown-task adaptation), >= 20 for the 7 other metrics.
# 2) Preflight (no model calls; must not refuse):
python tools/intelligence_preservation_run.py --model bossman-fast-qwen36-35b-a3b-q5:latest `
  --tasks ..\owner-corpus\retention-v3.json --sha $SHA --preflight-only `
  --out ..\bossman-owner-evidence\ip-preflight.json --gate-report ..\bossman-owner-evidence\ip-preflight-gate.json
# 3) Measurement (13-33 h; nothing else on the GPU, no Ollama model swaps):
python tools/intelligence_preservation_run.py --model bossman-fast-qwen36-35b-a3b-q5:latest `
  --endpoint http://127.0.0.1:11434 --tasks ..\owner-corpus\retention-v3.json --sha $SHA `
  --quantization Q5 --hardware "Ryzen AI Max+ 395 / Radeon 8060S / 128 GB" `
  --out ..\bossman-owner-evidence\intelligence-current.json `
  --gate-report ..\bossman-owner-evidence\intelligence-report.json
# 4) Redacted summary (refuses unless the unchanged gate PASSes on the private evidence):
python tools/intelligence_evidence_transport.py prepare ..\bossman-owner-evidence\intelligence-current.json `
  --expect-sha $SHA --out ..\bossman-owner-evidence\intelligence-comment-request.json
# 5) Owner account only (the owner's own gh login), then re-run the failed job:
gh api --method POST repos/$REPO/commits/$SHA/comments --input ..\bossman-owner-evidence\intelligence-comment-request.json
gh run rerun <intelligence-preservation run id> --repo $REPO --failed
```

A NO_GO or INSUFFICIENT result is the answer, not a reason to lower the bar or shrink the confidence.

## 3. Windows bundle — build, install side by side, start/stop/restart, rollback

Script: `tools/rc19_side_by_side.ps1` (Windows PowerShell 5.1 or pwsh 7). It refuses (exit 2) a data dir
that is, contains or lies in `%LOCALAPPDATA%\Bossman\CommandCenter`, a junction in the data-dir path,
a busy port, an install folder that holds another build, an archive whose SHA-256 differs from
`-ArchiveSha256`, any shortcut of the same name that it did not write, and a data dir already served
by another backend (builds with `bcc/backend_lock.py`: `<data>\backend.lock` held, holder read from
`<data>\backend.json`, or `bcc.app` exit code 5; the same port + build is reported `ALREADY RUNNING`,
exit 0). Regression tests: `tests/test_rc19_side_by_side_refusals.py` (26 cases, Windows).

`tools/build_windows_bundle.py` `verify_runtime` now also makes the embedded `python -I` import the
computer-use modules (`pywinauto`, `pyautogui`, `win32clipboard`, `psutil`) from the runtime and requires
`WindowsDesktop.preflight()` to report no gap; the files comtypes generates during that probe are removed,
so the archive is unchanged. On the real 762e96d2 runtime on this PC (Smart App Control ON): preflight PASS,
all four modules from the runtime, runtime tree unchanged (`evidence\rc19\a\verify-runtime-cu-real.txt`).

```powershell
$S    = "$PWD\tools\rc19_side_by_side.ps1"
$DATA = "$env:USERPROFILE\Bossman\rc19-data\rc-final"      # NEVER the owner data root
$PORT = 8831

# Build exactly like windows-bundle.yml, from a clean clone (the worktree may stay dirty):
pwsh -File $S -Action Build -Sha $SHA -SourceRepo "$PWD"
#   -> <Root>\rc19-build\<sha8>\dist\BOSSMAN-Windows-x64-<sha12>.zip, prints bytes + SHA-256
# or use the CI-built archive: gh run download --repo $REPO -n "bossman-windows-$SHA" -D dl

# Install next to the existing installs (verifies every file against SHA256SUMS):
pwsh -File $S -Action Install -Sha $SHA -ArchiveSha256 <sha256 of the zip>
# Full rehearsal: start -> marker -> graceful stop -> start -> marker survived -> stop -> rollback check
pwsh -File $S -Action Rehearse -Sha $SHA -DataDir $DATA -Port $PORT
# Individual steps:
pwsh -File $S -Action Start   -Sha $SHA -DataDir $DATA -Port $PORT
pwsh -File $S -Action Status  -Sha $SHA -DataDir $DATA -Port $PORT
pwsh -File $S -Action Restart -Sha $SHA -DataDir $DATA -Port $PORT
pwsh -File $S -Action Stop    -Sha $SHA -DataDir $DATA -Port $PORT
pwsh -File $S -Action RollbackCheck -Sha $SHA   # previous installs in <Root>\app: launcher/runtime/MANIFEST

# Window + terminal shortcuts for ONE install, ONE port, ONE data dir (written to the evidence folder;
# copying them to the Desktop is a separate decision; the owner's Bossman.lnk / Bossman CMD.lnk are never touched):
pwsh -File $S -Action Shortcuts    -Sha $SHA -DataDir $DATA -Port $PORT
pwsh -File $S -Action ShortcutTest -Sha $SHA -DataDir $DATA -Port $PORT   # opens both via explorer.exe, both orders
pwsh -File $S -Action StopRC       -Sha $SHA -DataDir $DATA -Port $PORT   # stops window, terminal, backend of this RC
```

Rollback = close the RC (`-Action StopRC` / `-Action Stop`) and start the previous install's own launcher
(`<Root>\app\BOSSMAN-Windows-x64-<old>\Start-Bossman.cmd`). The RC never wrote the owner data root, so there is
nothing to restore; `docs/owner/ROLLBACK_RU.md` still applies when a build was run against the owner data.

### Rehearsal result on 762e96d2 (2026-09-28)

| Item | Value |
|---|---|
| Build | clean clone `rc19-build\762e96d2\src` at 762e96d2, venv with hash-pinned pip 26.2.1 / setuptools 84.0.0 / packaging 26.3 / wheel 0.48.0 |
| Duration | 214 s (`build_windows_bundle.py --zip --profile release`), 229 s incl. icon check and lock record |
| Archive | `BOSSMAN-Windows-x64-762e96d2c46b.zip`, 789,162,808 bytes, SHA-256 `da11e02d5300074bad07cf42bb1d38f9dd81398e8318ac57ddf951042ea2e2b3` (23,146 files, 2,013,704,196 bytes unpacked) |
| CI archive, same SHA | 789,029,236 bytes, SHA-256 `66d6917b062f546c650df5ebfa07a15ad712170527486b68b9909f8cf39f2f4b` (2,013,507,375 bytes unpacked) — not byte-identical, see below |
| Inputs | `BOSSMAN_BUILD_INPUTS=LOCKED`; CPython 3.12.10 embeddable and FFmpeg `ffmpeg-n8.1-11-g75d37c499d-win64-gpl-8.1.zip` digest-verified before extraction; 118 third-party packages via `pip download --require-hashes`, installed `--no-index`; embedded runtime verified = lock + 11 Bossman wheels (`matches_lock: true`) |
| Unlocked download | **Chromium**: `playwright install chromium` fetches from the Playwright CDN (observed: `node.exe` → 142.251.209.27:443); the build checks only the revision directory (`chromium-1243`), not a byte digest. Everything else fetched was digest-pinned |
| Local `verify_windows_bundle.py` | **FAIL on this PC only**: Smart App Control blocks `opentimelineio\_opentime.cp312-win_amd64.pyd` ("Политика управления приложениями заблокировала этот файл"); the same import fails in all six installed builds under `app\`. CI (no SAC) PASSES the same check. Do not disable SAC |
| Install | `rc19-install\762e96d2`, extracted in 17 s, 23,146 files match `SHA256SUMS` |
| Start 1 | PID 3796, 127.0.0.1:8831, `/health/live` ALIVE, build_sha 762e96d2…, source_identity PASS, exactly one listener |
| Stop 1 | graceful (console Ctrl+C → "Application shutdown complete"), port free |
| Start 2 (restart) | PID 24704, same checks; persistence marker (provider row) and access token survived |
| Stop 2 | graceful, port free |
| Rollback | all 6 previous installs READY (launcher + runtime + MANIFEST), untouched |
| Shortcuts (one backend) | window first: backend PID 25780 (`pythonw -m bcc.desktop`, port 8832), terminal attached, no second backend; terminal first: backend PID 13912 (`python -m bcc.app`, started by the terminal after the owner-style Enter), window attached ("Command Center уже работает … сборка 762e96d2c46b — подключаюсь к нему"); build 762e96d2 in both |
| Owner data root | not written by any RC process (its writes at 02:13–02:18 came from the owner's own `Bossman CMD.lnk` → e97bad5b backend on :8800) |

Per-file comparison with the CI archive of the same SHA (`evidence\rc19\a\bundle-local-vs-ci.txt`): 23,146 files
each; 14,154 identical; **all 610 Chromium files identical** (so the unhashed Chromium download produced the
same bytes in both places today); differing: 8,934 `.pyc`, 45 `dist-info/RECORD`, 11 `direct_url.json`, and
the greenlet header location (`include/site/python3.12/…` locally vs `include/python/…` on CI — venv vs
system interpreter). The `.pyc` differences exceed what MANIFEST `known_differences_between_builds` claims:
`compileall` embeds the absolute build path in each code object, so "identical bytecode for identical sources"
holds only for identical build folders. No product source file differs.

## 4. SQLite "database is locked" (one-off, run 36281682557, py3.11 job)

`test_rt_p2_engine_ask_survives_model_self_approval_and_uses_one_approval_once` failed after **30.60 s** in
`ApprovalService.accept_for_execution` (`UPDATE approvals SET status='consumed' …`): the full 30 s busy timeout
elapsed, i.e. some connection held the write lock ≥ 30 s. `bcc/db.py` sets WAL + `busy_timeout=30000`
(+ `connect_args timeout=30`). Not reproduced on Windows: 300 isolated iterations, 300 iterations under
coverage with busy_timeout lowered to 1 s and SQL tracing, 3,300 random-cancellation trials of a
`Database.session()` writer on SQLAlchemy 2.0.43 and 2,000 more on CI's 2.0.54 (aiosqlite 0.22.1) —
0 lock events, 0 leaked write locks. A static
scan of 517 `session()` blocks found no write transaction held across another await (e.g. `bus.emit`).
Most likely mechanism (not proven): the test helper `_run_task` hard-cancels `engine.worker_loop()` while the
worker is still writing its post-completion rows (visible in the SQL trace), and on the slow CI leg a
cancelled aiosqlite connection outlived its task — the same CI log shows leaked aiosqlite worker threads
("Event loop is closed") from another test. No code change was made; a retry would only hide it.

## 5. The ASTRA 6 freeze blocker on 762e96d2

`ui-sweep.json` = REVIEW_REQUIRED because one click in a fresh app ends in a console error:
page `v15-owner-run`, button "⚡ Quick Test" → `POST /api/v15/owner-run/quick-test` returns 200 with problems
`jev_key_missing, openrouter_key_missing`, and `command-center/ui/pages/v15_owner_run.js:49` reports that
expected "not configured yet" state through `toastError(new Error(...))`. Showing it as a normal
"requires preparation" notice (not an Error) is a UI change in workstream ownership outside A.

## 6. Secret scan (762e96d2 tree + all 11,091 blobs reachable from every ref)

Script `evidence\rc19\a\secret_scan.py` (values never printed; path + line + type only). Patterns: OpenRouter
`sk-or-`, Telegram bot token, GitHub `ghp_/gho_/ghu_/ghs_/ghr_/github_pat_`, AWS `AKIA/ASIA`, PEM private keys,
OpenAI/Anthropic-style `sk-`. Result: **no real secret**. Tracked hits are test canaries: PEM headers without
key material in `apps/social-farm/tests/unit/test_media_store.py:151`, `bossman-core/tests/test_search_everything.py:98`,
`command-center/tests/test_web_research_safety.py:132,501`; `sk-live…` canaries in history of
`bossman-core/tests/test_cybersec_integration.py:106` and `command-center/tests/test_v21_snapshot.py:18`;
the rest are placeholders. Nothing to rotate.
