# OWNER ACCEPTANCE — end of day 2026-09-23 (consolidated)

Built only from existing documents. No tests were run to write it. Sources:
`../../OR0923-bd2fe23d/` {TR_MATRIX, HW_MATRIX, UX_CLI_PARITY, BUGS, REPORT_RU, CHECKPOINT (1–5), GUI_VS_CLI_MEASUREMENTS.json} and `../` {REPORT_RU, CONTINUE}.
Below, `OR/` means `OR0923-bd2fe23d/`.

**Current head:** `f72af6a8` (`fix/owner-run-20260923-p1`, RC0-pre, not certified). **No live owner result exists for f72af6a8.** Each row shows the SHA it was measured on:

| Label | SHA | What it is |
|---|---|---|
| SHA1 | `bd2fe23d` | pre-fix baseline, morning run |
| SHA2 | `d53f3b12` | + chat-context, CRLF and sidecar-truncation fixes |
| SHA3 | `cdb4b09d` | + NO_PROGRESS detector; the afternoon live candidate |
| SHA4 | `12612c81` | + CLI parity slash commands (`/doctor`, `/compact` …) |

A PASS on an older SHA does not count as a PASS on f72af6a8. The only f72af6a8 results are unit and regression suites: command-center 233 pass; bossman-core apprentice + security 213 pass / 18 skip. These are not live acceptance.

Source discrepancy: `FINAL-1.0/REPORT_RU.md` says «TR-01…22 на SHA4 — из утреннего прогона». This file does not use that label. `OR/TR_MATRIX.md` shows TR was measured on SHA1, SHA2 and SHA3. SHA4 was used only for the CLI slash-command smoke.

Statuses:

| Status | Meaning |
|---|---|
| PASS | Verified effect on the SHA shown |
| FAIL | Product defect observed and still open, or the result was not met |
| BLOCKED | Environment or owner action required |
| NOT_RUN | Not executed |
| FIXED_NEEDS_RETEST | A failure was observed; code is fixed on f72af6a8 (unit tests only), but it has not been re-run live |

"PARTIAL" rows from the source matrices are split into sub-rows so that each row has exactly one status.

---

## 1. HW — owner hardware (HW-01…HW-13)

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| HW-01 local model and routing (Ollama substituted for llama.cpp because of SAC) | PASS | bd2fe23d | OR/HW_MATRIX.md; OR/RUNTIME_IDENTITY.json; CHECKPOINT 1 |
| HW-02 Windows control: computer STOP/resume/persistence | PASS | bd2fe23d → d53f3b12 (STOP survived upgrade) | OR/cli c43–c46; CHECKPOINT 3 |
| HW-02 Notepad task via agent (computer.* hidden by action contract; APP-CONTRACT-OVERRIDES-AGENT-TOOLS) | FIXED_NEEDS_RETEST | failed on d53f3b12 (tasks 31–33); fix `aa6bee3d` is in f72af6a8 | OR/BUGS.md; FINAL-1.0/REPORT_RU.md §4 |
| HW-03 browser PDF download after approval (13 264 B, sha256 3df79d34…adb4; deny → no 2nd file) | PASS | bd2fe23d (tasks 18/19 era) | OR/HW_MATRIX.md; CHECKPOINT 3 |
| HW-03 «Скачай PDF-файл …» false PASS (DOWNLOAD-FALSE-SUCCESS) | FIXED_NEEDS_RETEST | failed on bd2fe23d (task 18); fix `c0a7e039` is in f72af6a8 | OR/BUGS.md; FINAL-1.0/REPORT_RU.md §4 |
| HW-03 re-download loop after verify FAIL (DOWNLOAD-VERIFY-LOOP, P2) | FAIL | bd2fe23d (task 19) | OR/BUGS.md |
| HW-03 login handover | NOT_RUN | — | OR/HW_MATRIX.md |
| HW-04 files: Web Designer save/reopen/versions/rollback | PASS | bd2fe23d | CHECKPOINT 2; OR/screens s27–s35 |
| HW-04 chat agents have no file tools (AGENT-TOOLS-NO-UI, P2) | FAIL | bd2fe23d | OR/BUGS.md |
| HW-05 restart/resume (×6; tasks, approvals and STOP persist) | PASS | bd2fe23d → cdb4b09d | OR/HW_MATRIX.md; CHECKPOINT 3–5 |
| HW-05 window-close lingering backend (WINDOW-CLOSE-LINGERING-BACKEND, P2) | FAIL | d53f3b12 (PID 9248, ~8 min) | OR/BUGS.md; CHECKPOINT 3 |
| HW-06 Telegram product path | BLOCKED | — (OWNER_ACTION_REQUIRED: one poller per token; test companion config prepared, not run) | OR/HW_MATRIX.md; FINAL-1.0/CONTINUE.md §7 |
| HW-07 Image Studio via sd.cpp (768×768 PNG, decode, provenance) | PASS | cdb4b09d | OR/artifacts/media-image-job.json; CHECKPOINT 4 |
| HW-07 Image Studio via ComfyUI | BLOCKED | cdb4b09d (ENVIRONMENT: SAC blocks scipy) | CHECKPOINT 4 |
| HW-08 Video Studio (Wan2.2 test_1s) | PASS | cdb4b09d | OR/artifacts/media-video-*.json; CHECKPOINT 5 |
| HW-09 planner → executor → verifier via coding path | PASS | cdb4b09d | OR/learning/record-D1-L4-cdb4b09d.json |
| HW-09 multi-agent team page | NOT_RUN | — | OR/HW_MATRIX.md |
| HW-10 MVČR (synthetic data, live official sources, PARTIAL_MISSING_DATA, nothing submitted) | PASS | bd2fe23d | OR/artifacts/mvcr-live-synthetic; CHECKPOINT 2 |
| HW-11 unseen task D2 (STUDENT_UNASSISTED_PASS) | PASS | cdb4b09d | OR/learning/record-D2-L0-cdb4b09d-RAW.json |
| HW-12 long autonomy soak 24h/48h | NOT_RUN | — | OR/HW_MATRIX.md |
| HW-13 cloud route (local-first, no silent paid fallback, $3/day cap enforced) | PASS | d53f3b12 → cdb4b09d | OR/cli c47–c49, c68; CHECKPOINT 4 |
| HW-13 premium approval | NOT_RUN | — | OR/HW_MATRIX.md |
| HW-13 spend meter off by default (SPEND-METER-OFF, P2) | FAIL | bd2fe23d | OR/BUGS.md |

## 2. TR-01…TR-22 — Terminal Run 1.2

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| TR-01 clean ZIP, path with spaces, no checkout/system Python | PASS | bd2fe23d (ZIPs for SHA2/SHA3/SHA4 also bundle-acceptance PASS) | OR/artifacts/bundle-acceptance*.json; OR/cli c01–c11 |
| TR-02 Windows Terminal (Cyrillic, colors, close mid-task, shortcut) | PASS | bd2fe23d | OR/screens t15, t31, t32; OR/cli c41–c42 |
| TR-03 old CLI help/exit contracts | PASS | bd2fe23d | OR/cli c10, c11 |
| TR-04 Cyrillic/paths/UTF-8 (paste P2 → CLI table) | PASS | bd2fe23d | OR/screens t04–t14; OR/cli c13 |
| TR-05 shared instance (same build/data root/started_at) | PASS | bd2fe23d | OR/cli c04, c12, c32 |
| TR-06 shared files: CLI file write into a project | FAIL | bd2fe23d (no apply; controlled apply is in section 10) | OR/BUGS.md AGENT-TOOLS-NO-UI; OR/screens s28/s29 |
| TR-07 shared memory: write via CLI, recall after restart | PASS | d53f3b12 | OR/cli c71–c77; OR/screens s43 |
| TR-08 real chat, multi-turn context (CHAT-CONTEXT-REQUEST) | FAIL | bd2fe23d (tasks 10–12) | OR/BUGS.md; OR/screens t04–t14 |
| TR-08 real chat after fix | PASS | d53f3b12 (task 22); cdb4b09d (tasks 35/36) | CHECKPOINT 3, 4 |
| TR-09 structured exec (JSONL, no ANSI, no token) | PASS | bd2fe23d | OR/cli/commands.jsonl |
| TR-10 replay/reconnect, idempotent request_id | PASS | bd2fe23d | OR/cli c15–c23 |
| TR-10 two-phase submit draft (TWO-PHASE-SUBMIT-DRAFT, P2) | FAIL | bd2fe23d | OR/cli c21/c22; OR/BUGS.md |
| TR-11 close/detach, task survives | PASS | bd2fe23d | OR/cli c23–c25, c41–c42 |
| TR-12 STOP/cancel/resume from CLI/UI | PASS | bd2fe23d → d53f3b12 | OR/cli c27–c31, c43–c46 |
| TR-12 STOP from Telegram | NOT_RUN | — | OR/TR_MATRIX.md; FINAL-1.0/REPORT_RU.md |
| TR-13 approvals: teacher cannot approve; UI↔CLI approve/deny | PASS | bd2fe23d → d53f3b12 | OR/cli c37–c40; OR/screens s37, s38, s44/s45 |
| TR-13 concurrent two-channel approve | NOT_RUN | — | OR/TR_MATRIX.md |
| TR-14 terminal injection (ESC/OSC52/BEL/RLO/CR) | PASS | bd2fe23d | OR/cli c34–c36 |
| TR-15 file boundaries: scope/protected paths refused by coding path | PASS | bd2fe23d (product tests plus scope errors in records; no live CLI evidence id) | OR/TR_MATRIX.md |
| TR-15 junction/reparse point and mid-write change | NOT_RUN | — | OR/TR_MATRIX.md |
| TR-16 coding: CLI → API → sidecar → REAL_MODEL handshake | PASS | bd2fe23d → cdb4b09d | OR/cli c50–c56; OR/learning/ |
| TR-16 coding on full repo (CODING-SNAPSHOT-32MB) | FIXED_NEEDS_RETEST | failed on bd2fe23d (c51); fix `f72af6a8` | OR/BUGS.md; FINAL-1.0/REPORT_RU.md §4 |
| TR-16 evolution backend campaign | NOT_RUN | — | OR/TR_MATRIX.md |
| TR-17 browser: real PDF download | PASS | bd2fe23d | OR/cli c57–c66 |
| TR-17 Computer Use via agent | FIXED_NEEDS_RETEST | failed on d53f3b12 (tasks 31–33); fix `aa6bee3d` | OR/BUGS.md |
| TR-18 media from the terminal | NOT_RUN | — (media was done through UI/API; see section 13) | OR/TR_MATRIX.md |
| TR-19 all capabilities: `list tools` 98 tools with effect policy | PASS | bd2fe23d | OR/cli c08 |
| TR-19 PARITY_MATRIX/TERMINAL.md stale (DOC-STALE, P2) | FAIL | bd2fe23d | OR/BUGS.md |
| TR-20 no model/network: local fallback, no silent paid fallback | PASS | bd2fe23d → d53f3b12 | OR/cli c47–c49, c68 |
| TR-21 performance: GUI vs CLI overhead (pilot) | PASS | d53f3b12 (n=3); cdb4b09d (A/C) | OR/GUI_VS_CLI_MEASUREMENTS.json |
| TR-21 idle-client CPU | NOT_RUN | — | OR/TR_MATRIX.md |
| TR-22 teacher and memory process (hint levels, lesson, transfer logged) | PASS | bd2fe23d → cdb4b09d | OR/learning/COACHING_EPISODES.jsonl |

## 3. CLI (interactive terminal UX)

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| ConHost + WT: Cyrillic, Tab completion, Ctrl+C cancel confirmed by backend, right-click multi-line paste not run line by line, resume | PASS | bd2fe23d | OR/screens t01–t15; CHECKPOINT 1 |
| Desktop shortcut «Bossman CMD» (WT, non-admin) | PASS | cdb4b09d (later used for SHA4) | OR/screens t31/t32 |
| SHA4 slash commands /doctor /context /cost /compact /export live | PASS | 12612c81 | CHECKPOINT 5; OR/artifacts/bundle-acceptance-SHA4-12612c81.json |
| /compact on a short conversation grows context 220 → 2037 chars (P2) | FAIL | 12612c81 | CHECKPOINT 5 |
| CONHOST-PASTE: Ctrl+V pastes nothing; Shift+Insert inserts `[2;2~` (P2) | FAIL | bd2fe23d | OR/screens t08–t10; OR/BUGS.md |
| TERMINATE-BATCH-PROMPT after Ctrl+C then /exit (P2) | FAIL | bd2fe23d | OR/BUGS.md |
| CHAT-PROMPT-LOOKS-LIKE-CMD (P2) | FAIL | bd2fe23d | OR/BUGS.md |
| EVAL-NOISE: 10× «проверка: NOT_APPLICABLE» (P2) | FAIL | bd2fe23d | OR/BUGS.md |
| RESUME-AGENT-LOST: resume uses the default agent (P2) | FAIL | bd2fe23d | OR/screens t14; OR/BUGS.md |
| CMD-CRCRLF: .cmd files in the ZIP end with CR CR LF (P2) | FAIL | bd2fe23d | OR/BUGS.md |

## 4. UI ↔ CLI parity

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Models: UI → CLI `list models` | PASS | bd2fe23d | OR/UX_CLI_PARITY.md; s02–s05, s12, c05 |
| Agents: UI → CLI; CLI tasks run under UI agents | PASS | bd2fe23d | s08/c12, s19 |
| Tasks: both directions, same status | PASS | bd2fe23d | s20, s21, c32, c33 |
| Approvals: both directions («✓ разрешение #13: одобрено (ui)») | PASS | d53f3b12 | c37–c40, s22, t20–t21 |
| STOP: both directions, persists after restart | PASS | bd2fe23d → d53f3b12 | c28–c31, c43–c46, s23 |
| Coding tasks: UI form ↔ `bossman code`, with diff | PASS | cdb4b09d | s28/s29; OR/gui_cli_sha3 |
| Memory: configured in UI, written via CLI, recalled after restart | PASS | d53f3b12 | s43, c71–c77 |
| Files in a project (no CLI write path; UI agents have no tools) | FAIL | bd2fe23d | OR/BUGS.md AGENT-TOOLS-NO-UI |
| Studio sd.cpp models missing from the UI selector (API only, P2) | FAIL | cdb4b09d | CHECKPOINT 4 |
| MODEL-DELETE-ORPHANS-AGENT (P2) | FAIL | bd2fe23d | OR/BUGS.md |
| TG-SETTINGS-NOT-ISOLATED (P2) | FAIL | d53f3b12 | OR/BUGS.md; CHECKPOINT 3 |
| Telegram ↔ UI/CLI | BLOCKED | — (OWNER_ACTION_REQUIRED) | OR/UX_CLI_PARITY.md |
| GUI vs CLI pilot: short task, n=3 (MEASURED_OVERHEAD_REDUCTION) | PASS | d53f3b12 | OR/GUI_VS_CLI_MEASUREMENTS.json `sha2_pilot` |
| GUI vs CLI A, memory task: 4.12 → 1.82 s | PASS | cdb4b09d | OR/GUI_VS_CLI_MEASUREMENTS.json `sha3` |
| GUI vs CLI C, browser: 6.54 → 3.44 s | PASS | cdb4b09d | same |
| GUI vs CLI B, coding: wall clock 21.8 vs 26.9 s (NO_MEASURED_GAIN; 17× fewer teacher actions) | FAIL | cdb4b09d | same |

## 5. Memory

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Fresh data root: memory unconfigured, memory/stats 503 (MEMORY-NOT-CONFIGURED, P2) | FAIL | d53f3b12 | OR/BUGS.md; CHECKPOINT 3 |
| memory.fact.add approved → fact#1 stored | PASS | d53f3b12 | CHECKPOINT 3; c71–c77 |
| Task 23 false FAIL after a successful retry (EFFECT-RETRY-FALSE-FAIL, P2) | FAIL | d53f3b12 | OR/BUGS.md |
| Recall after backend restart (task 24 «Бероунка») | PASS | d53f3b12 | CHECKPOINT 3; s43 |
| «Запомни» → question: no new approval (tasks 35/36) | PASS | cdb4b09d | CHECKPOINT 4 |
| Lesson `coach-lesson:29a7424bd33b115b` survives full restart and is retrieved on D2 | PASS | cdb4b09d | OR/learning/LESSON_SAVED.json; record-D2-L0-cdb4b09d-LESSON.json |

Model-quality notes, not product defects and not counted:
- `valid_at` 2024 was invented (d53f3b12).
- The FAST model answered «Турецкий» on recall, task 45 (cdb4b09d).

## 6. Restart / lifecycle

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Restarts ×6: tasks, approvals and STOP persist | PASS | bd2fe23d → cdb4b09d | OR/HW_MATRIX.md HW-05 |
| Upgrade SHA1 → SHA2 → SHA3 on the same data root | PASS | d53f3b12, cdb4b09d | OR/UX_CLI_PARITY.md; CHECKPOINT 3–4 |
| Interrupted coding task → UNKNOWN_OUTCOME, no blind retry | PASS | d53f3b12 | CHECKPOINT 3 |
| WINDOW-CLOSE-LINGERING-BACKEND: old backend lived ~8 min beside the new one (P2) | FAIL | d53f3b12 | OR/BUGS.md; CHECKPOINT 3 |
| Closing the window during a media task leaves the backend running (P2, same class) | FAIL | cdb4b09d | CHECKPOINT 5 |
| Full restart PID 9780 → 15556, lesson present | PASS | cdb4b09d | CHECKPOINT 4 |
| Media backend hard crash: no orphan sd-cli, `interrupted_unknown`, retry → 409 | PASS | cdb4b09d | OR/artifacts/media-video-restart.json |

## 7. STOP

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| `stop` → STOPPED, exit 6 | PASS | bd2fe23d | OR/cli c27–c31 |
| `stop --all` stops tasks and Computer Use | PASS | bd2fe23d | OR/cli c43–c46 |
| Computer STOP survives restart | PASS | bd2fe23d | OR/cli c43–c46 |
| Computer STOP survives upgrade | PASS | d53f3b12 | CHECKPOINT 3 |
| Pending approval voided by STOP | PASS | bd2fe23d | OR/TR_MATRIX.md TR-12 |
| Resume creates a new generation/session with a fresh observation | PASS | bd2fe23d | OR/TR_MATRIX.md TR-12 |
| Media cancel → sd-cli exits in ≤2 s, no result | PASS | cdb4b09d | OR/artifacts/media-video-cancel.json |
| STOP as standard user | PASS | cdb4b09d | OR/artifacts/std-smoke |
| STOP from Telegram (TR-12) | NOT_RUN | — | OR/TR_MATRIX.md |

## 8. Approvals

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Teacher window cannot approve (NO_APPROVE) | PASS | bd2fe23d | OR/cli c37–c40 |
| UI approve/deny reflected in CLI; deny → no effect | PASS | d53f3b12 | s37, s38, t20–t21 |
| Download approve → exactly one file | PASS | bd2fe23d | s44/s45; CHECKPOINT 3 |
| computer.act still `waiting_approval` after the aa6bee3d fix | FIXED_NEEDS_RETEST | unit tests only on f72af6a8; no live SHA | FINAL-1.0/REPORT_RU.md §4 |
| Concurrent two-channel approve (TR-13) | NOT_RUN | — | OR/TR_MATRIX.md |

## 9. Browser

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Public PDF download verified: size, `%PDF-1.4`, independent sha256 | PASS | bd2fe23d | OR/cli c57–c66; CHECKPOINT 3 |
| PDF false PASS without download (DOWNLOAD-FALSE-SUCCESS) | FIXED_NEEDS_RETEST | failed on bd2fe23d (task 18); fix `c0a7e039` | OR/BUGS.md; FINAL-1.0/REPORT_RU.md §4 |
| Re-download loop after verify FAIL (DOWNLOAD-VERIFY-LOOP, P2) | FAIL | bd2fe23d (task 19) | OR/BUGS.md |
| Browser → question with verified browser evidence (task 37) | PASS | cdb4b09d | CHECKPOINT 4 |
| Login handover | NOT_RUN | — | OR/HW_MATRIX.md |

## 10. Coding / self-repair

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| CODING-CRLF-EVIDENCE on untouched repo with autocrlf=true | FAIL | bd2fe23d (c50) | OR/BUGS.md |
| CRLF fix with normal Git, no workaround (coding task e2f63e6235f9) | PASS | cdb4b09d | CHECKPOINT 4 |
| Full-repo coding, 80 MB > 32 MB (CODING-SNAPSHOT-32MB) | FIXED_NEEDS_RETEST | failed on bd2fe23d (c51); fix `f72af6a8` | OR/BUGS.md; FINAL-1.0/REPORT_RU.md §4 |
| Controlled apply → canonical project | NOT_RUN | — (WIP `3fa3d33f` not merged; FINAL-1.0/WIP_controlled_apply_3fa3d33f.patch) | FINAL-1.0/REPORT_RU.md §4; CONTINUE.md §3 |
| D1 self-repair, coached L4 (SELF_REPAIR_SINGLE_CYCLE_PASS) | PASS | cdb4b09d | OR/learning/record-D1-L4-cdb4b09d.json |
| D1 L0–L3 unassisted or lightly hinted (MODEL + TOOL causes) | FAIL | cdb4b09d (SHA1 attempts 1–4 are confounded pre-fix) | OR/learning/record-D1-L0…L3-cdb4b09d.json |
| Coaching pack 5+5 (10/10; CEILING_SATURATED, NO_MEASURED_GAIN) | PASS | bd2fe23d | OR/learning/coaching-pilot-main |
| Transfer gain D2 with lesson vs RAW (NO_MEASURED_GAIN, n=1) | FAIL | cdb4b09d | OR/learning/record-D2-L0-cdb4b09d-*.json |
| Self-repair ×3 new cycles | NOT_RUN | — (exam cases INCOMPLETE) | FINAL-1.0/REPORT_RU.md; CONTINUE.md §8 |
| Evidence diff with autocrlf=true not applicable to an LF repo (P2) | FAIL | cdb4b09d | CHECKPOINT 4 |
| CODE-INSTRUCTION-NEWLINE: no instruction from file; newline truncates (P2) | FAIL | bd2fe23d | OR/BUGS.md |

## 11. Standard user (STANDARD_USER_RUN)

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| TR-01 launch via Explorer as a standard-user shell | PASS | bd2fe23d | OR/TR_MATRIX.md TR-01 |
| Smoke run: status, Cyrillic «Прага», coding, data-root write, approval → dummy.pdf 13 264 B, STOP; no difference from ADMIN_RUN | PASS | cdb4b09d | OR/artifacts/std-smoke; CHECKPOINT 5 |
| SHA4 CLI slash commands in «Bossman CMD» without admin | PASS | 12612c81 | CHECKPOINT 5 |
| Full TR/HW matrix as STANDARD_USER_RUN | NOT_RUN | — | OR/UX_CLI_PARITY.md (smoke only) |

## 12. Jev

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Jev harness selftest | PASS | d53f3b12 | CHECKPOINT 3 |
| Jev via OpenRouter System One: contract VERIFIED, 6/6 functional, 4/4 protective, consent 18/18, ≈$0.00084 (CONTRACT_VERIFIED + SHADOW_PASS) | PASS | cdb4b09d | OR/artifacts/jev; CHECKPOINT 4 |
| Jev phase 2 (INSUFFICIENT_EVIDENCE; owner decision) | BLOCKED | — | OR/REPORT_RU.md; CHECKPOINT 4 |

## 13. Media

| Item | Status | Measured on | Evidence |
|---|---|---|---|
| Image via sd.cpp Z-Image-Turbo 768×768 (decode, sha256 4b9d6247…, Vulkan) | PASS | cdb4b09d | OR/artifacts/media-image-job.json, studio-image-*.png |
| Image via ComfyUI | BLOCKED | cdb4b09d (ENVIRONMENT: SAC blocks scipy `_nd_image.pyd`) | CHECKPOINT 4 |
| Video Wan2.2 test_1s: h264 640×352, 17 frames, 16 fps, full decode | PASS | cdb4b09d | OR/artifacts/media-video-job.json, studio-video-*.mp4 |
| Video cancel and backend crash: no orphans, no blind retry | PASS | cdb4b09d | OR/artifacts/media-video-cancel.json, media-video-restart.json |
| Studio task record shows default 1024×1024/30 steps (P2) | FAIL | cdb4b09d | CHECKPOINT 4 |
| Video Studio: endless skeleton on first visit until reload (P2) | FAIL | cdb4b09d | CHECKPOINT 4 |
| Music Studio | NOT_RUN | — | OR/REPORT_RU.md §6 |

---

## Counts per status

Counts cover every row in sections 1–13. Some issues appear in more than one area: HW-02 ↔ TR-17, PDF false PASS ↔ HW-03/browser, and full-repo coding ↔ TR-16/coding. Each such issue is counted once per area where it appears.

| Section | PASS | FAIL | BLOCKED | NOT_RUN | FIXED_NEEDS_RETEST | Rows |
|---|---|---|---|---|---|---|
| 1 HW | 11 | 4 | 2 | 4 | 2 | 23 |
| 2 TR-01…22 | 20 | 4 | 0 | 6 | 2 | 32 |
| 3 CLI | 3 | 7 | 0 | 0 | 0 | 10 |
| 4 UI↔CLI | 10 | 5 | 1 | 0 | 0 | 16 |
| 5 Memory | 4 | 2 | 0 | 0 | 0 | 6 |
| 6 Restart | 5 | 2 | 0 | 0 | 0 | 7 |
| 7 STOP | 8 | 0 | 0 | 1 | 0 | 9 |
| 8 Approvals | 3 | 0 | 0 | 1 | 1 | 5 |
| 9 Browser | 2 | 1 | 0 | 1 | 1 | 5 |
| 10 Coding | 3 | 5 | 0 | 2 | 1 | 11 |
| 11 Standard user | 3 | 0 | 0 | 1 | 0 | 4 |
| 12 Jev | 2 | 0 | 1 | 0 | 0 | 3 |
| 13 Media | 4 | 2 | 1 | 1 | 0 | 8 |
| **Total** | **78** | **32** | **5** | **17** | **7** | **139** |

**On f72af6a8 (current head): 0 live results of any status.** All 78 PASS rows were measured on bd2fe23d, d53f3b12, cdb4b09d or 12612c81.

No P0 was found in the owner run. Freeze is still blocked by points outside these tables (FINAL-1.0/REPORT_RU.md «Блокеры freeze»):
- P0/P1 candidates C1–C3 from code reading are not yet reproduced;
- the three P1 fixes have not been verified independently;
- the SHA4 full regression is unfinished, with 6 root and 7 + 2 core failures not yet classified;
- CI exact-SHA: NOT_RUN;
- Windows-100: not written.
