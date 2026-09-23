# TR-01…TR-22 — Terminal Run 1.2 on the owner's Windows (OR0923)

Labels: ADMIN_RUN unless marked STANDARD_USER_RUN · RUNTIME=OLLAMA_PROXY · SHA1=bd2fe23d (pre-fix), SHA2=d53f3b12, SHA3=cdb4b09d.
Evidence: `cli/*.out|err` + `cli/commands.jsonl` (exit, time, token-leak and ANSI flags per command), `screens/t*.png`, `logs/ui-*.jsonl`.

| ID | Status | What was actually done | Evidence |
|---|---|---|---|
| TR-01 Clean ZIP | PASS (ADMIN_RUN; launch via Explorer = STANDARD_USER shell) | ZIP built locally from exact SHA (PACKAGING_BLOCKER in CI), unpacked to path with spaces; CLI runs from the archive runtime; no checkout/system Python/Node/WSL | logs/build*.log, artifacts/bundle-acceptance*.json, c01–c11 |
| TR-02 Windows Terminal | PASS | WT window: Cyrillic, colors, PASS line; closing WT mid-task leaves task running; desktop shortcut «Bossman CMD» (WT, non-admin) | t15, t31, t32, c41–c42 |
| TR-03 Old CLI | PASS (help/exit contracts) | `bossman models --help`, `task --help` present and exit 0 | c10, c11 |
| TR-04 Cyrillic/paths | PASS with P2 | Russian in ConHost/WT without mojibake; data root and install with spaces; project path Cyrillic+spaces; UTF-8 input files. P2: Ctrl+V/Shift+Insert paste in ConHost chat | t04–t14, c13 |
| TR-05 Shared instance | PASS | CLI status shows same build/data root/started_at as UI; agents/tasks/approvals identical both ways. instance_id not implemented (documented) | c04, s08/c12, s20, s21/c32 |
| TR-06 Shared files | PARTIAL/FAIL | No CLI file-write path into a project: coding path works in a disposable clone (no apply); UI-created agents have no tools. Shared task/coding-task/diff records visible in both | s28/s29, BUGS AGENT-TOOLS-NO-UI |
| TR-07 Shared memory | PASS (SHA2) | fact written via CLI task with UI approval (delegated) → backend restart → recalled automatically in new task 24. Memory needed first-run configuration in UI | c71–c77, s43 |
| TR-08 Real chat | PASS (SHA2), FAIL on SHA1 | multi-turn context works; SHA1 P1 CHAT-CONTEXT-REQUEST fixed in SHA2 (task 22) | t04–t14, t20–t22 |
| TR-09 Structured exec | PASS | no TTY, JSONL only, no ANSI, no token in stdout/stderr (flag per command) | cli/commands.jsonl |
| TR-10 Replay/reconnect | PASS with P2 | same request_id → same task (replayed); cursor replay without duplicates; kill after submit → one task/one run; P2 draft stays until reconnect | c15–c23 |
| TR-11 Close/detach | PASS | client killed while running → task completes, followed from new CLI; WT window closed mid-task → completes | c23–c25, c41–c42 |
| TR-12 STOP/cancel/resume | PASS (CLI/UI); Telegram NOT_RUN | stop → STOPPED exit 6; stop --all stops tasks + Computer Use; computer STOP survives restart and upgrade; pending approval voided by STOP; Resume creates new generation/session (fresh observation) | c27–c31, c43–c46 |
| TR-13 Approval | PASS (negative + delegated positive) | teacher window cannot approve; UI approve/deny reflected in CLI; deny → no effect; download approve → exactly one file. Concurrent two-channel approve NOT_RUN | c37–c40, s37, s38, s44/s45 |
| TR-14 Terminal injection | PASS | ESC/OSC52/BEL/RLO/CR in title and answer never reach stdout (JSON/text) | c34–c36 |
| TR-15 File boundaries | PARTIAL | coding path refuses scope/protected paths (product tests + scope errors in records); junction/reparse & mid-write change NOT_RUN live | — |
| TR-16 Coding/evolution | PARTIAL | CLI → API → local sidecar → REAL_MODEL handshake works; P1 fixes (CRLF evidence) in SHA2; 80 MB repo > 32 MB cap; student results in MODEL_AND_LEARNING_RESULTS.json; /api/evolution/status works, backend campaign NOT_RUN | c50–c56, learning/ |
| TR-17 Browser/Computer Use | PARTIAL | real PDF download verified (hash/size); Computer Use via agent FAIL (action contract replaces agent computer tools) | c57–c66, tasks 31–33 |
| TR-18 Media | NOT_RUN / see HW-07/08 | — | — |
| TR-19 All capabilities | PARTIAL | `list tools` 98 tools with effect policy; PARITY_MATRIX stale (evolution) | c08 |
| TR-20 No model/network | PASS with note | dead endpoint → retries → another LOCAL model, PASS; unpriced cloud blocked; priced / 457 OpenRouter models not chosen; no silent paid fallback | c47–c49, c68 |
| TR-21 Performance | PASS (pilot) | idle client not measured for CPU; same-task GUI vs CLI: MEASURED_OVERHEAD_REDUCTION (n=3, SHA2) | GUI_VS_CLI_MEASUREMENTS.json |
| TR-22 Teacher & memory | PASS (process) | teacher drives via exec/code/events/result; hints logged per level; lesson/transfer in learning/ | learning/COACHING_EPISODES.jsonl |
