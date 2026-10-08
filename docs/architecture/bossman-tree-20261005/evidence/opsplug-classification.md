# Lane opsplug - classification (ops + plugins leaves still blue at 679d019e)

Total leaves: 89; GREEN 52, RETIRE 1, KEEP 36; TOP 12 (13%).

Receipts: evidence/opsplug.json (GREEN and FAIL), evidence/opsplug-retire.json (RETIRE); outputs in evidence/out/<id>.txt. Re-run: tools/tree_proof/opsplug_probe.py, opsplug_plugins_probe.py, opsplug_finalize.py.

| id | label | verdict | reason | value |
|---|---|---|---|---|
| cap-46 | Ресурсы · VRAM · offline · безопасность | GREEN | pytest receipt @ d394085d: import+pytest (7 passed in 8.65s) | OK |
| mod-agentmap | agentmap | GREEN | pytest receipt @ d394085d: import+pytest+authored_by_lane (4 passed in 2.90s) | LOW |
| mod-assistant_plan | assistant_plan | KEEP | code only on branch claude/bossman-closure-engineering-b8h722; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-autonomy | autonomy | KEEP | FAIL: test_autonomy_bounded_loop::test_tree_runner_kills_the_whole_worker_tree_when_stop_appears (TreeRunner.run returns before the killed worker pid is gone, ~50 ms race; product bug or flaky test, owner decision) | OK |
| mod-benchlab | benchlab | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (6 passed in 14.26s) | OK |
| mod-candidate_generator | candidate_generator | KEEP | code only on branch claude/v2-reasoning-engine; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-eval_engine | eval_engine | KEEP | code only on branch claude/v2-reasoning-engine; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-jeff_insights | jeff_insights | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (41 passed, 2 skipped in 6.45s) | TOP |
| mod-mimik | mimik | KEEP | code only on branch claude/bossman-final-convergence-hu2702; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-observatory | observatory | KEEP | code only on branch feat/viral-vfx-observatory-20260921; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-open_news | open_news | KEEP | code only on branch claude/bossman-final-convergence-hu2702; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-osiris | osiris | KEEP | FAIL: test_web_research_net::test_szhatyy_otvet_otvergaetsya_transportom (gzip guard no longer fires: safe_get strips Content-Encoding since 20e2c879) - real regression, not fixed here by rule | OK |
| mod-pattern_miner | pattern_miner | KEEP | code only on branch claude/v2-reasoning-engine; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-promotion_gate | promotion_gate | KEEP | code only on branch claude/v2-reasoning-engine; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-trace_recorder | trace_recorder | KEEP | code only on branch claude/v2-reasoning-engine; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-twitter | twitter | KEEP | needs owner (Twitter API credentials) and the code lives only on branch feature/osiris-data-acquisition | OK |
| mod-v15_owner | v15_owner | RETIRE | Дубликат: файл удалён из истории HEAD коммитом 8a2fbaad 'remove duplicate owner control backend'; живой аналог v15_owner_run.py; на слово v15_owner нет ни одной ссылки в коде, CI и конфиге. | LOW |
| mod-viral_vfx | viral_vfx | KEEP | code only on branch feat/viral-vfx-observatory-20260921; absent from this checkout, cannot be run; needs a merge decision | OK |
| mod-workflow | workflow | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (5 passed in 7.66s) | OK |
| module-028fe7482116 | bossman/cybersec/benchmark.py | GREEN | pytest receipt @ da3ca4d3: import+pytest (14 passed in 0.12s) | OK |
| module-0dc288edd324 | bossman/sandbox/toolbox.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (12 passed in 0.91s) | OK |
| module-0ef222c310ce | bossman/cognitive/storage.py | KEEP | code only on branch feature/cognitive-10-10-memory-context-reasoning-longtasks; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-0ef2333ef23c | bossman/resource_brain/brain.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (13 passed in 0.64s) | TOP |
| module-1622458b008a | bossman/sandbox/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (12 passed in 0.98s) | OK |
| module-22827787ecc5 | bossman/resource_brain/models.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (13 passed in 0.63s) | OK |
| module-45ef5a5cbb02 | bossman/notifications/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed, 20 warnings in 1.77s) | OK |
| module-4780a1d5fdf7 | bossman/apprentice/guards.py | GREEN | pytest receipt @ d394085d: import+pytest (7 passed in 2.10s) | TOP |
| module-49743fcdb397 | bossman/cognitive/reasoning.py | KEEP | code only on branch feature/cognitive-10-10-memory-context-reasoning-longtasks; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-531fc123cd17 | bossman/profiles/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed in 0.61s) | OK |
| module-5579b793e038 | bossman/search_everything/service.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (11 passed in 0.92s) | OK |
| module-575dcc5a99f5 | bossman/computer_operator/applist.py | GREEN | pytest receipt @ d394085d: import+pytest (48 passed in 2.64s) | LOW |
| module-59f6fbe27bd5 | bossman/toolkit/_proc.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (7 passed in 2.83s) | OK |
| module-5bfdc6603e86 | bossman/remote_client/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed in 0.62s) | OK |
| module-5fcedb9e4e99 | bossman/resource_brain/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (13 passed in 0.80s) | OK |
| module-6c727703c8ea | bossman/apprentice/owner_auth.py | GREEN | pytest receipt @ d394085d: import+pytest (7 passed in 2.20s) | TOP |
| module-6e615a668c7b | bossman/cost_control/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (7 passed, 32 warnings in 2.30s) | TOP |
| module-725a466f4b8f | bossman/notifications/bridge.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (3 passed in 0.45s) | OK |
| module-7280885daae5 | bossman/dev_factory/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed, 16 warnings in 1.41s) | OK |
| module-73976a1cf54d | bossman/apprentice/_bootstrap.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (5 passed in 0.12s) | LOW |
| module-79b7a3bcfc36 | bossman/cognitive/tasks.py | KEEP | code only on branch feature/cognitive-10-10-memory-context-reasoning-longtasks; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-7b69eef1f974 | bossman/cognitive/verify.py | KEEP | code only on branch feature/cognitive-10-10-memory-context-reasoning-longtasks; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-930117f0081f | bossman/cognitive/runtime.py | KEEP | code only on branch feature/cognitive-10-10-memory-context-reasoning-longtasks; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-9958189ad4d4 | bossman/apprentice/proc_tree.py | GREEN | pytest receipt @ d394085d: import+pytest (26 passed, 1 skipped in 28.05s) | TOP |
| module-a11338c485f5 | bossman/sandbox/resources.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (12 passed in 0.69s) | OK |
| module-a71668fad443 | bossman/video_factory/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (21 passed in 1.60s) | OK |
| module-a7ad0d90993c | bossman/apprentice/composition.py | GREEN | pytest receipt @ d394085d: import+pytest (7 passed in 2.14s) | OK |
| module-aaff14c2d261 | bossman/notifications/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (2 passed in 1.30s) | OK |
| module-abd99037a01e | bossman/sandbox/dataset.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (12 passed in 0.73s) | TOP |
| module-ae9598734759 | bossman/toolkit/office.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (10 passed in 0.34s) | LOW |
| module-b52d0534a802 | bossman/reality/mission_ir.py | KEEP | code only on branch v7/phase1-reality-core-20260908; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-bc12e1784cb5 | bossman/apprentice/durable.py | GREEN | pytest receipt @ d394085d: import+pytest (9 passed in 1.53s) | OK |
| module-cb1aad058144 | bossman/research/models.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (5 passed in 0.09s) | OK |
| module-d3f5977af5ba | bossman/benchmark/sandbox_row.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (10 passed in 0.19s) | TOP |
| module-d8ca33ba94f4 | bossman/apprentice/outreach.py | GREEN | pytest receipt @ d394085d: import+pytest (8 passed in 0.95s) | OK |
| module-dadaec7d827b | bossman/benchmark/engine.py | GREEN | pytest receipt @ da3ca4d3: import+pytest (6 passed in 17.08s) | OK |
| module-de49198eacce | bossman/remote_client/auth.py | GREEN | pytest receipt @ d394085d: import+pytest (9 passed in 1.38s) | OK |
| module-df0754d72d22 | bossman/cost_control/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed in 0.33s) | OK |
| module-e11418351fd7 | bossman/search_everything/connectors.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (11 passed in 1.00s) | TOP |
| module-ecf0929e5652 | bossman/apprentice/selector_repair.py | KEEP | code only on branch feature/ai-streamer-higgsfield-browser-20260908; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-eea9bcf7e3ac | bossman/company/runtime.py | GREEN | pytest receipt @ d394085d: import+pytest (6 passed in 0.27s) | OK |
| module-f21597ba1fd6 | bossman/video_factory/subsystem.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (21 passed in 1.70s) | OK |
| module-f538937e8cf0 | bossman/reality/strategy.py | KEEP | code only on branch v7/phase1-reality-core-20260908; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-fd070cf37fd7 | bossman/reality/world_state.py | KEEP | code only on branch v7/phase1-reality-core-20260908; absent from this checkout, cannot be run; needs a merge decision | OK |
| module-fe0ffb0d2f88 | bossman/resource_brain/routes.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (3 passed, 12 warnings in 1.25s) | OK |
| module-ff7440d8b674 | bossman/benchmark/__main__.py | GREEN | pytest receipt @ da3ca4d3: import+pytest+authored_by_lane (4 passed in 0.64s) | OK |
| reg-blue_leaf_audit_tool | Аудит синих листьев по записанным прогонам тестов | GREEN | pytest receipt @ d394085d: import+pytest (9 passed in 0.31s) | LOW |
| reg-leaf_usefulness_queue | Очередь проверки листьев по полезности (гипотеза) | GREEN | pytest receipt @ d394085d: import+pytest+authored_by_lane (5 passed in 0.06s) | LOW |
| reg-tree_registry_sync | Синхронизация дерева с реестром: уровни доказательств | GREEN | pytest receipt @ d394085d: import+pytest (7 passed in 0.48s) | LOW |
| plugin-10 | github.issue_create | KEEP | outward/writing: github.issue_create posts to GitHub (ASK); needs owner token + approved effect | OK |
| plugin-11 | gmail.search | KEEP | needs owner: Gmail OAuth (GMAIL_OAUTH) not available in this environment | OK |
| plugin-12 | gmail.send | KEEP | outward/writing: gmail.send sends mail (ASK, destructive); needs owner OAuth + approved effect | OK |
| plugin-13 | calendar.search | KEEP | needs owner: Google OAuth (GOOGLE_OAUTH) not available in this environment | OK |
| plugin-14 | calendar.create | KEEP | outward/writing: calendar.create writes to the owner's calendar (ASK); needs OAuth + approved effect | OK |
| plugin-15 | drive.search | KEEP | needs owner: Google OAuth (GOOGLE_OAUTH) not available in this environment | OK |
| plugin-16 | drive.write | KEEP | outward/writing: drive.write writes files to Drive (ASK); needs OAuth + approved effect | OK |
| plugin-17 | telegram.status | KEEP | needs owner: Telegram bot token (TELEGRAM_BOT_TOKEN); handler is still the NOT_TESTED_LIVE stub | OK |
| plugin-18 | telegram.send | KEEP | outward/writing: telegram.send messages people (ASK, destructive); needs approved effect | OK |
| plugin-19 | n8n.workflow_list | KEEP | needs env: a configured n8n instance + N8N_API_KEY; handler is still the NOT_TESTED_LIVE stub | OK |
| plugin-20 | n8n.workflow_run | KEEP | outward/writing: n8n.workflow_run executes workflows (ASK, destructive); needs n8n + approved effect | OK |
| plugin-21 | browser.open | KEEP | needs real work: browser.open handler not implemented (stub); must be wired to the existing browser subsystem | OK |
| plugin-22 | browser.form_submit | KEEP | outward/writing: browser.form_submit submits forms (ASK, destructive); needs approved effect | OK |
| plugin-5 | mcp.tool_list | GREEN | live_call receipt @ da3ca4d3: live_call+pytest authored_by_lane (====================== 3 passed, 24 deselected in 3.34s =======================) | OK |
| plugin-6 | mcp.tool_call | KEEP | outward/writing: mcp.tool_call runs a foreign tool (ASK, destructive); effect not exercised, needs owner approval flow | OK |
| plugin-7 | ollama.chat | GREEN | live_call receipt @ da3ca4d3: live_call+pytest authored_by_lane (====================== 5 passed, 22 deselected in 1.55s =======================) | TOP |
| plugin-8 | openrouter.chat | GREEN | live_call receipt @ 9fc68e0d: live_call+pytest authored_by_lane (====================== 5 passed, 22 deselected in 1.68s =======================) | TOP |
| plugin-9 | github.repo_read | GREEN | live_call receipt @ da3ca4d3: live_call+pytest authored_by_lane (====================== 12 passed, 15 deselected in 2.54s ======================) | OK |
| plugins-mcp | MCP · хаб и runtime | GREEN | pytest receipt @ d394085d: import+pytest (9 passed in 0.52s) | OK |
| plugins-oss | OSS-движки · инвентарь | GREEN | pytest receipt @ d394085d: import+pytest (12 passed in 0.67s) | LOW |
| plugins-security | Безопасность плагинов · scopes | GREEN | pytest receipt @ d394085d: import+pytest+authored_by_lane (3 passed in 0.52s) | TOP |
