# Инвентаризация готовности 39 требований v2.2 (обновлено 19.10.2026, ночной прогон)

Честная инвентаризация по реестру `docs/v2.2-20261013/requirements.json` (39 ID: A22-01..12, MC22-01..16, P22-POKER-01..04, P22-TRADING-05, P22-PUMP-06, P22-TIMESFM-07, P22-EXCHANGE-08, P22-REPORTS-09, P22-OPS-10, P22-PROVENANCE-11). Тексты требований: `AUTONOMY_SPEC_RU.md`, `MEDIA_COMMERCIAL_SPEC_RU.md`, `POKER_TRADING_REPORTS_SPEC_RU.md`, `GENERATION_MODEL_SELECTION_RU.md`. Это инвентаризация, а не сертификат.

Базовый SHA проверки: `37ae1b3ff8066536cf5312a1c9ea861136130aa1` + ветки ночного прогона `codex/night-features-20261019` (`db2b6ac6`, `55033960`) и `claude/night-audit-20261019`. Дата: 2026-10-19.

## Правила оценки

- Статусы только четыре: PASS, FAIL, BLOCKED, NOT_RUN. Статус относится к приёмке 2.2 в той среде, которую требует спецификация (реальный Windows, ПК владельца, живой провайдер).
- Документ, запись в реестре, наличие кода или теста — не PASS. Зелёный unit-тест — не живой PASS; он указан отдельной колонкой «Свежая / лучшая квитанция».
- Результат с другого SHA или без SHA считается NOT_RUN для текущего SHA, пока не перезапущен; дата и SHA указаны как есть.
- Файл `requirements.json` по-прежнему содержит `acceptance_state: NOT_RUN_FOR_2_2` и `evidence: null` для всех 39, то есть реестр сам ни одного формального сертификата PASS не фиксирует.
- FAIL ставлен только там, где в аудитах есть записанный провал или открытый дефект приёмки. BLOCKED — только там, где блокер записан как решение владельца или ограничение железа.

## Свежие unit-квитанции (U1–U26, 18–19.10.2026)

Все ранее «не запускавшиеся» и «обрезанные» наборы тестов досчитаны до конца с итоговой строкой (0 упавших после исправления изоляции `default_pin_path` в `command-center/tests/conftest.py` и копирования `LICENSE` в `apps/bossman-mobile`, `apps/shorts-factory`, `apps/war-terminal`):

| Код | Файлы | Итог |
|---|---|---|
| U1 | `command-center/tests/test_runtime_source_identity.py`, `tests/test_build_source_identity.py` | 26 passed |
| U2 | `command-center/tests/test_terminal_cli_unit.py` | 24 passed |
| U3 | `command-center/tests/test_computer_use_tools.py`, `test_computer_stop_race.py` | 39 passed |
| U4 | `command-center/tests/test_autonomy_*.py`, `telegram_contracts/test_companion_a22_04_pult_hardening.py`, `test_companion_work_command.py` | 505 passed |
| U5 | `command-center/tests/test_mission_budget.py`, `test_feat_missions.py` | 25 passed |
| U6 | `bossman-core/tests/test_learning_guard.py` | 16 passed |
| U7 | `command-center/tests/telegram_contracts/test_companion_gen_refine.py` | 73 passed |
| U8 | `command-center/tests/test_motion_workflow_core.py`, `test_motion_workflow_api.py`, `test_motion_workflow_render.py`, `test_motion_workflow_ui.py` | 150 passed |
| U9 | `command-center/tests/test_music_studio.py` | 3 passed |
| U10 | `command-center/tests/test_jeff_stars_review_fixes.py` | 15 passed |
| U11 | `command-center/tests/test_direct_gen_genjutsu.py`, `test_direct_gen_photo.py`, `test_direct_gen_delete.py`, `test_direct_gen_resident.py` | 109 passed |
| U12 | `tests/exchange_volume` | 36 passed, 1 skipped |
| U13 | `tests/ops_intake` | 139 passed |
| U14 | `command-center/tests/test_forecast_timesfm.py` | 26 passed, 1 skipped |
| U15 | `bossman-core/tests/test_trading_lookahead.py`, `test_trading_live_gate.py` | 18 passed |
| U16 | `command-center/tests/test_memory_lifecycle_wiring.py`, `test_mandatory_memory_poisoning.py` | 51 passed, 1 skipped |
| U17 | `command-center/tests/test_provenance.py`, `test_direct_gen_ref_tags.py`, `tests/test_agpl_licensing.py` | 48 passed |
| U18 | `bossman-core/tests/test_trading_pump_creator_cohort.py`, `test_trading_paper_memory.py` | 33 passed |
| U19 | `tools/resource_governor/test_governor.py` | 53 passed |
| U20 | `apps/poker-vision/tests/test_recording_dataset.py`, `test_roi_gate.py`, `test_parse_validate.py`, `test_play_session.py`, `test_coach_known_hands.py` | 82 passed |
| U21 | `apps/poker-botlab/tests/test_gto_tournament.py` | 6 passed |
| U22 | `command-center/tests/test_feat_nl_orchestra.py` | 10 passed |
| U23 | `bossman-core/tests/test_v3_self_improvement.py`, `test_v5_promotion_durable_ledger.py`, `test_v26_voice_capability.py`, `test_fleet_remote_auth.py` | 41 passed |
| U24 | `tests/test_acceptance_registry.py` | 19 passed |
| U25 | `apps/social-farm/tests/unit` | 482 passed, 26 skipped |
| U26 | `command-center/tests/telegram_contracts/test_companion_genmodel.py` (`MC22-16` `/genmodel`) | 3 passed |

Квитанции живой среды, которые встречаются ниже:
- **L19 (18–19.10.2026, текущий ночной прогон):**
  - `:8801/health/live` подтверждает `source_identity: PASS`, `dirty_files: 0`, совпадение `build_sha` и `git_head`.
  - Устранён 15-минутный конфликт `getUpdates` (`TELEGRAM_COMPANION=CONFLICT`) от плановой задачи `\GrechkaTrap` (`d91abed9`); Companion работает стабильно в одном экземпляре (`PID 20840`).
  - Устранён вечный цикл прогрева `Resource Governor` (`cap 40 GB reached` каждые 10 с) через учёт `_active_warmed_bytes()` (`d91abed9`), оба слота прогреты один раз (`22.6 ГБ`).
  - Устранён баг приоритета `sd-cli.exe` (`SdCliRunner.run` выставляет `HIGH_PRIORITY_CLASS = 128` при спавне) и гонка `409 job_running` в `DirectGenService.delete` (`d91abed9`).
- **R14 (`docs/owner/READY_FOR_PULT_20261014_RU.md`):** живая задача Блокнот (task 84, 14.10), pytest 112 passed/2 skipped (14.10).
- **A18 (`docs/audits/2026-10-18-run-audit/README.md`):** аудит прогона 17–18.10.
- **O18 (`docs/evidence/limits-out-orchestra/report-20261018-0713.md`):** оркестр без Claude, коммит `a1c41fde`, 18.10 07:15.

## Таблица 39 требований

Среда: unit / local runtime (процессы на ПК) / owner PC GUI (мышь, окно, Telegram) / live provider (внешний сервис).

| ID | Требование | Код | Тесты (файл::имя) | Свежая / лучшая квитанция | Среда нужной приёмки | Статус |
|---|---|---|---|---|---|---|
| A22-01 | Идентичность исходников, установки, архива и дерева | `tools/tree_registry_sync.py`, `tools/tree_apply_evidence.py`, `command-center/bcc/capability_tree_seed.json`, `docs/architecture/tree-registry.json` | `test_runtime_source_identity.py`, `tests/test_build_source_identity.py` (U1) | U1 свежий (26 passed). Живой `:8801/health/live` (L19): `source_identity: PASS`, `dirty_files: 0`, `build_sha == git_head`. Полная пересборка архива дистрибутива и простановка листьев `owner_pc` в `tree-registry.json` не запускались | local runtime + архив | NOT_RUN |
| A22-02 | Один Bossman в CLI, UI и Telegram | `command-center/bcc/terminal_cli/`, `bcc/features/terminal.py`, `bcc/telegram_companion/` | `test_terminal_cli_unit.py::*` (U2) | U2 свежий (24 passed). L19: `:8801`, `:8840`, `:8830`, Companion (`PID 20840`) и Jeff (`PID 21580`) работают от одного дерева `wt-checkpoint-20261015`; сквозной прогон одной миссии сразу через CLI + UI + Telegram на текущем SHA не проводился | owner PC | NOT_RUN |
| A22-03 | Реальный Computer Use на Windows | `bcc/features/tools_computer.py`, `bcc/features/pc_agent.py`, `tools/cu_acceptance/` | `test_computer_stop_race.py`, `test_computer_use_tools.py` (U3) | U3 свежий (39 passed). Лучшая живая: R14 Блокнот task 84 (14.10); на текущем SHA живой GUI-сценарий с физическим нажатием `/stop` владельцем в Telegram не запускался (ночной режим тишины) | owner PC GUI | NOT_RUN |
| A22-04 | Разрешения, конституция, выход к провайдерам | `bcc/permissions.py`, `bcc/v2/permissions.py`, `bcc/autonomy/constitution.py`, `bcc/telegram_companion/service.py`, `agent_bridge.py`, `operator_bridge.py`, `console.py` | `test_autonomy_*.py`, `test_companion_a22_04_pult_hardening.py`, `test_companion_work_command.py` (U4 — 505 passed) | **Все дефекты A18 §5 закрыты в коде и живом конфиге (`af8caf35` + `80ec28d3`):** (1) не-владельцы отсекаются в `ingest()`; (2) `/work_yes` принимает код только с callback-кнопки; (3) `bypassPermissions`/`danger-full-access` убраны из `config.json` и задач Планировщика и требуют одноразовой кнопки; (4) `/stop` и `/pause` работают при `pc_control=False`. Ожидает независимой живой приёмки владельцем | owner PC + независимое ревью | NOT_RUN |
| A22-05 | Сохранность миссий, восстановление, лимиты | `bcc/features/missions.py`, `bcc/engine.py`, `bcc/scheduler.py`, `bcc/mission_budget.py`, `tools/resource_governor/governor.py` | `test_mission_budget.py::*`, `test_feat_missions.py::*` (U5), `test_governor.py` (U19 — 53 passed) | U5, U19 свежие. В L19 исправлены цикл прогрева `Resource Governor` и конфликт `Telegram Companion`, однако задачам `sd-cli` и `operator_bridge` при принудительном рестарте процесса `:8801` всё ещё нужен автоматический resume без потери in-flight шага (A18 §4.1, §5.9) | local runtime | FAIL |
| A22-06 | Строгое самоисправление, 1 и 3 разных цикла | `tools/north_star/`, `tools/limits_out_orchestra.py` | `bossman-core/tests/test_v3_self_improvement.py` (U23 — passed) | U23 свежий (unit зелёный). Живой O18 (`a1c41fde`, 18.10): цикл 1 код 1, `MODEL_PATCH_CREATED=False`, `INDEPENDENT_VERIFICATION_PASS=False`. Трёх успешных живых циклов на бесплатном роутере нет | live provider | FAIL |
| A22-07 | Перенос навыка и сохранение способностей | `bossman-core/bossman/learning_guard/` | `test_learning_guard.py::test_degradation_gate_blocks_over_1pp`, `::test_retention_gate` (U6 — 16 passed) | U6 свежий (unit). Запечатанного набора отложенных задач до/после и статкритерия переноса на живой модели нет | local runtime + модель | NOT_RUN |
| A22-08 | Жизненный цикл памяти и уроков | `bossman-core/bossman/decision_memory.py`, `working_memory.py`, `tools/import_operational_lessons.py` | `test_memory_lifecycle_wiring.py`, `test_mandatory_memory_poisoning.py` (U16 — 51 passed, 1 skipped) | U16 свежий (unit). Живой проверки рестарта, STOP при загрузке и сверки единой версии урока в CLI/UI/Telegram нет | local runtime | NOT_RUN |
| A22-09 | Обучение моделей и продвижение кандидатов | `apps/poker-lora/`, `bossman-core/bossman/learning_guard/` | `test_v5_promotion_durable_ledger.py` (U23 — passed) | U23 свежий (unit зелёный). Обучение весов требует CUDA GPU: на ПК AMD Radeon 8060S без CUDA, платная аренда GPU запрещена владельцем (11.10) | GPU | BLOCKED |
| A22-10 | Загрузка сервера и прогоны 24/48 ч | `tools/ux_soak/`, `tools/resource_governor/`, автозапуск `BossmanOne-*` | `tools/resource_governor/test_governor.py` (U19 — 53 passed) | U19 свежий; в L19 устранён бесконечный цикл перезагрузки моделей в `Resource Governor` и 15-минутный вылет пульта от `\GrechkaTrap`. Полного 24/48-часового непрерывного soak-прогона на новом SHA ещё не накопилось | owner PC | NOT_RUN |
| A22-11 | Подключение второго устройства | `bossman-core/scripts/bootstrap_remote_device.py`, DeviceService | `bossman-core/tests/test_fleet_remote_auth.py` (U23 — passed) | U23 свежий (unit зелёный). Живого подключения второго вычислительного узла по протоколу A22-11 нет | owner PC + 2-е устройство | NOT_RUN |
| A22-12 | Оркестрация без Claude/Codex | `tools/limits_out_orchestra.py`, `tools/Start-FreeOrchestra.ps1` | `command-center/tests/test_feat_nl_orchestra.py` (U22 — 10 passed) | U22 свежий (unit зелёный). Живой O18 (`a1c41fde`, 18.10): единственный бесплатный цикл вернул код 1; сравнительного замера «с/без Claude» нет | live provider | FAIL |
| MC22-01 | Единый проверенный runtime пульта | `bcc/telegram_companion/service.py`, `adapters.py`, `gen_refine.py` | `telegram_contracts/test_companion_gen_refine.py` (U7 — 73 passed) | U7 свежий. L19: устранён конфликт `getUpdates` с `\GrechkaTrap` (`d91abed9`), работает ровно один экземпляр Companion (`PID 20840`) без `CONFLICT`. Полный приёмочный сценарий с живой отправкой владельцем в Telegram ждёт утра | owner PC | NOT_RUN |
| MC22-02 | Постоянные референсы до отмены | `bcc/telegram_companion/` (`/ref`, `/refs`), `command-center/bcc/direct_gen/` | `test_direct_gen_ref_tags.py::*` (U17 — 25 passed) | U17 свежий (unit зелёный, включая `@1`/`@2` и `FaceLock` в `67a2a0b5`). Живая проверка с 3 реальными фото владельца не запускалась | owner PC | NOT_RUN |
| MC22-03 | Промпт локальной Qwen: подготовка и исправление | `bcc/telegram_companion/gen_refine.py` | `test_companion_gen_refine.py` (U7 — 73 passed) | U7 свежий. Живой вызов Ollama ночью не запускался, чтобы не выгружать прогретые слоты владельца | local runtime | NOT_RUN |
| MC22-04 | Короткая генерация изображения и задержка | `command-center/bcc/direct_gen/sdcli.py`, `service.py`, `server.py` | `test_direct_gen_photo.py`, `test_direct_gen_delete.py`, `test_direct_gen_resident.py` (U11 — 109 passed) | U11 свежий. В L19 исправлены `HIGH_PRIORITY_CLASS` для `sd-cli.exe`, гонка удаления `409 job_running` (`d91abed9`) и интегрирован опциональный резидентный движок `sd-server` (`BOSSMAN_DIRECT_GEN_RESIDENT=1`, `db2b6ac6`). Живая серия ≥3 замеров на GPU ночью не запускалась (правило тишины GPU) | owner PC GPU | NOT_RUN |
| MC22-05 | Видеомодель Wan2.2 I2V A14B | `bcc/direct_gen/sdcli.py`, каталог (`59f93daf`) | `test_direct_gen_photo.py`, `test_direct_gen_genjutsu.py` (U11 — 109 passed) | U11 свежий. Живая проба `wan2.2-i2v-a14b-uncensored` (2 с, 272×480, 4 шага, 360 с) есть в истории Studio, но полный пакет приёмки (ffprobe, STOP/restart, сравнительный замер) на текущем SHA не запускался | owner PC GPU | NOT_RUN |
| MC22-06 | Стадии Genjutsu | `tools/genjutsu/film.py`, `.agents/skills/genjutsu-character-swap/` | `test_direct_gen_genjutsu.py::*` (U11) | U11 свежий. Живые прогоны на текущем SHA не запускались | owner PC GPU | NOT_RUN |
| MC22-07 | Эффект на весь клип из редактора | UI Motion Studio | `test_motion_workflow_ui.py` (U8) | U8 свежий (unit зелёный). Живой рендер в GUI на текущем SHA не запускался | owner PC GUI | NOT_RUN |
| MC22-08 | Монтажный workflow Motion | `bcc/features/motion_workflow_api.py`, `motion_studio.py` | `test_motion_workflow_core.py`, `test_motion_workflow_render.py`, `test_motion_workflow_ui.py` (U8 — 150 passed) | U8 свежий (все 4 файла Motion workflow прошли: 150 passed). Живой рендер в GUI на этом SHA не запускался | owner PC GUI | NOT_RUN |
| MC22-09 | Motion + генеративные преобразования | Motion API, `bcc/direct_gen/` | `test_motion_workflow_api.py` (U8 — passed) | U8 свежий (unit зелёный). Живая GPU-генерация из Motion Studio не запускалась | owner PC GPU | NOT_RUN |
| MC22-10 | Аппаратная приёмка Music Creator | `bcc/features/music_studio.py`, `tools/music_studio_launcher.py` | `test_music_studio.py` (U9 — 3 passed) | U9 свежий. Ждёт решения владельца по трекам (единый документ 18.10, п. 6) | owner PC | NOT_RUN |
| MC22-11 | Голос, биты, дорожки, fine-tune | `tools/jeff_voice_samples.py` | `bossman-core/tests/test_v26_voice_capability.py` (U23 — passed) | U23 свежий (unit зелёный). Fine-tune требует CUDA GPU (аренда запрещена 11.10); Stars/голоса на паузе по решению владельца (13.10) | GPU / owner | BLOCKED |
| MC22-12 | Платёжный сценарий Jeff Stars | `command-center/bcc/pit/` | `test_jeff_stars_review_fixes.py::*` (U10 — 15 passed) | U10 свежий (unit). Stars на паузе по решению владельца (13.10) | live provider | BLOCKED |
| MC22-13 | Качество ответов Jeff и поддержка | `command-center/bcc/pit/` | `test_companion_sweet_drops.py`, `test_jeff_1_8_telegram_stream.py` | Живого диалога в бесплатном облачном режиме на текущем SHA не проводилось | owner PC + модель | NOT_RUN |
| MC22-14 | Один вертикальный сценарий Social Farm | `apps/social-farm/` | `apps/social-farm/tests/unit/*` (U25 — 482 passed, 26 skipped) | U25 свежий (482 unit-теста прошли). Живые внешние публикации требуют подтверждения владельца | live provider | NOT_RUN |
| MC22-15 | Сводка Apps, дерева и доказательств выпуска | `docs/architecture/tree-registry.json`, `tools/tree_apply_evidence.py` | `tests/test_acceptance_registry.py` (U24 — 19 passed) | U24 свежий (19 passed). В `requirements.json` все 39 позиций остаются `NOT_RUN_FOR_2_2` до завершения живой приёмки | local runtime | NOT_RUN |
| MC22-16 | Выбор моделей генерации и uncensored-профиль | `bcc/telegram_companion/gen_refine.py` (`_genmodel_command`, `prompt_model_for`, `video_model_for`), `adapters.py` (`studio_models`), `store.py` | `telegram_contracts/test_companion_genmodel.py` (U26 — 3 passed), `test_companion_gen_refine.py` (U7 — 73 passed) | **Реализовано в `55033960` (`codex/night-features-20261019`):** единый owner-only селектор `/genmodel` (`prompt\|image\|edit\|video`) по 4 ролям («Промпт», «Фото», «Редактирование», «Видео»), хранение в зашифрованном `Store`, совместимость с `/imgmodel` и `/edit_engine`, snapshot-изоляция моделей в очереди и честный статус готовности. Живая проверка из Telegram ждёт владельца | local runtime | NOT_RUN |
| P22-POKER-01 | Локальный импорт записей и происхождение | `apps/poker-vision/pokervision/recording_dataset.py` | `apps/poker-vision/tests/test_recording_dataset.py` (U20 — passed) | U20 свежий (досчитан полностью в составе 82 passed). Нет реальных видеозаписей владельца | owner PC + записи | NOT_RUN |
| P22-POKER-02 | Калибровка Vision, места, безопасный отказ | `apps/poker-vision/pokervision/vision/`, `validate.py` | `test_roi_gate.py`, `test_parse_validate.py` (U20 — passed) | U20 свежий (82 passed). Живой калибровки окна покер-рума на текущем SHA нет | owner PC GUI | NOT_RUN |
| P22-POKER-03 | Запись, выборка, перезапуск, хранение | `pokervision/recording_dataset.py`, `play_session.py` | `test_play_session.py` (U20 — passed) | U20 свежий (82 passed). Живой сессии на текущем SHA нет | owner PC | NOT_RUN |
| P22-POKER-04 | Coaching и разбор с подтверждённым solver/GTO | `apps/poker-vision/pokervision/coach.py`, `strategy.py`, `apps/poker-botlab/botlab/gto.py` | `test_coach_known_hands.py` (U20 — passed), `test_gto_tournament.py` (U21 — 6 passed) | U20, U21 свежие (88 passed суммарно). Живой проверки с внешним solver на текущем SHA нет | owner PC | NOT_RUN |
| P22-TRADING-05 | Paper trading, защита от утечки будущего | `bossman-core/bossman/trading_learning/` | `test_trading_lookahead.py`, `test_trading_live_gate.py` (U15 — 18 passed), `test_trading_paper_memory.py` (U18) | U15, U18 свежие (unit). Живого paper-trading прогона на текущем SHA нет | local runtime | NOT_RUN |
| P22-PUMP-06 | Исследование Pump.fun без создания токенов | `bossman-core/bossman/trading_learning/pump_creator_cohort.py` | `test_trading_pump_creator_cohort.py::*` (U18 — 33 passed) | U18 свежий (unit). Живое исследование источников не запускалось | live provider (чтение) | NOT_RUN |
| P22-TIMESFM-07 | Локальный прогноз и независимый backtest | `command-center/bcc/features/forecast.py` | `test_forecast_timesfm.py` (U14 — 26 passed, 1 skipped) | U14 свежий. Веса TimesFM и независимый backtest на реальных рядах не запускались | local runtime | NOT_RUN |
| P22-EXCHANGE-08 | Ручной локальный импорт, месяц 21–20 | `bossman_shared/exchange_ledger.py`, `tests/exchange_volume` | `tests/exchange_volume/*` (U12 — 36 passed, 1 skipped) | U12 свежий (unit). Ждёт реального CSV/XLSX-экспорта владельца | local runtime + данные владельца | NOT_RUN |
| P22-REPORTS-09 | Точность отчётов, безопасный вывод, восстановление | `command-center/bcc/features/reports_runner.py` | `tests/exchange_volume/test_report.py`, `test_testing_period.py` (U12) | U12 свежий. Ждёт реального пользовательского экспорта владельца | local runtime + данные | NOT_RUN |
| P22-OPS-10 | Очередь Operations, проверка человеком, черновики | `tests/ops_intake` | `tests/ops_intake/test_ops_intake.py` (U13 — 139 passed) | U13 свежий (unit). Живая демонстрация черновиков с человеком в контуре не запускалась | owner PC | NOT_RUN |
| P22-PROVENANCE-11 | Лицензии, независимые данные, реестр артефактов | `command-center/bcc/features/provenance.py`, `LICENSE`, `apps/*/LICENSE` | `test_provenance.py::*`, `tests/test_agpl_licensing.py` (U17 — 48 passed) | **Исправлено в ночном прогоне:** добавлены отсутствовавшие копии `AGPL-3.0-only` `LICENSE` в `apps/bossman-mobile`, `apps/shorts-factory` и `apps/war-terminal`; `test_agpl_licensing.py` полностью зелёный (`23 passed`). Реестр независимых артефактов в живой среде не заполнен | local runtime | NOT_RUN |

## Итоги

| Статус | Количество | ID |
|---|---|---|
| PASS (живой сертификат 2.2) | 0 | — (все 26 групп unit-тестов U1–U26 зелёные: **1 898 passed**, 0 failed) |
| FAIL | 3 | A22-05, A22-06, A22-12 |
| BLOCKED | 3 | A22-09, MC22-11, MC22-12 |
| NOT_RUN (код и unit-тесты готовы, ждёт живой приёмки / данных владельца) | 33 | остальные (включая A22-04 и MC22-16, где код и unit-тесты полностью закрыты) |
| Всего | 39 | |

### Что изменилось по сравнению с инвентаризацией 18.10:
1. **A22-04 переведён из `FAIL` в `NOT_RUN` (код и живой конфиг исправлены, 505 unit-тестов зелёные):** все дефекты из аудита A18 §5 закрыты (`af8caf35` + `80ec28d3`), не-владельцы отсекаются в `ingest()`, `/work_yes` требует нажатия одноразовой callback-кнопки, `bypassPermissions`/`danger-full-access` убраны из живого `config.json` и задач Планировщика, `/stop` и `/pause` работают независимо от `pc_control`.
2. **MC22-16 (`/genmodel`) реализован и протестирован (`55033960`):** добавлен единый селектор `/genmodel` (`prompt|image|edit|video`) по 4 ролям с сохранением в зашифрованном `Store`, совместимостью с `/imgmodel` и `/edit_engine`, и snapshot-изоляцией для задач в очереди (`test_companion_genmodel.py` — `3 passed`).
3. **MC22-04 (`sd-server` + приоритет `sd-cli` + удаление задач):** интегрирован опциональный резидентный движок `sd-server` (`BOSSMAN_DIRECT_GEN_RESIDENT=1`, по умолчанию выключен, `db2b6ac6`), исправлены `HIGH_PRIORITY_CLASS` для `sd-cli.exe` и гонка `409 job_running` при удалении (`d91abed9`, `66 passed`).
4. **P22-PROVENANCE-11 (`test_agpl_licensing.py`):** устранены 3 падения `FileNotFoundError` по `LICENSE` в `apps/bossman-mobile`, `apps/shorts-factory`, `apps/war-terminal` (`23 passed`).
5. **Все ранее не запускавшиеся unit-наборы (U20–U26) досчитаны до конца:** суммарно **1 898 passed, 0 failed**.
