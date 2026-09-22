# Bossman 1.0 — repair ledger (append-only)

Actor legend: **teacher** = repair engineer (Claude Opus 5), **student** = local Bossman,
**verifier** = executable tests / separate context, **auditor** = prior audits (glm53, Aster).

Baseline tested SHA: `0c3e22ffd44c9b2c3e4f90b2e86456c28ca43f39` (release/bossman-owner).
Baseline installed artifact: `C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-0c3e22ffd44c`
(archive SHA-256 per audit: `437fadccd90330ce8e8acb21474adc7b33a54454d21ef72d1ccfd89f1b1f89d6`).
Worktree: `C:\Users\asd\Bossman\wt-release` (own worktree, worktree-scoped git identity).
Lease: `C:\Users\asd\Bossman\handoff\LEASE.json` (desktop UI, backend restart, GPU, model servers).

Models (verified on endpoints 2026-09-21): MAIN `qwen3.8-27b` Q5_K_M @ 127.0.0.1:8081,
FAST `qwen3.6-35b-a3b` Q5_K_M @ 127.0.0.1:8082, llama.cpp b10964 Vulkan, ctx 32768.

---

## 2026-09-21T19:40Z — teacher — read evidence
- Read ver2 FINAL-AUDIT, DEFECT-LEDGER, OWNER-SCENARIO-SCORECARD, CP-03..CP-10, CP-04C, Aster FINAL.
- Confirmed scope: P1 B4; P2 AP-ALL, TEL-001. Disproved (not repaired): approval restart loss,
  old memory P1, two-model thrashing.

## 2026-09-21T20:05Z — teacher — B4 browser download (P1) — FIXED
- Root cause: `browser_control.navigate()` let Playwright "Download is starting" escape goto → 500.
- Fix: per-page download listener; navigate/click/download claim their download, policy check,
  `.part` → size/sha256/type → unique final name; quarantine for executables; honest 422 on
  failure/interrupt/timeout/empty/oversize; `browser.download` bus event; agent `browser.download`
  tool is ASK even with `browser.control`.
- Regression: `command-center/tests/test_browser_download_b4.py` (4 tests, real Chromium):
  HTML, direct PDF, attachment, redirect→download, TXT, ZIP, click, explicit download, duplicate
  names, interrupted, stalled timeout, stop mid-download, traversal/reserved name, exe quarantine,
  agent approval + one-shot replay. Result: PASS.
- Commit: `79a12276`.

## 2026-09-21T20:12Z — teacher — AP-ALL approvals filter (P2) — FIXED
- `status=all`/empty = all states; comma lists; unknown → 422.
- Regression: `test_approvals_status_filter_apall.py` (restart, all states, anti-replay). PASS.
  Adjacent approval suites 65 passed.
- Commit: `84cbd788`.

## 2026-09-21T20:25Z — teacher — TEL-001 model speed telemetry (P2) — FIXED
- Server `timings` → TTFT/prefill/generation; differential fallback; null otherwise.
- Live regression (evidence/tel001-live.json): MAIN Bossman 10.22 vs native 10.28/10.31 tok/s
  (err 0.7%); FAST 50.11 vs 48.89/50.23 (err 1.1%); differential cross-check within 1.3%.
  Old formula on the same run: 8.88 / 40.76; on the old short prompt: 1.6 / 3.3.
- Commit: `8fe29925`.

## 2026-09-21T20:40Z — teacher — Computer Use provider gap — FIXED (wiring defect, class B)
- Classification: not an auditor-tool gap (A) nor Windows session (C): the owner product had no
  desktop tools; bossman-core operator was not wired into Command Center.
- Fix: `bcc/features/tools_computer.py` reusing shipped bossman-core adapters (see commit).
- Live run defects found and fixed on the way (all on the real desktop, TEST-ONLY files):
  1. Keystrokes went to Windows Settings search: Windows kept focus away from launched Notepad
     → launch focuses the new window; input refused when foreground ≠ observed window.
  2. RU keyboard layout: pyautogui key map empty for Latin letters → ctrl+v/ctrl+a/ctrl+s pressed
     only Ctrl; "stop-test" arrived as "- - -" → VK-code hotkeys; layout-aware typing via
     clipboard chunks with STOP checks.
  3. Save As "Имя файла:" focus hit the ComboBox wrapper → focus the editable control and confirm
     keyboard focus; `replace` option.
  4. Win11 Notepad keeps focus on frame after window focus → single editable field auto-focused.
- Side effect cleaned: one test file saved under the default name in `Documents`
  ("ИТОГ указание изменено владельцем,.txt", 83 bytes, own test text) — removed. Settings/Store
  windows opened by the first run — closed.
- Live evidence: `evidence/computer-use-live-notepad.json` — 12/12 PASS (launch+focus, Cyrillic
  typing verified, STOP at 448/6000 chars, STOP blocks actions, resume re-observe, mid-flight
  instruction change, Save As to disk with content check, rename prepared then cancelled,
  coordinate fallback refusal + click after fresh observation).
- Known limitation kept (security, P2 note): bossman-core policy refuses typing text containing
  "bossman"/"approve"/"confirm action"/"emergency" (anti self-approval via chat windows). Paths
  inside `C:\Users\asd\Bossman\...` therefore cannot be typed by the agent.
- Commit: `9cf8fe4c`.

## 2026-09-21T20:58Z — teacher — Higgsfield / local media engine — WIP
- Finding: no Higgsfield fork in repo; `wide-trace/open-higgsfield` has no license → REFERENCE_ONLY,
  not vendored. Studio had only cloud video (disabled) + ComfyUI text→image; Video Factory default
  "generation" = ffmpeg testsrc.
- Added `sdcpp` Studio provider (commit 6384c8f0): Wan2.2 TI2V-5B (video, T2V+I2V) and Z-Image-Turbo
  (image), Apache-2.0, pinned HF revisions; downloads ≈ 25 GB into `Bossman\models\media`.
- Live engine: 832x480/17 frames/20 steps → correct prompt-matched clip, 293 s, sha256 33a95853…;
  480x288/10 steps with/without flash-attn → noise (cause under isolation; flags disabled).
- Product-path proof, manifest hashes, I2V, cancel-in-product: NOT DONE yet.

## 2026-09-21T21:00Z — teacher — checkpoint push on owner request
- See CONTINUATION.md. Full-suite triage and exact-SHA CI pending. Not 1.0.

---

## 2026-09-21 — integration session II (GitHub-only) — teacher

Context switched from audit to integration on owner request. Five fixes pushed to
release/bossman-owner, each small, tested, and non-weakening.

- **cb7aefe7 — CI obligatory root lane green.** tests/test_studio_catalog.py hard-coded
  6 models / a summary literal / models[-1]=higgsfield; the sd.cpp provider (6384c8f0)
  grew the catalog to 8. Bumped 6→8, selected higgsfield by provider identity, replaced
  the blanket `all(price.usd is None)` with a teeth-keeping invariant (no committed
  non-zero charge; usd==0 only when free) — negative control: usd=5 and usd=0/free=False
  both still rejected. Regenerated docs/testing/SKIPS_REGISTRY.md (entries=239,
  without_reason=0). Also unblocks browser-user-paths.
- **59daf7c3 — Computer Use CU-VERIFY + CU-APPROVAL.** verify() returned verified=True
  for an unknown non-empty expect with zero checks — now empty→None, unknown/mistyped→
  False, True only when a known condition ran. Approval was stamped from the model's
  `semantic` field while the ASK fired only on declared_consequence(semantic): a benign
  semantic="click" on target="Delete account" executed without an owner ASK. New
  ComputerPolicy.ask_consequence (declared OR named target/text label) drives the ASK;
  _t_act no longer derives approval from a model field; act() takes a trusted `approved`
  param and refuses a foreground-only consequence until it is named. 9 new regressions,
  57 existing computer tests unchanged.
- **def1a714 — F-17 UX-settle oracle.** #view stamped data-rendered=<page id> only after
  the target content is in place; oracle waits for it. Probe over 36 pages: old condition
  raced on stale content 33/36, new oracle accepted a bad state 0/36. Makes the obligatory
  Command Center CI monkey test deterministic (it had reddened six candidates today).
- **26ef7f76 — sd.cpp MEDIA-HASH + MEDIA-CANCEL.** engine_files verified size only and
  provenance recorded the manifest sha as observed; now _verified_sha hashes each file
  once (cached by path,size,mtime) and records expected+observed as distinct fields. _run
  no longer spawns after cancel and kills the process tree; the read loop is deadline-bounded.
  2 new regressions. MEDIA-RESTART (in-memory _jobs) left OPEN — needs the durable store.

Full-suite triage: the local 62-fail run lacked ffmpeg; with ffmpeg+Chromium 67/69 pass,
the 2 residuals being F-17 (fixed) and the missing `mcp` extra (installs and passes).
Zero real product regressions.

---

## 2026-09-21T21:30Z — integrator (Claude Fable 5.1, GitHub-only) — resume from 9c3369b
- Mode: repository + isolated engineering container + GitHub Actions only. No owner machine access.
- Branch: `claude/bossman-1-0-rc-owner-ready-cfesui` (harness-designated), based on release/bossman-owner @ 9c3369b;
  PR #71 → release/bossman-owner (draft). No force-push, no history rewrite.
- Read: CONTINUATION, ledger, cc-full-suite-failures, tel001-live, computer-use-live-notepad, INSTALL,
  OWNER_ACCEPTANCE, KNOWN_LIMITATIONS, exact_sha_certify, release_candidate contract.
- Remote CI on 9c3369b (measured): root-ci FAILED (tests/test_studio_catalog.py: 6→8 models after the sd.cpp commit),
  Editors user safety FAILED (skips registry drift), Core/CC CI cancelled (superseded), three path-filtered
  Windows/owner jobs MISSING. Baseline 0c3e22ff: CC CI red only on py3.14 `test_real_chromium_app_window…` (CDP timeout).

## 2026-09-21T22:00Z — teacher — CU-VERIFY/APPROVAL/STOP/TARGET/PATH — FIXED (b8134d5)
- CU-APPROVAL reproduced: `semantic="noop"` (AUTO in the engine) + observed "Удалить" target executed without
  approval because `_t_act` set `_approved_consequence=True` for any truthy semantic; forged
  `_approved_consequence` in args also accepted. Fix: ToolContext.approval_id (engine) is the only source;
  bound to the consequence kind; re-classified on a fresh screen before the effect.
- CU-VERIFY: unknown/typed-wrong/short expect → invalid (False), not verified; file_exists/file_contains read
  the disk with the action start time.
- CU-STOP: STOP after lock and before effect; persisted `data_dir/computer/STOP`; Resume bumps generation;
  adapter calls bounded (outcome unknown → re-observe). Owner buttons on the Пульт page (real Chromium test).
- CU-TARGET: launch window attributed to launched pid / allowlisted exe; identical names need `index`;
  MAX_OBS_AGE_S; process-unique generation; self-reported confidence ignored.
- CU-PATH: policy surface by window identity (Bossman/UAC/Windows Security/credential dialogs) and sensitive
  target names with unknown identity; typed text no longer blacklisted. Negative controls kept
  (bossman-core red-team suite green, 467 operator tests).
- Regressions: command-center/tests/test_computer_use_tools.py (27), test_owner_control_ui.py (+1).

## 2026-09-21T22:10Z — teacher — coaching/learning loop — FIXED (deb9470)
- learning/lessons.py on the canonical LearningStore; tools/coaching_runner.py with 5 train + 5 holdout.
- Status: COACHING_PIPELINE_TESTED (MOCK backend in CI), LOCAL_LEARNING_GAIN_NOT_MEASURED, WEIGHTS_UNCHANGED.

## 2026-09-21T22:20Z — teacher — release tooling (fe56874)
- Owner-Run.cmd / Media-Setup.cmd / Coaching.cmd / Collect-Diagnostics.cmd; owner_run_tomorrow.py;
  doctor `model-endpoints` (llama-server :8081/:8082) and `media-engine`; START_TOMORROW_RU.md; ROLLBACK_RU.md.

## 2026-09-21T22:40Z — verifier (separate context) — full-suite triage (4e74a23)
- owner-repair/full-suite-triage.md: 69 ids, baseline vs HEAD on Linux with ffmpeg+Chromium; REGRESSION 0;
  HARNESS_CONFIG 45 (32 = ffmpeg off PATH), PREEXISTING_SOFTWARE 4 (3 root causes fixed: apps process identity
  in a Windows venv, runtime_lock liveness probe on Windows, Studio→Web positional read), UNSUPPORTED_PLATFORM 3,
  UNRESOLVED 12 (need the Windows traceback — rerun tomorrow with `PATH=<app>\media;%PATH%`).
- Full CC suite on Linux/HEAD (ffmpeg+Chromium, 37 min): 8 failed / 3941 passed / 37 skipped — the run overlapped
  concurrent edits; reruns recorded in CONTINUATION.

## 2026-09-21T22:50Z — teacher (media context) — sd.cpp hardening (c16ba1b, fcaf2ad)
- MEDIA-HASH/CANCEL/RESTART/INPUT-OUTPUT/PRESET closed with 68 hostile MOCK_ENGINE tests + 27 bootstrap tests.
- Integrator fix on top: `asyncio.shield` in cancel() replaced by `bcc.single_flight.await_shared` (BL-098 guard
  test `test_no_module_went_back_to_asyncio_shield`).
- Reproduced hazard: the HEAD `readline()` pump hung on a 6 MiB stdout line and the MOCK child outlived
  pytest-timeout — exactly MEDIA-CANCEL. Fixed by the chunked bounded pump.
- Not done today: real generation, I2V, noise-cause isolation (A/B plan prepared: app-support/media_ab_preset.py).


---

## 2026-09-22 — integrator (GitHub-only) — merge of canonical `release/bossman-owner` (0c1cbe6) into FINAL_BRANCH
- Canonical line moved 11 commits (cb7aefe…0c1cbe6) while candidates 1–4 were certifying. Nothing rolled back;
  the delta was ported by meaning, newer + stricter wins, conflicts resolved explicitly:
  - **CU-APPROVAL**: their `ComputerPolicy.ask_consequence` (declared semantic OR named target/text) now drives both
    the effect-hook ASK and the approved-kind binding on this line (`approved_consequence`), on top of the
    engine-provided `ctx.approval_id` (this line). A benign `semantic="click"` on «Delete account» → ASK; a model
    field alone still never counts as approval. Both regression files pass (theirs: 9; this line: 27 + policy 8).
  - **CU-VERIFY**: this line's stricter `verify()` (unknown/typed-wrong/short expect → False; `file_exists`/
    `file_contains` read the disk) satisfies their regression file unchanged.
  - **sd.cpp**: this line's provider kept (full sha256 with persisted HashCache, sidecars, orphan reconciliation,
    atomic verified output, `await_shared` cancel). Their `test_sdcpp_provider_safety.py` ported: `_VERIFIED` is now the
    in-process memo the HashCache uses when no cache dir is given; the fixture declares all required roles and a
    (unpinned) engine binary file, because this line refuses a manifest lacking `vae`/`text_encoder`.
    MEDIA-RESTART (their OPEN item) is closed on this line by durable sidecars + `reconcile_orphans`.
  - **F-17 oracle**: unified on their `#view[data-rendered]` marker (also stamped on the error page); this line's
    `previous`-node-gone check retained in the torture oracle.
  - **Catalog test**: theirs (identity-based higgsfield selection + price invariant with teeth).
  - **README**: this line's text, scorecard block regenerated from their corrected `docs/benchmark/current-scorecard.json`.
  - **START_TOMORROW_RU**: both kept — root file is the shipped owner protocol (Owner-Run/Media-Setup/Coaching);
    `owner-repair/START_TOMORROW_RU.md` (theirs) cross-references it.
  - Their `docs/evo/*`, scorecard files, `test_computer_operator_ask_consequence.py` merged clean.
- Candidate re-declared (5) so the merged SHA gets the full ten-lane matrix.

---

## 2026-09-22 — final integrator (owner machine, `integrate/owner-final-20260922`) — media, Telegram console, lifecycle

Base: `feat/telegram-local-llm-20260922` @ fc266856 (= release/bossman-owner ⊂ RC candidate 6 ⊂ video presets ⊂
Telegram line; one line, no back-merge needed). Lease taken from a session that no longer exists; the previous
LEASE.json is preserved as `handoff/LEASE.prev-20260922.json`.

### Product defects closed (reproduce → regression → fix → neighbours)
| ID | What | Class | Evidence |
|---|---|---|---|
| MEDIA-LIFECYCLE | The engine outlived a dead backend (live case: sd-cli pid 1644 kept 38 GB for 40 min while its Studio job was already `failed`). Engines are now bound to the owner's Windows Job Object; hosts without job objects degrade honestly. | PRODUCT_CODE | `test_studio_media_lifecycle.py` |
| CU-UNKNOWN-RESTART | `outcome_unknown` lived only in memory: after a backend restart the next action ran without re-reading the screen. Now persisted next to STOP and lifted only by a fresh observation. STOP itself already survived restart (negative control kept). | PRODUCT_CODE | `test_owner_stop_lifecycle.py` |
| DEADLINE-CEILING | The work-proportional deadline (d059ac60) had no absolute ceiling: catalogue maxima gave ~17 h per segment and >4 days for a 30 s chain; invalid values raised `TypeError` and `inf` gave an infinite budget. Two configurable ceilings (12 h segment / 24 h job) above the owner's real heavy run; the Studio watchdog now reads the same budget. | PRODUCT_CODE | `test_studio_deadline_limits.py` (21 cases, 11 red before) |
| MEDIA-PARTIAL | Cancel and timeout deleted every finished segment of a chain. A stop now keeps the verified segments, concatenated and marked partial (n of m, real duration, reason); nothing finished → nothing written, and a partial result is never reported as complete. | PRODUCT_CODE | `test_studio_partial_segments.py` |
| FFMPEG-PATHEXT | Provenance named the binary from PATHEXT (`ffmpeg.EXE`) instead of the filesystem entry, so the same binary appeared under two names depending on the host registry. | PRODUCT_CODE | `test_ffmpeg_is_named_by_the_filesystem_not_by_pathext` |
| SETUP-RESET | The local setup form refused a POST (bad Origin/token/Host) without draining the body; Windows reset the connection, so the owner's browser saw a broken connection instead of the 403. This was the cause of a 1-in-5 flake present on the certified RC too. | PRODUCT_CODE | `test_setup_ui_drain.py`; the old flaky test now 8/8 |

### Telegram: one execution path (owner requirement)
The direct path added earlier the same day (`/sh`, `/claude` via the Claude Code CLI in bypass mode, local
screenshotter, `pc_control.py`) is **removed**, for the owner as well. The console is Telegram → owner identity →
Bossman task → policy/approval → executor → verification: Russian button menu (status, queue, new task, approve /
reject, allowed screenshot via `/api/computer/observe`, open app/folder, project files, photo, video/TestRun,
diagnostics, lessons, propose a fix, pause, STOP, resume). A chat decision opens a one-shot gate bound to
owner+chat+approval id+kind+argument digest, TTL 180 s, nonce; approval is re-read and re-checked against a fresh
observation before the effect. Bot-token rotation and revocation live in Bossman settings, never in the chat.

### Media models
`sdcpp:flux2-klein-4b` added (Apache-2.0 in every component, ungated): live on this machine 1024×1024 / 4 steps /
69.8 s, output matches the prompt. Files pinned by HF revision and verified against Hugging Face LFS ids
(expected-from-source, not self-hashing). Baselines Wan2.2 TI2V-5B and Z-Image-Turbo untouched as the rollback path.
Candidate matrix with licences, sizes and support status: `docs/media/MODEL_CANDIDATE_MATRIX.md`
(LTX-2.5 = OWNER_REQUIRED_LICENSE; Qwen-Image-2.1 excluded from the commercial profile; MiniMax-H3 DO_NOT_DEPLOY).

### Gates on this line
`telegram_contracts` + `test_telegram_settings` + `test_studio_*` + `test_owner_stop_lifecycle`:
**429 passed, 0 failed** with `app/BOSSMAN-Windows-x64-0c1cbe651f52/media` on PATH (ffmpeg/ffprobe).
Without ffmpeg on PATH seven Studio tests fail for lack of a skip-guard: ENVIRONMENT/HARNESS, not a product defect.

---

## 2026-09-22 — teacher (CLOUD_PREPARE, Linux; Windows simulated) — residual defects a–f on d6e25fb4

Windows paths below are reproduced on Linux by substituting the Windows seam (platform flag, command runner,
code page); **Windows itself is unverified** until Windows CI / the owner's machine reruns them.

| Item | What | Status | Evidence |
|---|---|---|---|
| a / OS-105 | Social Farm account-context directories on Windows: `prepare()` only called `os.chmod` (NTFS: toggles read-only, ACL untouched → session dir stays open to whatever the inherited ACL allows, e.g. `BUILTIN\Users:(RX)`), and `assert_private()` compared `st_mode` bits that NTFS always reports as 0o777 → the owner's own correctly-closed directory was refused, an open one could not be detected. Now on `nt`: owner-only inheritable DACL via icacls (`/inheritance:r /grant:r DOMAIN\user:(OI)(CI)F`, same argv family as `bossman_shared/evidence.py::restrict_to_owner`, twinned because social-farm has `dependencies = []`), applied to the root and the account dir BEFORE the marker is written; `assert_private` reads `icacls <dir>` (decoded with the OEM code page) and refuses any allow-ACE for another principal, an unreadable ACL, a failed/missing icacls, or an unknown owner. POSIX path unchanged. | FIXED (Windows unverified) | `apps/social-farm/tests/unit/test_browser_isolation_windows_acl.py` 7 tests: red before (owner-only dir refused with "права 0o777", open ACL `DID NOT RAISE`, no icacls call, no warning), green after; negative controls: other-user ACE, never-narrowed inherited ACL, failed icacls, missing icacls, unknown owner. Neighbours: social-farm `440 passed, 13 skipped`; `tests/owner_scenarios` `31 passed`. Residual: `scn_23` OS-105 still asserts `S_IMODE == 0o700`, which is meaningless on NTFS — harness-only, left untouched. |
