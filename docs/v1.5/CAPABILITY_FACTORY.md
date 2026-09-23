# Capability / Skill / App Factory

**Целевое поведение 1.5.** Это расширение существующих coding, skills и runtime registry, не агент с прямым shell-доступом к stable.

## 1. Не завершать задачу только на tool unavailable

Для неопасной выполнимой задачи: проверить registry → существующий официальный API/MCP/OSS → адаптация → минимальный собственный tool/app. Поиск ограничен бюджетом и acquisition depth. Отсутствие результата после лимита — конкретный BLOCKED, не бесконечная генерация инструментов.

Три сущности различаются:
- **Skill:** методика/рецепт с provenance; текст не выдаёт прав.
- **Capability:** версионированный executable adapter с input/output schemas, scope и verifier.
- **App:** артефакт/мини-приложение, которое можно запустить, проверить, обновить и удалить без поломки Bossman.

## 2. Обязательный pipeline

```text
DISCOVER → SELECT_SOURCE → PIN_AND_INSPECT → PROPOSE_MANIFEST
→ ISOLATED_BUILD → REGRESSION + NEGATIVE_CONTROLS
→ INDEPENDENT_VERIFY → REGISTER_CANDIDATE
→ GRANTED_ACTIVATION → INVOKE_FROM_BOSSMAN_CLI
→ VERIFY_ORIGINAL_GOAL → LEARN → RESTART → REUSE
```

Manifest содержит type, name/version, source/revision/license, files/digests, dependencies, entrypoint, input/output schema references, tools/egress requested, verifier, resource limits, rollback/removal, evidence and author. Проект формата: [contracts/capability-manifest.schema.json](contracts/capability-manifest.schema.json).

Проверка JSON и лицензии — не security audit. Не исполнять install/postinstall hooks просто ради inspection. Archive unpack проверяется на traversal, symlink/junction/hardlink и resource bombs. Candidate не читает host home, vault, браузерный профиль владельца и скрытые тесты; network начинается выключенным и выдаётся narrowly scoped.

## 3. Локальный ученик и Claude

Qwen/выбранная локальная модель читает, пишет diff и тесты через штатный coding path. Claude координирует и проверяет. Внешний shell Claude допустим для инфраструктурной диагностики/сборки, но такая работа маркируется TEACHER/ENGINEERING, не LOCAL_STUDENT.

Помощь L0–L5 записывается с task/run/time. Готовый teacher patch не становится самостоятельной победой ученика. Успешный код со слабыми/изменёнными assertions не проходит. Bugfix test должен падать на baseline и проходить на candidate; новая функция проверяется положительным и отрицательным контролями независимого verifier.

## 4. Полный репозиторий и apply

Переиспользовать сегодняшние streaming-digest/scoped-evidence fixes, сначала повторить реальные тесты. Весь большой repo не отправляется в prompt и не копируется бесконтрольно в RAM. Hash inventory, changed-file bytes и paging дают проверяемую границу без удаления лимитов.

Candidate → diff → tests → independent receipt → текущая revision canonical project → governed apply. Владелец один раз разрешает auto-apply для конкретной sandbox/app-папки; тогда рутина без вопросов. Apply stable самого Bossman — отдельный release gate, не side effect ученического инструмента. Stale revision отклоняется; rollback сохраняет старую версию. UI немедленно видит те же байты, что CLI.

## 5. Подключение и эксплуатация приложения

Предпочитать небольшой plugin/CLI/service внутри существующего host и registry, а не ещё один dashboard. В manifest: запуск/health/stop, порт только loopback по умолчанию, data root, resource caps, tests и удалить/откатить. Приложение получает task-scoped credentials references, не копию vault. Выход и логи очищаются перед общим evidence.

Процедура регистрации проверяет version/digest, policy compatibility, ownership и независимую подпись/receipt. Изменились executable bytes или dependency lock — прежний verified status не переносится. Revoked capability не запускается через старый cache после restart.

## 6. Минимальные реальные упражнения

1. Из CSV сделать локальное приложение отчёта с фильтрами и экспортом; проверить данные не только screenshot.
2. Написать новый parser/adapter для ранее неизвестного синтетического формата и использовать его в исходной задаче.
3. Починить неизвестный небольшой repo, доказать красный/зелёный test и не затронуть соседние файлы.
4. Создать reusable skill из выполненного hybrid-media workflow и воспроизвести pipeline из новой CLI-сессии.
5. Отозвать вредный/битый candidate; после restart он остаётся отозванным, предыдущий working version доступен.

Не публиковать решения закрытых acceptance задач как skills. Модель, успешно выполнившая упражнение, ещё не доказала обобщённое обучение.

## 7. Выход Factory

`capability_id/version/digest`, `manifest`, `diff`, `tests`, `independent verification`, `registry receipt`, `invocation task_id`, `artifact/receipt`, `rollback`, `lesson`. Отсутствует хотя бы original-goal verified outcome — сообщать capability built, task not yet completed, а не общий PASS.
