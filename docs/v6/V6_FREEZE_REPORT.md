# V6 Freeze Report — velocity phase 0/1 (Fable 5 completion pass)

**Branch:** `v6/velocity-phase0-baseline-20260907`
**Exact tested HEAD:** `ae3dc3fac70cab3856f15ca5730346762bd21a7e (code HEAD; the report commit itself follows it)`
**Exact tree SHA:** `24c194957c468e903dc6a672f474d49bb5ca8233`
**Base of this pass:** `d9d0caf1` (V4/V5 freeze `196a55ea` + seven owner-session fixes)
**Generated:** 2026-09-07, Linux sandbox (Python 3.11.15, Chromium 141.0.7390.37 headless, 4 logical CPU, no GPU, no local model runtime, Playwright FFmpeg build without libx264/aac)

## FREEZE_DECISION

**REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING**

Everything GitHub/this sandbox can prove is done and green (see §CI). What still needs the owner's machine: Windows path/launcher behaviour of the new code, the local model runtime (residency/reload — never observed here), a sustained video export under interactive load, and the owner-session items marked EVIDENCE_GAP below. No fake PASS: the decision is not PASS because those validations have not been run, not because anything known is red.

## Commits in this pass (oldest first)

| SHA | What | Evidence class |
|---|---|---|
| `298202dd` | `Services.start` phase trace (`StartupTrace`) exposed at `/api/system.startup`; ready=false/total_ms=null until finished; immutable after finish; fresh trace per restart | MEASURED (4 tests) |
| `76ee3209` | Lazy `FEATURE_PAGES` registry (`import()` on first render, idle preload, `onEvent` false until loaded); `bossman:ui_ready` / `bossman:first_page_rendered` marks; `window.__bxTiming()` | MEASURED before/after (3 Playwright tests) |
| `ce31846e` | One `httpx.AsyncClient` + one SSL context per launcher probe; manifests cached by (path, mtime, size) | MEASURED before/after (3 tests) |
| `12c97187` | §5 single-flight for `apps.collect` (N callers → one probe; errors to all; cancel-safe; loop-safe) | TESTED (2 concurrency tests) |
| `c3f0a6b5` | OPEN_FINDINGS: H-CLUSTER → FIXED_IN_6cc9523e; Windows-only items → EVIDENCE_GAP | docs |
| `b4518594` | Computer Use per-phase wall-time (`phase_timing` in the operator manager; in `tools/operator_step_profile.py`) | TESTED (1 test; measurement, not gate) |
| `3de81f02` | FFmpeg children below normal CPU priority (Windows class at spawn; POSIX renice after spawn; failure never breaks export) | CODE_EVIDENCED + 4 tests; interactive-p95-under-export **NOT_MEASURED** |
| `c837acf8` | Skips registry regenerated (root-ci gate was red on c3f0a6b5) | CI hygiene |
| `ae3dc3fa` | Python 3.14 becomes a hard CI gate: run 34137665700 (`ca9a4a7a`) pytest(py3.14) fully green → `continue-on-error` removed | CI (measured once, green) |

## Owner-session bugs (session 6cbb17ce84db / log 7df8cab43cad) — status on this HEAD

Carried in from `d9d0caf1` (already on the branch, re-verified here by the suites listed in §Tests):

| Item | Status | Proof |
|---|---|---|
| Apps "409 / won't open" after server restart (in-memory `_processes` lost, 14 restarts in log) | FIXED (`4a397c2f`) | `test_apps_control.py` restart tests |
| Video "no reason for unavailable preview" (409 reasons flattened) | FIXED (`8cd55a2d`) | video studio tests |
| OpenRouter model-name search while typing in the add-model wizard | FIXED (`d9d0caf1`) | `-k "openrouter or models"` |
| Missions stuck "blocked" forever | FIXED (`1a328ed9`) | `test_feat_missions.py` blocked-mission tests |
| Web designer / video "no model connects" | FIXED (`a857ef34`, `e7c38d6e`) | see commit messages |
| Dashboard "loads very slowly everywhere" | ROOT-CAUSED + FIXED in this pass (see §Performance): (1) 28 eager page modules on the critical path; (2) `/api/apps` froze the event loop ~200 ms per probe, stalling every concurrent dashboard request | measured before/after |
| H-CLUSTER (Windows child encoding, P1) | FIXED_IN_6cc9523e; Windows re-run **EVIDENCE_GAP** | `test_apps_control_child_encoding.py` (Linux) |
| "История видео и чат — redesign", "never stop, fall back to cloud when local model is slow" | **NOT DONE — awaiting owner decisions** (design direction; privacy rule for cloud fallback: private-marked work never leaves the machine) | — |
| Owner's Python 3.14.3 (8/8 OpenRouter failures on 3.14 in the log) | CI matrix runs 3.14 as `continue-on-error`; owner-side fix = run on 3.11/3.12 | session.env correlation |

## EVIDENCE_GAPS

- Windows behaviour of: `child_priority_kwargs()` (BELOW_NORMAL_PRIORITY_CLASS), apps child encoding fix, CC-VIDEO-READVERIFICATION-WINFILE (P2, needs NT open-file semantics).
- Local model residency/reloads (§D): **NOT_RUN** — no Ollama/LM Studio/llama.cpp runtime in the sandbox; the code base has no model-load path of its own (models are HTTP providers), so single-flight model load / reload counters were **not implemented** because nothing here could measure them. `tools/v6_baseline.py` reports `model_runtime: NOT_RUN`, `gpu: NOT_RUN`.
- Interactive p95 during a long video export (§G): NOT_MEASURED (Playwright's FFmpeg lacks libx264/aac; system ffmpeg absent).
- FIRST_USEFUL_RESPONSE (chat TTFR) and VERIFIED_ACTION end-to-end on the owner's host: NOT_RUN here (no model). The measurement hooks now exist: `/api/system.startup`, `window.__bxTiming()`, operator `phase_timing`.
- Intelligence preservation artefact `docs/benchmark/intelligence-preservation-current.json`: INSUFFICIENT_EVIDENCE — never existed on any branch; nothing was stamped.

## Performance — before/after (same tree, same harness, every sample listed, none discarded)

Harness: live uvicorn server + real Chromium login (`scratchpad/measure_live.py`, loopback). "Before" = `d9d0caf1`, "after" = `ce31846e`+.

| Metric | Before (3 samples) | After (3 samples) |
|---|---|---|
| JS modules on the critical path to first render | 42 / 788 KiB | **14 / 290 KiB** |
| `ui_ready` (nav start → shell shown, incl. scripted login) | 401, 411, 409 ms | **287, 244, 281 ms** (later runs 326/240/285, 270/254/261) |
| `first_page_rendered` | 900, 603, 554 ms | 733, 437, 479 ms (later runs 759/390/460, 550/460/553) |
| Event-loop stall during launcher probe (`apps.collect`, 9 manifests) | **202 ms** | **18 ms** cold / 5 ms warm |
| `apps.collect()` cold | 245 ms | 113 ms (remaining = 9 refused loopback connects, awaited) |
| Home-page burst of 10 API calls (waterfall) | each ~290–320 ms (all waiting on the frozen loop) | each 89–147 ms |
| Bytes over the whole session | 908 KiB | 914 KiB (deferred, not removed — by design) |
| Idle API traffic on Home, visible, 30 s | — | 4 requests (2× testing/status, 1 testing/log, 1 system) — polling is not a problem (§E: no change) |
| `Services.start` (fresh DB, 3 runs) | — | 139–204 ms total; `db.create_all` 126–192 ms; all 30 feature setups < 12 ms combined |

`first_page_rendered` is noisy (one ~740 ms outlier in every 3-sample set, both before and after); p50 moved from ~600 → ~460 ms. No claim beyond that.

## Resource usage (sandbox, `tools/v6_baseline.py`, exact SHA `c3f0a6b5`, 10 s idle, live server in-process)

process-tree RSS median 127.2 MB, p95 127.2 MB, HWM 127.2 MB; CPU median 0.0 %, p95 1.0 % (one-core scale); GPU: NOT_RUN; model runtime: NOT_RUN. No values invented, no RSS-as-VRAM inference.

## Tests and CI

| Suite | Head | Result |
|---|---|---|
| command-center full (`pytest tests`, Linux sandbox, no system ffmpeg) | `ce31846e` (detached worktree) | **2084 passed, 9 failed (all 9 = sandbox environment, see below), 142 skipped**, 14:56 |
| command-center full, same sandbox, before this pass (freeze worktrees) | `196a55ea`-line | 2070 passed, same 9 failed, 142 skipped — the 9 are pre-existing and environmental |
| browser/UI suites (18 files incl. `test_v6_lazy_pages.py`) | `76ee3209`+ | 105 passed, same 9 env failures, 1 skipped |
| apps/probe/single-flight (`test_v6_apps_probe_cost.py`, `test_apps_control.py`, golden missions, live-owner smoke) | `12c97187` | 46 passed, 1 skipped; 34 passed |
| startup trace + api + lifecycle + lane4 | `298202dd` | 24 passed |
| bossman-core operator/computer (`-k "operator or computer"`) | `b4518594` | 208 passed, 1 skipped |
| root `tests/test_operator_step_profile.py` | `b4518594` | 8 passed |
| media child priority + video render/receipt | `3de81f02` | 5 passed, 44 skipped (ffmpeg absent) |
| root suite (`pytest tests`) | `ce31846e` | 1137 passed, 1 failed = stale skips registry → fixed in `c837acf8` (`tools/skips_registry.py --check` PASS, `test_skips_registry.py` 2 passed) |

CI on GitHub (branch `v6/velocity-phase0-baseline-20260907`): `47a49ffa` all workflows green incl. Command Center CI (3.11/3.12/3.14, Windows paths); `d9d0caf1` green except Command Center CI still running at report time; `c3f0a6b5` root-ci **red** (stale skips registry, fixed by `c837acf8`), others green/running; `ae3dc3fa` (this HEAD) triggered, result pending — read it before calling anything final. Python 3.14 on PR #56 `ca9a4a7a` (run 34137665700): pytest fully green → 3.14 is now a hard gate.

Browser tests that fail **only in this sandbox** (all pass in Command Center CI on the same code, e.g. run 34138046899 on `47a49ffa`):
- 6 × `test_editors_user_acceptance` / `test_video_studio_playback_stall`: `ffmpeg` absent (`FileNotFoundError`); with Playwright's FFmpeg on PATH they fail on `libx264`/`aac` (exit 234) — encoder unavailable here, not a code defect.
- 3 × web-designer picker tests: real pointer input does not reach the `sandbox="allow-scripts"` (opaque-origin) iframe in this container's headless Chromium 141; a synthetic `click` inside the frame produces the expected `select` message, so the bridge itself works. Environment, not code.

## Safety invariants (checked, not weakened)

- No test skipped/xfailed/weakened; no threshold changed; the skips registry was regenerated to list new environment gates, not to hide anything.
- Lazy pages: `onEvent` for an unloaded page returns `false`; a failed `import()` clears the pending promise so "Повторить" retries honestly; manifest ≡ module is enforced by a test in real Chromium.
- Single-flight is applied only to a side-effect-free, always-fresh probe; errors propagate to every waiter; a cancelled waiter cannot cancel the shared work; a task from a dead event loop is never awaited.
- Startup trace is immutable after finish; a missing measurement is `null`, never `0`.
- Computer Use: phase timing is recorded around the existing `_bounded` wrapper; no observation reuse, freshness boundary, approval, or timeout semantics changed (208 operator/computer tests pass).
- FFmpeg priority: identical argv, identical outputs; only scheduler class; failure to renice is ignored, never fatal.
- No force push, no merge of default/main, no branch deleted.

## OPEN_P0 / OPEN_P1 / OPEN_P2

- **OPEN_P0:** 0 known repository-fixable.
- **OPEN_P1:** 0 known repository-fixable. Owner-decision items (chat/history redesign, cloud-fallback privacy rule) are not repository-fixable without the owner's answer.
- **OPEN_P2:** CC-VIDEO-READVERIFICATION-WINFILE (Windows-only triage), GOLDEN-MISSIONS-SH-DIALECT and FINALIZE-UNCLASSIFIED-STALE-CONTRACT (test debt, unchanged), HOST-SENSITIVE-PERF-GATES (P3, documented).

## External validation still required

1. Owner Windows PC: start the app on this HEAD, open `/api/system` → `startup.phases`; open the dashboard and read `window.__bxTiming()` in DevTools; report both.
2. Local model runtime attached: run a chat and a 5-step safe Computer Use mission; `tools/operator_step_profile.py` and the operator's `phase_timing` give observe/plan/act split.
3. A real video export while clicking around: confirm the UI stays responsive (§G change).
4. Python 3.11 or 3.12 on the owner's machine (3.14 remains `continue-on-error` in CI).

## Rollback

Every change is an ordinary commit on the branch; `git revert <sha>` of any row in the commits table restores the previous behaviour independently. The lazy registry (`76ee3209`) can be reverted alone — `app.js` only needs `FEATURE_PAGES` (and `preloadFeaturePages`, which the revert removes together with its call). No schema/DB migration was introduced in this pass.
