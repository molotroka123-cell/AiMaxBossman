# Прогон на ПК владельца 06.10.2026 — чекпоинт 1 (установка + проверки)

Ветка `goal/bossman-self-improvement-tree-20261005`, собранный и установленный SHA: `12e003e74f5bd667d542f3235f8c0c6f3afb052d`.
Same-product Terminal Run contract: пульт, CLI и дашборд — один backend/данные/ключи/одобрения; этот отчёт ничего отдельно не вводит.
Лестница North Star: достигнут только `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT`. `SELF_REPAIR_SINGLE_CYCLE_PASS` **не доказан**.

Правило: PASS — только с логом. Частичное названо частичным.

## Что собрано и установлено (PASS, с логами)
- Клон `-c core.autocrlf=false`, `git rev-parse HEAD` = 12e003e7…; `build_windows_bundle.py --zip` → `BOSSMAN-Windows-x64-12e003e74f5b.zip`, 792 437 655 байт.
- SHA256 архива: `AFA40DC4A996D7044D1AB4010226F06137BFE629B3F50A5E23168B7BB06961CD`.
- `verify_windows_bundle.py --expected-sha 12e003e7…` → `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`.
- `owner_one_bossman.ps1 -Action Switch` → `SWITCH DONE`; `/health/live`: `build_sha=12e003e74f5bd667d542f3235f8c0c6f3afb052d`, `source_identity=PASS`.
  Откат: `Bossman\bugtest-20261001\tree-1005\switch-12e003e7\rollback.ps1` (предыдущий пакет b96e2a7c).
- Находка при установке: после Switch **старый Jeff (b96e2a7c) остался жить** и держал poller-lock, новый не стартовал.
  Остановлен штатно (`owner_one_bossman.py stop --kinds jeff --except-home <новый>`, stop.flag, без force), затем `Start-ScheduledTask BossmanOne-2-Jeff`.
  Итог: ровно один `-m bcc --host … --port 8801`, один `bcc.pit.cli start`, один `bcc.telegram_companion`, все из 12e003e74f5b. Дефект Switch (не гасит Jeff, пока тот держит lock) **не исправлен**, только обойдён.

## Jeff doctor (PASS)
Все проверки PASS: running_jeff_build 12e003e74f5b, pollers=1, free_route PASS (nemotron-3-super/ultra, gemma-4 — ZERO_COST_OK),
`web: active=SEARXNG; searxng=OK; keyless_ddg=OK(5 results)`, participant_tool_perimeter PASS (location/device/computer denied).
Живой поиск по свежей новости и голос «только по просьбе» руками не проверялись.

## Быстрые проверки
| Проверка | Результат |
|---|---|
| `tools/skips_registry.py --check` | PASS, 377 записей, 0 без причины |
| `bossman-core tests/test_v26_file_intel.py` (в т.ч. EPUB) | PASS: 15 passed, 2 skipped |
| `astra_security_gate.py --component windows-bundle` | FINDINGS: pyjwt 2.14.0 — PYSEC-2026-4141 и CVE-2026-102275 (известный риск, не исправлен). Первый запуск был TOOL_ERROR: не было pip-audit, установлен |
| `test_answer_streaming`, `test_web_designer_create_after_response` | PASS (10 passed вместе с 1-м тестом deeplink) |
| `test_agents_deeplink_stale_modal::test_window_does_not_open_after_leaving_during_its_own_models_fetch` на 12e003e7 | **FAIL, нестабильно**: `the race window was not exercised, assert 1 >= 2`; из 4 прогонов старой версии 2 упали, 2 прошли |
| тот же тест в версии `2b95fef1` (ветка ушла вперёд), запущен против кода 12e003e7 | **FAIL 3 из 3** на этом ПК (тот же assert). Чинящий коммит заявляет 3/3 pass на их стороне — здесь не воспроизвелось |

Вывод по окну «новый агент»: поведение (окно не появляется после ухода на Чат) **не подтверждено на этом ПК**; тест не ослаблялся. Вероятная причина провала — предусловие теста (повторный запрос `/api/models` не происходит), поскольку окно открывается через `setTimeout(0)` с проверкой hash и первая защита срабатывает раньше второй; это гипотеза по коду, не доказано. CI по точному SHA на момент записи не завершён.
Не проверялось руками в продукте: двойной клик создания проекта в web designer, загрузка EPUB через интерфейс (покрыто только тестами выше).

## Самоулучшение: НЕ доказано
| Попытка | Исполнитель | Итог |
|---|---|---|
| 1 | — | `NOT_RUN`: скрипт стучался на порт 8800 вместо 8801 (моя ошибка параметра) |
| 2 | nemotron-ultra-free | клиент не дождался ответа POST /work, задача на backend прошла и завершилась `failed` за 144 с (`сайдкар сообщил о неудаче`), файлы не менялись |
| 3 | openrouter-free (Nemotron 3 Super) | **`TREE_SELF_IMPROVE=FAILED`**: изменил `discovery.py`, но добавил 4 лишних тестовых файла и тронул `test_pit_foundation.py`; задача `failed`, независимая проверка не пройдена |

Ничего не применено в проект. Дефект `choose_discovery_question` (NaN) остаётся. Вердикта `VERIFIED_CANDIDATE` нет → уровень не заявляется.

## Дерево вчера → сегодня (реальные данные, git `2a69b34b` → `12e003e7`, 52 коммита)
764 → 779 узлов: +15, 0 удалено, у старых узлов статусы не менялись. Код 460→466, записи 166→175, новые зоны `plugins-mcp`, `plugins-oss` и OSS-записи.
Улучшено самим Bossman: 0, проверено: 0. Анимация отправлена в пульт (сообщение 1084).
Оговорка: снимки сканера `scan-latest.json` (вчера) и сегодняшний сняты на разных наборах веток (100 и 260), поэтому сравнение сделано по `capability_tree_seed.json` в git.

## Не сделано / осталось владельцу
- (а) пульт `/task Открой Блокнот…` и `/confirm`: из Telegram от лица владельца я не могу; не проверено.
- (б) «Bossman, работай здесь» через интерфейс: использован тот же API-путь (`POST /api/capability-tree/work`), задача стартовала; отчётов «начал/результат» в пульте не проверял.
- Merge PR в main — владелец. Звонки (`bossman call setup`), запись голоса, окружение `owner-live` для секрета (аудит #9) — владельцу.
- Открытые риски без изменений: «database is locked» в миссионных тестах; красный гейт Intelligence Preservation.
