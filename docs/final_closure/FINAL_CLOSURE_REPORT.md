# Final audit closure — report (2026-09-08, evening)

BASE_BRANCH=`night/v7-convergence-20260908`
BASE_SHA=`45027d3e9aef554407a0a9ff07fb8678d1b849f3`
FIX_BRANCH=`claude/bossman-final-audit-closure-aucx9x` (also pushed as `freeze/final-audit-closure-20260908`)
PR=#61 (draft, base `night/v7-convergence-20260908`)
FINAL_CODE_SHA=`910ca90` (2026-09-09, 01:35 UTC) — the head of both branches and of PR #61.
Closure code = `69df482` + the two CI-driven fixes named below (`4001b7a`, `cbb2991`) + the owner's three
release commits pushed onto the PR branch (`4264fd6`, `83c1a02`, `2da38b2`; 181 files: Windows jobs,
installed-bundle acceptance, byte-exact OpenHands patches, terminal container proof) + seven follow-up
commits that made the combined tree pass CI (§2a). The authoritative exact-SHA result is the CI run on
`910ca90` in PR #61, recorded in §4; the local numbers in §3 were measured on the trees named there.

The owner asked for the logs to be written down now, with little budget left. This report therefore
records what was MEASURED at the moment of writing and names what was still running. Numbers from
different trees are never added together.

## 1. What was reproduced before any code changed (all on BASE_SHA 45027d3)

| Finding | Reproducer | Result on BASE_SHA |
|---|---|---|
| Astra F1 protected-file mutation hidden by `assume-unchanged` | Astra `test_breaker.py` | 2/2 failed (defect present) |
| Astra F2 stream cap does not bound reads | same | 100 frames consumed for `max_chunks=2` |
| Astra F3 partial+malformed stream `ok=True` | same | reproduced |
| Astra F5 NaN/-1024 memory admitted by the route | Astra `test_final_boundaries.py` | 2/2 reproduced |
| Astra F6 alternating error classes never terminate | same | 2/2 reproduced (12 transitions `queued`) |
| Astra F7 negated tool request → TERMINAL_FILE_ACTION | direct `classify_all` probe (8 prompts EN/RU) | reproduced |
| Owner P0 empty result completed (tasks 22/44 shapes + plain prompt) | `tests/test_p0_completion_truth.py` | 7 of 10 tests failed on BASE (3 positive controls passed) |
| Owner P0 CAPTCHA excuse completed, `url_contains` verified on a challenge page | same | reproduced |
| Web Designer second Apply reverts the edit | `tests/test_web_designer_apply_idempotent_ui.py` in real Chromium against the live server | reproduced on the unfixed UI code (line 101: code reverted to SAVED) |
| Astra F4 Windows cleanup swallows errors | code inspection (`rmtree(ignore_errors=True)`) | not reproducible on Linux |

## 2. What was fixed (code commit `69df482`, one-line follow-up in FINAL_CODE_SHA)

See `MASTER_FINDINGS.md` (33 rows). Summary: P0 ×2 fixed and proven; P1 ×3 fixed and proven
(OpenHands evidence independence, Web Designer model choice, Coding → OpenHands owner path);
P2 ×7 fixed and proven (recovery budget, stream cap, stream completion classes, route validation,
sandbox cleanup honesty, action-contract negation, Web Designer apply idempotence). No gate,
threshold or test was weakened or skipped to obtain green.

### 2a. Follow-up commits after the owner's release checkpoint (all test/CI-wiring, no product logic)

Every red job on the combined tree was reproduced (log read; locally in real Chromium where the test is a
browser test) before the change. None widens a gate; the one product-adjacent change (`5e9e246`) makes
the evidence hash stricter about what counts as a change, not looser.

| Commit | Job that was red | Root cause → change |
|---|---|---|
| `5e9e246` | Windows workspace and PID contracts | `core.autocrlf=true` checkout made every text file an "evidence mismatch" → blob ids computed through git's clean filter (`git hash-object --stdin-paths`); symlinks raw; test with an autocrlf fixture |
| `942d13b` | покрытие (Core coverage) | job lacked Chromium while the partitions install it → same install step |
| `6124ec7` | Windows workspace and PID contracts | teacher fakes wrote through Windows text mode (LF→CRLF); the owner's byte-exact patch (`2da38b2`) preserved it correctly → fakes write exact bytes; CRLF positive control added |
| `e5884bd` | local bundle | owner test looked for the disabled Start by its launch title, but the UI swaps the title to «Управление приложениями выключено» when policy is off → assert that button disabled and no launch-titled Start left |
| `7634af5` | root pytest + hygiene, browser-user-paths | skips registry stale after a line shift in `test_openhands_release_evidence.py` → regenerated |
| `67fa440` | browser-user-paths | Web Designer test clicked the preview before the post-Ctrl+S reload; the fresh frame honestly reported the old selection `lost` → helper waits for the reloaded preview |
| `910ca90` | Command Center pytest (py3.14) | owner's terminal AP-001 container test (first completed run: docker exists on the runner) required `res.error is True` for a failed `cat` outside the roots; `terminal.run`'s contract makes a non-zero exit data, `error=True` only when the command could not run → assert `exit_code != 0` in a usable container + no token in output. Not run locally (no docker); built from the CI-observed ToolResult |

## 3. Suites run locally in this run (Linux, Python 3.11.15, real ffmpeg, real Chromium)

Targeted suites, run on the working tree that became `69df482` (pre-commit, identical code):

| Suite | Result |
|---|---|
| New closure tests: P0 completion truth (14), action-contract negation (45 incl. old suite), OpenHands evidence (25), cleanup honesty (7), streaming honesty (22), reality route (16), recovery bounded (18), Web Designer model choice (5), apply-idempotence UI (1), coding tasks (8), apps owner path (2) | all passed |
| Regression batch 1 (policy algebra, authorization at effect time, review deadlock, approval scope, mission budget, apps control ×3, trading lab, resources unmeasured, approval revocation, engine stop, sandbox user-run regressions, no-direct-completed-writes, lazy pages, apps probe cost, crash-after-effect, restart durability) | 229 passed, 1 skipped |
| Regression batch 2 (24 Video Studio files with real ffmpeg + 5 browser suites with Chromium) | 399 passed, 11 skipped |
| Finalize/review/verifier/action suites (finalize gate ×3, review deadlock, governor review, verifiers, action gate, action router) | 76 passed after updating one expectation (deeper `youtube.com/watch` goal) |
| Streaming contract + reality + openrouter router + model health | 109 passed (two SSE tests updated to the DONE-yielding contract, one ladder test updated to bounded semantics) |
| bossman-core `tests/apprentice/` | 102 passed, 10 skipped |
| Root suite (`tests/`) | 1177 passed, 2 skipped, 1 failed → the failure was the stale skips registry, regenerated in `69df482`; `SKIPS_REGISTRY_CURRENT=PASS`, `README_SCORECARD_CURRENT=PASS`, secret scan PASS, whitespace clean |

Full suites on the frozen code:

| Suite | Tree | Result |
|---|---|---|
| bossman-core full (`tests/`, `BOSSMAN_RUN_REAL_SANDBOX=0`) | started on `69df482`, docs commit `413d160` landed mid-run | 3021 passed, 57 skipped, **7 failed — all `ShaMismatch: requested 69df482 but the executing checkout is 413d160`** (the benchmark integrity check refusing a moved HEAD, i.e. the guard working); the same 7 tests re-run on `413d160`: 11/11 passed |
| bossman-core full re-run on `413d160` | in progress at the time of writing | not claimed |
| Command Center full (CI-equivalent: `--cov=bcc --cov-fail-under=72`, `BCC_REQUIRE_BROWSER=1`) | started on `69df482` | **2860 passed, 16 skipped, 1 failed**, coverage 78.85 % (gate 72 %); the failure was `test_ux2_desktop::test_tests_never_write_into_the_owner_data_dir` — test-order pollution from the NEW `test_coding_tasks` (it pinned `tempfile.tempdir`); fixed by restoring it via monkeypatch (also in the new cleanup tests, whose `never_deletes_outside` case had passed only through that pollution and now pins the sandbox parent explicitly) |
| bossman-core full re-run on `413d160` | HEAD moved again (`4001b7a`) mid-run | 3020 passed, 57 skipped, 8 failed — all `ShaMismatch`, same guard as above; CI on the final head is the authoritative Core result |
| Root full + hygiene re-run on `413d160` | stable | **1178 passed, 2 skipped**; `README_SCORECARD_CURRENT=PASS`, `SKIPS_REGISTRY_CURRENT=PASS` (157 entries, 0 without reason), secret scan PASS, whitespace hygiene exit 0 |

## 4. CI on FINAL_CODE_SHA `910ca90` (exact SHA; read 2026-09-09 02:12 UTC)

Every job below ran on commit `910ca901ddcd84c13d65f9246dfec394088c29bf`. Several workflows ran twice on
the same commit (push + pull_request event); a job is listed PASS only when every completed instance of
it is green and none failed.

| Workflow / job | Result on `910ca90` |
|---|---|
| root-ci: root pytest + hygiene (py3.11, py3.12), bossman-core container ships bossman-shared | PASS |
| Command Center CI: pytest (py3.11, py3.12, py3.14), windows paths (py3.12), секреты/JS/запрещённые файлы | PASS (py3.11/3.12/3.14 green in the completed run; two duplicate instances of the same jobs were still running at read time) |
| Bossman Core CI: compile + секреты, security, stage8-14, gateway-context, rest (py3.11, py3.12), Windows workspace and PID contracts (py3.11, py3.12), покрытие (неснижаемый порог) | PASS |
| PostgreSQL contracts (py3.11, py3.12, py3.14) | PASS |
| ASTRA acceptance: portable (ubuntu, windows), runner recovery; real sandbox skipped by design | PASS |
| Editors user safety: browser-user-paths | PASS |
| Existing Social Farm media and browser (py3.11, py3.12); File Commander (ubuntu, windows) | PASS |
| Real media, Web and Fleet (py3.11, py3.12) | PASS |
| Installed local bundle (ubuntu-latest, windows-latest) | PASS |
| Solana safety (3.11, 3.12); deterministic-benchmark; anti-dumbness gate contract | PASS |
| Human speed: component measurements (ubuntu-latest) | PASS (on `e5884bd` the same job failed on wall-clock host stalls — 3 samples over 10 ms with the host floor itself at 11 ms — in the owner's latency contract; this run is its one re-run) |
| Human speed: component measurements (windows-latest) | **FAIL — owner tooling, standing down.** `tests/test_v5_human_speed.py::test_objective_cas_under_10ms_and_stale_write_is_denied`: wall PASS, `thread_cpu` = INSUFFICIENT_EVIDENCE / `degenerate_measurement`. `time.thread_time_ns()` on the Windows runner advances in 15.625 ms quanta, so per-op thread CPU reads 0 or 15.625 ms and the contract's `p50 == 0 → degenerate` rule fires by construction. No finer thread-CPU clock exists in the stdlib on Windows; the contract (added in `2da38b2`) was not weakened. Proposed patch (PR comment): measure the clock quantum once; when quantum ≥ limit, return INSUFFICIENT_EVIDENCE with reason `thread_cpu_clock_quantum` and let the Windows job record a host limitation, wall clock keeping its own verdict |
| Intelligence Preservation: measured intelligence retention | **FAIL — EXTERNAL_EVIDENCE_REQUIRED.** `docs/benchmark/intelligence-preservation-current.json` missing → `INSUFFICIENT_EVIDENCE`. The gate was not weakened; a genuine current same-model measurement is the owner's step |

Earlier heads, for the record (not combined with the numbers above): the root-ci py3.11 latency
test flaked once on `cbb2991` and passed on every later head; the Core `rest (py3.11)` failure on a
non-root runner and bandit B324 were fixed in `cbb2991` / `4001b7a`; no re-run rights were available
in this session, so every "green" above is a fresh run on the commit named.

## 5. Freeze verdict

* Repository-fixable P0/P1 and confirmed P2 findings: fixed with negative and positive controls.
* Open in the repository: MF-012 (Web Designer one-click retry, P2 UX), MF-031 (agentless task creation
  UX, P2), MF-032 (liveness URL, feedback-less buttons, P2–P3), MF-033 (Video Studio 404/ValueError on the
  owner's runtime, not reproduced on the closure code).
* EXTERNAL_EVIDENCE_REQUIRED: Intelligence Preservation.
* OWNER_LIVE_REQUIRED: live OpenRouter/GLM, real OpenHands SDK run, Windows file locks, Higgsfield
  authenticated generation, and the breaker corpus B1–B10 in `OWNER_BREAKER_SETUP.md`.

Freeze status: **READY_FOR_OWNER_BREAKER on `910ca90`** — CI on FINAL_CODE_SHA is green apart from the
two items above that the repository cannot close: Intelligence Preservation (external evidence) and the
Windows thread-CPU measurement contract (owner's tooling, proposed patch on PR #61). Both branches
(`claude/bossman-final-audit-closure-aucx9x`, `freeze/final-audit-closure-20260908`) point at the same
commit; the freeze branch is not to be force-pushed.
