# AUDIT RC19 — отчёт о завершении аудита (2026-09-28)

Сессия: OpenCode/GLM (по поручению владельца, продолжение аудита Bossman RC19).
Все команды выполнены на Windows, работа только в интеграционном worktree
`C:\Users\asd\Bossman\wt19-audit-int` (ветка `claude/rc19-audit-integration`).

## 1. Итоговый SHA и ветка

- Ветка: `claude/rc19-audit-integration`, worktree `wt19-audit-int`.
- Базовый SHA аудируемой цепочки: `8003d75d8bf6ca633f5794b38250072a1972a074`
  (фикс Fable budget: retry `os.replace` 100×20ms на Windows PermissionError).
- Изменения этой сессии: 5 файлов тестов + conftest (см. §4), коммит в этот worktree.
- Git push НЕ выполнялся (по исходному запрету; документы для Claude запушены
  отдельно владельцем в `feat/jeff-ux-integration-test-20260926` @ `0325155c`).

## 2. Проверки (точные команды, SHA, результаты)

| Проверка | SHA/дерево | Результат | Артефакты |
|---|---|---|---|
| Узкие Fable-тесты (ДО окружения-PYTHONPATH) | wt19-audit-int, но импорт перехвачен старым деревом | **1 failed** (PermissionError — гоняли СТАРЫЙ код без фикса) | консоль |
| Узкие Fable-тесты (с PYTHONPATH на worktree) | 8003d75+ | **12 passed + 1 flaky** (cross-process: 1 сбой из 4 запусков, затем 3/3 зелёных) | консоль |
| **Полный bossman-core** | wt19-audit-int @ 8003d75+ | **22 failed, 3292 passed, 136 skipped, 2 errors** за 9:39 | `artifacts/audit-rc19/core_full_junit.xml`, `core_full_console.log` |
| Целевой перезапуск затронутых файлов (после фиксов) | тот же | **183 passed, 21 skipped, 1 failed** (только cross_layer) | консоль |
| **Полный Command Center** | wt19-audit-int @ 8003d75+ | **5490 passed, 59 skipped, 0 failed, 0 errors** за 49:06 — ПОЛНОСТЬЮ ЗЕЛЁНЫЙ; бывшая точка зависания (psutil descriptor-тест) пройдена | `artifacts/audit-rc19/cc_full_junit.xml`, `cc_full_console.log` |

Сравнение с заявленным прошлым прогоном (3284/27/136/2): passed +8, failed −5.
Точный список прошлых падений на диске НЕ существовал (лог не сохранялся) —
впервые собран junitxml этой сессией.

Дополнение (после инвентаризации агента): прошлый полный bossman-core
(`C:\Users\asd\Bossman\evidence\rc19\audit\final-core.log`, 27 failed / 3284 passed)
завершился ЗА ~10 минут до коммита фикса 8003d75 → прогон этой сессии является
первым полным послеремонтным подтверждением. Прошлый полный Command Center
завершился (`final-cc.log`: 2 failed / 5486 passed / 61 skipped — на другой ветке);
ветки 84f5e0ac (PR #84) и 8003d75 — расходящиеся сиблинги (merge-base 6a2ac44a),
истинное слияние обязано сохранить 3 PR-коммита (cc548917, 445c32a0, 84f5e0ac).
Заявка «~45 фиксов» = фактически 43 коммита. Прошлый отчёт этого же дня:
`C:\Users\asd\Bossman\evidence\rc19\AUDIT_20260928_FINAL_RU.md` (в нём же — P0
«самозваный бэкенд на :8800» из того же editable-чекаута, что и найденный мной
перехват импортов — одна и та же корневая причина).

## 3. Fable-фикс 8003d75d — ПОДТВЕРЖДЁН

- `test_a_reader_holding_the_ledger_does_not_fail_a_spend` — зелёный на фикс-ветке
  (на старом коде — PermissionError). Найден побочный флейк
  `test_cross_process_concurrent_reserve_never_exceeds_cap` (пустой stdout одного
  из 5 подпроцессов при холодном старте) — задокументирован, не ослаблялся.
- Фикс НИГДЕ не смёрж beyond `claude/rc19-audit-integration`: его нет в
  wt19-verify (84f5e0a — живой установленный билд), main, rc19/lead, PR #84.

## 4. Классификация 22 failed + 2 errors и применённые фиксы

| Класс | Кол-во | Тесты | Действие |
|---|---|---|---|
| WinError 1314 (нет привилегии symlink на Windows) | 18 | openhands_path_boundary (3), rt01/rt02/rt04 (6), pass3 (2), real_workload (1), secrem_s1 (2), sibling_sweep (4) | conftest-hook → честный skip с причиной (паттерн уже существовал в test_sandbox_security / stage13). Зелёно на POSIX/CI |
| Тест ждёт POSIX `sh` на Windows | 1 | test_windows_host_shell::test_posix_local_shell_unchanged | skipif os.name=="nt" (Windows-ветку покрывают 3 соседних теста) |
| shebang-стаб не исполняется на Windows (WinError 193) | 1 | test_teacher_isolation::test_teacher_iso_001 | skipif sys.platform=="win32" |
| cp1251 вместо utf-8 при чтении JSON | 1 | test_intelligence_measurement_full_lane | `read_text(encoding="utf-8")` |
| env-var > 32767 (гигантский parametrize-id) | 1 ERROR | test_fleet_remote_auth::test_strict_bounded_json[xxxx…] | добавлены короткие ids |
| **НЕ РЕШЕНО — кандидат в регрессии** | 1 | **test_v3_cross_layer_e2e::test_cross_layer_positive_chain_to_scorecard_evidence** | НЕ ослаблялся. `ActionReceipt.verified()`=False: `fresh()[0]`=False — поля времени/подписи вreceipt не заполнены цепочкой. Указатель: `bossman_v3/execution/compound.py::_action_receipt` (строки 79–110, коммиты d6260adf/7b61d00d), `bossman_shared/action_receipt.py::verified()/fresh()`. Нужна связь с владельцем: это незавершённый wiring receipts-фичи |

Расхождение: консоль сообщила «2 errors», junit содержит 1 ERROR-entry —
второй error не создал testcase-запись; в отчёте зафиксировано как есть.

## 5. Найденный дефект окружения (важно для владельца)

Системный Python имел редактируемую установку СТАРОГО дерева
(`AiMaxBossman-integrated-old-20260927`): `import bossman` из wt19-audit-int
резолвился в старый код — узкие тесты гоняли не ту реализацию. Добавлен
регрессионный guard `tests/test_import_visibility.py` (падает громко при
загрязнении sys.path). Все прогоны аудита — с `PYTHONPATH=<worktree>;<worktree>\bossman-core`.
Рекомендация: один чекаут + venv на машину (в PR #84 уже есть Phase 0 с этой же идеей).

## 6. GitHub PR #84 — актуальный статус

- Всё ещё **Draft**: `claude/bossman-freeze-closure-ohvmon` → `candidate/freeze-20260926`, 125+ коммитов (обновлён 28.09), НЕ содержит фикс Fable 8003d75 и изменения этой сессии.
- CI: 3 красных проверки — `Intelligence Preservation` (fail-closed, INSUFFICIENT_EVIDENCE — честно, файл улик не существует), `root pytest py3.11` (флейк тайминга operator_step_profile на shared runner, дважды), `owner scenarios OS-60` (гон тайминга сценария). Описание PR честно перечисляет, что НЕ доказано из облака: реальный Windows-прогон, установка, Jeff/голос, Computer Use.

## 7. Требует проверки владельцем (Windows/Jeff, вручную)

1. Полный Command Center прогон этой сессии (завершается) — сверить с PR #84 CI.
2. `owner-acceptance.ps1` + `scripts/evening_acceptance.py run` на реальном железе.
3. Jeff вживую: Telegram-диалог, голос (русский локальный TTS — проверен только ASR-паттерном), установка/автозапуск, Computer Use, маршруты моделей.
4. Решение по cross_layer (см. §4) — подпись/время в ActionReceipt: это авторитетные поля, чинить должен ответственный за receipts-фичу.
5. Решение о мерже: Fable-фикс 8003d75 + тестовые фиксы этой сессии существуют ТОЛЬКО в wt19-audit-int — их надо перенести в PR #84 отдельной веткой после вашего OK (не делаю без прямой команды).

## 8. Один следующий шаг

Дождаться завершения полного Command Center прогона в этом же worktree (идёт),
затем: если он зелёный/объяснимо-красный — фикс-ветка от 8003d75 с конверсией
этого отчёта и фиксов в PR #84 (только после явного «пуш/мерж» от владельца).
