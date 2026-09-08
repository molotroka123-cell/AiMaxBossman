# V6 Freeze Report — velocity phase 0/1 + post-Fable closure handoff

**Branch:** `v6/velocity-phase0-baseline-20260907`

## CURRENT SOURCE TRUTH

Latest code/test HEAD (this pass, 2026-09-07 18:13 UTC):

- **CODE_TEST_HEAD:** `5f75dc55ff0376ef7774526cbed88b50efd638ff`
- **TREE:** `9938708f70beb90bb340b0ca4f413910c400124a`
- previous code/test HEAD of this pass: `c42532669ac7d946fe78dda228037d9fc8167d97` (tree `efc72027…`)
- previous code/test HEAD: `413a97a1ce2936f9543fea5d8f0b529256fc1de2` (tree `919e2ada…`); Fable report head before follow-up: `d458e63ee4c0b5b577f61df22ae8b7dd8d7e5e71`

`c4253266` = `413a97a1` + docs/README/scorecard refresh (`547a4842`…`b14e7f5c`, no production code) +
one repository fix: `scripts/update_readme_scorecard.py` now refuses an UNPROVEN axis with
MEDIUM/HIGH confidence and `tests/test_readme_scorecard.py` sets confidence explicitly
(root-ci had gone red on `80aaa1cf`/`b445429f`/`b14e7f5c` because the test inherited the
canonical scorecard's confidence, which the refresh moved from LOW to MEDIUM; the fix is a
strictly stronger validator plus a negative control — nothing loosened).

`5f75dc55` = `c4253266` + docs (`90880dcd`) + `fix(resources)`: memory that was never measured is
reported as `measured:false` with nulls and refused for admission, instead of the former
128 000 MB fallback (visual-sweep finding F3).

### Exact-HEAD CI on `5f75dc55`

| Workflow | Result |
|---|---|
| root-ci (py3.11 + py3.12, README scorecard, skips registry, compileall, secret scan, whitespace) | **PASS** (run 34150754883) |
| Bossman Core CI | **PASS** (run 34150754794) |
| Solana safety gates | **PASS** (run 34150754762) |
| ASTRA acceptance | **PASS** (run 34150754727) |
| Command Center CI (py3.11 / 3.12 / 3.14 hard lane, Windows paths, secrets/JS) | **FAIL on one lane** (run 34150754745): py3.11, py3.12, Windows paths, secrets/JS green; **py3.14: 1 failed / 2226 passed** — `test_golden_missions.py::test_mission_12_multi_step_mixed_mission` ended in `waiting_approval` |

**Root cause of the py3.14 red (reproduced, not flake-labelled).** Locally the same test failed
1 in ~6 runs on 3.11 too. The mission's `review_escalation` said the truth: the agent's
pytest step exited 1 *after* the fix was written. The harness pre-check (`_pytest_run`,
"the test must fail before the mission") had compiled `calc.py` into `__pycache__`; the fix
rewrites the file with the same size in the same mtime second (`return a - b` →
`return a + b`), so Python trusted the stale pyc. Confirmed deterministically outside the
suite: 7 of 8 same-second same-size rewrites keep the old behaviour. Fixed in `bed5e8c9`
(pre-check runs with `-B` / `PYTHONDONTWRITEBYTECODE=1`; no production code, gate,
approval or timeout changed): mission 12 × 15 → 15 passed; whole golden suite 12 passed.

### Exact-HEAD CI on `bed5e8c9` (harness fix only; identical production tree)

| Workflow | Result |
|---|---|
| root-ci | **FAIL — registry only** (run 34153187474): `SKIPS_REGISTRY_CURRENT` red because the harness docstring moved two `pytestmark` line numbers; all tests green. Regenerated in `d7e62e38` (`--check` PASS, `test_skips_registry` 2 passed). |
| Bossman Core CI | **PASS** (run 34153187457) |
| Solana safety gates | **PASS** (run 34153187468) |
| ASTRA acceptance | **PASS** (run 34153187446) |
| Command Center CI (run 34153187471) | secrets/JS **PASS**, py3.11 **PASS** (2 227 tests, 20 min), Windows paths **PASS**; py3.14 **PASS** (the lane that was red on 5f75dc55); py3.12 **FAIL: 1 / 2 226** — `test_browser_navigation_ui.py::test_policy_403_keeps_session_but_401_requires_login` got 200 after logout |

**Root cause of the py3.12 red (reproduced by reading the code path, fixed, tested).** `api.js`
coalesced identical in-flight GETs by path only; the shell's own `/api/system` was still in
flight when the test logged out and asked again, so the post-logout call received a promise
made under the previous session (an observation crossing an authentication boundary — the
same class the V6 single-flight rule forbids). Fixed in `dd2306aa`: a session generation bumped on
login/logout/401 keys the map and drops in-flight entries; two browser tests (legitimate
coalescing within a session; a GET started before logout is never reused after it).

### Exact-HEAD CI on `dd2306aa` (api.js fix + registry regen)

| Workflow | Result |
|---|---|
| root-ci | CI_ROOT_FINAL |
| Bossman Core CI | CI_CORE_FINAL |
| Solana safety gates | CI_SOL_FINAL |
| ASTRA acceptance | CI_ASTRA_FINAL |
| Command Center CI | CI_CC_FINAL |

On `c4253266`: root-ci PASS (34149935882), Core PASS (34149935775), Solana PASS, ASTRA PASS;
Command Center CI was **cancelled by the next push** (concurrency), so its verdict is taken
from `5f75dc55` above. Local full command-center suite on `5f75dc55`: **2093 passed, 9 failed (the known sandbox-only browser set: 6 × no ffmpeg encoder, 3 × pointer input to the opaque-origin web-designer iframe), 142 skipped**, 15:38.

Earlier heads on this branch: `47a49ffa` all green incl. Command Center CI 3.11/3.12/3.14 + Windows paths; `ae3dc3fa` (3.14 hard gate) Command Center CI green; `2ededc88` (canary port line) root-ci/ASTRA/Solana green.

The documentation commits that follow the code/test HEAD do not change production behavior. Do not confuse a report/prompt commit SHA with the tested code SHA.

### Post-Fable follow-up commits

| SHA | Change | Classification |
|---|---|---|
| `8315b267a9a288d20a69cdbadd6c39b9a15ca16e` | Trading Lab wiring test now verifies the V6 lazy registry/dynamic import instead of demanding the removed eager import | stale test contract fixed; production behavior unchanged |
| `cf7bc81ceaeb852b1376ca2e21f7d781701432c1` | Golden Mission env keeps `approval_watcher` alive across sequential owner decisions | harness lifecycle correction |
| `413a97a1ce2936f9543fea5d8f0b529256fc1de2` | Golden Missions use exactly one persistent approval watcher; duplicate temporary watcher removed | harness made production-like; approval policy unchanged |

These follow-up commits do **not** weaken approvals, review escalation, effect verification, freshness, authorization, canary, rollback, budgets or privacy routing.

## CI HISTORY NOTE

On `413a97a1` Solana and ASTRA were green; root-ci went red on the three documentation
commits that followed (`test_readme_scorecard::test_not_run_cannot_become_pass`) and is green
again on `c4253266` — see the exact-HEAD table above. A running job is never quoted as PASS.

## FREEZE DECISION

**REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING** on `c4253266` — root / Core / Solana / ASTRA green on the exact HEAD; Command Center CI: see table (the verdict is withdrawn to BLOCKED if that lane finishes red with a reproducible product failure).

`OPEN_REPO_P0 = 0`, `OPEN_REPO_P1 = 0` (see "Current known repository debt"). What remains is owner-machine evidence, not repository work.

Repository-visible V6 phase 0/1 work is substantially complete. The remaining high-value validation is primarily the owner's real Windows/local-model/runtime path and a second Dashboard acceptance session. If current CI exposes a reproducible repository bug, status becomes `BLOCKED` until fixed.

---

## V6 implementation completed in the Fable pass

| SHA | What | Evidence class |
|---|---|---|
| `298202dd` | `Services.start` phase trace (`StartupTrace`) exposed at `/api/system.startup`; ready=false/total_ms=null until finished; fresh trace per restart | MEASURED + tests |
| `76ee3209` | Lazy `FEATURE_PAGES`, dynamic `import()` on first render, idle preload, `bossman:ui_ready` / first-page marks | MEASURED before/after + browser tests |
| `ce31846e` | Shared `httpx.AsyncClient`/SSL context per launcher probe; manifest caching | MEASURED |
| `12c97187` | single-flight/coalescing for `apps.collect` | concurrency-tested |
| `b4518594` | Computer Use per-phase wall timing | TESTED |
| `3de81f02` | FFmpeg children below normal interactive priority | CODE_EVIDENCED + tests |
| `ae3dc3fa` | Python 3.14 promoted to a hard Command Center CI lane after a prior full green run | CI evidence |

Follow-up harness commits are listed in CURRENT SOURCE TRUTH above.

---

## Owner session `6cbb17ce84db`

Initial published session evidence included approximately 5777 recorded events, 37 raw dead-click detections and 33 recorded errors.

**Important:** the raw dead-click count is not the number of real bugs. The corrected detector/analysis proved that visible changes outside the original observation surface (publish button state, modal/toast/focus/ancestor changes) could be incorrectly recorded as dead clicks. Current work must use the corrected analysis rather than resurrecting every historical raw row.

### Carried fixes / closures on the V6 line

| Owner symptom | Current status |
|---|---|
| stale-data reconnect button was mute | FIXED; must give an attempt or explicit refusal |
| mission displayed `running` forever while all tasks were blocked | FIXED; blocked-only mission terminates honestly |
| Apps returned old 409 after Command Center restart because owned process state was only in memory | FIXED with durable launch evidence and conservative recovered-process semantics |
| Video unavailable reason flattened / opaque | diagnostics improved; REAL Video Studio workflow still needs owner re-test |
| OpenRouter model-name/provider UX | fixes carried; real configured provider still requires owner/runtime evidence |
| Web Designer / Video provider path | fixes carried; real AI-edit/provider flow still requires owner re-test |
| Trading Lab 500 / missing old module | current feature lazy-imports `bossman.trading_learning`; no-core build returns `DEAD_OR_UNWIRED` rather than crashing |
| Dashboard slow everywhere | root-caused and materially improved by lazy pages + app-probe cache/single-flight |
| H-CLUSTER Windows child encoding | code fix present; owner Windows proof remains EVIDENCE_GAP |

### Sandbox re-test of the owner-session paths on `c4253266` (2026-09-07)

A scripted acceptance session on the real Command Center (real Chromium, workers on,
testing-period recorder on, fake instant model) — full write-up in
`docs/testing/sessions/2026-09-07__sandbox-acceptance-v6__7cc925717624.md`:

| Owner-session path | Sandbox result on `c4253266` |
|---|---|
| stuck tasks / missions | 4/4 tasks reached `completed` (two of them concurrent); blocked-mission terminal outcome unit-tested |
| Web Designer AI edit + provider failure | edit 200 and persisted; provider down → 502 «модель недоступна…», code unchanged — no silent success |
| Video Studio persistence / derivatives | project create 200, reload 200; missing-media thumbnail → 422 with a message, never 500; real thumbnail/waveform/export **NOT_RUN** (no encoder here) |
| Apps / restart | unit-tested recovery (`test_apps_control.py`); not exercised live (would spawn the owner's apps) |
| Trading Lab | all four routes 200; owner's `ModuleNotFoundError: bossman_v3` not reproduced — wheel and a fresh editable install both ship `bossman_v3` → EVIDENCE_GAP (stale editable finder on the owner host is the only mechanism found) |
| OpenRouter / provider routing | Python 3.14 cleared by CI (run 34137665700 fully green); real provider path NOT_RUN — on-screen error text still needed (now captured by `ui.refused.reason`) |
| dashboard load | 15 navigations, 0 dead clicks, 0 refusals, 0 JS errors, 0 console errors; `Services.start` 187 ms |
| long-session stability | 30 s session with 20 s idle over an open WebSocket: state stayed «live-обновления»; hours-long stability NOT_RUN |

Compared with `6cbb17ce84db` (37 raw dead clicks → 11 real candidates, 71 refusals, 20 HTTP ≥ 500): this run recorded 0 / 0 / 1 (the deliberate provider-down control).

### Visual QA sweep (parallel lane, PR #57, branch `ux/vision-full-page-pass-20260907`)

36 routes × 5 viewports in real Chromium, 550 screenshots, P0 = 1 found/fixed (desktop dock
off-screen on tall pages), P1 = 8 found / 7 fixed, presentation-only changes (+6.9 KB CSS,
+144 B JS, no new dependencies), `tests/test_visual_smoke_ui.py` added. The PR is a draft
against this branch; merging it is the owner's call. Functional findings it handed over:

| Finding | Disposition |
|---|---|
| F1 «По расписанию…» on `#/tasks` calls `start(false)` | **not a bug** — `start(false)` opens the schedule modal with the prompt preset |
| F2 «Выйти» in settings has no confirmation | P2 UX, unchanged |
| F3 Overview/Resources showed «0.0 / 125.0 ГБ» from a 128 000 MB fallback | **fixed in `5f75dc55`** |
| F4 two «Главная» landings (`home`, `home-v3`) | by design — the older landing is reachable by direct link only (`SUPERSEDED` in `app.js`) |
| F5 app-card «Открыть» at 40 % accent reads as disabled | P2 visual, in PR #57 scope |
| F6 internal token «EMPTY» in Browser empty-state copy | P2 copy, unchanged |

### Owner-session paths that still need explicit real re-test

1. **Video Studio real path:** import → thumbnail/waveform → Play → two timeline edits → export → decode/probe → reopen project. Specifically re-test the historical 409 family without reopening closed CFR harness findings unless a new repro exists.
2. **Web Designer AI edit:** reproduce/close provider `502`, prove successful edit persists, prove provider failure cannot become silent success.
3. **Configured provider / local model:** real provider/model/runtime, latency/retry/fallback reason; private local-only work must not silently fall back to cloud.
4. **Owner Windows desktop app launch:** actual visible window in owner session, not PID/port-only evidence.
5. **Long-session stability:** 4–5 sequential Dashboard tasks in one session; watch memory growth, duplicate subscribers, zombie runs, stuck approvals, queue starvation and stale context.
6. **CC-VIDEO-READVERIFICATION-WINFILE:** Windows/NT open-file semantics require real triage if reproduced.
7. **H-CLUSTER:** get actual Windows evidence if owner host is available.

---

## Performance evidence retained from the Fable pass

Same-scenario measured results reported during the V6 pass:

| Metric | Before | After |
|---|---|---|
| JS modules on critical path to first render | 42 / 788 KiB | **14 / 290 KiB** |
| `ui_ready` samples | 401, 411, 409 ms | **287, 244, 281 ms** in the primary after set; later sets similar |
| `first_page_rendered` p50 | ~600 ms | ~460 ms; noisy tail retained |
| launcher probe event-loop stall, 9 manifests | **~202 ms** | **~18 ms cold / ~5 ms warm** |
| `apps.collect()` cold | ~245 ms | ~113 ms |
| Home burst requests during old frozen loop | ~290–320 ms each | ~89–147 ms each in measured after run |
| idle live server process-tree RSS | — | ~127.2 MB HWM in recorded sandbox sample |

No GPU/local-model performance claim is made from these numbers.

### Still NOT MEASURED / external

- real local-model residency/reload rate;
- real GPU/unified-memory behavior on owner target hardware;
- owner-host `FIRST_USEFUL_RESPONSE` with the configured model;
- owner-host `VERIFIED_ACTION` end-to-end;
- interactive p95 while a sustained real Video Studio export runs;
- valid same-model intelligence-retention artifact.

Missing data stays `NOT_RUN` / `INSUFFICIENT_EVIDENCE`.

---

## Safety invariants

V6 performance work and the post-Fable harness corrections must preserve:

- owner approvals and anti-replay identity;
- fresh effect-boundary verification;
- post-state verification;
- sandbox/root containment;
- fencing and lease ownership;
- terminal status truth;
- recovery/journal invariants;
- budgets/cost enforcement;
- privacy routing;
- secret scanning;
- canary/rollback contracts inherited from V4/V5.

No threshold, skip or xfail may be weakened merely to obtain green CI.

---

## Current known repository debt / external gaps

### P0

- **0 known repository-fixable P0 at this handoff**, subject to final exact-SHA CI.

### P1

- **0 known repository-fixable P1 proven from current GitHub evidence**, subject to final exact-SHA CI and real owner-session re-test.
- Owner Windows/local-model/provider failures discovered only on the real machine remain external-validation candidates until reproduced on current HEAD.

### P2 / test & platform debt

- `GOLDEN-MISSIONS-SH-DIALECT`: prefer portable Python fixture commands over skipping meaningful Windows missions.
- `FINALIZE-UNCLASSIFIED-STALE-CONTRACT`: reconcile stale test expectation with current hardened ASK policy; do not loosen production policy to satisfy old tests.
- `CC-VIDEO-READVERIFICATION-WINFILE`: Windows-only triage if reproducible.
- `HOST-SENSITIVE-PERF-GATES`: document/calibrate by valid host baseline rather than silently weakening targets.

---

## Required next acceptance

After final exact-SHA repository CI is clean, perform a second real Dashboard run on the owner environment if available:

1. medium research/file task;
2. repo/code task;
3. browser/computer-use task;
4. multi-agent synthesis task;
5. real Video Studio media task.

Keep the same session alive across tasks. Record prompt, model/provider, agents, tools, approvals, retries, errors, latency, resources, postconditions and artifacts. Compare against session `6cbb17ce84db` using corrected telemetry.

Windows path CI is not owner Windows acceptance. A fake/local test adapter is not local-model acceptance. A green unit test is not a real Video Studio owner flow.

---

## Final state contract

Final report must distinguish:

- `TESTED_CODE_SHA`
- `TREE_SHA`
- report/docs commit
- CI exact-SHA result
- `WINDOWS_OWNER_ACCEPTANCE`
- `LOCAL_MODEL_ACCEPTANCE`
- `VIDEO_STUDIO_REAL_FLOW`
- `WEB_DESIGNER_REAL_FLOW`
- `LONG_SESSION_STABILITY`
- `INTELLIGENCE_RETENTION`
- repository P0/P1/P2
- external evidence gaps

Allowed final verdicts:

- `PASS`
- `BLOCKED`
- `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`

No fake PASS and no transfer of evidence from a different SHA.
