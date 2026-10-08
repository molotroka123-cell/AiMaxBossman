# jeffa classification (zone jeff, first 67 code/branch leaves by sorted id)

RETIRE: 0. Аудит (tools/tree_proof/jeffa_audit.py + grep): 6 модулей без импортёров вне тестов (j2.model_guard, j2.research, j2.media, j2.memory_palace, presentation_profile, voice_bench) не мёртвые: j2-модули подгружаются pipeline.KNOWN_MODULES и описаны в docs/pit/JEFF_2_0_*.md, voice_bench это `python -m` CLI, presentation_profile в tools/jeff_ux_packet.py; model_guard к тому же safety/health. Дубль cap-4 и module-639c8365e585 (один файл participant_admin.py) оставлены оба: cap-4 это публичная capability.

| id | label | verdict | reason | value |
|---|---|---|---|---|
| cap-1 | Память участника и согласие | GREEN | import в чистом subprocess из worktree + 59 passed (pytest); proxy-проверка capability: vault + passport_commands, тесты consent/forget/pause_memory | TOP |
| cap-10 | Звонки | KEEP | Звонки: код и тесты на отдельных ветках; реальный двусторонний звонок и задержки требуют живого Telegram-аккаунта владельца и подтверждения (нужен владелец/env). | OK |
| cap-2 | Паспорта и master parser | GREEN | import в чистом subprocess из worktree + 8 passed (pytest) | OK |
| cap-3 | Настроение и персона | GREEN | import в чистом subprocess из worktree + 48 passed (pytest) | OK |
| cap-4 | Admin Panel | GREEN | import в чистом subprocess из worktree + 153 passed (pytest) | OK |
| cap-5 | Telegram polling и изоляция | GREEN | import в чистом subprocess из worktree + 76 passed (pytest) | OK |
| cap-7 | Голос · STT · TTS | GREEN | import в чистом subprocess из worktree + 19 passed (pytest) | OK |
| module-011a03489f77 | pit/participant_context.py | GREEN | import в чистом subprocess из worktree + 423 passed (pytest) | OK |
| module-01c789a2b739 | pit/passport_commands.py | GREEN | import в чистом subprocess из worktree + 113 passed (pytest) | OK |
| module-0787a5345c6d | pit/photo_edit.py | GREEN | import в чистом subprocess из worktree + 106 passed (pytest) | OK |
| module-08b732dc1d84 | pit/call_surface.py | GREEN | import в чистом subprocess из worktree + 76 passed (pytest) | OK |
| module-09f63bf64e58 | pit/master_parser/passport_sink.py | GREEN | import в чистом subprocess из worktree + 7 passed (pytest); тест authored_by_lane: test_leaf_passport_sink.py | TOP |
| module-0a1e9aa39f26 | pit/router.py | GREEN | import в чистом subprocess из worktree + 139 passed (pytest) | TOP |
| module-0b449131095b | telegram_calls/call/worker.py | GREEN | import в чистом subprocess из worktree + 25 passed (pytest) | OK |
| module-0bd270fb5248 | pit/studio_image_edit.py | GREEN | import в чистом subprocess из worktree + 127 passed (pytest) | OK |
| module-0df0a7bc5733 | telegram_calls/speech/scripted.py | GREEN | import в чистом subprocess из worktree + 14 passed (pytest) | OK |
| module-10b906cde60e | pit/j2/contract.py | GREEN | import в чистом subprocess из worktree + 412 passed (pytest) | OK |
| module-11fe3588a67d | pit/j2/model_guard.py | GREEN | import в чистом subprocess из worktree + 69 passed (pytest) | OK |
| module-1a35294a664a | pit/j2/insights.py | GREEN | import в чистом subprocess из worktree + 41 passed (pytest) | OK |
| module-1d61f405af4c | pit/resilient_chat.py | GREEN | import в чистом subprocess из worktree + 7 passed (pytest); тест authored_by_lane: test_leaf_resilient_chat.py | TOP |
| module-2057e75206c8 | pit/telegram_contract.py | GREEN | import в чистом subprocess из worktree + 48 passed (pytest) | OK |
| module-27f9c4217e34 | telegram_calls/deps.py | GREEN | import в чистом subprocess из worktree + 4 passed (pytest); тест authored_by_lane: test_leaf_telegram_calls_deps.py | OK |
| module-291791405b2c | pit/presentation_profile.py | GREEN | import в чистом subprocess из worktree + 4 passed (pytest) | OK |
| module-2a4383829dfa | pit/photo_commands.py | GREEN | import в чистом subprocess из worktree + 16 passed (pytest) | OK |
| module-2cab55c89d9c | pit/version.py | GREEN | import в чистом subprocess из worktree + 15 passed (pytest) | OK |
| module-3c2cd6ed725d | telegram_calls/call/session.py | GREEN | import в чистом subprocess из worktree + 39 passed (pytest) | OK |
| module-3fb56e2abdc6 | telegram_calls/call/loopback.py | GREEN | import в чистом subprocess из worktree + 53 passed (pytest) | OK |
| module-41bd7c7bdbe4 | pit/web.py | GREEN | import в чистом subprocess из worktree + 105 passed (pytest) | OK |
| module-459def4e9443 | telegram_calls/speech/jeff_engines.py | GREEN | import в чистом subprocess из worktree + 69 passed (pytest) | OK |
| module-46ca918cf2a2 | telegram_calls/account/guard.py | GREEN | import в чистом subprocess из worktree + 55 passed (pytest) | OK |
| module-476d3d1ef4a9 | pit/qwen_vision.py | GREEN | import в чистом subprocess из worktree + 16 passed (pytest) | OK |
| module-4a1f6b8497c8 | pit/master_parser/corpus.py | GREEN | import в чистом subprocess из worktree + 8 passed (pytest); тест authored_by_lane: test_leaf_master_parser_corpus.py | TOP |
| module-4c04aa7a520a | telegram_calls/call/pytgcalls_transport.py | GREEN | import в чистом subprocess из worktree + 22 passed (pytest) | OK |
| module-55c83e0493e7 | pit/topic_policy.py | GREEN | import в чистом subprocess из worktree + 48 passed (pytest) | OK |
| module-566f6d772d69 | pit/master_parser/narrative.py | GREEN | import в чистом subprocess из worktree + 40 passed (pytest) | OK |
| module-579cbbb18435 | telegram_calls/account/login.py | GREEN | import в чистом subprocess из worktree + 40 passed (pytest) | OK |
| module-5a8578230115 | telegram_calls/audio/playout.py | GREEN | import в чистом subprocess из worktree + 5 passed (pytest) | OK |
| module-5ab761d4c710 | telegram_calls/postcall.py | GREEN | import в чистом subprocess из worktree + 20 passed (pytest) | OK |
| module-5b78e96e567a | pit/j2/research.py | GREEN | import в чистом subprocess из worktree + 59 passed (pytest) | OK |
| module-5c2af9d244e7 | pit/voice_bench.py | GREEN | import в чистом subprocess из worktree + 19 passed (pytest) | OK |
| module-5ca4b43f294a | pit/roleplay_commands.py | GREEN | import в чистом subprocess из worktree + 129 passed (pytest) | OK |
| module-5eb076bfba97 | pit/participant_profile.py | GREEN | import в чистом subprocess из worktree + 41 passed (pytest) | OK |
| module-62de71348d62 | pit/blocklist.py | GREEN | import в чистом subprocess из worktree + 10 passed (pytest) | TOP |
| module-639c8365e585 | pit/participant_admin.py | GREEN | import в чистом subprocess из worktree + 153 passed (pytest) | OK |
| module-6429e61dd089 | pit/master_parser/speed.py | GREEN | import в чистом subprocess из worktree + 21 passed (pytest) | OK |
| module-643bc9bd9ef3 | pit/j2/media.py | GREEN | import в чистом subprocess из worktree + 99 passed (pytest) | OK |
| module-64645c8f9fb0 | pit/j2/memory_palace.py | GREEN | import в чистом subprocess из worktree + 46 passed (pytest) | TOP |
| module-679b42c085a9 | telegram_calls/call/manager.py | GREEN | import в чистом subprocess из worktree + 67 passed (pytest) | OK |
| module-67a5b6d5c767 | pit/identity_guard.py | GREEN | import в чистом subprocess из worktree + 169 passed (pytest) | TOP |
| module-67cee7d9cae4 | pit/j2/proactive.py | GREEN | import в чистом subprocess из worktree + 154 passed (pytest) | OK |
| module-6858e3e84765 | telegram_calls/settings.py | GREEN | import в чистом subprocess из worktree + 196 passed (pytest) | OK |
| module-6e060497c158 | pit/broadcast.py | GREEN | import в чистом subprocess из worktree + 7 passed (pytest) | OK |
| module-6ee86a43e787 | pit/tasks.py | GREEN | import в чистом subprocess из worktree + 169 passed (pytest) | OK |
| module-6f3b41ccd19f | telegram_calls/account/stopflag.py | GREEN | import в чистом subprocess из worktree + 134 passed (pytest) | TOP |
| module-6f5944305093 | pit/jeff_settings.py | GREEN | import в чистом subprocess из worktree + 241 passed (pytest) | OK |
| module-715359be81c9 | pit/learning_counters.py | GREEN | import в чистом subprocess из worktree + 15 passed (pytest) | OK |
| module-74990de09699 | telegram_calls/speech/factory.py | GREEN | import в чистом subprocess из worktree + 20 passed (pytest) | OK |
| module-7637ffa19df1 | pit/reply_stream.py | GREEN | import в чистом subprocess из worktree + 56 passed (pytest) | OK |
| module-7974acf47711 | pit/master_parser/sources.py | GREEN | import в чистом subprocess из worktree + 4 passed (pytest) | OK |
| module-7a7a9e12efb7 | pit/moderate_discovery.py | GREEN | import в чистом subprocess из worktree + 48 passed (pytest) | OK |
| module-7a8593010c20 | telegram_calls/doctor_rows.py | GREEN | import в чистом subprocess из worktree + 9 passed (pytest) | OK |
| module-7afc7ce94420 | pit/latency.py | GREEN | import в чистом subprocess из worktree + 16 passed (pytest) | OK |
| module-7c917a080544 | telegram_calls/speech/testing.py | KEEP | telegram_calls/speech/testing.py отсутствует в этом worktree (ветка tgcalls-work); branch-лист без кода здесь: KEEP, не retire. | OK |
| module-7d6b56dd11d4 | telegram_calls/guard.py | KEEP | telegram_calls/guard.py отсутствует в этом worktree (ветка tgcalls-work); branch-лист без кода здесь: KEEP, не retire. | OK |
| module-7fe26d8e1063 | pit/crisis.py | GREEN | import в чистом subprocess из worktree + 102 passed (pytest) | TOP |
| module-81ae3126d244 | pit/capabilities.py | GREEN | import в чистом subprocess из worktree + 52 passed (pytest) | OK |
| module-81fe82cd46bb | pit/photo_runtime.py | GREEN | import в чистом subprocess из worktree + 35 passed (pytest) | OK |
