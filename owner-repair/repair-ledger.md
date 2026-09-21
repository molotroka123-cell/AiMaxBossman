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
