# memapps lane classification (zones memory + apps)

Leaves in scope (status code|branch): 79. GREEN 63, RETIRE 0, KEEP 16.

RETIRE = 0: nothing met the proof bar (every candidate has importers/tests/registration or is safety/consent related).
Receipts: evidence/memapps.json (PASS and executed FAIL), evidence/memapps-retire.json (empty), out/<id>.txt.
Re-run: python tools/tree_proof/memapps_probe.py && python tools/tree_proof/memapps_classify.py

| id | label | verdict | reason | value |
|---|---|---|---|---|
| app-ai-3d-maker | ai-3d-maker | KEEP | FAIL: 7 test_slicer tests fail on this Windows host (no CuraEngine binary / stub-slicer fixtures), 342 pass; needs env or test-portability work | OK |
| app-ai-webcam-vision | ai-webcam-vision | KEEP | FAIL: 6 tests fail on Windows (tzdata missing for Europe/Prague, POSIX 0600 owner-only mode not reproduced, ffmpeg absent); 220 pass; needs env + Windows ACL decision | OK |
| app-bossman-accountant | bossman-accountant | GREEN | import + pytest PASS @3a1b8469; ['tests'] | OK |
| app-exam-trainer-ai | exam-trainer-ai | GREEN | import + pytest PASS @3a1b8469; ['tests'] | OK |
| app-file-commander-mini | file-commander-mini | GREEN | import + pytest PASS @3a1b8469; ['tests'] | OK |
| app-osiris | osiris | KEEP | apps/osiris absent in this worktree (branch feature/osiris-data-acquisition); feature module is the separate mod-osiris leaf | OK |
| app-pc-autopilot-mini | pc-autopilot-mini | GREEN | import + pytest PASS @3a1b8469; ['tests'] | OK |
| app-solana-volume-suite | solana-volume-suite | KEEP | FAIL env: code is at repo-root solana_volume_suite/ (not apps/); 26 root safety tests pass but app suite needs solders/solana packages; wash-trading/Bubblemaps-evasion scope needs owner decision | OK |
| app-travel-architect | travel-architect | GREEN | import + pytest PASS @3a1b8469; ['tests'] | OK |
| cap-33 | Контекст · retrieval · knowledge fabric | GREEN | import + pytest PASS @3a1b8469; ['test_bossnet_foundation_v16.py'] | OK |
| cap-34 | Обучение на ошибках | GREEN | import + pytest PASS @3a1b8469; ['test_failure_to_case.py'] | OK |
| cap-42 | SwapMe admin · FreshVibes admin | GREEN | import + pytest PASS @3a1b8469; ['tests/owner_journeys/test_admin_journeys.py', 'tests/owner_journeys/test_route_ladder.py'] | OK |
| mod-context_budget_v16 | context_budget_v16 | GREEN | import + pytest PASS @3a1b8469; ['test_bossnet_foundation_v16.py'] | OK |
| mod-evolution | evolution | GREEN | import + pytest PASS @3a1b8469; ['tests/test_evolution_api_bootstrap.py', 'test_capability_tree.py'] | OK |
| mod-failure_to_case | failure_to_case | GREEN | import + pytest PASS @3a1b8469; ['test_failure_to_case.py'] | OK |
| mod-healing | healing | GREEN | import + pytest PASS @3a1b8469; ['test_rc19_model_recovery.py', 'test_ux_soak_healing_escalation_dedupe.py'] ; TOP: healing: Bossman reliability / self-repair | TOP |
| mod-knowledge_fabric_v16 | knowledge_fabric_v16 | GREEN | import + pytest PASS @3a1b8469; ['test_bossnet_foundation_v16.py'] | OK |
| mod-memory_retrieval_v16 | memory_retrieval_v16 | GREEN | import + pytest PASS @3a1b8469; ['test_bossnet_foundation_v16.py'] | OK |
| mod-tools_memory | tools_memory | GREEN | import + pytest PASS @3a1b8469; ['test_fable_context_memory_authority.py', 'test_oss_qdrant.py', 'telegram_calls/test_postcall.py'] | OK |
| module-07d13d7463b7 | bossman/learning_guard/promotion.py | GREEN | import + pytest PASS @3a1b8469; ['test_apprentice_learning.py', 'test_leaf_learning_guard_service.py', 'audit001/test_f4_promotion_baseline.py', 'audit001/test_f5_cross_corpus.py'] [authored_by_lane] ; TOP: learning_guard.promotion: staged promotion, never auto owner-promote | TOP |
| module-0ca465774554 | bossman/trading_learning/models.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py', 'test_trading_lookahead.py', 'test_trading_paper_memory.py'] | OK |
| module-0eb18b3e4f15 | bossman/trading_learning/routes.py | GREEN | import + pytest PASS @3a1b8469; ['test_coinwise_observation.py', 'test_trading_pipeline_benchmark.py', 'test_trading_wiring.py'] | OK |
| module-10135418ed52 | bossman/trading_learning/cli.py | KEEP | FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test | OK |
| module-1754cbd5cf01 | bossman/context_engine/embeddings.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py', 'test_context_owner_delete.py', 'test_leaf_context_engine_retrieval.py'] [authored_by_lane] | OK |
| module-1c240a45339a | bossman/learning_guard/autonomy_trainer.py | GREEN | import + pytest PASS @3a1b8469; ['test_audit_learn_promotion.py', 'test_pass3_autonomy_trainer.py', 'audit001/test_f4_promotion_baseline.py', 'audit001/test_f5_cross_corpus.py'] ; TOP: learning_guard.autonomy_trainer: gated self-improvement trainer | TOP |
| module-1e377785ed16 | bossman/trading_learning/memory.py | GREEN | import + pytest PASS @3a1b8469; ['test_coinwise_observation.py', 'test_trading_paper_memory.py'] | OK |
| module-1f786cfcb2c8 | bossman/trading_learning/safety.py | GREEN | import + pytest PASS @3a1b8469; ['test_coinwise_observation.py', 'test_trading_lookahead.py', 'test_trading_paper_memory.py', 'test_trading_pipeline_benchmark.py', 'test_trading_safe | OK |
| module-23fb0a4fb4a5 | bossman/trading_learning/market.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py'] | OK |
| module-25fe3b1d97f3 | bossman/context_engine/models.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py', 'test_leaf_context_engine_memory.py', 'test_leaf_context_engine_plugins.py', 'test_leaf_context_engine_retrie [authored_by_lane] | OK |
| module-2719fd94eacc | bossman/trading_learning/backtest.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_paper_memory.py'] | OK |
| module-2911cc4fe8e0 | bossman/trading_learning/telemetry.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_safety.py'] | OK |
| module-2efac2124b8a | bossman/context_engine/chunking.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py'] | OK |
| module-35d5cb7d9ebd | bossman/context_engine/retrieval.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_context_engine_retrieval.py'] [authored_by_lane] ; TOP: context_engine.retrieval: sensitivity-aware hybrid retrieval | TOP |
| module-389fe1a92fa7 | bossman/trading_learning/adapters.py | GREEN | import + pytest PASS @3a1b8469; ['test_coinwise_observation.py', 'test_trading_pipeline_benchmark.py'] | OK |
| module-3b203abe62da | bossman/trading_learning/metrics.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_paper_memory.py'] | OK |
| module-3d1858d49e75 | bossman/cybersec/evidence.py | GREEN | import + pytest PASS @3a1b8469; ['test_cybersec_integration.py'] | OK |
| module-4464eb0b222b | bossman/context_engine/distill.py | GREEN | import + pytest PASS @3a1b8469; ['test_memory_and_state_integrity.py'] | OK |
| module-4a2642e13aea | bossman/trading_learning/sanitize.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py', 'test_trading_sanitize_gaps.py'] | OK |
| module-4e4b608586a8 | bossman/cognitive/context.py | KEEP | branch leaf: bossman-core/bossman/cognitive/context.py absent in this worktree; keep until branch is merged | OK |
| module-54de14cfacf9 | bossman/context_engine/telemetry.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_telemetry.py'] | OK |
| module-5da7135e967e | bossman/context_engine/memory.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_context_engine_memory.py'] [authored_by_lane] ; TOP: context_engine.memory: candidate->promote memory, provenance, conflicts | TOP |
| module-690bf4d0f3a1 | bossman/context_engine/store.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py', 'test_context_owner_delete.py', 'test_leaf_context_engine_memory.py', 'test_leaf_context_engine_retrieval.py' [authored_by_lane] ; TOP: context_engine.store: owner delete (forget) removes chunks+index | TOP |
| module-6bf6aca94f5f | bossman/trading_learning/benchmark.py | KEEP | FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test | OK |
| module-6f6f10fdb204 | bossman/learning_guard/models.py | GREEN | import + pytest PASS @3a1b8469; ['test_apprentice_learning.py', 'test_audit_learn_promotion.py', 'test_learning_evidence_ledger.py', 'test_pass3_autonomy_trainer.py', 'audit001/test_ | OK |
| module-72a2f39e685d | bossman/trading_learning/ingest.py | KEEP | FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test | OK |
| module-72d4da1606dd | bossman/learning_guard/runtime_bridge.py | GREEN | import + pytest PASS @3a1b8469; ['tests/test_audit_learn_store_location.py', 'test_audit_learn_runtime_bridge.py', 'test_audit_p0_runtime_wiring.py'] | OK |
| module-7337f0855536 | bossman/context_engine/ingest.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py', 'test_context_owner_delete.py', 'test_leaf_context_engine_retrieval.py'] [authored_by_lane] | OK |
| module-74d93ed6921a | bossman/dev_factory/evidence.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_dev_factory_evidence.py'] [authored_by_lane] | OK |
| module-7c2b27fd9ac6 | bossman/trading_learning/verify.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py'] | OK |
| module-7e1490293776 | bossman/cybersec/learning.py | GREEN | import + pytest PASS @3a1b8469; ['test_cybersec_integration.py', 'test_cybersec_v1.py'] | OK |
| module-8319884cf006 | bossman/learning_guard/ab.py | GREEN | import + pytest PASS @3a1b8469; ['test_audit_learn_promotion.py'] ; TOP: learning_guard.ab: same-model A/B with verified-success gates | TOP |
| module-8740ba43b723 | bossman/context_engine/plugins.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_context_engine_plugins.py'] [authored_by_lane] | OK |
| module-8c080805b7d7 | bossman/trading_learning/seed.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py'] | OK |
| module-91d844ed59bd | bossman/context_engine/utils.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py'] | OK |
| module-98c19da7eaec | bossman/trading_learning/claims.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py'] | OK |
| module-9b294ff2bf89 | bossman/trading_learning/lessons.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_paper_memory.py'] | OK |
| module-9bcb4ce56153 | bossman/learning_guard/evidence_ledger.py | GREEN | import + pytest PASS @3a1b8469; ['test_audit_learn_promotion.py', 'test_evidence_ledger_hostile.py', 'test_learning_evidence_ledger.py', 'test_v5_promotion_durable_ledger.py', 'audit ; TOP: learning_guard.evidence_ledger: evidence behind improvement claims | TOP |
| module-9d355deb8dee | bossman/profiles/memory.py | GREEN | import + pytest PASS @3a1b8469; ['test_profiles.py', 'test_profiles_knowledge_isolation.py'] | OK |
| module-b4a66a051db3 | bossman/trading_learning/paper.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_paper_memory.py'] | OK |
| module-c04cfc2bef49 | bossman/context_engine/service.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_ingest_project_scope.py'] | OK |
| module-cdb451a7640c | bossman/trading_learning/replay.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_lookahead.py'] | OK |
| module-d33b374941cd | bossman/learning_guard/service.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_learning_guard_service.py'] [authored_by_lane] ; TOP: learning_guard.service: one-call promotion guard (holdout + A/B + anti-degradation) | TOP |
| module-d68747affe30 | bossman/cybersec/security_memory.py | GREEN | import + pytest PASS @3a1b8469; ['test_cybersec_integration.py'] | OK |
| module-dca65ddd8cea | bossman/trading_learning/frames.py | KEEP | FAIL env: only test (test_trading_pipeline_benchmark) is importorskip(cv2), skipped; no executed test | OK |
| module-e0361cc49cbe | bossman/context_engine/compiler.py | GREEN | import + pytest PASS @3a1b8469; ['test_context_memory_recall_order.py', 'test_stage9_agent_smoke.py'] | OK |
| module-e298a2a29a4b | bossman/learning_guard/holdout.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_learning_guard_service.py', 'test_pass3_autonomy_trainer.py', 'test_trading_pipeline_benchmark.py'] [authored_by_lane] ; TOP: learning_guard.holdout: sealed secret holdout, no learning around it | TOP |
| module-e4cfe203dd2c | bossman/trading_learning/strategy.py | GREEN | import + pytest PASS @3a1b8469; ['test_trading_claims.py', 'test_trading_paper_memory.py'] | OK |
| module-f92b1c5ccf84 | bossman/cognitive/memory.py | KEEP | branch leaf: bossman-core/bossman/cognitive/memory.py absent in this worktree; keep until branch is merged | OK |
| module-fc4458307ec6 | bossman/context_engine/compact.py | GREEN | import + pytest PASS @3a1b8469; ['test_leaf_context_engine_compact.py'] [authored_by_lane] ; TOP: context_engine.compact: anchor-preserving compaction | TOP |
| pv-act | Автоматические действия: только свой тренажёр | KEEP | FAIL env: pokervision.actuator imports cv2 (opencv not installed); tests cannot run | OK |
| pv-coach | COACH: рекомендация с объяснением и неопределённостью | GREEN | import + pytest PASS @3a1b8469; ['apps/poker-vision/tests/test_coach_known_hands.py', 'tests/test_blue_leaf_audit.py', 'apps/poker-vision/tests/test_policy_route.py'] | OK |
| pv-eval | Оценка: сплиты, baseline→after, p50/p95 | KEEP | FAIL env: pokervision.eval.run_eval imports cv2; also no direct run_eval test | OK |
| pv-executor | EXECUTOR: проверки → клик → подтверждение (только свой трена | KEEP | FAIL env: pokervision.control.executor imports cv2; test_control cannot be collected | OK |
| pv-lora-route | Маршрут Vision → политика → проверка ответа → executor | GREEN | import + pytest PASS @3a1b8469; ['apps/poker-vision/tests/test_policy_route.py'] | OK |
| pv-pipeline | Конвейер: кадр → поля → сверка → история | KEEP | FAIL env: pokervision.adapters.poker_train imports cv2 (opencv not installed); tests cannot run | OK |
| pv-source-panel | Панель источника: выбор окна с превью, живой поток, overlay | KEEP | JS panel: same page as pv-ui; needs browser + live source; not testable in this env | OK |
| pv-ui | Страница Poker Vision и один backend | KEEP | JS page: needs browser + running Poker Train (POKERTRAIN_URL) and cv2; no headless test possible here | OK |
| reg-earning_emulator | Эмулятор заработка на удалённой вакансии (симуляция) | GREEN | import + pytest PASS @3a1b8469; ['test_earning_emulator.py'] | OK |
| reg-earning_emulator_cli | Эмулятор заработка: командная строка (один GET публичной стр | GREEN | import + pytest PASS @3a1b8469; ['test_earning_emulator.py'] | OK |
