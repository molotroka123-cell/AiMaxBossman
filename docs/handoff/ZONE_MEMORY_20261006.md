# ZONE MEMORY — «Память и обучение», 2026-10-06

Branch `zone/memory-20261006` from `4bad4bd2` (integrate/bossman-2.1-one-20261006).
Only facts below; the seed's statuses were NOT edited. Memory hits are not learning:
none of these fixes is evidence of learning — they fix recall/filter/isolation code.

Same-product contract: these are fixes in the one Bossman backend (bossman-core
context_engine / trading_learning / profiles); no separate memory store, CLI or
engine was added. North Star ladder: unchanged by this zone work
(SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT at most); nothing here proves
SELF_REPAIR or TRANSFER.

## How tests were run

Per test file, cwd = `command-center` / `bossman-core` / repo root (as CI does),
`PYTHONPATH=<wt>\command-center;<wt>\bossman-core;<wt>`, a tmp `BCC_DATA_DIR`,
Python 3.12. Running all command-center files in ONE pytest process gave
51 errors + 7 failures from cross-file state (test_oss_qdrant, telegram_calls);
each of those files passes alone — test-isolation issue outside this zone.

## Leaf table (57 blue `code` leaves, parent = `memory`)

All 57 source files exist. Results are on base `4bad4bd2`, BEFORE the fixes.

| leaf | source | tests (import) | result 06.10 (по файлу) |
|---|---|---|---|
| cap-33 (Контекст · retrieval · knowledge fabric) | `command-center/bcc/features/knowledge_fabric_v16.py` | `test_bossnet_foundation_v16.py` | 8 passed |
| cap-34 (Обучение на ошибках) | `command-center/bcc/features/failure_to_case.py` | `test_failure_to_case.py` | 6 passed |
| mod-context_budget_v16 (context_budget_v16) | `command-center/bcc/features/context_budget_v16.py` | `test_bossnet_foundation_v16.py` | 8 passed |
| mod-evolution (evolution) | `command-center/bcc/features/evolution.py` | `test_evolution_api_bootstrap.py`<br>`test_capability_tree.py` | 1 passed<br>18 passed, 2 failed (KeyError campaign_id: loop/campaign API, вне памяти) |
| mod-failure_to_case (failure_to_case) | `command-center/bcc/features/failure_to_case.py` | `test_failure_to_case.py` | 6 passed |
| mod-healing (healing) | `command-center/bcc/features/healing.py` | `test_rc19_model_recovery.py`<br>`test_ux_soak_healing_escalation_dedupe.py` | 20 passed<br>2 passed |
| mod-knowledge_fabric_v16 (knowledge_fabric_v16) | `command-center/bcc/features/knowledge_fabric_v16.py` | `test_bossnet_foundation_v16.py` | 8 passed |
| mod-memory_retrieval_v16 (memory_retrieval_v16) | `command-center/bcc/features/memory_retrieval_v16.py` | `test_bossnet_foundation_v16.py` | 8 passed |
| mod-tools_memory (tools_memory) | `command-center/bcc/features/tools_memory.py` | `test_oss_qdrant.py`<br>`test_fable_context_memory_authority.py`<br>`test_postcall.py` | 15 passed, 7 skipped<br>17 passed<br>20 passed |
| module-2efac2124b8a (bossman/context_engine/chunking.py) | `bossman-core/bossman/context_engine/chunking.py` | `test_context_ingest_project_scope.py` | 10 passed |
| module-fc4458307ec6 (bossman/context_engine/compact.py) | `bossman-core/bossman/context_engine/compact.py` | `test_compact_structured.py`<br>`test_compact_plugins.py` | 3 passed<br>1 passed |
| module-e0361cc49cbe (bossman/context_engine/compiler.py) | `bossman-core/bossman/context_engine/compiler.py` | `test_stage9_agent_smoke.py` | 1 passed, 1 skipped |
| module-4464eb0b222b (bossman/context_engine/distill.py) | `bossman-core/bossman/context_engine/distill.py` | `test_memory_and_state_integrity.py` | 9 passed |
| module-1754cbd5cf01 (bossman/context_engine/embeddings.py) | `bossman-core/bossman/context_engine/embeddings.py` | `test_context_ingest_project_scope.py`<br>`test_context_owner_delete.py` | 10 passed<br>2 passed |
| module-7337f0855536 (bossman/context_engine/ingest.py) | `bossman-core/bossman/context_engine/ingest.py` | `test_context_ingest_project_scope.py`<br>`test_context_owner_delete.py` | 10 passed<br>2 passed |
| module-5da7135e967e (bossman/context_engine/memory.py) | `bossman-core/bossman/context_engine/memory.py` | `test_memory_classes.py`<br>`test_context_engine_integration.py` | 7 passed<br>9 passed |
| module-25fe3b1d97f3 (bossman/context_engine/models.py) | `bossman-core/bossman/context_engine/models.py` | `test_context_ingest_project_scope.py` | 10 passed |
| module-8740ba43b723 (bossman/context_engine/plugins.py) | `bossman-core/bossman/context_engine/plugins.py` | `test_compact_plugins.py` | 1 passed |
| module-35d5cb7d9ebd (bossman/context_engine/retrieval.py) | `bossman-core/bossman/context_engine/retrieval.py` | `test_retrieval_extra.py`<br>`test_context_quality_benchmark.py` | 4 passed<br>27 passed |
| module-c04cfc2bef49 (bossman/context_engine/service.py) | `bossman-core/bossman/context_engine/service.py` | `test_context_ingest_project_scope.py`<br>`test_context_engine_integration.py` | 10 passed<br>9 passed |
| module-690bf4d0f3a1 (bossman/context_engine/store.py) | `bossman-core/bossman/context_engine/store.py` | `test_context_ingest_project_scope.py`<br>`test_context_owner_delete.py` | 10 passed<br>2 passed |
| module-54de14cfacf9 (bossman/context_engine/telemetry.py) | `bossman-core/bossman/context_engine/telemetry.py` | `test_context_telemetry.py` | 4 passed |
| module-91d844ed59bd (bossman/context_engine/utils.py) | `bossman-core/bossman/context_engine/utils.py` | `test_context_ingest_project_scope.py` | 10 passed |
| module-3d1858d49e75 (bossman/cybersec/evidence.py) | `bossman-core/bossman/cybersec/evidence.py` | `test_cybersec_integration.py` | 30 passed |
| module-7e1490293776 (bossman/cybersec/learning.py) | `bossman-core/bossman/cybersec/learning.py` | `test_cybersec_integration.py` | 30 passed |
| module-d68747affe30 (bossman/cybersec/security_memory.py) | `bossman-core/bossman/cybersec/security_memory.py` | нет прямого теста | — |
| module-74d93ed6921a (bossman/dev_factory/evidence.py) | `bossman-core/bossman/dev_factory/evidence.py` | нет прямого теста | — |
| module-8319884cf006 (bossman/learning_guard/ab.py) | `bossman-core/bossman/learning_guard/ab.py` | `test_audit_learn_promotion.py`<br>`test_learning_guard.py` | 6 passed<br>16 passed |
| module-1c240a45339a (bossman/learning_guard/autonomy_trainer.py) | `bossman-core/bossman/learning_guard/autonomy_trainer.py` | `test_pass3_autonomy_trainer.py`<br>`test_f4_promotion_baseline.py` | 13 passed<br>16 passed |
| module-9bcb4ce56153 (bossman/learning_guard/evidence_ledger.py) | `bossman-core/bossman/learning_guard/evidence_ledger.py` | `test_learning_evidence_ledger.py`<br>`test_evidence_ledger_hostile.py`<br>`test_v5_promotion_durable_ledger.py` | 12 passed<br>11 passed<br>5 passed |
| module-e298a2a29a4b (bossman/learning_guard/holdout.py) | `bossman-core/bossman/learning_guard/holdout.py` | `test_pass3_autonomy_trainer.py`<br>`test_runner_holdout_exclusion.py` | 13 passed<br>3 passed |
| module-6f6f10fdb204 (bossman/learning_guard/models.py) | `bossman-core/bossman/learning_guard/models.py` | `test_learning_evidence_ledger.py`<br>`test_apprentice_learning.py` | 12 passed<br>7 passed |
| module-07d13d7463b7 (bossman/learning_guard/promotion.py) | `bossman-core/bossman/learning_guard/promotion.py` | `test_f4_promotion_baseline.py`<br>`test_f5_cross_corpus.py`<br>`test_apprentice_learning.py` | 16 passed<br>15 passed<br>7 passed |
| module-72d4da1606dd (bossman/learning_guard/runtime_bridge.py) | `bossman-core/bossman/learning_guard/runtime_bridge.py` | `test_audit_learn_runtime_bridge.py`<br>`test_audit_p0_runtime_wiring.py`<br>`test_audit_learn_store_location.py` | 2 passed<br>6 passed<br>5 passed |
| module-d33b374941cd (bossman/learning_guard/service.py) | `bossman-core/bossman/learning_guard/service.py` | `test_learning_guard.py` | 16 passed |
| module-9d355deb8dee (bossman/profiles/memory.py) | `bossman-core/bossman/profiles/memory.py` | `test_profiles.py` | 24 passed |
| module-389fe1a92fa7 (bossman/trading_learning/adapters.py) | `bossman-core/bossman/trading_learning/adapters.py` | `test_coinwise_observation.py` | 39 passed |
| module-2719fd94eacc (bossman/trading_learning/backtest.py) | `bossman-core/bossman/trading_learning/backtest.py` | `test_trading_paper_memory.py` | 30 passed |
| module-6bf6aca94f5f (bossman/trading_learning/benchmark.py) | `bossman-core/bossman/trading_learning/benchmark.py` | `test_trading_pipeline_benchmark.py` | 1 skipped |
| module-98c19da7eaec (bossman/trading_learning/claims.py) | `bossman-core/bossman/trading_learning/claims.py` | `test_trading_claims.py` | 33 passed |
| module-10135418ed52 (bossman/trading_learning/cli.py) | `bossman-core/bossman/trading_learning/cli.py` | `test_trading_pipeline_benchmark.py` | 1 skipped |
| module-dca65ddd8cea (bossman/trading_learning/frames.py) | `bossman-core/bossman/trading_learning/frames.py` | `test_trading_pipeline_benchmark.py` | 1 skipped |
| module-72a2f39e685d (bossman/trading_learning/ingest.py) | `bossman-core/bossman/trading_learning/ingest.py` | `test_trading_pipeline_benchmark.py` | 1 skipped |
| module-9b294ff2bf89 (bossman/trading_learning/lessons.py) | `bossman-core/bossman/trading_learning/lessons.py` | `test_trading_paper_memory.py` | 30 passed |
| module-23fb0a4fb4a5 (bossman/trading_learning/market.py) | `bossman-core/bossman/trading_learning/market.py` | `test_trading_claims.py` | 33 passed |
| module-1e377785ed16 (bossman/trading_learning/memory.py) | `bossman-core/bossman/trading_learning/memory.py` | `test_trading_paper_memory.py` | 30 passed |
| module-3b203abe62da (bossman/trading_learning/metrics.py) | `bossman-core/bossman/trading_learning/metrics.py` | `test_trading_paper_memory.py` | 30 passed |
| module-0ca465774554 (bossman/trading_learning/models.py) | `bossman-core/bossman/trading_learning/models.py` | `test_trading_claims.py` | 33 passed |
| module-b4a66a051db3 (bossman/trading_learning/paper.py) | `bossman-core/bossman/trading_learning/paper.py` | `test_trading_paper_memory.py` | 30 passed |
| module-cdb451a7640c (bossman/trading_learning/replay.py) | `bossman-core/bossman/trading_learning/replay.py` | `test_trading_lookahead.py` | 14 passed |
| module-0eb18b3e4f15 (bossman/trading_learning/routes.py) | `bossman-core/bossman/trading_learning/routes.py` | `test_trading_wiring.py` | 6 passed |
| module-1f786cfcb2c8 (bossman/trading_learning/safety.py) | `bossman-core/bossman/trading_learning/safety.py` | `test_trading_safety.py` | 20 passed |
| module-4a2642e13aea (bossman/trading_learning/sanitize.py) | `bossman-core/bossman/trading_learning/sanitize.py` | `test_trading_claims.py` | 33 passed |
| module-8c080805b7d7 (bossman/trading_learning/seed.py) | `bossman-core/bossman/trading_learning/seed.py` | `test_trading_claims.py` | 33 passed |
| module-e4cfe203dd2c (bossman/trading_learning/strategy.py) | `bossman-core/bossman/trading_learning/strategy.py` | `test_trading_claims.py` | 33 passed |
| module-2911cc4fe8e0 (bossman/trading_learning/telemetry.py) | `bossman-core/bossman/trading_learning/telemetry.py` | `test_trading_safety.py` | 20 passed |
| module-7c2b27fd9ac6 (bossman/trading_learning/verify.py) | `bossman-core/bossman/trading_learning/verify.py` | `test_trading_claims.py` | 33 passed |

Leaves without a direct test: `cybersec/security_memory.py` (async Postgres
failure_memory adapter; not exercised), `dev_factory/evidence.py` (probed by hand:
from_test_output gives PASS/FAIL/UNKNOWN correctly on 11 pytest summaries;
a test name like `test_2 errors` gives a conservative false FAIL — not fixed).
`test_capability_tree.py` 2 failures (`KeyError: 'campaign_id'`) belong to the
loop/campaign API, not to `evolution.py` memory behaviour.

## Fixes (each: failing test first, then minimal fix)

| # | commit | leaf | defect | before → after | author |
|---|---|---|---|---|---|
| 1 | `8d1683fd` | module-4a2642e13aea trading_learning/sanitize | poison filter gap: «ignore the/your previous instructions», «ignore all of the above instructions», «забудь свои предыдущие инструкции», soft hyphen U+00AD inside the verb, Unicode tag chars U+E0000–E007F — all passed as clean data | new test 9 failed/3 passed → 12 passed; trading suites 154 passed, 1 skipped | Claude |
| 2 | `ff94fc8a` | module-5da7135e967e context_engine/memory | wrong recall ranking: `MemoryManager.retrieve` re-sorted by importance only; ContextCompiler clips memory from the tail, so the relevant fact was cut out of the compiled prompt | new test 2 failed/1 passed → 3 passed; context suites 105 passed, 1 skipped | Claude |
| 3 | `b4a8cd4a` | module-5da7135e967e memory + module-8740ba43b723 plugins | data loss / recall wiped: `candidate()` wrote to read-only Markdown/JSON plugins (RuntimeError; listed first → store write never happened); a corrupt JSON export made `retrieve()` raise → `inject_into_builder` injected nothing | new test 3 failed → 4 passed (incl. fail-closed when no writable plugin); context suites 120 passed, 1 skipped | Claude |
| 4 | `3aac2db3` | module-9d355deb8dee profiles/memory | cross-profile mixing: two profiles with the same 48+ char name got ONE knowledge folder (safe_id truncation cut off the random id suffix). `knowledge_dir` is used only by tests today | new test 1 failed/2 passed → 3 passed; test_profiles + computer_control_authorization 39 passed | Claude |

After all fixes (per file): sanitize_gaps 12, recall_order 3, readonly_plugins 4,
profiles_knowledge_isolation 3, trading_claims 33, trading_safety 20,
trading_paper_memory 30, coinwise_observation 39, memory_classes 7,
context_engine_integration 9, context_quality_benchmark 27, profiles 24,
compact_plugins 1, command-center test_trading_lab_unwired 35 — all passed.
`tools/skips_registry.py --check`: PASS (389 entries, 0 without reason; no new skips).
`git diff --check`: clean.

## GLM worker (glm-flash, z-ai/glm-5.3-flash) tasks

| task | fix | outcome (from /api/coding-tasks) |
|---|---|---|
| `fecbe17b95b7` | sanitize | failed — `независимая проверка Bossman не прошла: exit=2`; sidecar summary `"..."`; changed only the test file; every run_tests errored |
| `5813744c2ae4` | recall order | failed — `сайдкар сообщил о неудаче`, stop_reason=max_steps (42 tool calls, ~35 run_tests errors); summary empty; only the test file |
| `12c3efaae2d8` | read-only plugins | failed — `независимая проверка Bossman не прошла: exit=2`; summary `"..."`; only the test file |
| `d624e57876a3` | profiles | last seen `running`; polling stopped by coordinator order, result not read, nothing ported |

Root cause of the GLM failures (environment, not the model): in the verify
sandbox `import bossman` resolves through the owner's Python312 editable install
`__editable___bossman_core_0_3_0_finder.py` → `C:\Users\asd\Documents\Default Project\AiMaxBossman-integrated-old-20260927\bossman-core`;
the sidecar guard then refuses the read outside the workspace
(`PermissionError: bossman sidecar guard: read outside workspace refused`), so
no test can even be collected — for the worker or for Bossman's own verification.
No GLM patch was verified, so none was ported; all 4 fixes are Claude-authored.

The coordinator then ordered: no more /api/coding-tasks calls (each call's
handshake loads a 27 GB local Ollama model into the GPU, colliding with the video
render). GLM was therefore skipped for the remaining work.

## What stays `code` and why

- All 57 leaves stay `code`: tests are unit/integration in tmp dirs; none of
  these paths was exercised on the owner's real installed Bossman with real data.
- Durable memory writes (`MemoryManager.candidate/decision/promote`) are not called
  by the production runner (only the benchmark sandbox); the runner only reads
  (`inject_into_builder`). So memory *recall* is wired, memory *accumulation* is not.
- `cybersec/security_memory.py`, `dev_factory/evidence.py`: no direct tests.
- `trading_learning/*`: well tested (≈200 tests) but paper/mock only by design.

## Recommended tree status changes (for the owner/coordinator; not applied)

- None upgraded beyond `code` from this work.
- Suggest a note on module-5da7135e967e (context_engine/memory): "recall order +
  read-only plugin fixes 06.10; production does not write durable memory".
- Suggest a note on module-4a2642e13aea (sanitize): "injection filter gaps closed
  06.10; regex filter remains bypassable by homoglyphs/paraphrase — not a guarantee".
- Infra blocker for every coding-task zone: fix the stale editable install of
  bossman-core (points to AiMaxBossman-integrated-old-20260927) or make the verify
  sandbox put its own bossman-core first on sys.path.

## Open (not fixed, found)

- `tools_memory.tool_search`: a small `max_context_tokens` makes every hit
  exceed the budget → reports "в памяти ничего не найдено" although memory exists.
- `learning_guard/holdout`: ids are not normalized (" t1" ≠ "t1") before sealing.
- `context_engine/compiler`: the "Relevant memory" section's `source_refs` lists
  memory ids that were clipped out of its text.
