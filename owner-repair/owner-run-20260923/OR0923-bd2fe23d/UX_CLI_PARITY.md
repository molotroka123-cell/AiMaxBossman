# UX ↔ CLI ↔ Telegram parity — owner run 2026-09-23

One backend (port 8810), one data root `C:\Users\asd\Bossman Test 0923\data`, builds SHA1→SHA2→SHA3 on the same data root (owner upgrade path). ADMIN_RUN unless noted; STANDARD_USER_RUN smoke at the end.

| Object | UI → CLI | CLI → UI | Evidence |
|---|---|---|---|
| Models (Ollama, cloud pin) | added in «Модели», visible in `list models` | — (CLI has `/models use`) | s02–s05, s12, s51; c05 |
| Agents | created in «Агенты», visible in `list agents` | CLI tasks run under UI agents | s08/c12, s19 |
| Tasks | UI Task Composer task 6 → `list tasks` / `result 6` | CLI tasks 1–5 in UI «Задачи» with same status | s20, s21, c32, c33 |
| Approvals | UI approve/deny → CLI chat shows «✓ разрешение #13: одобрено (ui)», `list approvals` empties | CLI-created approval #1 shown in UI «Ждут решения» | c37–c40, s22, t20–t21 |
| STOP | UI shows stopped tasks; computer STOP from API persists | `stop` / `stop --all` reflected in UI and after restart | c28–c31, c43–c46, s23 |
| Coding tasks | UI form «Новая задача агенту» → same records | `bossman code` tasks listed in UI «Coding-сессии» with diff | s28/s29, gui_cli_sha3 |
| Memory | configured in UI («Локальные инструменты»), fact written via CLI task with UI approval | recalled by a CLI task after restart | s43, c71–c77 |
| Files in a project | **gap**: no CLI file write into a project (coding path works in a disposable clone, no apply); UI-created agents have no tools | — | BUGS AGENT-TOOLS-NO-UI |
| Web Designer / Studio / Video | UI only (CLI: UNAVAILABLE as dedicated ops, per PARITY_MATRIX); Studio sd.cpp models reachable via API only (UI selector lacks them) | — | s27–s35, media artifacts |
| Telegram | **OWNER_ACTION_REQUIRED**: companion is bound to the owner's working setup (one poller per bot token); not attached to the test instance. The bot was used as owner↔Claude correction channel only | — | tg_inbox / tg.py |

Interactive terminals: ConHost (t01–t14), Windows Terminal (t15), desktop shortcut «Bossman CMD» (t31/t32, non-admin).

GUI vs CLI overhead: `GUI_VS_CLI_MEASUREMENTS.json` (SHA3: A memory/task and C browser = MEASURED_CLI_OVERHEAD_REDUCTION; B coding = NO_MEASURED_GAIN on wall-clock, 17× fewer teacher actions).
