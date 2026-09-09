# Final audit closure — report (2026-09-08, evening)

> **2026-09-09:** for owner testing this report is superseded by the unified PR60 + PR61 candidate — `docs/final/UNIFIED_FREEZE_20260909.md`, code `92e89e0` (this closure through `c7e75cc` merged with PR60). The B1–B10 order in `OWNER_BREAKER_SETUP.md` stands unchanged.

BASE_BRANCH=`night/v7-convergence-20260908`
BASE_SHA=`45027d3e9aef554407a0a9ff07fb8678d1b849f3`
FIX_BRANCH=`claude/bossman-final-audit-closure-aucx9x` (also pushed as `freeze/final-audit-closure-20260908`)
PR=#61 (draft, base `night/v7-convergence-20260908`)
FINAL_CODE_SHA=`cbb2991` — code = `69df482` plus two CI-driven one-function fixes:
`hashlib.sha1(usedforsecurity=False)` in `openhands_client.py` (Core CI bandit B324, severity high, on the
git-blob hash — `4001b7a`) and the sandbox cleanup making the read-only PARENT directory writable before
retrying (Core CI `pytest rest (py3.11)` on a non-root runner: `test_readonly_files_do_not_defeat_cleanup`
failed with `PermissionError`; root locally deleted regardless — `cbb2991`). The authoritative exact-SHA
result is the CI run on `cbb2991` in PR #61; the numbers below were measured on the trees named.

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

## 4. CI on the pushed head `413d160` (exact SHA; the follow-up commit re-runs everything)

| Workflow | Result |
|---|---|
| root-ci (py3.11, py3.12, container smoke) | PASS |
| ASTRA acceptance (portable ubuntu + windows, runner recovery; real sandbox skipped by design) | PASS |
| Solana safety gates | PASS |
| Editors user safety (browser user paths) | PASS |
| Bossman internal benchmark (deterministic) | PASS |
| Command Center CI — checks job (secrets, JS, forbidden files), windows paths (py3.12) | PASS; pytest py3.11/3.12/3.14 in progress at the time of writing |
| Bossman Core CI — gateway-context (3.11, 3.12) | PASS; security/stage8-14/rest/coverage in progress; **compile + секреты FAIL**: bandit B324 (SHA1 without `usedforsecurity=False`) in the new evidence code → fixed in FINAL_CODE_SHA |
| Fable media and Fleet acceptance | py3.12 PASS, py3.11 in progress |
| Intelligence Preservation | **FAIL — `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE`, `docs/benchmark/intelligence-preservation-current.json` missing** → EXTERNAL_EVIDENCE_REQUIRED. The gate was not weakened; a genuine current same-model measurement is the owner's/external step |

A workflow is listed PASS only when every required job in it completed green. In-progress jobs are
not PASS. The authoritative exact-SHA result for FINAL_CODE_SHA is the CI run on that commit
(PR #61), which had not completed when this report was written.

## 5. Freeze verdict

* Repository-fixable P0/P1 and confirmed P2 findings: fixed with negative and positive controls.
* Open in the repository: MF-012 (Web Designer one-click retry, P2 UX), MF-031 (agentless task creation
  UX, P2), MF-032 (liveness URL, feedback-less buttons, P2–P3), MF-033 (Video Studio 404/ValueError on the
  owner's runtime, not reproduced on the closure code).
* EXTERNAL_EVIDENCE_REQUIRED: Intelligence Preservation.
* OWNER_LIVE_REQUIRED: live OpenRouter/GLM, real OpenHands SDK run, Windows file locks, Higgsfield
  authenticated generation, and the breaker corpus B1–B10 in `OWNER_BREAKER_SETUP.md`.

Freeze status: **READY_FOR_OWNER_BREAKER once the CI run on FINAL_CODE_SHA is green apart from
Intelligence Preservation**; not "frozen" before that run is read.
