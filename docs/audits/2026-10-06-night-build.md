# Ночная сборка 05→06.10.2026 — журнал чекпоинтов

Ветка: `goal/bossman-self-improvement-tree-20261005`. Базовый SHA: `2a69b34b`.
Same-product Terminal Run contract: пульт, CLI и дашборд — один и тот же backend; этот журнал ничего не вводит отдельно.
Лестница North Star: достигнут только уровень `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`; `SELF_REPAIR_SINGLE_CYCLE_PASS`
в эту ночь **не заявляется**, пока в журнале нет строки с независимой проверкой (см. задачу 5).

Правило: PASS пишется только со ссылкой на лог/CI по точному SHA. Всё остальное — «частично» или «не проверено».

## Чекпоинт 1 — CI по HEAD (задача 1)

Состояние на `2a69b34b` (GitHub Actions, ветка):
- root-ci (run 37383214099): **FAIL** — `3 failed, 2935 passed, 47 skipped` на py3.11 и py3.12. Тот же провал на `6a5e3d26`,
  `98e6b073`, `469e51e8`, `5d888ce9` → красный не от последних правок, а накопленный.
- Solana safety gates, PostgreSQL run contracts, ASTRA acceptance: success. Command Center CI и Bossman Core CI на этом SHA
  были `pending`/`in_progress` в момент проверки (на предыдущих SHA — `cancelled` из-за гонки concurrency, не FAIL).

Первопричины трёх падений root-ci (воспроизведены локально, Linux, py3.11):
1. `tests/test_ci_secret_scan.py::test_repository_itself_is_clean` — две независимые причины:
   a. `command-center/ui/tests/technical_log.test.mjs` содержит синтетические «ключи»-муляжи для проверки редактирования
      (`sk-fakecredential…`, `AKIA000…`, jwt `…fakeSignature`) без штатной пометки `ci-secret-scan: allow`.
      Фикс: пометка на 4 строках, ровно по штатному механизму (значения заведомо фальшивые). Node-тест файла: 15/15.
   b. Два журнала `docs/testing/sessions/2026-10-02_*.jsonl` по ~5 МБ превышали `MAX_BYTES` (2 МБ) и отбраковывались как
      «unscannable oversized file». Фикс в `tools/ci_secret_scan.py`: крупные **текстовые** журналы (.jsonl/.log/.json/.md/…, до 64 МБ)
      сканируются построчно паттернами провайдеров; не-текст и сверх лимита — по-прежнему fail-closed. Это ужесточение покрытия,
      а не ослабление. В обоих журналах 0 находок.
      Тест `test_oversized_text_log_is_scanned_by_stream_and_still_fails_closed_otherwise` (чистый крупный лог проходит;
      крупный лог с секретом ловится; крупный не-текст отвергается) — **падает на старом сканере**, проходит на новом.
2. `docs/testing/SKIPS_REGISTRY.md` устарел (`skips_registry.py --check`): добавлен новый skip в
   `test_chat_technical_log_browser.py`, сдвинулись номера строк. Фикс: регенерация штатным `tools/skips_registry.py`
   (373 записи, без пустых причин).
3. `tests/test_exact_sha_certify.py::test_owner_release_scenario_79_…` — зависимость от порядка файлов: тест импортировал
   `scn_18_recovery_release` под обычным именем, а `scenario_runner.discover()` — под `bossman_owner_scn_scn_18_…`;
   декоратор `@scenario` регистрирует id один раз → `ValueError: сценарий OS-76 уже реализован`. Воспроизведено парой
   `pytest tests/owner_scenarios tests/test_exact_sha_certify.py` (1 failed) и в обратном порядке (1 failed + 8 errors).
   Фикс: тест грузит модуль под тем же именем, что и `discover()`. После фикса оба порядка: 117 passed.

Локальная проверка после фиксов (не заменяет CI): `pytest tests` — см. ниже строку «итог корневого набора»;
`skips_registry --check` PASS; `update_readme_scorecard --check` PASS; `compileall` OK; `git diff --check` OK;
`ci_secret_scan` PASS.

Итог корневого набора (локально, Linux py3.11, после фиксов): `2939 passed, 47 skipped, 0 failed`. CI по новому SHA — ниже, когда завершится.

## Чекпоинт 2 — сведение веток (задача 2)

Репозиторий был клонирован неглубоко (shallow) — `git fetch --unshallow` выполнен, иначе `HEAD..ветка` врёт. Ветки `origin/pr89-latest`
в удалённом репозитории уже нет — взят `refs/pull/89/head` (0ec2ff52).

| Источник | Что сделано | SHA |
|---|---|---|
| PR #89 (`pr89-latest`, 5 docs 2.1: архитектура autonomous operator, 5 кандидатов, Colibri отложен) | merge, конфликт README → оставлен README линии (заметка 24.09; строка аудита 02.10 совпадала) | `9b2318b3` |
| `claude/bossman-freeze-closure-ohvmon` (1 docs-коммит `68a65803`) | merge, тот же README-конфликт, то же решение | `54f0f38d` |
| `telegram-live-calls-s7-work` `12db1299` (STOP/hangup побеждают дозвон) | cherry-pick -x. Большая часть уже была в линии в новой форме (worker `_dialing/_stop_epoch`, manager `busy/dial_pending/_stop_latched`, control_plane `_calls_inventory`, hardening) → конфликты решены стороной линии. **Не хватало и добавлено:** `CallSession._dial_until_done` (STOP/hangup прерывают дозвон; STOP до run не звонит), замена STOP-файла, если старый недоступен для записи. Три теста падали на линии до порта (`test_stop_while_ringing_*`, `test_hangup_while_ringing_*`, `test_the_stop_flag_is_replaced_*`), проходят после; `test_worker_dial_races` (2) добавлен. Пакет `tests/telegram_calls` без `test_session.py`: 471 passed, 4 skipped; `test_session.py`+`test_account_store.py`: проходят | `ea558d2f` |
| `a0087401` (DACL: `(OI)(CI)F` для каталогов) | смысл **уже в линии** (`bcc/auth.py`), не было теста. Добавлен переносимый тест (подмена icacls) — падает при удалении фикса (проверено), + оригинальный Windows-тест (на Linux skip) | `ab6d6d71` |
| `a6a1b03b` (один префикс API, приёмка панели) | префикс `/api/telegram/calls` **уже в коде линии**; перенесены браузерная приёмка панели (`test_panel_browser`, 2 passed в Chromium) и тест owner stop-all/без перезвона (в линии `stop-all` ставит computer STOP первым, и тот уже кладёт трубку — тест проверяет инварианты, а не порядок). Docs `ARCHITECTURE/CONTINUE` из этого коммита не переносились (docs-only) | `ab6d6d71` |

`wip/*` — только просмотр, в линию **не влито**; полезное:
- `wip/cv-d-20260929`: Jeff `runtime.py` — при рестарте (не STOP) не помечать генерацию Studio как `delivery_unknown`, если фото ещё не ушло: resume
  доставит ровно один раз (теряло готовые картинки). Полезно, без тестов в снапшоте → нужен порт с тестом.
- `wip/cv-e-20260929`: каталог OpenRouter — модель, пропавшая из живого каталога, получает `unavailable` (авто-выбор её пропускает;
  `nex-agi/nex-n2.5-pro:free` снят, а строка оставалась online и валила запуски «unknown cloud pricing»). Полезно для пула бесплатных моделей; без тестов в снапшоте.
- `wip/autonomy-b-20260929`: правка тестов `test_autonomy_*` (−419/+420), по сути переписывание; проверить против текущих тестов, как есть не брать.
- `wip/autonomy-c-20260929`: набор red-team/«mandatory» тестов (identity, disclosure, memory poisoning, pre-TTS audit; ~1500 строк) — ценный кандидат на порт, нужна проверка на текущем коде.
- `wip/motion56-20260929`: Motion Studio library (build_library.py +623, тесты +236, docs) — не связано с ночной целью, оставлено.
- Не вливались (вне задач ночи): `handoff/continuation-20260929` (3244 файла снимка docs → кандидат в `docs/owner/archive/` по решению владельца),
  `feat/bossman-autonomy-funding` (docs заявки, NOT_SUBMITTED — решение владельца), `scratch/root-ci-debug-19` (удалять только с согласия владельца).

## Чекпоинт 3 — открытые находки аудита 05.10 (задача 3)

Поправка к чекпоинту 1: третьим красным в root-ci был шаг `git diff --check` (CRLF в `test_secrem_f009_terminal.py`); его уже
исправила параллельная сессия (`6a7b9ebd`), мой merge `91cab14f` её включает. На ветку пишет ещё одна сессия — перед каждым push делается fetch/merge, без force.

**#5 — привязка sha256 удалённого скрипта (`curl | sh`).** Воспроизведено на настоящем движке (`test_terminal_remote_script_dispatch`):
в карточке одобрения не было sha256. Первопричина: `tools.context_denial` отдаёт хуку **копию** `dict(args)`, поэтому
`_bind_remote_script_content` писал `_remote_content_sha256` в копию; `approval_digest` и припаркованный вызов sha не видели, а на
исполнении оболочка заново качала `curl … | sh` — байты, которых владелец не одобрял. Изолированные тесты хелпера этого не ловили.
Фикс (минимальный): `ToolSpec.bind_args` + `tools.bind_arguments` (реальные аргументы, до digest) → вызывается движком после `context_denial`;
на исполнении `_tool_run` перекачивает скрипт, сверяет sha256 с одобренным и запускает `sh -s`/`bash -s` со скриптом через **stdin**
(`TerminalManager.start(stdin_data=…)`, для docker добавлен `-i`); вызов без привязанного sha или с другими байтами — отказ.
Тесты (`tests/test_terminal_remote_script_dispatch.py`, 4): на старом коде 3 падают (sha нет в одобрении; байты не идут через stdin;
`_remote_exec_plan` отсутствует), 1 («подмена после одобрения») на старом коде проходит вхолостую — в тестовой среде нет сети, поэтому
доказательную силу несут остальные. С фиксом 4 passed; регрессия terminal/approval/engine/tools: 1000 passed, 14 skipped.

**#3 — чужой scratch в индексе кода.** Если корень индекса — предок `<data_dir>/scratch` (корень проекта или сам data dir), `rglob`
индексировал черновики других агентов, а пул индексов общий → они становились доступны поиску любого агента; `scratch.check`
смотрит только случай «кандидат внутри scratch». Фикс: `CodeIndex.exclude_dirs` (+ `get_handle` исключает `scratch` для корней вне него),
и при загрузке индекса, сохранённого старой сборкой, записи из scratch отбрасываются. Корень внутри scratch (собственный алиас) работает как раньше.
Тесты `tests/test_code_index_scratch_exclusion.py` (3): на старом коде падают 2 (чужой черновик находится поиском; старый индекс не очищается),
«свой scratch как корень» проходит и до и после (негативный контроль). Регрессия code/index/scratch/terminal: 630 passed, 15 skipped.
