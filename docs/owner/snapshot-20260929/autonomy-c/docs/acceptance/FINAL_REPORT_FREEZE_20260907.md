# FINAL REPORT — freeze-20260907-130104

RUN_ID: freeze-20260907-130104
START_REMOTE_SHA: ddea211 (origin/claude/bossman-control-v03-43igbk)
TESTED_RUNNING_SHA: 931584d (feat/web-designer-live-panel = running BCC :8800, core :8700 from same worktree)
FINAL_REMOTE_SHA: 7311c70 + this commit (docs only)
PUSH_CONFIRMED: yes

HOST: Windows 11 · Python 3.14.3 (cc venv) / 3.12 (ci312 venv) · Docker 29.3.0 · GPU: nvidia (nvidia-smi present)
ACTUAL_MODEL: z-ai/glm-5.3 · PROVIDER: OpenRouter · INFERENCE_LOCATION: CLOUD
OPENROUTER_KEY_STORED_SECURELY: yes (temp file outside repo; verified absent from git history and index)
OPENROUTER_CONNECT: PASS (live; 430 models synced; bad key → 400 correct; double connect OK)
CATALOG_TOTAL: 430 · GLM_MODEL_ID: z-ai/glm-5.3 (live-confirmed, context 1310720)
MODEL_IDENTITY_PROOF: PASS at DB level (task_runs.model_alias), FAIL at UI level (MR-001: model/provider stripped from API + empty event payloads)

OPENROUTER_GLM53: PASS (chat route token exact, JSON sum=5480 exact, 5.0–5.4s, $0.000678/$0.00084)
SCENARIO_A_FILES: FAIL (live: ModuleNotFoundError, 21 replans burned, DO-001+DO-017)
SCENARIO_B_CALCULATOR: NOT_RUN (blocked by DO-001; no OCR path exists, DO-007)
SCENARIO_C_BROWSER: NOT_RUN (owner absent for interactive state-change test; browser toolchain audited only)
VIDEO_STUDIO: NOT_RUN (app not in this branch, VS-001) · video_factory backend audited: VS-002..006
WEB_DESIGNER: static PASS-with-findings (WD-001/002 live-code confirmed; sandbox isolation excellent)
APPROVAL_ALLOW/DENY: audited (AP-001..012); owner-interactive flows NOT_RUN (owner absent)
PAUSE/STOP/TAKE_CONTROL: bcc engine machinery audited OK; AP-006/007/008 gaps
RESTART_RECOVERY: audited (AP-002 crash-window replay hole; AP-009 memory-only WAIT_APPROVAL; V3 stack OK)
AT01: FAIL in production desktop path (DO-009); PASS in bcc finalize gate (with AP-011 caveat)
AT03: PARTIAL (DO-010 approval-window drift, DO-011 ungrounded coordinates)
OR001: PARTIALLY FIXED (residual) · OR002: CONFIRMED LIVE (P1) · OR003: CONFIRMED LIVE (P2)
HERMES: DOES_NOT_EXIST · FUSION: DOES_NOT_EXIST (both honest-absent; no baseline risk)
IMGEN_OPENROUTER: NOT_IMPLEMENTED (mock only, live-proven)
V5_N4: partial (learning_guard gates OK, unwired) · V5_N5: N5_PRODUCTION_EVIDENCE=NOT_PROVEN · V5_N8: NOT_PROVEN (branch-only)
LOCAL_MODEL_TEST: NOT_AVAILABLE (Ollama stopped by owner request pre-run)

TEST_COUNTS_NON_OVERLAPPING: audit-only run — no test suites re-run; static+live probes recorded in checkpoints
CI_RUNS: not triggered by docs commits · EXACT_SHA_CI: n/a (docs-only)

PERFORMANCE_SAMPLES: 16 endpoints × 10 live samples (0 errors) + 2 model round-trips
UI_P95: 43–9578ms (failures: apps 9578, testing/events 495, system 396 — all sync-IO/subprocess in async routes)
MODEL_P50_P95: 4997–5383ms observed (N=2, bcc); core single sample 77.6s (PERF-003)
VERIFIED_CYCLE_P50_P95: not measured end-to-end (needs owner-interactive scenarios)
STOP_P95: not measured (owner absent)

BUGS_FOUND: 105+ (see BACKLOG_110_FIXES file) · BUGS_FIXED: 0 (audit-only by owner instruction)
OPEN_P0: 7 · OPEN_P1: 24 · OPEN_P2: 46 · ENVIRONMENT_BLOCKERS: 5 (ENV-001..005, worked around env-only)
NOT_RUN: 24h soak, human comparison, intelligence retention pairing, Fusion/Hermes runtime, scenario B/C live, owner-interactive approvals

FREEZE_STATUS: NOT_READY_FOR_FREEZE
Blocking: SEC-001 (password in git history + live .env), DO-001/017 (desktop unrunnable + no fail-fast), OR-002/OR-004 (catalog/onboarding), MR-001 (model invisible in UI), VS-001 (video studio unmerged), core setup chain ENV-001..005.
Allowed next statuses after fixes: FREEZE_CANDIDATE_WITH_EXTERNAL_ACCEPTANCE_REMAINING (24h soak etc. remain).
