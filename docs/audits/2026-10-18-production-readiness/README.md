# Инвентаризация готовности 39 требований v2.2 (18.10.2026)

Честная инвентаризация по реестру `docs/v2.2-20261013/requirements.json` (39 ID: A22-01..12, MC22-01..16, P22-POKER-01..04, P22-TRADING-05, P22-PUMP-06, P22-TIMESFM-07, P22-EXCHANGE-08, P22-REPORTS-09, P22-OPS-10, P22-PROVENANCE-11). Тексты требований: `AUTONOMY_SPEC_RU.md`, `MEDIA_COMMERCIAL_SPEC_RU.md`, `POKER_TRADING_REPORTS_SPEC_RU.md`, `GENERATION_MODEL_SELECTION_RU.md`. Это инвентаризация, а не сертификат.

Текущий SHA проверки: `67a2a0b54239ff78c89c724d254e9f575d33dfb3` (`main`, рабочая папка `wt-checkpoint-20261015`). Дата: 2026-10-18.

## Правила оценки

- Статусы только четыре: PASS, FAIL, BLOCKED, NOT_RUN. Статус относится к приёмке 2.2 в той среде, которую требует спецификация (реальный Windows, ПК владельца, живой провайдер).
- Документ, запись в реестре, наличие кода или теста — не PASS. Зелёный unit-тест — не живой PASS; он указан отдельной колонкой «unit (свежий)».
- Результат с другого SHA или без SHA считается NOT_RUN для текущего SHA, пока не перезапущен; дата и SHA указаны как есть.
- Если квитанцию не нашёл — NOT_RUN. Файл `requirements.json` по-прежнему содержит `acceptance_state: NOT_RUN_FOR_2_2` и `evidence: null` для всех 39, то есть реестр сам ни одного PASS не фиксирует.
- FAIL ставлен только там, где в аудитах есть записанный провал или открытый дефект приёмки. BLOCKED — только там, где блокер записан как решение владельца или ограничение железа.
- Итог: **PASS = 0**. Это не «код отсутствует», а «нет ни одной квитанции на текущий SHA в нужной среде».

## Свежие unit-квитанции (получены в этой работе)

Команда (из корня репозитория, интерпретатор `C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe`, приоритет BELOW_NORMAL, без сети и GPU, службы не трогались): `python -m pytest -q -x --no-header -p no:cacheprovider <файлы>`. SHA `67a2a0b5`, 2026-10-18. Результаты:

| Код | Файлы | Итог |
|---|---|---|
| U1 | `command-center/tests/test_runtime_source_identity.py`, `tests/test_build_source_identity.py` | 26 passed |
| U2 | `command-center/tests/test_terminal_cli_unit.py` | 24 passed |
| U3 | `command-center/tests/test_computer_use_tools.py`, `test_computer_stop_race.py` | 39 passed |
| U4 | `command-center/tests/test_autonomy_constitution.py` | 15 passed |
| U5 | `command-center/tests/test_mission_budget.py`, `test_feat_missions.py` | 25 passed |
| U6 | `bossman-core/tests/test_learning_guard.py` | 16 passed |
| U7 | `command-center/tests/telegram_contracts/test_companion_gen_refine.py` | 71 passed |
| U8 | `command-center/tests/test_motion_workflow_core.py` | 100 passed |
| U9 | `command-center/tests/test_music_studio.py` | 3 passed |
| U10 | `command-center/tests/test_jeff_stars_review_fixes.py` | 15 passed |
| U11 | `command-center/tests/test_direct_gen_genjutsu.py`, `test_direct_gen_photo.py` | 49 passed |
| U12 | `tests/exchange_volume` | 36 passed, 1 skipped |
| U13 | `tests/ops_intake` | 139 passed |
| U14 | `command-center/tests/test_forecast_timesfm.py` | 26 passed, 1 skipped |
| U15 | `bossman-core/tests/test_trading_lookahead.py`, `test_trading_live_gate.py` | 18 passed |
| U16 | `command-center/tests/test_memory_lifecycle_wiring.py`, `test_mandatory_memory_poisoning.py` | 51 passed, 1 skipped |
| U17 | `command-center/tests/test_provenance.py`, `test_direct_gen_ref_tags.py` | 25 passed |
| U18 | `bossman-core/tests/test_trading_pump_creator_cohort.py`, `test_trading_paper_memory.py` | 33 passed |
| U19 | `tools/resource_governor/test_governor.py` | 51 passed |

Прогон `apps/poker-vision/tests/test_recording_dataset.py` + `test_roi_gate.py` не дал итоговой строки в моём выводе (обрезан хвост) — как квитанцию не учитываю.

Старые квитанции, которые встречаются ниже (все с другого SHA, для текущего — NOT_RUN):
- R14: `docs/owner/READY_FOR_PULT_20261014_RU.md` — живая задача Блокнот (task 84, 14.10, ветка `fix/pc-agent-security-20261014`, SHA в документе не указан), pytest 112 passed/2 skipped (14.10), решение `release_main #42` PENDING.
- R13: `docs/owner/evidence/stage22/task_receipt.json` — доказательство Qwen-оператора на изолированной базе, 13.10 (коммит `8752a6e8`).
- A18: `docs/audits/2026-10-18-run-audit/README.md` — аудит прогона 17–18.10 (уровни LIVE/CODE+TESTS/CLAIM; SHA не привязан, это отчёт агента, а не файл квитанции).
- O18: `docs/evidence/limits-out-orchestra/report-20261018-0713.md` — оркестр без Claude, коммит `a1c41fde`, 18.10 07:15.

## Таблица 39 требований

Среда: unit / local runtime (процессы на ПК) / owner PC GUI (мышь, окно, Telegram) / live provider (внешний сервис).

| ID | Требование | Код | Тесты (файл::имя) | Свежая / лучшая квитанция | Среда нужной приёмки | Статус |
|---|---|---|---|---|---|---|
| A22-01 | Идентичность исходников, установки, архива и дерева | `tools/tree_registry_sync.py`, `tools/tree_apply_evidence.py`, `command-center/bcc/capability_tree_seed.json`, `docs/architecture/tree-registry.json` | `test_runtime_source_identity.py`, `tests/test_build_source_identity.py` (U1) | U1 свежий (unit). Реестр дерева: 0 листьев `owner_pc` (AGENTS.md 08.10, обновление `e1f3e517` 15.10); аудит A18 §5.10: SHA `main` расходится в четырёх документах. Живой идентичности установки/архива на этот SHA нет | local runtime + архив | NOT_RUN |
| A22-02 | Один Bossman в CLI, UI и Telegram | `command-center/bcc/terminal_cli/`, `bcc/features/terminal.py`, `bcc/telegram_companion/` | `test_terminal_cli_unit.py::*` (U2), `test_terminal_cli_e2e.py` (не запускался) | U2 свежий. Живое: пульт и мобильное работают (A18), но сверки «одна идентичность в CLI/UI/Telegram» нет; A18 §5.1: пульт и бэкенд шли с разных версий (частично исправлено) | owner PC | NOT_RUN |
| A22-03 | Реальный Computer Use на Windows | `bcc/features/tools_computer.py`, `bcc/features/pc_agent.py`, `tools/cu_acceptance/` | `test_computer_stop_race.py::test_stop_during_in_flight_action_blocks_the_queued_one`, `test_computer_use_tools.py` (U3) | U3 свежий. Лучшая живая: R14 Блокнот task 84 (Telegram → окно → файл 84 байта SHA-256 `86eaacfc...`), 14.10, SHA не записан; реальный `/stop` владельцем из Telegram не нажимался (R14 §6.2); STOP в целом проверен только в тестах (A18 §3) | owner PC GUI | NOT_RUN |
| A22-04 | Разрешения, конституция, выход к провайдерам | `bcc/permissions.py`, `bcc/v2/permissions.py`, `bcc/autonomy/constitution.py`, `bcc/features/nl_permissions.py`, `bossman_shared/privacy.py` | `test_autonomy_constitution.py::test_real_constitution_exists_and_hashes`, `::test_missing_pin_blocks` (U4) | U4 свежий; пин v2 совпал на 14.10 (R14, `7d845db3...`). Открытые дефекты по A18 §5 (чтение кода, вживую не воспроизводились): `bypassPermissions` у Claude/Codex в конфиге пульта, `/work_yes` обходит одноразовую кнопку, сообщения не-владельцев обрабатываются, одобрение = знание единого токена. Пока они стоят, приёмка пройти не может | owner PC + независимое ревью | FAIL |
| A22-05 | Сохранность миссий, восстановление, лимиты | `bcc/features/missions.py`, `bcc/engine.py`, `bcc/scheduler.py`, `bcc/mission_budget.py` | `test_mission_budget.py::*`, `test_feat_missions.py::*` (U5) | U5 свежий. Живой провал A18 §4.1 (17.10): рестарт бэкенда оборвал идущую генерацию; A18 §5.9: зависший бэкенд не перезапускается, задачи оператора теряются при рестарте. Распределитель: 51 тест (U19) — это не миссии | local runtime | FAIL |
| A22-06 | Строгое самоисправление, 1 и 3 разных цикла | `tools/north_star/`, `tools/limits_out_orchestra.py` | `bossman-core/tests/test_v3_self_improvement.py` (не запускался) | O18 (a1c41fde, 18.10): цикл 1 код 1, MODEL_PATCH_CREATED=False, INDEPENDENT_VERIFICATION_PASS=False. Единый документ состояния 18.10: «первый цикл честно провален». Трёх циклов нет | live provider | FAIL |
| A22-07 | Перенос навыка и сохранение способностей | `bossman-core/bossman/learning_guard/` | `test_learning_guard.py::test_degradation_gate_blocks_over_1pp`, `::test_retention_gate` (U6) | U6 свежий (unit). Запечатанного набора, замера переноса и статкритерия нет | local runtime + модель | NOT_RUN |
| A22-08 | Жизненный цикл памяти и уроков | `bossman-core/bossman/decision_memory.py`, `working_memory.py`, `tools/import_operational_lessons.py` | `test_memory_lifecycle_wiring.py`, `test_mandatory_memory_poisoning.py` (U16) | U16 свежий (unit). Проверки рестарта, STOP при загрузке, единой версии урока в CLI/UI/Telegram нет. Память: уроки не меняют исход (запись памяти 2026-10, только как контекст) | local runtime | NOT_RUN |
| A22-09 | Обучение моделей и продвижение кандидатов | `apps/poker-lora/`, `bossman-core/bossman/learning_guard/`, `bossman-core/tests/test_v5_promotion_durable_ledger.py` | `apps/poker-lora/tests/*`, `test_v5_promotion_durable_ledger.py` (не запускались) | Квитанций нет. Обучение требует GPU: на ПК AMD без CUDA, аренда GPU запрещена владельцем (11.10) | GPU | BLOCKED |
| A22-10 | Загрузка сервера и прогоны 24/48 ч | `tools/ux_soak/`, `tools/resource_governor/`, автозапуск `BossmanOne-*` | `tools/resource_governor/test_governor.py` (U19) | Перезагрузка ПК (06:08, A18 §8) подняла службы — одна пара наблюдений, не soak. Зависание ПК 05:47 (Kernel-Power 41) не объяснено. 24/48-часового прогона нет | owner PC | NOT_RUN |
| A22-11 | Подключение второго устройства | `bossman-core/scripts/bootstrap_remote_device.py`, DeviceService | `bossman-core/tests/test_remote_client.py`, `test_fleet_remote_auth.py` (не запускались) | Вход с телефона в мобильное приложение по Tailscale (A18) — другой контур и не подключение вычислительного устройства по A22-11. Квитанции нет | owner PC + 2-е устройство | NOT_RUN |
| A22-12 | Оркестрация без Claude/Codex | `tools/limits_out_orchestra.py`, `tools/Start-FreeOrchestra.ps1`, `command-center/tests/test_feat_nl_orchestra.py` | `test_feat_nl_orchestra.py` (не запускался) | O18 (a1c41fde, 18.10): единственный цикл провален (см. A22-06); автозапуска оркестра нет; сравнительного замера «с/без Claude» нет | live provider | FAIL |
| MC22-01 | Единый проверенный runtime пульта | `bcc/telegram_companion/service.py`, `adapters.py`, `gen_refine.py` | `telegram_contracts/test_companion_gen_refine.py::*` (U7) | U7 свежий. R14: один poller и ответ владельцу, но 14.10 и другой SHA; A18 §5.1 пульт/бэкенд разных версий; дубли Джеффа (A18 §4.3). На текущий SHA ответа в Telegram нет | owner PC | NOT_RUN |
| MC22-02 | Постоянные референсы до отмены | `bcc/telegram_companion/` (`/ref`, `/refs`), `command-center/bcc/direct_gen/` | `test_direct_gen_ref_tags.py::*` (U17) | U17 свежий. Живая проверка хешей и порядка входов на реальном редактировании не найдена; «с 3 фото не проверено» (единый документ 18.10) | owner PC | NOT_RUN |
| MC22-03 | Промпт локальной Qwen: подготовка и исправление | `bcc/telegram_companion/gen_refine.py` | `test_companion_gen_refine.py::test_vid_launches_at_once_with_the_refined_prompt`, `::test_image_entry_points_launch_at_once` (U7) | U7 свежий. Настоящего ответа локальной модели с именем и задержкой на этом SHA нет | local runtime | NOT_RUN |
| MC22-04 | Короткая генерация изображения и задержка | `command-center/bcc/direct_gen/` | `test_direct_gen_photo.py::*` (U11) | U11 свежий (unit). A18: LIVE `qwen-image-2.1-uc-edit` 25 шагов 286 с, 20 шагов 1861 с (холодная загрузка), без SHA, без хешей результата, без ≥3 повторов и теста cancel, как требует спецификация | owner PC GPU | NOT_RUN |
| MC22-05 | Видеомодель Wan2.2 I2V A14B | `bcc/direct_gen/sdcli.py`, каталог, коммит `59f93daf` | `test_direct_gen_photo.py`, `test_direct_gen_genjutsu.py` (U11) | U11 свежий. A18/единый документ: `wan2.2-i2v-a14b-uncensored` 2 с, 272×480, 4 шага, 360 с — одна живая проба, без манифеста с лицензиями/откатом, ffprobe, STOP/restart, сравнительного замера. T2V VACE A14B не работает (видеопамять) | owner PC GPU | NOT_RUN |
| MC22-06 | Стадии Genjutsu | `tools/genjutsu/film.py`, `.agents/skills/genjutsu-character-swap/` | `test_direct_gen_genjutsu.py::*` (U11) | U11 свежий. Живые прогоны 10.10 (Gate 0/1) — старый SHA; на текущий нет | owner PC GPU | NOT_RUN |
| MC22-07 | Эффект на весь клип из редактора | UI Motion Studio | специальных тестов не нашёл | Квитанций нет | owner PC GUI | NOT_RUN |
| MC22-08 | Монтажный workflow Motion | `bcc/features/motion_workflow_api.py`, `motion_studio.py` | `test_motion_workflow_core.py::*` (U8), `test_motion_workflow_render.py`, `..._ui.py` (не запускались) | U8 свежий (unit). Живой рендер в приложении на этом SHA не найден | owner PC GUI | NOT_RUN |
| MC22-09 | Motion + генеративные преобразования | Motion API, `bcc/direct_gen/` | `test_motion_workflow_api.py` (не запускался) | Квитанций нет | owner PC GPU | NOT_RUN |
| MC22-10 | Аппаратная приёмка Music Creator | `bcc/features/music_studio.py`, `tools/music_studio_launcher.py` | `test_music_studio.py::test_provider_is_local_loopback_only` (U9) | U9 свежий. Решение по трекам открыто (единый документ 18.10, пункт 6); замера на железе нет | owner PC | NOT_RUN |
| MC22-11 | Голос, биты, дорожки, fine-tune | `tools/jeff_voice_samples.py`, `bossman-core/tests/test_v26_voice_capability.py` | не запускались | Fine-tune требует GPU: нет CUDA, аренда запрещена (11.10); Stars/голоса на паузе по решению владельца (13.10) | GPU / owner | BLOCKED |
| MC22-12 | Платёжный сценарий Jeff Stars | `command-center/bcc/pit/` | `test_jeff_stars_review_fixes.py::*` (U10) | U10 свежий (unit). Stars на паузе по решению владельца (память 13.10; READY_FOR_PULT §6.3); платёж не проверялся | live provider | BLOCKED |
| MC22-13 | Качество ответов Jeff и поддержка | `command-center/bcc/pit/` | `-k "pit or jeff"` — 16.10: 2164 passed, 1 skipped (чужой SHA, не повторён) | Живого диалога в облачном бесплатном режиме не было (A18 §2) | owner PC + модель | NOT_RUN |
| MC22-14 | Один вертикальный сценарий Social Farm | `apps/social-farm/` | `apps/social-farm/tests/unit/*` (не запускались), `tests/owner_scenarios/scn_23_social_farm.py` | Квитанций нет; внешние публикации требуют решения владельца | live provider | NOT_RUN |
| MC22-15 | Сводка Apps, дерева и доказательств выпуска | `docs/architecture/tree-registry.json`, `tools/tree_apply_evidence.py` | `tests/test_acceptance_registry.py` (не запускался) | Реестр: 0 `owner_pc`; требования: `evidence: null` у всех 39; `verify_manifest.py` не запускался. Документы расходятся (A18 §5.10) | local runtime | NOT_RUN |
| MC22-16 | Выбор моделей генерации и uncensored-профиль | в коде `/genmodel` и снимков предпочтений не найдено (`git grep genmodel` пусто); частично `bcc/direct_gen/` каталог | тестов нет | Не реализовано: AGENTS.md сам говорит «документация не активирует /genmodel». Две Wan A14B в каталоге с пометкой NOT_RUN | local runtime | NOT_RUN |
| P22-POKER-01 | Локальный импорт записей и происхождение | `apps/poker-vision/pokervision/recording_dataset.py` | `apps/poker-vision/tests/test_recording_dataset.py` (прогон без итоговой строки, не учитываю) | Нет реальных записей владельца и их манифеста; `evidence/trainer_match_20261010` — старый SHA | owner PC + записи | NOT_RUN |
| P22-POKER-02 | Калибровка Vision, места, безопасный отказ | `apps/poker-vision/pokervision/vision/`, `validate.py` | `test_roi_gate.py`, `test_parse_validate.py` (не запускались) | Квитанций на SHA нет | owner PC GUI | NOT_RUN |
| P22-POKER-03 | Запись, выборка, перезапуск, хранение | `pokervision/recording_dataset.py`, `play_session.py` | `test_play_session.py` (не запускался) | Квитанций нет | owner PC | NOT_RUN |
| P22-POKER-04 | Coaching и разбор с подтверждённым solver/GTO | `apps/poker-vision/pokervision/coach.py`, `strategy.py`, `apps/poker-lora/` | `apps/poker-vision/tests/test_coach_known_hands.py`, `apps/poker-botlab/tests/test_gto_tournament.py` (не запускались) | В отдельной папке Argon есть незакоммиченная правка `strategy.py` (см. ниже); происхождение solver не подтверждено | owner PC | NOT_RUN |
| P22-TRADING-05 | Paper trading, защита от утечки будущего | `bossman-core/bossman/trading_learning/` | `test_trading_lookahead.py::test_assert_no_lookahead_catches_a_leak`, `test_trading_live_gate.py` (U15), `test_trading_paper_memory.py` (U18) | U15, U18 свежие (unit). Прогона на реальных данных нет | local runtime | NOT_RUN |
| P22-PUMP-06 | Исследование Pump.fun без создания токенов | `bossman-core/bossman/trading_learning/pump_creator_cohort.py` | `test_trading_pump_creator_cohort.py::*` (U18) | U18 свежий (unit). Источники/лицензии/живое исследование не проверены | live provider (чтение) | NOT_RUN |
| P22-TIMESFM-07 | Локальный прогноз и независимый backtest | `command-center/bcc/features/forecast.py` | `test_forecast_timesfm.py::test_disabled_by_default_even_when_the_model_exists` (U14) | U14 свежий (26 passed, 1 skipped). `docs/owner/TIMESFM_20261013_RU.md` — документ; веса и независимый backtest не подтверждены | local runtime | NOT_RUN |
| P22-EXCHANGE-08 | Ручной локальный импорт, месяц 21–20 | `bossman_shared/exchange_ledger.py`, `tests/exchange_volume` | `tests/exchange_volume/test_exchange_volume.py::test_business_period_close`, `::test_prague_dst_boundaries` (U12), `test_exchange_ledger.py` | U12 свежий, только синтетика. Реального экспорта владельца нет (R14: честный отказ без файла) | local runtime + данные владельца | NOT_RUN |
| P22-REPORTS-09 | Точность отчётов, безопасный вывод, восстановление | `command-center/bcc/features/reports_runner.py` | `tests/exchange_volume/test_report.py` (U12), `test_testing_period.py` | U12 свежий. R14: 112 passed, 2 skipped (14.10, чужой SHA). Реальный пользовательский экспорт и восстановление после сбоя не проверены | local runtime + данные | NOT_RUN |
| P22-OPS-10 | Очередь Operations, проверка человеком, черновики | `tests/ops_intake` | `tests/ops_intake/test_ops_intake.py::test_phone_without_country_or_wrong_length_is_ambiguous_never_completed` (U13) | U13 свежий (139 passed, unit). Человек-проверка и черновики без отправки на живом процессе не показаны | owner PC | NOT_RUN |
| P22-PROVENANCE-11 | Лицензии, независимые данные, реестр артефактов | `command-center/bcc/features/provenance.py` | `test_provenance.py::*` (U17), `test_agpl_licensing.py` (не запускался) | U17 свежий (unit). Реестра артефактов с независимыми данными нет | local runtime | NOT_RUN |

## Итоги

| Статус | Количество | ID |
|---|---|---|
| PASS | 0 | — |
| FAIL | 4 | A22-04, A22-05, A22-06, A22-12 |
| BLOCKED | 3 | A22-09, MC22-11, MC22-12 |
| NOT_RUN | 32 | остальные |
| Всего | 39 | |

Пояснения. FAIL по A22-04 основан на открытых дефектах из аудита A18 (чтение кода, вживую не воспроизводились), а не на упавшем автотесте: свежий unit U4 зелёный. 19 свежих unit-прогонов зелёные (всего 0 упавших), но по правилам это не PASS ни одного требования.

## Состояние соседних веток (только чтение)

- `git -C C:\Users\asd\Bossman\wt-pc-agent-argon-20261013 status --short`: ` M apps/poker-vision/pokervision/strategy.py`, `?? apps/poker-botlab/botlab/gto.py`, `?? apps/poker-botlab/tests/test_gto_tournament.py`, `?? tools/poker_gto_tournament.py`. Папка стоит на ветке `release/bossman-one-20261014`, HEAD `8a956d07`. Незакоммиченные Poker-файлы: `strategy.py` (правка, +26/−10 строк) и три новых файла GTO-турнира. Не трогал. Замечание: в `main` файлы `botlab/gto.py` и `test_gto_tournament.py` уже есть как отслеживаемые, то есть эта папка отстаёт от `main` и её новые файлы, вероятно, дубликаты или более старая версия — это надо сверить владельцу/Argon, я не сравнивал содержимое.
- `release/bossman-one-20261014` относительно `origin/main` (`67a2a0b5`): позади на 108 коммитов, впереди на 2 (`8a956d07` — merge `origin/main`, и `3d3d2b76` — fix pc-agent policy_paths/документация). `git cherry` показывает, что `3d3d2b76` в `main` не попал (патч не эквивалентен); `release` не предок `main`. Различие `main...release`: 6 файлов, +135/−41, включая `READY_FOR_PULT_20261014_RU.md` и `BOSSMAN_ONE_PRESENTATION_20261014_RU.md`.
- Рабочая папка этой проверки: untracked `command-center/tgapp.py` — не моя и не тронута (в коммит не входит).

## 5 самых ценных следующих шагов

1. Закрыть дефекты A22-04 (A18 §5): убрать `bypassPermissions` у Claude/Codex в пульте и в задачах `BossmanClaudeRemote`/`BossmanNightAutonomy`, починить обход одноразовой кнопки в `/work_yes`, отсечь сообщения не-владельцев. Ждёт решения владельца; пока это стоит, A22-04 и всё, что от него зависит (A22-02, A22-03, A22-05), не пройти.
2. Снять живой набор квитанций на текущий SHA в одном окне, когда ПК свободен: Telegram → Блокнот → файл → verifier → доставка, реальный `/stop` владельцем, рестарт (A22-02/03/05, MC22-01). Один структурированный файл с SHA и хешами, внесённый в `requirements.json` через штатный инструмент.
3. Починить устойчивость очереди: не терять идущую генерацию и задачи оператора при рестарте, перезапускать зависший бэкенд (A22-05, FAIL). Затем повторить запуск с 3 перезапусками.
4. Довести оркестр без Claude до одного успешного цикла самоисправления с независимой проверкой, затем трёх разных (A22-06, A22-12). Первый цикл 18.10 дал код 1 и пустой патч — разобрать причину по `report-20261018-0713.md`.
5. Для медиа: по каждому из MC22-04/05/06 сделать одну приёмочную пробу с хешем результата, ffprobe, ≥3 повторами и тестом cancel; параллельно реализовать или явно отложить MC22-16 (`/genmodel`), иначе в реестре останется незакрытое требование без кода. Отдельно — получить реальный экспорт владельца для P22-EXCHANGE-08/REPORTS-09 (самые близкие к доходу).

## Что я не смог определить

- Какой именно SHA лежит в установленном runtime и в архиве: backend и пульт не опрашивались (запрещено рестартом и приоритетом генераций владельца), `/health/live` не вызывал.
- SHA живых запусков в A18 и в R14 (R14 называет ветку, но не коммит). Все эти результаты я считаю для текущего SHA непроверенными.
- Работают ли сейчас :8801, :8840, :8830, пульт, Джефф: не проверял.
- Результат `apps/poker-vision/tests/test_recording_dataset.py` и `test_roi_gate.py`: вывод обрезан, итоговой строки нет — повторить.
- Полнота сопоставления «ID → тесты»: прямых ссылок ID в тестах нет (grep по `A22-`/`MC22-`/`P22-` находит только документы), сопоставление сделано по смыслу и именам файлов. Часть файлов упомянута как «не запускался» и могла бы дать больше unit-квитанций; для MC22-07 и MC22-09 тестов я не нашёл.
- Фактическая причина зависания ПК 05:47 и отказа замены лица на тяжёлом видео (A18 §8): в аудите она сама отмечена как недоказанная.
- Наличие у владельца собственных покерных записей и разрешений (P22-POKER-01/03): из репозитория это не видно.
- Содержимое незакоммиченных Poker-файлов Argon и их связь с версиями в `main` — не сравнивал.
