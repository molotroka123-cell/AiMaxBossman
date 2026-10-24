# MP21 Track A, section 2: project map, 11.10.2026

Master prompt: `Bossman-Final-Master-Prompt.md` section 2. Local, read-only audit; no push, no deploy, no network.

## 1. Repository state

| Item | Value |
|---|---|
| Repo | `C:\Users\asd\Bossman\wt-unified-20261010` is the unified worktree; this pass works in `C:\Users\asd\Bossman\wt-mp-a-20261011` |
| Branch of this pass | `mp/a-map-20261011`, created from `feat/bossman-genjutsu-jeff-unified-20261010` |
| Base HEAD | `250e24ecbc35999965d60a52e98b012d965d2635` (2026-10-10 17:48 -0700) "lessons: +2 (max-LoRA worse than dim16; face adapter beats LoRA)" |
| Remote | `origin https://github.com/molotroka123-cell/AiMaxBossman` (fetch and push). Nothing was fetched or pushed |
| Worktrees | 37 registered (`git worktree list`): integration-20261006 with 17 agent worktrees under `.claude/worktrees`, `wt-*` for ads, hairmask, jeff-one, lora, lora2, main-merge, main-prod, mimics, runpod, stage2, swarm-research, swarm-verify, unified, verify-green, and `zone-*` apps/memory/plugins/ux |
| Unified worktree status | tracked files clean; untracked `docs/evidence/limits-out-orchestra*`, `docs/evidence/orchestra-cloud/`, `tools/cloud_orchestra_generic.py` (another agent's, not touched) |
| Canonical line | per `AGENTS.md`: `main`; this branch is a candidate stage, not a freeze |

Recent commits: `250e24ec` lessons +2; `26bc8e35` LoRA v3 report (A/B NOT PASS, IP-Adapter-FaceID 0.61 unofficial, pod closed 2.23 USD);
`ab235ccb` orchestra interpreter fix; `77a2939c` limits-out orchestra; `9e46c094` lessons day catalog.

## 2. Active processes (observed, not touched)

| Process | What | Port |
|---|---|---|
| `ollama` 0.40.2 (pid 16868) plus `ollama app` | model server | 11434 |
| `llama-server` pids 3584 / 14148 / 32216 (`--offline`, ROCm) | resident models: 35B-A3B Q5 (26 GB), 0.8B decider, one more | 55372, 57079, 52829 |
| Bossman backend, installed bundle `BOSSMAN-Windows-x64-6de18f8d9a08` | owner backend | 8801 |
| `python -m bcc --port 8871` (system Python) | second backend, dev | 8871 |
| `bcc.telegram_companion`, `bcc.pit.cli start`, `bcc.pit.cli watch` | pult and Jeff | loopback |
| `search_proxy.py`, `keep_awake.py` | helpers | 8850 |
| `autopilot.py` (two) under `apps-local\facefusion` | another agent's video work | none |
| `node` x17 | tooling | |

Training: no `kohya`, `sd-scripts`, `accelerate` or `train` process was found on this machine. The video renders have owner priority,
so this pass kept CPU low (see the checks file).

## 3. How to read the inventory

- Rows: 186 `bcc` modules and packages, 55 UI pages in `command-center/ui/pages`, 143 `tools` entries. Generator:
  `python tools/mp21_project_map.py` (static; reads files and one `git log --since=2026-08-01`).
- `LOC` is lines of the file or package. `tests refs` is the number of test files that contain the name as a word
  (generic names over-count). `docs refs` is the same for `docs/**/*.md` and root `.md`. `TODO` counts TODO/FIXME/XXX.
  `tree leaves` is the number of seed leaves that cite a file of the item as a source. `last commit` is the newest
  commit date for any file of the item since 2026-08-01.
- Status vocabulary: `works` only when an executed test imports the module (executed test files and rc are in
  `docs/owner/runs/MP21_A_CHECKS_20261011.md`); `partial` means code exists and tests may exist but were not run in this
  pass, so it is NOT a failure; `missing` and `broken` would appear only with evidence, and none was found by static
  reading; `blocked` is carried by tree leaves (13 in the publisher copy, see the North Star doc).
- A `partial` row is an honest "not proven here", not "known to be broken". The full-suite run was not allowed by the
  owner's CPU priority; running it is the exact next step to convert most `partial` rows to `works` or `broken`.

## 4. Totals

- `works` (an executed test imports it): 23 rows. Packages with at least one module exercised by executed tests: 5 rows.
- `partial`, tests mention the name: 309 rows. `partial`, code only with no test reference: 41 rows
  (marked "code only" in the table).
- TODO/FIXME/XXX markers in all inventoried code: 1 (a docstring mention in `offline_mode.py`, not a real TODO).
  Outstanding work is tracked in docs and the tree (`blocked`: 13 leaves; `branch`: 31; `idea`: 25), not in code comments.
- Every inventoried item has a commit on or after 2026-08-28, so nothing is untouched since 2026-08-01 in this tree.
  The stalest are `agentmap`, `governor`, `nl_permissions`, `tools_openclaw`, `workflow`, `appcards` (2026-08-28), then
  `code_intel`, `coding_session(s)`, `eval_scorecard`, `lsp_bridge`, `rc_test_c` (2026-08-30). 55 items were touched
  on 2026-10-08 or later ("recently discussed").

## 5. Claims the code does not back

See `docs/archive/2026-10/owner/MP21_NORTH_STAR_TREE_20261011.md`: 49 tree leaves cite source files that are not tracked at this commit
(9 `mod-*` feature modules, 19 `module-*` leaves, 12 `cap-*` evidence files, 3 skills, `app-osiris`, 5 CI job names).

Documentation that contradicts this machine: `docs/hardware/LOCAL_HARDWARE_INVENTORY.md` (i9-14900HX, RTX 4060, 15.6 GiB RAM).

## 6. Function-level status of the areas owned by Track A

| Function | Evidence | Status |
|---|---|---|
| Offline report `/api/offline` | `command-center/bcc/features/offline_mode.py`, `tests/test_offline_mode.py` (21 passed with the local-only tests) | works |
| Local-only enforcement `BOSSMAN_LOCAL_ONLY` | `bossman_shared/privacy.py`, `tests/test_local_only_switch.py`, `tools/offline_smoke_local_only.py` rc 0 | works (provider boundary only) |
| Model providers (OpenAI-compatible, Anthropic, OpenRouter) | `bcc/providers.py`, `bcc/v2/openrouter_ext.py`, `test_openrouter_provider_isolation.py` (66-test run) | works |
| Queue, retry, cancel, restart survival | `test_queue_retry.py`, `test_fence_event_cancellation.py`, `test_jeff_queue_survival.py` (98 passed) | works |
| Failure recovery with a readable answer | `test_rc19_model_recovery.py`, engine `privacy:` failure path | works |
| Autonomy journal | `bcc/autonomy/journal.py`, `test_autonomy_journal.py` | works |
| Computer control: observe, act, verify | `bcc/features/tools_computer.py` (2153 LOC), `bossman-core/bossman/computer_operator/*` (6412 LOC), 4 test files | works against fakes; real desktop not driven here |
| Computer control: stop, resume, restart-safe STOP | `test_computer_stop_race.py` | works |
| Global emergency hotkey | none in repo | missing |
| 24 h unattended autonomous run | `docs/autonomy/BOSSMAN_2_1_AUTONOMOUS_COMPUTER_OPERATOR.md` says "not recorded" | missing |
| Multi-GPU / distributed inference | one GPU; `ggml-rpc` binaries present, no remote host | missing (hardware absent) |
| Local model inventory and licences | `docs/archive/2026-10/owner/MP21_LOCAL_MODELS_AUTONOMY_20261011.md` | works (measured); licences of 8 of 11 Ollama models unverified |
| Poker, swapme/exchange, trading bots, Motion/video | owned by other agents in parallel; not audited in this track | not covered here |

## 7. Inventory

| kind | item | LOC | tests refs | docs refs | TODO | tree leaves | last commit | status |
|---|---|---:|---:|---:|---:|---:|---|---|
| bcc | `__main__` | 13 | 24 | 6 | 0 | 0 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `_shared` | 24 | 6 | 8 | 0 | 0 | 2026-09-02 | partial (tests mention the name; not executed here) |
| bcc | `api` | 1607 | 464 | 300 | 0 | 0 | 2026-10-05 | works (executed tests import it, rc=0) |
| bcc | `app` | 92 | 293 | 207 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `approval_scope` | 391 | 5 | 5 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `approvals` | 194 | 141 | 237 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `auth` | 131 | 99 | 126 | 0 | 0 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `autonomy` | 7796 | 36 | 90 | 0 | 0 | 2026-10-07 | partial (package; at least one module is exercised by executed tests) |
| bcc | `backend_lock` | 208 | 7 | 4 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `browser_runtime` | 107 | 5 | 5 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| bcc | `build_identity` | 126 | 8 | 3 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `capability` | 186 | 63 | 176 | 0 | 0 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `clip_guard` | 102 | 1 | 0 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `code_mode` | 1374 | 4 | 1 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `coding_session` | 285 | 4 | 12 | 0 | 0 | 2026-08-30 | partial (tests mention the name; not executed here) |
| bcc | `config` | 81 | 248 | 151 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| bcc | `context_os` | 413 | 4 | 19 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| bcc | `conversation_context` | 29 | 2 | 3 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `db` | 920 | 327 | 102 | 0 | 0 | 2026-09-30 | works (executed tests import it, rc=0) |
| bcc | `desktop` | 1481 | 60 | 167 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `desktop_install` | 376 | 5 | 1 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `direct_gen` | 2897 | 7 | 7 | 0 | 5 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `discovery` | 401 | 33 | 128 | 0 | 0 | 2026-09-27 | works (executed tests import it, rc=0) |
| bcc | `earning_emulator` | 115 | 2 | 4 | 0 | 1 | 2026-10-06 | partial (tests mention the name; not executed here) |
| bcc | `economy_orchestrator` | 426 | 2 | 3 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `engine` | 3023 | 194 | 214 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `eval_scorecard` | 76 | 1 | 14 | 0 | 0 | 2026-08-30 | partial (tests mention the name; not executed here) |
| bcc | `events` | 259 | 139 | 141 | 0 | 0 | 2026-09-29 | works (executed tests import it, rc=0) |
| bcc | `fable_cap` | 221 | 3 | 6 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `__init__` | 47 | 337 | 44 | 0 | 0 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `action_contract` | 755 | 13 | 34 | 0 | 1 | 2026-09-28 | works (executed tests import it, rc=0) |
| bcc | `action_gate` | 202 | 3 | 5 | 0 | 1 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `action_preview` | 394 | 2 | 3 | 0 | 1 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `action_router` | 228 | 18 | 28 | 0 | 1 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `agentmap` | 107 | 3 | 16 | 0 | 2 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `apps` | 366 | 47 | 116 | 0 | 1 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `apps_control` | 1122 | 9 | 15 | 0 | 1 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `autonomy` | 148 | 36 | 90 | 0 | 1 | 2026-09-30 | works (executed tests import it, rc=0) |
| bcc | `benchlab` | 166 | 2 | 13 | 0 | 1 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `bossnet_nodes_v16` | 54 | 1 | 1 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `browser` | 304 | 189 | 357 | 0 | 1 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `browser_help` | 243 | 1 | 2 | 0 | 1 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `cache_intel` | 160 | 3 | 5 | 0 | 2 | 2026-09-06 | partial (tests mention the name; not executed here) |
| bcc | `capability_tree` | 505 | 2 | 1 | 0 | 0 | 2026-10-05 | works (executed tests import it, rc=0) |
| bcc | `chat_threads` | 1532 | 2 | 9 | 0 | 1 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `code_intel` | 180 | 1 | 12 | 0 | 1 | 2026-08-30 | partial (tests mention the name; not executed here) |
| bcc | `coding_limit_saver_v16` | 239 | 1 | 7 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `coding_recipes` | 601 | 5 | 6 | 0 | 1 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `coding_sessions` | 147 | 2 | 7 | 0 | 2 | 2026-08-30 | partial (tests mention the name; not executed here) |
| bcc | `coding_tasks` | 971 | 12 | 10 | 0 | 1 | 2026-10-09 | works (executed tests import it, rc=0) |
| bcc | `command_bar` | 1116 | 6 | 6 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `context_budget_v16` | 73 | 1 | 3 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `control_plane` | 617 | 9 | 27 | 0 | 2 | 2026-10-09 | partial (tests mention the name; not executed here) |
| bcc | `deep_fix` | 116 | 12 | 12 | 0 | 1 | 2026-09-02 | partial (tests mention the name; not executed here) |
| bcc | `diag_bundle` | 448 | 4 | 3 | 0 | 1 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `direct_gen` | 182 | 7 | 7 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `economy_swarm` | 239 | 2 | 5 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `evolution` | 215 | 14 | 62 | 0 | 1 | 2026-10-08 | works (executed tests import it, rc=0) |
| bcc | `failure_to_case` | 449 | 2 | 7 | 0 | 2 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `file_intelligence` | 247 | 8 | 13 | 0 | 1 | 2026-09-16 | partial (tests mention the name; not executed here) |
| bcc | `forks` | 161 | 3 | 24 | 0 | 1 | 2026-09-02 | partial (tests mention the name; not executed here) |
| bcc | `free_providers` | 168 | 1 | 3 | 0 | 5 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `game_bootstrap_v16` | 202 | 1 | 4 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `gmail_connect` | 215 | 0 | 0 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| bcc | `governor` | 193 | 13 | 47 | 0 | 1 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `healing` | 210 | 5 | 22 | 0 | 1 | 2026-09-28 | works (executed tests import it, rc=0) |
| bcc | `images` | 836 | 32 | 67 | 0 | 1 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `jeff_insights` | 114 | 2 | 5 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `jeff_master_parser` | 120 | 0 | 2 | 0 | 1 | 2026-09-28 | partial (code only, no test reference) |
| bcc | `jeff_settings` | 626 | 13 | 9 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `jev` | 213 | 23 | 30 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `knowledge_fabric_v16` | 35 | 1 | 3 | 0 | 2 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `lab_agents` | 358 | 1 | 1 | 0 | 2 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `local_first` | 337 | 1 | 5 | 0 | 1 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `market_metrics` | 76 | 1 | 2 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `memory_retrieval_v16` | 89 | 1 | 3 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `missions` | 386 | 26 | 67 | 0 | 2 | 2026-09-09 | works (executed tests import it, rc=0) |
| bcc | `model_foundry_v16` | 38 | 1 | 2 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `motion_studio` | 403 | 6 | 20 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `music_studio` | 769 | 3 | 5 | 0 | 1 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `nl_orchestra` | 213 | 2 | 6 | 0 | 1 | 2026-10-06 | partial (tests mention the name; not executed here) |
| bcc | `nl_permissions` | 427 | 1 | 3 | 0 | 1 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `objectives` | 342 | 19 | 21 | 0 | 1 | 2026-09-06 | partial (tests mention the name; not executed here) |
| bcc | `offline_mode` | 455 | 5 | 6 | 1 | 1 | 2026-09-29 | works (executed tests import it, rc=0) |
| bcc | `opencode` | 231 | 16 | 46 | 0 | 1 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `openrouter` | 587 | 100 | 96 | 0 | 2 | 2026-09-28 | works (executed tests import it, rc=0) |
| bcc | `organization` | 413 | 24 | 32 | 0 | 1 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `osiris` | 1065 | 7 | 12 | 0 | 1 | 2026-09-16 | partial (tests mention the name; not executed here) |
| bcc | `oss_integrations` | 91 | 1 | 5 | 0 | 2 | 2026-09-16 | partial (tests mention the name; not executed here) |
| bcc | `owner_input` | 81 | 3 | 1 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `plugins` | 915 | 26 | 52 | 0 | 24 | 2026-10-10 | works (executed tests import it, rc=0) |
| bcc | `poker_vision` | 306 | 4 | 2 | 0 | 1 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `provenance` | 262 | 70 | 154 | 0 | 1 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `provider_fleet_v16` | 119 | 1 | 2 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `qa_relay` | 148 | 1 | 1 | 0 | 1 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `rave` | 322 | 17 | 27 | 0 | 2 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `reality` | 179 | 10 | 29 | 0 | 1 | 2026-09-08 | works (executed tests import it, rc=0) |
| bcc | `resources` | 302 | 26 | 66 | 0 | 2 | 2026-09-19 | partial (tests mention the name; not executed here) |
| bcc | `review_gate` | 314 | 8 | 13 | 0 | 1 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `router` | 636 | 101 | 145 | 0 | 1 | 2026-09-28 | works (executed tests import it, rc=0) |
| bcc | `second_opinion` | 528 | 1 | 3 | 0 | 1 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `simulation_world_v16` | 31 | 1 | 1 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `skills` | 1129 | 40 | 170 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `snapshot` | 654 | 87 | 104 | 0 | 1 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `spend_meter` | 456 | 2 | 8 | 0 | 1 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `studio` | 284 | 69 | 91 | 0 | 2 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `studio_review` | 41 | 0 | 4 | 0 | 1 | 2026-09-23 | partial (code only, no test reference) |
| bcc | `task_exchange` | 357 | 4 | 11 | 0 | 1 | 2026-09-02 | partial (tests mention the name; not executed here) |
| bcc | `telegram_calls` | 731 | 46 | 20 | 0 | 1 | 2026-10-09 | partial (tests mention the name; not executed here) |
| bcc | `telegram_settings` | 859 | 5 | 2 | 0 | 1 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `terminal` | 469 | 148 | 157 | 0 | 1 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `testing_period` | 636 | 2 | 8 | 0 | 1 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `tools_apps` | 120 | 2 | 2 | 0 | 1 | 2026-09-09 | partial (tests mention the name; not executed here) |
| bcc | `tools_browser` | 1230 | 12 | 11 | 0 | 2 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `tools_code` | 460 | 6 | 7 | 0 | 1 | 2026-10-05 | partial (tests mention the name; not executed here) |
| bcc | `tools_computer` | 2154 | 9 | 6 | 0 | 2 | 2026-10-10 | works (executed tests import it, rc=0) |
| bcc | `tools_facts` | 208 | 3 | 9 | 0 | 1 | 2026-09-02 | partial (tests mention the name; not executed here) |
| bcc | `tools_jeff` | 56 | 1 | 1 | 0 | 1 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `tools_mcp` | 635 | 10 | 10 | 0 | 2 | 2026-10-06 | partial (tests mention the name; not executed here) |
| bcc | `tools_memory` | 518 | 4 | 18 | 0 | 1 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `tools_openclaw` | 573 | 2 | 5 | 0 | 1 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `tools_opencode` | 506 | 3 | 8 | 0 | 1 | 2026-09-23 | partial (tests mention the name; not executed here) |
| bcc | `tools_terminal` | 547 | 12 | 13 | 0 | 1 | 2026-10-05 | partial (tests mention the name; not executed here) |
| bcc | `trading_lab` | 109 | 4 | 14 | 0 | 1 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `unified_search` | 246 | 1 | 6 | 0 | 1 | 2026-09-03 | partial (tests mention the name; not executed here) |
| bcc | `v15_autonomy` | 128 | 3 | 4 | 0 | 1 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `v15_economy` | 200 | 1 | 3 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `v15_owner_run` | 115 | 2 | 6 | 0 | 1 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `v15_self_repair` | 221 | 1 | 2 | 0 | 1 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `video_studio` | 410 | 45 | 39 | 0 | 1 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `watchdog` | 426 | 7 | 34 | 0 | 1 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `web_designer` | 850 | 27 | 19 | 0 | 1 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `web_research` | 8819 | 9 | 15 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| bcc | `workflow` | 519 | 20 | 178 | 0 | 1 | 2026-08-28 | partial (tests mention the name; not executed here) |
| bcc | `file_intelligence` | 2223 | 8 | 13 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `finalize` | 460 | 23 | 26 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| bcc | `gmail_connector` | 1106 | 2 | 1 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `golden_trace` | 232 | 1 | 0 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `health` | 76 | 97 | 167 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| bcc | `html_text` | 1035 | 2 | 2 | 0 | 0 | 2026-09-04 | partial (tests mention the name; not executed here) |
| bcc | `hybrid` | 2256 | 14 | 39 | 0 | 0 | 2026-09-11 | partial (tests mention the name; not executed here) |
| bcc | `jeff_desktop` | 231 | 4 | 6 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `jev` | 1361 | 23 | 30 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `lifecycle` | 107 | 43 | 66 | 0 | 0 | 2026-09-07 | partial (tests mention the name; not executed here) |
| bcc | `login_guard` | 59 | 1 | 2 | 0 | 0 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `lsp_bridge` | 252 | 2 | 9 | 0 | 0 | 2026-08-30 | partial (tests mention the name; not executed here) |
| bcc | `market` | 2342 | 9 | 92 | 0 | 0 | 2026-10-01 | partial (tests mention the name; not executed here) |
| bcc | `metrics` | 269 | 53 | 138 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| bcc | `mission_budget` | 250 | 2 | 5 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `model_health` | 330 | 8 | 12 | 0 | 0 | 2026-09-28 | works (executed tests import it, rc=0) |
| bcc | `model_speed` | 109 | 1 | 2 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `oss` | 1184 | 32 | 54 | 0 | 4 | 2026-10-01 | partial (package; at least one module is exercised by executed tests) |
| bcc | `owner_acceptance` | 433 | 4 | 9 | 0 | 0 | 2026-09-27 | partial (tests mention the name; not executed here) |
| bcc | `owner_input` | 214 | 3 | 1 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| bcc | `permissions` | 55 | 87 | 109 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `pit` | 26461 | 109 | 81 | 0 | 93 | 2026-10-10 | partial (package; at least one module is exercised by executed tests) |
| bcc | `plugin_security` | 313 | 17 | 24 | 0 | 1 | 2026-10-07 | partial (tests mention the name; not executed here) |
| bcc | `portcheck` | 47 | 2 | 2 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `probe_cache` | 80 | 2 | 1 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `provider_governance` | 183 | 13 | 14 | 0 | 0 | 2026-10-01 | works (executed tests import it, rc=0) |
| bcc | `providers` | 719 | 132 | 124 | 0 | 0 | 2026-10-10 | works (executed tests import it, rc=0) |
| bcc | `qa_relay` | 450 | 1 | 1 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `rave` | 2928 | 17 | 27 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| bcc | `reality` | 1396 | 10 | 29 | 0 | 0 | 2026-09-28 | partial (package; at least one module is exercised by executed tests) |
| bcc | `registry` | 355 | 110 | 170 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `review_escalation` | 327 | 18 | 6 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `run_provenance` | 268 | 4 | 10 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| bcc | `scheduler` | 182 | 18 | 71 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `secrets` | 90 | 53 | 105 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `sessions` | 120 | 44 | 100 | 0 | 0 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `single_flight` | 69 | 2 | 7 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| bcc | `streaming` | 591 | 9 | 46 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| bcc | `studio` | 3463 | 69 | 91 | 0 | 6 | 2026-10-08 | partial (tests mention the name; not executed here) |
| bcc | `task_admission` | 122 | 3 | 4 | 0 | 0 | 2026-09-30 | works (executed tests import it, rc=0) |
| bcc | `telegram_calls` | 8376 | 46 | 20 | 0 | 28 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `telegram_companion` | 6581 | 68 | 22 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `terminal_cli` | 6455 | 28 | 19 | 0 | 31 | 2026-10-10 | partial (tests mention the name; not executed here) |
| bcc | `tools` | 537 | 321 | 395 | 0 | 0 | 2026-10-05 | works (executed tests import it, rc=0) |
| bcc | `trace` | 34 | 42 | 71 | 0 | 0 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `v2` | 12651 | 167 | 167 | 0 | 3 | 2026-10-05 | partial (package; at least one module is exercised by executed tests) |
| bcc | `video_studio` | 5296 | 45 | 39 | 0 | 8 | 2026-09-28 | partial (tests mention the name; not executed here) |
| bcc | `web_designer_ai_build` | 143 | 0 | 4 | 0 | 1 | 2026-09-21 | partial (code only, no test reference) |
| bcc | `web_designer_creative_brief` | 128 | 1 | 2 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| bcc | `web_designer_dom` | 1472 | 5 | 6 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| bcc | `web_designer_gen` | 569 | 1 | 3 | 0 | 1 | 2026-09-05 | partial (tests mention the name; not executed here) |
| bcc | `web_designer_visual` | 84 | 2 | 4 | 0 | 1 | 2026-09-16 | partial (tests mention the name; not executed here) |
| ui-page | `_shared` | 101 | 6 | 8 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `_studio` | 106 | 0 | 3 | 0 | 0 | 2026-09-22 | partial (code only, no test reference) |
| ui-page | `_ui` | 277 | 10 | 6 | 0 | 0 | 2026-10-01 | partial (tests mention the name; not executed here) |
| ui-page | `agentmap` | 149 | 3 | 16 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `appcards` | 148 | 0 | 2 | 0 | 0 | 2026-08-28 | partial (code only, no test reference) |
| ui-page | `apps` | 376 | 47 | 116 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `autonomy` | 195 | 36 | 90 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `benchmarks` | 132 | 6 | 41 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| ui-page | `browser` | 218 | 189 | 357 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| ui-page | `builder` | 646 | 15 | 32 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `capability_tree` | 212 | 2 | 1 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| ui-page | `capability_tree_scene` | 642 | 1 | 0 | 0 | 0 | 2026-10-05 | partial (tests mention the name; not executed here) |
| ui-page | `chat` | 60 | 217 | 180 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `coding` | 284 | 42 | 157 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `control` | 192 | 172 | 320 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| ui-page | `direct_gen` | 839 | 7 | 7 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| ui-page | `file_intelligence` | 297 | 8 | 13 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `forks` | 163 | 3 | 24 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `genjutsu` | 407 | 4 | 21 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| ui-page | `genjutsu_core` | 293 | 1 | 2 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| ui-page | `governor` | 85 | 13 | 47 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `healing` | 76 | 5 | 22 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `home` | 776 | 101 | 45 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `images` | 685 | 32 | 67 | 0 | 0 | 2026-09-20 | partial (tests mention the name; not executed here) |
| ui-page | `index` | 148 | 178 | 94 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| ui-page | `jeff_insights` | 174 | 2 | 5 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| ui-page | `jeff_passports` | 141 | 0 | 1 | 0 | 0 | 2026-09-28 | partial (code only, no test reference) |
| ui-page | `jeff_settings` | 441 | 13 | 9 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| ui-page | `mission_console` | 969 | 3 | 10 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `missions` | 268 | 26 | 67 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `mobile` | 577 | 10 | 49 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `motion_studio` | 120 | 6 | 20 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| ui-page | `music_studio` | 161 | 3 | 5 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `objectives` | 461 | 19 | 21 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `openrouter` | 399 | 100 | 96 | 0 | 0 | 2026-09-08 | works (executed tests import it, rc=0) |
| ui-page | `orchestras` | 124 | 4 | 8 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `oss` | 115 | 32 | 54 | 0 | 0 | 2026-09-15 | partial (tests mention the name; not executed here) |
| ui-page | `overview` | 218 | 6 | 20 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `poker_vision` | 265 | 4 | 2 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| ui-page | `rave` | 556 | 17 | 27 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `resources` | 129 | 26 | 66 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `router` | 174 | 101 | 145 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `skills` | 362 | 40 | 170 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| ui-page | `telegram_calls` | 764 | 46 | 20 | 0 | 0 | 2026-10-09 | partial (tests mention the name; not executed here) |
| ui-page | `telegram_settings` | 288 | 5 | 2 | 0 | 0 | 2026-10-01 | partial (tests mention the name; not executed here) |
| ui-page | `terminal` | 250 | 148 | 157 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| ui-page | `trading_lab` | 329 | 4 | 14 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| ui-page | `v15_owner_run` | 125 | 2 | 6 | 0 | 0 | 2026-10-05 | partial (tests mention the name; not executed here) |
| ui-page | `video_studio` | 829 | 45 | 39 | 0 | 0 | 2026-09-18 | partial (tests mention the name; not executed here) |
| ui-page | `video_studio_state` | 122 | 1 | 3 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| ui-page | `web_designer` | 1029 | 27 | 19 | 0 | 0 | 2026-10-06 | partial (tests mention the name; not executed here) |
| ui-page | `web_designer_viewport` | 73 | 0 | 2 | 0 | 0 | 2026-09-06 | partial (code only, no test reference) |
| ui-page | `web_designer_visual` | 116 | 2 | 4 | 0 | 0 | 2026-09-16 | partial (tests mention the name; not executed here) |
| ui-page | `web_designer_visual_frame` | 84 | 1 | 0 | 0 | 0 | 2026-09-16 | partial (tests mention the name; not executed here) |
| ui-page | `web_research` | 166 | 9 | 15 | 0 | 0 | 2026-09-08 | partial (tests mention the name; not executed here) |
| tool | `acceptance_registry` | 94 | 3 | 10 | 0 | 0 | 2026-09-17 | partial (tests mention the name; not executed here) |
| tool | `analyze_final_live_session` | 161 | 1 | 0 | 0 | 0 | 2026-09-05 | partial (tests mention the name; not executed here) |
| tool | `app_icons` | 424 | 2 | 3 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| tool | `astra6_freeze` | 374 | 3 | 11 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `astra_acceptance` | 46 | 0 | 3 | 0 | 0 | 2026-09-05 | partial (code only, no test reference) |
| tool | `astra_branch_protection` | 62 | 0 | 5 | 0 | 0 | 2026-09-05 | partial (code only, no test reference) |
| tool | `astra_security_gate` | 77 | 1 | 6 | 0 | 0 | 2026-10-05 | partial (tests mention the name; not executed here) |
| tool | `autonomy_supervisor` | 118 | 0 | 2 | 0 | 0 | 2026-09-30 | partial (code only, no test reference) |
| tool | `bench_bossman` | 136 | 0 | 1 | 0 | 0 | 2026-09-29 | partial (code only, no test reference) |
| tool | `blue_leaf_audit` | 237 | 1 | 1 | 0 | 1 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `bossfield_owner_run` | 340 | 1 | 1 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_economy_run` | 373 | 1 | 4 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_learning_compile` | 196 | 1 | 1 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_ling_coder` | 211 | 1 | 2 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_memory_sync` | 164 | 1 | 2 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_owner_ctl` | 100 | 1 | 0 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_owner_run` | 255 | 1 | 1 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_self_improve` | 581 | 2 | 9 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `bossman_15_setup` | 219 | 1 | 2 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `bossman_capability_scan` | 36 | 0 | 1 | 0 | 0 | 2026-10-05 | partial (code only, no test reference) |
| tool | `bossman_debug_recorder` | 257 | 0 | 4 | 0 | 0 | 2026-09-29 | partial (code only, no test reference) |
| tool | `bossman_evolve` | 122 | 1 | 4 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| tool | `bossman_tomorrow` | 322 | 0 | 0 | 0 | 0 | 2026-10-08 | partial (code only, no test reference) |
| tool | `build_local_bundle` | 291 | 3 | 3 | 0 | 0 | 2026-09-17 | partial (tests mention the name; not executed here) |
| tool | `build_source_identity` | 70 | 1 | 0 | 0 | 0 | 2026-09-09 | partial (tests mention the name; not executed here) |
| tool | `build_windows_bundle` | 1146 | 13 | 16 | 0 | 0 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `bundle_evening_test` | 563 | 5 | 8 | 0 | 0 | 2026-09-18 | partial (tests mention the name; not executed here) |
| tool | `capture_ux_evidence` | 223 | 0 | 2 | 0 | 0 | 2026-09-03 | partial (code only, no test reference) |
| tool | `ci_ai_provider` | 360 | 3 | 2 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `ci_secret_scan` | 323 | 4 | 35 | 0 | 0 | 2026-10-05 | partial (tests mention the name; not executed here) |
| tool | `coaching_exam` | 1164 | 1 | 2 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `coaching_runner` | 559 | 3 | 3 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| tool | `coding_path_owner` | 226 | 1 | 0 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `coding_value_sim` | 380 | 0 | 3 | 0 | 0 | 2026-09-27 | partial (code only, no test reference) |
| tool | `context_slice` | 283 | 2 | 4 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| tool | `convergence_inventory` | 211 | 0 | 0 | 0 | 0 | 2026-09-19 | partial (code only, no test reference) |
| tool | `cu_acceptance` | 1507 | 0 | 1 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| tool | `desktop` | 60 | 60 | 167 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| tool | `distill_export_bossman` | 69 | 1 | 1 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `distill_recorder` | 141 | 2 | 3 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `earn_research` | 321 | 0 | 1 | 0 | 0 | 2026-10-07 | partial (code only, no test reference) |
| tool | `earning_emulator` | 72 | 2 | 4 | 0 | 1 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `exact_sha_certify` | 432 | 4 | 23 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `fleet_green_probe` | 135 | 0 | 1 | 0 | 0 | 2026-09-24 | partial (code only, no test reference) |
| tool | `genjutsu` | 1341 | 4 | 21 | 0 | 2 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `hailuo_rescue` | 412 | 0 | 10 | 0 | 0 | 2026-09-26 | partial (code only, no test reference) |
| tool | `human_speed_gate` | 541 | 3 | 3 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `import_operational_lessons` | 372 | 1 | 1 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `installed_product_chain` | 852 | 1 | 3 | 0 | 0 | 2026-09-26 | partial (tests mention the name; not executed here) |
| tool | `installed_ui_sweep` | 237 | 4 | 6 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `intelligence` | 330 | 19 | 93 | 0 | 0 | 2026-09-02 | partial (tests mention the name; not executed here) |
| tool | `intelligence_evidence_transport` | 246 | 1 | 5 | 0 | 0 | 2026-09-27 | partial (tests mention the name; not executed here) |
| tool | `intelligence_preservation_gate` | 407 | 7 | 3 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `intelligence_preservation_run` | 1208 | 3 | 13 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `intro_video` | 83 | 1 | 1 | 0 | 0 | 2026-09-27 | partial (tests mention the name; not executed here) |
| tool | `jeff-ux-fast-check` | 53 | 0 | 3 | 0 | 0 | 2026-09-28 | partial (code only, no test reference) |
| tool | `jeff_ux_packet` | 215 | 1 | 4 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| tool | `jeff_voice_samples` | 76 | 1 | 2 | 0 | 0 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `jev_shadow_owner` | 605 | 1 | 2 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| tool | `k1m6a_youtube_batch` | 253 | 4 | 7 | 0 | 0 | 2026-09-30 | partial (tests mention the name; not executed here) |
| tool | `keys_guard` | 320 | 1 | 5 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `leaf_usefulness_queue` | 149 | 1 | 2 | 0 | 1 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `learning_migrate_identity` | 80 | 0 | 0 | 0 | 0 | 2026-09-02 | partial (code only, no test reference) |
| tool | `lessons_catalog_20261010` | 155 | 0 | 0 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| tool | `limits_out_orchestra` | 141 | 0 | 0 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| tool | `live_openrouter_owner` | 417 | 4 | 5 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `local_completeness_manifest` | 250 | 1 | 5 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `local_hardware_ab` | 360 | 2 | 6 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| tool | `local_model_strategy_compare` | 304 | 1 | 2 | 0 | 0 | 2026-09-11 | partial (tests mention the name; not executed here) |
| tool | `mcp_acceptance` | 601 | 1 | 10 | 0 | 0 | 2026-09-14 | partial (tests mention the name; not executed here) |
| tool | `measure_owner_paths` | 76 | 0 | 0 | 0 | 0 | 2026-09-08 | partial (code only, no test reference) |
| tool | `media_ab_preset` | 215 | 3 | 3 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| tool | `media_bootstrap` | 497 | 2 | 3 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| tool | `media_ci_preflight` | 62 | 1 | 2 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| tool | `media_roundtrip` | 642 | 4 | 3 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| tool | `metrics_smoke` | 178 | 2 | 1 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `model_bakeoff` | 341 | 3 | 3 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `model_fetch` | 1229 | 3 | 3 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `motion_concert` | 856 | 1 | 1 | 0 | 0 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `motion_studio` | 2002 | 6 | 20 | 0 | 2 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `music_studio_install` | 136 | 1 | 2 | 0 | 0 | 2026-10-01 | partial (tests mention the name; not executed here) |
| tool | `music_studio_launcher` | 64 | 0 | 1 | 0 | 0 | 2026-09-30 | partial (code only, no test reference) |
| tool | `mvcr_prepare` | 1401 | 2 | 3 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `north_star` | 1761 | 0 | 1 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| tool | `objective_cas_profile` | 136 | 2 | 1 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `operator_step_profile` | 292 | 1 | 3 | 0 | 0 | 2026-09-21 | partial (tests mention the name; not executed here) |
| tool | `owner_journeys` | 2785 | 5 | 10 | 0 | 1 | 2026-09-29 | partial (tests mention the name; not executed here) |
| tool | `owner_machine_report` | 393 | 2 | 3 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `owner_one_bossman` | 1215 | 3 | 12 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `owner_run_tomorrow` | 1852 | 3 | 1 | 0 | 0 | 2026-09-26 | partial (tests mention the name; not executed here) |
| tool | `owner_scenarios_20260924` | 276 | 1 | 1 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `promo_video` | 329 | 0 | 3 | 0 | 1 | 2026-09-27 | partial (code only, no test reference) |
| tool | `proof_dossier` | 389 | 1 | 0 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `pytest_watchdog` | 183 | 1 | 2 | 0 | 0 | 2026-09-06 | partial (tests mention the name; not executed here) |
| tool | `rc19_side_by_side` | 669 | 1 | 8 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| tool | `rc_test_c` | 410 | 0 | 4 | 0 | 0 | 2026-08-30 | partial (code only, no test reference) |
| tool | `recipe_book` | 85 | 0 | 0 | 0 | 0 | 2026-09-22 | partial (code only, no test reference) |
| tool | `register_bossman_tomorrow_tasks` | 42 | 0 | 0 | 0 | 0 | 2026-10-07 | partial (code only, no test reference) |
| tool | `remove_bossman_tomorrow_tasks` | 17 | 1 | 1 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| tool | `render_capability_matrix` | 92 | 1 | 2 | 0 | 0 | 2026-09-18 | partial (tests mention the name; not executed here) |
| tool | `require_acceptance_results` | 110 | 1 | 9 | 0 | 0 | 2026-10-02 | partial (tests mention the name; not executed here) |
| tool | `responsiveness_probe` | 500 | 1 | 3 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `route_guard` | 28 | 1 | 0 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| tool | `runpod` | 388 | 0 | 15 | 0 | 0 | 2026-10-10 | partial (code only, no test reference) |
| tool | `runpod_preflight` | 276 | 1 | 7 | 0 | 0 | 2026-09-01 | partial (tests mention the name; not executed here) |
| tool | `scenario_runner` | 627 | 28 | 5 | 0 | 0 | 2026-09-26 | partial (tests mention the name; not executed here) |
| tool | `seedance_shorts` | 220 | 0 | 9 | 0 | 0 | 2026-08-31 | partial (code only, no test reference) |
| tool | `self_improve_lab` | 2447 | 4 | 5 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| tool | `skips_registry` | 131 | 1 | 30 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| tool | `studio_live_owner` | 151 | 2 | 7 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `swarm_verify_patch` | 108 | 0 | 2 | 0 | 0 | 2026-10-07 | partial (code only, no test reference) |
| tool | `telegram_calls_addon_lock` | 67 | 0 | 0 | 0 | 0 | 2026-09-29 | partial (code only, no test reference) |
| tool | `terminal_launchers` | 48 | 1 | 0 | 0 | 0 | 2026-09-23 | partial (tests mention the name; not executed here) |
| tool | `training_data_preflight` | 151 | 1 | 3 | 0 | 0 | 2026-10-09 | partial (tests mention the name; not executed here) |
| tool | `tree_apply_evidence` | 318 | 1 | 8 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| tool | `tree_holdout` | 517 | 1 | 6 | 0 | 0 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `tree_proof` | 5812 | 5 | 8 | 0 | 2 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `tree_publish_site` | 153 | 1 | 1 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| tool | `tree_registry_sync` | 200 | 2 | 5 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `tree_self_improve` | 102 | 1 | 5 | 0 | 0 | 2026-10-07 | partial (tests mention the name; not executed here) |
| tool | `tree_self_repair_cycle` | 299 | 1 | 8 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `trend_edit` | 1580 | 1 | 6 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `ux_attack` | 286 | 0 | 0 | 0 | 0 | 2026-09-22 | partial (code only, no test reference) |
| tool | `ux_soak` | 2151 | 1 | 5 | 0 | 0 | 2026-09-28 | partial (tests mention the name; not executed here) |
| tool | `v15_economy_orchestrator` | 512 | 2 | 1 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `v15_provider_pool` | 295 | 1 | 0 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `v6_baseline` | 326 | 1 | 2 | 0 | 0 | 2026-09-07 | partial (tests mention the name; not executed here) |
| tool | `validate_curated_agent_skills` | 197 | 1 | 3 | 0 | 0 | 2026-09-07 | partial (tests mention the name; not executed here) |
| tool | `verify_installed_product` | 275 | 6 | 7 | 0 | 0 | 2026-10-06 | partial (tests mention the name; not executed here) |
| tool | `verify_windows_bundle` | 381 | 2 | 14 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `video_gate` | 1258 | 2 | 6 | 0 | 1 | 2026-10-10 | partial (tests mention the name; not executed here) |
| tool | `video_studio` | 621 | 45 | 39 | 0 | 1 | 2026-09-06 | partial (tests mention the name; not executed here) |
| tool | `web_access_probe` | 342 | 0 | 1 | 0 | 0 | 2026-09-30 | partial (code only, no test reference) |
| tool | `windows_100_plugin` | 43 | 0 | 0 | 0 | 0 | 2026-09-24 | partial (code only, no test reference) |
| tool | `windows_100_real` | 322 | 1 | 1 | 0 | 0 | 2026-09-24 | partial (tests mention the name; not executed here) |
| tool | `windows_bundle_lock` | 370 | 2 | 8 | 0 | 0 | 2026-09-22 | partial (tests mention the name; not executed here) |
| tool | `worker_client` | 228 | 2 | 2 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `workflow_filters` | 113 | 2 | 1 | 0 | 0 | 2026-09-19 | partial (tests mention the name; not executed here) |
| tool | `youtube_trader_ingest` | 572 | 4 | 4 | 0 | 0 | 2026-10-08 | partial (tests mention the name; not executed here) |
| tool | `youtube_trader_ingest_asr` | 71 | 0 | 1 | 0 | 0 | 2026-09-28 | partial (code only, no test reference) |
| tool | `youtube_trader_ingest_auto` | 164 | 3 | 6 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
| tool | `youtube_trader_ingest_batch` | 133 | 1 | 2 | 0 | 0 | 2026-09-25 | partial (tests mention the name; not executed here) |
| tool | `youtube_trader_ingest_claims` | 549 | 2 | 1 | 0 | 0 | 2026-09-29 | partial (tests mention the name; not executed here) |
