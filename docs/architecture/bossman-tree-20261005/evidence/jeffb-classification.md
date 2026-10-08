# jeffb lane classification (zone jeff, second half by sorted id, 67 leaves)

GREEN 61 / RETIRE 0 / KEEP 6 / FAIL 0 / TOP 10

RETIRE=0: grep audit (tools/tree_proof/jeffb_importers.py) found no module that is both dead and non-safety; zero-importer modules (j2/persona, j2/director, companion_profile, voice_capability) are Jeff core, tested, or consent/policy related, so they are kept.
Caveat: PASS = import + existing tests textually referencing the module (<=3-8 files per leaf), not semantic certification.

| id | label | verdict | reason | value |
|---|---|---|---|---|
| module-83a15f7aa768 | pit/math_assist.py | GREEN | import in clean subprocess inside worktree + 163 tests passed (test_jeff_math_assist.py, test_jeff_math_runtime.py; 2 candidate files). | OK |
| module-83b0bd4a24b3 | pit/identity.py | GREEN | import in clean subprocess inside worktree + 200 tests passed (test_jeff_2_proactive.py, test_pit_cli.py, test_pit_foundation.py, test_pit_web.py; 4 candidate files). | OK |
| module-842ef81754c0 | pit/passport_checkpoint.py | GREEN | import in clean subprocess inside worktree + 8 tests passed (test_pit_master_parser_2_0.py, test_pit_passport_checkpoint.py; 2 candidate files). | OK |
| module-85f118831d7b | telegram_calls/speech/text.py | GREEN | import in clean subprocess inside worktree + 7 tests passed (test_speech_text.py; 1 candidate files). | OK |
| module-8b9384b5a0ac | telegram_calls/doctor.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/doctor.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-8dfa4ca61d63 | telegram_calls/audio/pcm.py | GREEN | import in clean subprocess inside worktree + 16 tests passed (test_audio_pcm.py, test_loopback_echo_continuity.py; 2 candidate files). | OK |
| module-8ebcf42d990a | pit/bot_guard.py | GREEN | import in clean subprocess inside worktree + 103 tests passed (test_jeff_availability.py, test_jeff_watchdog.py, test_pit_rc19_audit_jeff.py, test_pit_telegram_fake.py, test_telegram_store_location.py, test_companion_local_routes.py; 6 candidate files). | OK |
| module-8f42d3cc74e5 | pit/photo_pipeline.py | GREEN | import in clean subprocess inside worktree + 106 tests passed (test_pit_image_delivery_contracts.py, test_pit_photo_foundation.py, test_pit_runtime.py; 3 candidate files). | OK |
| module-90209d82364c | pit/passport.py | GREEN | import in clean subprocess inside worktree + 109 tests passed (test_jeff_1_5_passport.py, test_jeff_2_memory_palace.py, test_jeff_2_persona.py; 3 candidate files). | TOP |
| module-91e8d95273c5 | pit/j2/safety.py | GREEN | import in clean subprocess inside worktree + 264 tests passed (test_jeff_2_safety.py, test_jeff_audit_fixes.py; 2 candidate files). | TOP |
| module-940780459eb4 | telegram_calls/audio/vad.py | GREEN | import in clean subprocess inside worktree + 64 tests passed (test_endpointer.py, test_session.py, test_worker_and_stop_races.py, test_worker_dial_races.py; 4 candidate files). | OK |
| module-9414ce79578c | pit/doctor_identity.py | GREEN | import in clean subprocess inside worktree + 15 tests passed (test_jeff_doctor_identity.py; 1 candidate files). | OK |
| module-94819ae06213 | pit/context.py | GREEN | import in clean subprocess inside worktree + 88 tests passed (test_pit_foundation.py, test_pit_rc19_jeff.py; 2 candidate files). | OK |
| module-9502d104e91e | pit/speech.py | GREEN | import in clean subprocess inside worktree + 160 tests passed (test_jeff_1_8_voice.py, test_jeff_audit_fixes.py, test_jeff_owner_bugtest_fixes.py; 8 candidate files). | OK |
| module-958ca83ceaa8 | pit/policy.py | GREEN | import in clean subprocess inside worktree + 52 tests passed (test_cu_participant_perimeter.py, test_pit_foundation.py; 2 candidate files). | OK |
| module-9818110824b0 | pit/heartbeat.py | GREEN | import in clean subprocess inside worktree + 89 tests passed (test_jeff_availability.py, test_jeff_doctor_identity.py, test_jeff_owner_bugtest_fixes.py, test_jeff_watchdog.py, test_jeff_watchdog_launcher_pid.py; 5 candidate files). | TOP |
| module-9a23ec9fe2eb | pit/cli.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_owner_one_bossman.py, test_jeff_availability.py, test_jeff_doctor_identity.py; 11 candidate files). | OK |
| module-9c7361a27b1a | pit/j2/pipeline.py | GREEN | import in clean subprocess inside worktree + 223 tests passed (test_jeff_2_insights.py, test_jeff_2_proactive.py, test_jeff_2_quality_lab.py, test_j2_call_perimeter.py; 4 candidate files). | OK |
| module-9de94c4cb278 | telegram_calls/call/selftest.py | GREEN | import in clean subprocess inside worktree + 11 tests passed (test_selftest.py; 1 candidate files). | OK |
| module-9f87418c31e0 | pit/runtime.py | GREEN | import in clean subprocess inside worktree + 172 tests passed (test_autonomy_probes.py, test_cu_participant_perimeter.py, test_jeff_1_8_local.py, test_jeff_1_8_route.py, test_jeff_1_8_stream.py, test_jeff_1_8_telegram_stream.py, test_jeff_1_8_web_stream.py, test_jeff_audit_fixes.py; 32 candidate files). | TOP |
| module-a00067262226 | telegram_calls/addon.py | GREEN | import in clean subprocess inside worktree + 52 tests passed (test_addon.py, test_api_calls.py; 2 candidate files). | OK |
| module-a090c121b689 | pit/master_parser/cli.py | GREEN | import in clean subprocess inside worktree + 21 tests passed (test_pit_master_parser_2_0_narrative.py; 1 candidate files). | OK |
| module-a28716ea704d | pit/config.py | GREEN | import in clean subprocess inside worktree + 41 tests passed (test_jeff_admin.py, test_jeff_availability.py, test_jeff_broadcast.py; 22 candidate files). | OK |
| module-a2a8be1c06e1 | pit/model_route.py | GREEN | import in clean subprocess inside worktree + 42 tests passed (test_jeff_1_8_route.py, test_mandatory_model_policy.py; 2 candidate files). | TOP |
| module-a513ff702c46 | pit/risk.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-a7105ffd5da5 | pit/discovery.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-aacf13d1fab5 | telegram_calls/addons.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/addons.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-b1142d03395c | telegram_calls/types.py | GREEN | import in clean subprocess inside worktree + 86 tests passed (test_account_login.py, test_account_store.py, test_addon.py; 12 candidate files). | OK |
| module-b2924df01fad | pit/j2/persona.py | GREEN | import in clean subprocess inside worktree + 44 tests passed (test_jeff_2_persona.py; 1 candidate files). | OK |
| module-b2e0eb3245b9 | pit/ollama_native.py | GREEN | import in clean subprocess inside worktree + 37 tests passed (test_jeff_1_8_local.py, test_jeff_1_8_stream.py, test_pit_master_parser_2_0.py, test_pit_ollama_native.py; 4 candidate files). | OK |
| module-b36c82e81920 | telegram_calls/account/credentials.py | GREEN | import in clean subprocess inside worktree + 113 tests passed (test_account_login.py, test_account_store.py, test_acl_windows.py, test_hardening.py, test_worker_and_stop_races.py; 5 candidate files). | OK |
| module-b3a1a55f12b0 | telegram_calls/speech/tts.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/speech/tts.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-b864400370f1 | pit/master_parser/engine.py | GREEN | import in clean subprocess inside worktree + 49 tests passed (test_jeff_master_parser_surfaces.py, test_pit_master_parser.py, test_pit_master_parser_2_0.py, test_pit_master_parser_2_0_narrative.py; 4 candidate files). | OK |
| module-bc4dba6e1890 | pit/roleplay.py | GREEN | import in clean subprocess inside worktree + 129 tests passed (test_pit_foundation.py, test_pit_runtime.py; 2 candidate files). | OK |
| module-bf7480cb496c | telegram_calls/__main__.py | GREEN | import in clean subprocess inside worktree + 2 tests passed (test_leaf_telegram_calls_main.py; 1 candidate files). authored_by_lane test. | LOW |
| module-c0879a714994 | telegram_calls/audio/echo.py | GREEN | import in clean subprocess inside worktree + 15 tests passed (test_echo.py, test_echo_early_fit.py; 2 candidate files). | OK |
| module-ca6609a6b448 | pit/presentation.py | GREEN | import in clean subprocess inside worktree + 96 tests passed (test_local_russian_voice.py, test_pit_foundation.py, test_pit_rc19_jeff.py; 3 candidate files). | OK |
| module-ce4bec7cff7e | pit/behavior_controller.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-cf717b6f2546 | telegram_calls/call/offline_mode.py | GREEN | import in clean subprocess inside worktree + 59 tests passed (test_api_calls.py, test_worker_and_stop_races.py; 2 candidate files). | OK |
| module-d0bec156ed2c | pit/master_parser/documents.py | GREEN | import in clean subprocess inside worktree + 5 tests passed (test_master_parser_docling_adapter.py; 1 candidate files). | OK |
| module-d25f75686dc7 | telegram_calls/speech/brain.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/speech/brain.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-d6fff5861410 | pit/master_parser/__main__.py | GREEN | import in clean subprocess inside worktree + 3 tests passed (test_leaf_master_parser_main.py; 1 candidate files). authored_by_lane test. | LOW |
| module-d794cbd77cf3 | pit/categories.py | GREEN | import in clean subprocess inside worktree + 5 tests passed (test_leaf_categories.py; 1 candidate files). authored_by_lane test. | LOW |
| module-d87d5bf5141c | telegram_calls/hardening.py | GREEN | import in clean subprocess inside worktree + 25 tests passed (test_hardening.py, test_acl_windows.py, test_calls_e2e_offline.py; 5 candidate files). | OK |
| module-d93b8a2e7320 | pit/vault.py | GREEN | import in clean subprocess inside worktree + 251 tests passed (test_jeff_1_5_passport.py, test_jeff_2_insights.py, test_jeff_2_media.py, test_jeff_2_memory_palace.py, test_jeff_2_persona.py, test_jeff_admin.py, test_jeff_memory_self_description.py, test_jeff_settings_overlay.py; 19 candidate files). | TOP |
| module-dc92260872d5 | pit/j2/quality_lab.py | GREEN | import in clean subprocess inside worktree + 104 tests passed (test_jeff_2_insights.py, test_jeff_2_quality_lab.py; 2 candidate files). | OK |
| module-dfb6c31ae04f | pit/resources.py | GREEN | import in clean subprocess inside worktree + 140 tests passed (test_pit_rc19_jeff.py, test_pit_resources.py, test_pit_runtime.py; 5 candidate files). | OK |
| module-e2624a8bf0fc | pit/speech_audit.py | GREEN | import in clean subprocess inside worktree + 115 tests passed (test_jeff_audit_fixes.py, test_mandatory_pre_tts_audit.py; 2 candidate files). | OK |
| module-e3621a3791e3 | pit/j2/director.py | GREEN | import in clean subprocess inside worktree + 183 tests passed (test_jeff_2_director.py, test_jeff_audit_fixes.py; 2 candidate files). | OK |
| module-e3b155c353db | pit/model_policy.py | GREEN | import in clean subprocess inside worktree + 34 tests passed (test_mandatory_model_policy.py; 1 candidate files). | TOP |
| module-e52098ab7553 | pit/voice.py | GREEN | import in clean subprocess inside worktree + 19 tests passed (test_pit_telegram_fake.py, test_pit_voice.py; 2 candidate files). | OK |
| module-e67d89bd6ff2 | pit/collector.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-ebc21c85581a | telegram_calls/audio/endpointer.py | GREEN | import in clean subprocess inside worktree + 42 tests passed (test_endpointer.py, test_session.py; 2 candidate files). | OK |
| module-eddee4708a7d | pit/secret_filter.py | GREEN | import in clean subprocess inside worktree + 24 tests passed (test_blue_leaf_audit.py, test_pit_secret_filter.py; 2 candidate files). | TOP |
| module-eeb5c7530a12 | pit/models.py | GREEN | import in clean subprocess inside worktree + 227 tests passed (test_cu_participant_perimeter.py, test_jeff_1_5_passport.py, test_jeff_2_insights.py, test_jeff_2_media.py, test_jeff_2_memory_palace.py, test_jeff_2_persona.py, test_jeff_admin.py, test_jeff_memory_self_description.py; 29 candidate files). | OK |
| module-eff0fd116111 | pit/behavior_scores.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-f26b63157c2f | pit/public_guard.py | GREEN | import in clean subprocess inside worktree + 300 tests passed (test_autonomy_identity_task.py, test_jeff_audit_fixes.py, test_mandatory_disclosure.py, test_pit_foundation.py, test_pit_rc19_jeff.py, test_pit_reply_self_disclosure.py; 6 candidate files). | TOP |
| module-f4c52a69b401 | telegram_calls/speech/stt.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/speech/stt.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-f526d2fd0ef3 | pit/cloud_budget.py | GREEN | import in clean subprocess inside worktree + 42 tests passed (test_jeff_settings_overlay.py, test_pit_rc19_audit_jeff.py; 2 candidate files). | TOP |
| module-f8c8574f9ee2 | pit/companion_profile.py | GREEN | import in clean subprocess inside worktree + 48 tests passed (test_pit_foundation.py; 1 candidate files). | OK |
| module-fa8e48038ace | pit/passport_api.py | GREEN | import in clean subprocess inside worktree + 4 tests passed (test_leaf_passport_api.py; 1 candidate files). authored_by_lane test. | OK |
| module-fb3e7ddff256 | telegram_calls/stopflag.py | KEEP | code absent from this worktree (branch tgcalls-work only, command-center/bcc/telegram_calls/stopflag.py); not retired, no audit proof of duplication; needs the branch merged | OK |
| module-ffd7b5564673 | pit/tts_engines.py | GREEN | import in clean subprocess inside worktree + 27 tests passed (test_jeff_1_8_voice.py, test_jeff_english.py; 2 candidate files). | OK |
| reg-oss_chatterbox | Chatterbox: офлайн-клон голоса (отдельный воркер) | GREEN | import in clean subprocess inside worktree + 3 tests passed (test_local_voice_clone.py; 1 candidate files). | OK |
| reg-oss_piper | Piper: локальный русский TTS | GREEN | import in clean subprocess inside worktree + 30 tests passed (test_jeff_1_8_voice.py, test_local_russian_voice.py, test_local_voice_clone.py; 3 candidate files). | OK |
| reg-oss_whisper | Whisper: офлайн-распознавание речи | GREEN | import in clean subprocess inside worktree + 96 tests passed (test_oss_integrations.py, test_oss_integrations_routes.py, test_oss_whisper.py, test_doctor_row.py, test_manager.py, test_stt_real_path.py; 6 candidate files). | OK |
| reg-voice_capability | Возможности голосовых провайдеров | GREEN | import in clean subprocess inside worktree + 7 tests passed (test_v26_voice_capability.py; 1 candidate files). | OK |
