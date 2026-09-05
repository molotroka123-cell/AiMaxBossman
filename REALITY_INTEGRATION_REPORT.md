# Reality Compiler v0.1.0 — отчёт об интеграции

Дата: 2026-09-05. Ветка: `codex/reality-compiler-v010`.

**Статус: PARTIAL для полного FABLE_CONNECT; реализована защищённая локальная
интеграция с включением только из доверенного host bootstrap.** Обычные задачи
не включаются в Reality автоматически. Полная production-приёмка не заявлена.

## Ревизии и исходники

- `BASE_SHA`: `f0051ee77c54b4a98ae019e82be9e2ede224d3f8` — база изолированной ветки.
- Начальный HEAD общего checkout: `0c925dd0fb0c7b2d8940cf46681844d304407455`.
  Во время параллельной работы другой процесс обновил общий checkout; собственные
  изменения восстановлены в отдельную рабочую копию, чужой stash сохранён.
- `FINAL_SHA` (финальный исполняемый код): `dbb65a298944acfdb0cedfa8b4cbfb39840e8a4e`.
  Итоговый документационный commit может быть новее и не меняет эти исходники.
- Архив: `Bossman_Reality_Compiler_v0.1.0.zip`, SHA256
  `814a5f23be2e07c4601e8ab5593a2a315b34bcad1166e46a9fdb8a0b65cb886a`.
  Все семь исходных Python-модулей пакета сохранены побайтно;
  [проверка архива](docs/reality/evidence/archive-integrity.json).
  `bossman_shared/__init__.py` не заменён.

Документы архива использованы как спецификация в пределах запроса пользователя.
Указанный в них старый HEAD не использовался для reset, а их инструкции не
расширяли полномочия терминала, бюджет, доступ к данным или публикацию.

## Подключение к реальным путям исполнения

| Подсистема | Файл / функция | Подключение | Существующие защиты |
|---|---|---|---|
| Shared admission | `bossman_shared/reality_guard.py`: `install`, `enroll`, `lookup` | Сначала неизменяемый participant marker, затем полный IR; сравнение proposal с полным host-approved contract, привязка task/run/actor/plan. | Нет model-facing API регистрации; ошибку профиля, IR, импорта или смену run нельзя обойти выключением флага. |
| Shared IO | `reality_guard.Session`, `dispatch`, `dispatch_sync` | `store.claim` до IO, независимое свежее наблюдение и подписанный receipt до `store.confirm`; повторная проверка policy/args/fence после async-пауз. | Короткие отдельные SQLite-операции через `to_thread`; отмена/ошибка удерживает escrow, повтор не автоматический. |
| Core | `bossman-core/bossman/runner.py`: `_call_tool`, `run_task` | Обёртка фактического handler; Reality hard gate перед записью завершения. | Старый CompletionGate, grants, approval и бюджетные проверки сохранены; обычные задачи не требуют optional Reality. |
| BCC | `command-center/bcc/engine.py`: constructor, `_run`, `_call_model`, `_run_tool_now`, `_finish` | Critical completion hook; фактический dispatch после разрешений; повторная проверка actor/receipt перед final write. | Существующие critical hooks, `requeue=False` при Reality failure, DB fence, approvals, replay, global cap не заменены. |
| Compound | `bossman-core/bossman_v3/execution/compound.py`: `CompoundRunner.run` | Exact plan/actor admission, каждый шаг через Reality и старый UniversalComputerAgent, final hard gate. | `journal.bind_plan`, signed execution journal, policy, approval, verifier и execution_guard сохранены. |
| Local Fleet | `bossman-core/bossman_v3/fleet/control_plane.py`: `FleetExecutionBridge.execute` | Проверка IR по `mission__work`, текущая lease при dispatch и completion, запрет enrolled remote transport. | Существующие leases, mutation guard, signed evidence и потери узла остаются обязательными. |
| Host recovery | `bossman_shared/reality/host.py`: `LocalHost`, `reconcile_written` | Стабильные domain-separated ключи поверх существующего evidence signer; перезапуск читает полный IR; свежий observer подтверждает прежний escrow без повторного IO. | Подтверждение только исходных owner/fence; неизвестная попытка не переходит в blind retry. |

BCC использует эквивалентный `reality_guard.completion_hook`, а не прямой вызов
поставленного `make_completion_hook`: обёртка сохраняет дополнительный реальный
Fleet fence. Сигнатура `(task, run_id, answer)`, явный FAIL и запрет requeue
сохранены; текст ответа не является доказательством.

## Поддерживающие слои и модели

`compare_world` вызывается после независимого наблюдения. Расхождение сохраняет
снижение автономности до уровня 0 в durable state и переживает перезапуск.
Quarantine проверяется перед фактическим action admission, включая повторную
проверку после await. Опциональные доверенные локальные bids и learning hooks
фиксируют проверенные результаты и редактированные уроки; подробности и проверка
покрытия — в [матрице приёмки](docs/reality/evidence/acceptance-coverage.md).
Допускаются только bids для уже скомпилированного локального действия с нулевой
стоимостью. `choose` не меняет action/target/args; `settle` выполняется после
CONFIRMED и свежей проверки подписи. Сохраняются только hashes и фиксированные
строки через существующий host-redactor. При ошибке learning store эффект
остаётся CONFIRMED: host может повторить запись аудита, но повторять IO нельзя.

Provider routing, dependency slicing в настоящем ContextBuilder и общий путь
обучения/продвижения skill ещё не подключены к Reality. Для enrolled runs все
model/provider-вызовы, включая fallback/compaction, запрещены до их корректной
интеграции. Существующий глобальный бюджет не подменяется локальным ledger.

Общий [протокол работы](docs/MODEL_REASONING_PLAYBOOK.md) подключён к Core/BCC
system payload, включая resume и ограничение контекста. Это практический метод
«цель → ограничения → план → действие → независимая проверка → честный статус»,
а не перенос скрытых рассуждений или обучение весов. Другие standalone chat loops
и embeddings не объявляются охваченными. [Топ-10 предложений](docs/TOP_10_IMPROVEMENTS.md)
и выноска **СДЕСЬ БЫЛ АСТРА** с видением V4/V5 добавлены в README.

## UNIT_TESTS / INTEGRATION_TESTS / EXISTING_REGRESSIONS

Точные финальные количества, команды, версии runtime, SHA и результаты:

- [Shared + installed packaging](docs/reality/evidence/final-package-tests.json).
- [Core/BCC security и интеграции](docs/reality/evidence/final-engine-tests.json).
- [Все 15 сценариев ACCEPTANCE](docs/reality/evidence/acceptance-coverage.md).
- [Git-миссия, синтаксис и secret scan](docs/reality/evidence/final-root-checks.json).

На финальном коде `dbb65a2`: shared + новый binary-key regression — **91 passed,
5 subtests passed на каждой из Python 3.11.16 и 3.12.14**; Core — **79 passed**;
BCC — **157 passed, 1 failed, 1 skipped**, 14 предупреждений aiosqlite teardown.
Исходные 43 теста пакета включены. Проверены синтаксис всех 25 изменённых Python
файлов, `git diff --check` и secret scan. Самостоятельный review разрешает
локальную публикацию с Reality OFF; deployment не одобрен.

Единственное известное падение выбранного BCC regression run: создание symlink
в подготовке теста вызывает Windows `WinError 1314` до исполнения кода авторизации.
Docker mount-проверка пропущена существующим условием недоступности daemon.
Предупреждения aiosqlite о закрытом event loop сохранены. Падение не скрывалось
через skip/xfail и не превращалось в PASS. Три исходных падения action_contract,
воспроизведённые также на чистой базе `f0051ee`, исправлены терминальным патчем.
Дополнительный полный `tests/test_evidence_signing_shared.py`: 4 passed, 1 failed.
Существующий тест требует POSIX mode 0600, тогда как Windows `stat` возвращает
0666. Это не доказательство Windows ACL и не исправляется фиктивной проверкой
режима; rollout-blocker указан ниже. Новый тест точного бинарного ключа проходит.

## LIVE_CHECKS

1. **PASS, реальный локальный git.** `scripts/reality_git_acceptance.py` создаёт
   временный repo и controlled bare remote. Пять отдельных obligations:
   reproduce/fix/test/commit/push. Проверка до и после привязана к неизменяемым
   деревьям; remote SHA, tree и exact patch читаются отдельно от ответа push.
   После пяти независимо подтверждённых эффектов в реальном learning ledger
   прочитаны ровно пять success settlements и пять редактированных уроков.
   [Доказательство](docs/reality/evidence/git-acceptance.json).
2. **PASS, реальный Windows subprocess.** Точная вложенная команда из Sol task8
   создаёт `glm_acceptance.txt` с `GLM_OK` в разрешённом временном каталоге;
   независимое чтение подтверждает результат. Exit 17 отражён как ошибка в run/status.
3. **PASS по импорту, fresh installed wheels.** Shared/Core/BCC собираются в wheels;
   изолированный `python -I` вне checkout подтверждает пути site-packages и общий
   объект guard на 3.11/3.12. Рабочая установка пользователя не перезаписывалась.
4. **MIXED, локальная Qwen.** Одна проба `qwen2.5:7b`, 10.735 с, без облака и повторов.
   Отказ ложному DONE верен, формат и пояснение не прошли строгий критерий.
   [Параметры и полный результат](docs/reality/evidence/local-model-eval.json).
5. **PASS для опубликованных чекпоинтов.** Обычный push отдельной ветки разрешён
   пользователем; `git ls-remote` независимо проверяет опубликованный SHA.
   Это отдельная проверка от controlled bare remote и не означает deploy.

## Исправленные баги и аудит Sol

[Подробный отчёт](docs/reality/SOL_AUDIT_FOLLOWUP.md) содержит воспроизведения,
границы серьёзности и сопоставление ASTRA-LIVE-01…07. Исправлены обход отзыва
разрешений на resume, запись устаревшим worker, async-устаревание полномочий,
Windows quoting, потеря nonzero error и повреждение бинарного ключа подписи
при записи Windows CRT. Они не переименованы в три удалённых P0.

## KNOWN_LIMITATIONS / OPEN_P0 / OPEN_P1

`OPEN_P0`: подтверждённых неисправленных P0 в проверенном локальном пути не
установлено. Это ограниченный результат аудита, не доказательство отсутствия P0.

`OPEN_P1` / блокеры полного rollout:

- Нужен доверенный production bootstrap с утверждённым контрактом, namespace БД/
  tenant, observers и повторной установкой того же профиля после restart.
  Сам по себе feature flag ничего не регистрирует.
- Защита Windows ACL для marker/store/key от agent-readable mounts не доказана
  в production. До её проверки автоматическое массовое включение недопустимо.
  Исправление бинарной записи применяется к новым ключам. Старые ключи не
  переписываются автоматически: это могло бы уничтожить валидность уже выданных
  подписей; исторические повреждённые ключи требуют отдельного восстановления.
- Нет distributed RealityStore/shared-backend fencing и приёмки на двух узлах.
  Enrolled remote dispatch явно заблокирован.
- Не завершены actual provider-router budget/privacy/support integrations:
  racing paid Core+BCC cap и clinical dependency egress здесь не проверены
  реальными вызовами. Они не выдаются за PASS из unit-тестов пакета.
- Нет полного deployment acceptance: symlink/Docker-проверки ограничены средой;
  UI screenshot/OCR и 402 same-mission fallback из аудита Sol остаются открытыми.
- Runtime-изменения опубликованы в отдельной ветке. Уже работающий процесс
  приложения не обновлён и не перезапущен; веса моделей не изменены.

## ENABLED_MODES и откат

По умолчанию `BOSSMAN_REALITY_ENABLED=0`. Доступен только явно зарегистрированный
host-owned local tool-only run. Новый admission разрешается значением `1`, но
требует `install` и `enroll` с настоящими owner/target/policy/observer. Нет нового
model-facing инструмента, позволяющего самому себе выдать полномочия.

Для отката сначала остановить новые admissions (флаг 0). У уже участвующих задач
marker и gates остаются обязательными. Не удалять marker/store/key и не создавать
новый run для обхода escrow. Доверенный оператор должен проверить внешний target
и согласовать pending эффекты; только после этого допустим штатный переход на
предыдущую совместимую версию. Публикацию откатывают обычным revert в отдельной
ветке после проверки совместимости, без reset/force-push чужой истории.

## Изменённые файлы и воспроизводимость

Полный список: `git diff --name-only f0051ee77c54b4a98ae019e82be9e2ede224d3f8 HEAD`.
Изменены shared package/guard/protocol, перечисленные четыре runtime-пути,
терминальный adapter, их regression tests, root packaging/CI, acceptance script,
README и документы/артефакты. Временные repo, wheels, журналы и ключи находятся
в `.audit-work/`, который исключён из Git. В репозиторий включены только
предназначенные для публикации тестовые доказательства.

Команды проверки записаны в JSON-артефактах, включая cwd/PYTHONPATH, реальные
интерпретаторы и индивидуальные статусы. Нельзя суммировать прогоны одной и той же
матрицы как независимые доказательства отсутствия регрессий во всём проекте.
