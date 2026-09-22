# YESTERDAY_REMAINDER — 2026-09-21 → 2026-09-22

Source of truth: remote `release/bossman-owner` @ `9e3aa192` (docs-only on top of candidate `0c1cbe65`),
exact-SHA certifier, installed artifact on the owner machine. Old claims are not re-used as PASS.

| # | Item | State | Evidence / reason |
|---|------|-------|-------------------|
| R1 | Exact-SHA certification of declared candidate `0c1cbe65` | DONE | root-ci failed on one timing test (`test_v5_human_speed::test_objective_cas_under_10ms…`, wall p100 21.5 ms on a shared runner); identical code passed root-ci on 4 later SHAs; rerun (attempt 2) green → CERTIFIED 10/10 (`cert-0c1cbe65.json`) |
| R2 | Windows artifact for the candidate | DONE | `BOSSMAN-Windows-x64-0c1cbe651f52.zip` sha256 `3f0f72d2…961dc`; CI binding matches; installed outside checkout; SHA256SUMS 22370/22370; manifest source_sha matches, not dirty |
| R3 | Installed product starts via owner launcher `Start-Bossman.cmd` | NEEDS_FIX | **DESK-EDGE-RELAUNCH (P1, new)**: on this machine `msedge.exe --app` re-spawns itself and the launched PID exits in 0.2–1.0 s with code 0; `launch_window()` waits on that PID, so the launcher stops the backend ~1 s after start. Reproduced 3×, incl. outside Bossman. Yesterday's `desktop-run.log` shows the same 1.0 s exit; yesterday's session silently switched to `--web`. CI never opens the window. |
| R4 | B4, AP-ALL, TEL-001, CU-VERIFY, CU-APPROVAL, F-17, MEDIA-HASH, MEDIA-CANCEL | NEEDS_RETEST (installed) | code + regressions landed 2026-09-21; TEL-001 re-verified live today (server_timings) |
| R5 | MEDIA-RESTART (sd.cpp `_jobs` in memory) | NEEDS_FIX (reproduce first) | open item #1 of CONTINUATION |
| R6 | CU STOP/resume/fresh-target on installed bytes; owner-visible STOP button | NEEDS_RETEST / P2 | HTTP endpoints exist; button absent |
| R7 | Non-deterministic mandatory-CI tests | NEEDS_FIX (P2, reliability) | on identical code: `test_action_contract::test_code_action_not_satisfied_by_an_unrelated_terminal_call` (py3.11, `waiting_approval` vs `failed`), `test_refresh_during_typing_ui::test_a_late_connection_does_not_eat_what_the_owner_is_typing` (py3.14), root human-speed timing test |
| R8 | Media low-profile noise (preset A/B) | OWNER_HARDWARE_REQUIRED | P2 |
| R9 | Qwen baseline / coaching / holdout | OWNER_HARDWARE_REQUIRED | never started; **LearningStore/apprentice tables are empty** in the owner state → no lesson from 2026-09-21 exists to transfer |
| R10 | Multi-agent, MVČR, long session, independent red-team | OWNER_HARDWARE_REQUIRED | NOT_RUN yesterday |
| R11 | `start-models.ps1` (owner machine, not in repo) | DONE (local) | stale: ports 8080/8081 and no `--reasoning off`; product model test returned empty answers until corrected to the audited config (8081/8082, `--jinja --reasoning off`) — ENVIRONMENT, not product |
| R12 | Bossman ↔ opencode bridge | NEEDS_FIX (P2, new) | installed OpenCode is v2.0.12 (API under `/api/session`); bridge calls v1 `/session`; v2 answers v1 paths with the SPA HTML + 200, so Bossman's health probe (`/config`) would report "available" while every session call fails |
| R13 | `test_ux2_desktop::test_second_window_refused_while_first_instance_alive` on Windows | HARNESS (P3) | test fakes a live process with `pid=1`, which does not exist on Windows |
