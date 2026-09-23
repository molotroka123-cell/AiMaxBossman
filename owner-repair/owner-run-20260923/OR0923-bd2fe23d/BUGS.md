# BUGS — owner run 2026-09-23 (RUN_ID OR0923-bd2fe23d)

TESTED_SHA `bd2fe23d` (SHA1). Candidate fixes: SHA2 `d53f3b12`, SHA3 `cdb4b09d` on branch `fix/owner-run-20260923-p1` (NOT merged into release/integrate).
Labels: RUNTIME=OLLAMA_PROXY · ADMIN_RUN. Severity: P0 safety/data loss · P1 release/critical path or false success · P2 UX/robustness.
P0 found: **none** (no leak, no approval bypass, no action after STOP, no payment, no data destruction).

| ID | Sev | Area | Reproducer (evidence) | Status |
|---|---|---|---|---|
| CHAT-CONTEXT-REQUEST | P1 | CLI chat ↔ backend action contract | chat: «Запомни …» (task 9) → every later turn (tasks 10, 11, 12) = FAIL `action_contract/no_verified_action` + new memory approval; screens t04–t14 | **FIXED in SHA2** ea866084; regression `command-center/tests/test_chat_context_not_a_request.py` (2 fail → 4 pass); live recheck task 22 PASS, no approval |
| CODING-CRLF-EVIDENCE | P1 | coding path (openhands_client) | system `core.autocrlf=true` (Git for Windows default) + CSV blobs with CRLF → «evidence mismatch: git hides changes» on an untouched Bossman checkout (cli/c50) | **FIXED in SHA2** 7439917f; regression in `test_openhands_evidence_independence.py`; run used env workaround `GIT_CONFIG core.autocrlf=false` |
| CODING-SNAPSHOT-32MB | P1 (functional gap) | coding path | full Bossman repo 80 MB > `_MAX_SNAPSHOT_BYTES` 32 MB → «workspace evidence exceeds bounded snapshot size» (cli/c51) | OPEN — needs bounded/scoped evidence design (owner doc §7.2); lab used `tools/` subset |
| SIDECAR-SILENT-CUT | P2 (learning-critical) | local_sidecar | tool result cut to 6000 chars with no marker; student re-read a 55 KB file 6× and looped (D1 attempts 1–4) | **FIXED in SHA2** d53f3b12; regression test; did not alone fix looping (SHA2 L0 still FAIL) |
| SIDECAR-NO-PROGRESS | P2 (learning-critical) | local_sidecar | identical observations repeated up to 23× to max_steps (D1 SHA1 a1–a4, SHA2 L0) | **FIXED in SHA3** cdb4b09d (flag from 1st repeat, stop after 8; threshold fixed beforehand); tests |
| DOWNLOAD-FALSE-SUCCESS | P1 | action router | «Скачай PDF-файл https://…» (no «браузер/открой») → router attaches no tools, model says «нет инструментов», task 18 = **completed/PASS** | OPEN |
| DOWNLOAD-VERIFY-LOOP | P2 | browser evidence / review gate | after a verified download (dummy.pdf 13 264 B, sha256 3df79d34…) review FAIL with empty reason (page about:blank ≠ url_contains) → task 19 requeued, asks download again (approvals #11, #14; after restart with `{}` args) | OPEN (stopped by owner-delegate) |
| APP-CONTRACT-OVERRIDES-AGENT-TOOLS | P1 (Computer Use) | action contract | agent «Оператор ПК» with tools computer.observe/act; «Открой Блокнот …» → run exposes only apps_start/apps_stop (model states it), computer.* unavailable → HW-02 impossible via agent (tasks 31–33) | OPEN |
| AGENT-TOOLS-NO-UI | P2 | UI/CLI agents | agents created in UI/CLI get `tools=[]`; no UI/CLI way to grant tools; only API | OPEN |
| MEMORY-NOT-CONFIGURED | P2 | first run | fresh data root: memory service unconfigured → memory.write fails, memory/stats 503; configurable in «Локальные инструменты» | OPEN (UX) |
| EFFECT-RETRY-FALSE-FAIL | P2 | action gate | memory.fact.add failed once (validation), succeeded on retry (fact#1) → task 23 FAIL «effectful tool did not succeed» | OPEN |
| WINDOW-CLOSE-LINGERING-BACKEND | P2 | desktop lifecycle | closing the window during a coding task: old backend (PID 9248) freed the port but lived ~8 min next to the new one on the same data root; record stayed UNKNOWN (no overwrite) | OPEN |
| TWO-PHASE-SUBMIT-DRAFT | P2 | CLI exec | client killed between draft and run → task stays draft until the same request_id is resent (c21/c22) | OPEN |
| RESUME-AGENT-LOST | P2 | CLI chat | `bossman resume <id>` continues with the default agent, not the session's agent (t14) | OPEN |
| CONHOST-PASTE | P2 | CLI chat (prompt_toolkit on ConHost) | Ctrl+V pastes nothing; Shift+Insert inserts `[2;2~`; right-click paste works (t08–t10) | OPEN |
| TERMINATE-BATCH-PROMPT | P2 | launchers | after Ctrl+C in chat, `/exit` shows «Terminate batch job (Y/N)?» | OPEN |
| CHAT-PROMPT-LOOKS-LIKE-CMD | P2 | CLI chat | input prompt is the cwd path, identical to cmd's prompt | OPEN |
| EVAL-NOISE | P2 | CLI chat | 10× «проверка: NOT_APPLICABLE» per answer | OPEN |
| MODEL-DELETE-ORPHANS-AGENT | P2 | UI models | deleting a model used by an agent silently leaves the agent without a model | OPEN |
| WD-HISTORY-STALE | P2 | Web Designer | version history panel not refreshed after save until reload | OPEN |
| CMD-CRCRLF | P2 | packaging | all .cmd in the ZIP have CR CR LF endings (harmless in cmd) — also the frozen student case D1 | OPEN (student exercise) |
| CODE-INSTRUCTION-NEWLINE | P2 | CLI | `bossman code` has no instruction-from-file; a newline in the argument truncates it through bossman.cmd | OPEN |
| SPEND-METER-OFF | P2 (HW-13) | budgets | global cloud spend meter disabled by default (`BOSSMAN_SPEND_METER_ENABLED`); only per-run $2 cap | OPEN (enabled for this run) |
| TG-SETTINGS-NOT-ISOLATED | P2 | settings | test instance Settings→Telegram shows the user-global companion config, not the data root's | OPEN |
| UNCONFIGURED-FALLBACK | P2 (note) | recovery ladder | agent without fallback: dead model → ladder switches to another healthy LOCAL model and returns PASS; cloud models (unpriced / priced / 457 OpenRouter) were NOT selected | behaviour noted |
| DOC-STALE | P2 | docs | TERMINAL.md says prompt_toolkit not in ZIP (it is, 3.0.52); PARITY says /api/evolution absent (status works) | OPEN |

Environment (not product): Smart App Control On blocks unsigned llama.cpp → Ollama runtime; session elevated (ADMIN_RUN); `NoDefaultCurrentDirectoryInExePath=1` inherited from the Claude Code session.
