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
