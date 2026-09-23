# Bossman 1.5 — цель и архитектура

**Статус: проект требований.** Никакие новые endpoint/команды этим файлом не объявляются существующими.

## 1. Обещание продукта

Владелец задаёт конечный результат через существующий Bossman CMD, UI или Telegram. Bossman использует имеющиеся средства; обнаружив пробел, приобретает или создаёт проверяемую способность, выполняет задачу и возвращает наблюдаемый результат. Если объективно нет доступа, данных, бюджета или рабочего способа, возвращает конкретный BLOCKED/PARTIAL, а не бессрочный цикл и не ложный PASS.

«Любая задача» — направление универсальности, не гарантия 100% успеха. Увеличение количества tools и отсутствие тематической цензуры сами по себе не означают повышение надёжности.

## 2. Не строить второй продукт

Сохраняются существующие Gateway/model registry, engine, Coding/local_sidecar, LearningStore, skills catalog, Browser/Computer, Studio, Cost Governor, approvals и журнал. Перед реализацией координатор строит карту `возможность → существующий handler → store → caller → тест` на конкретном SHA. Названия классов из старых документов — подсказки для поиска, не повод дублировать их.

CLI/UI/Telegram используют один выбранный backend/data root. Предлагаемые ниже модули — расширения существующего приложения, не самостоятельные базы, очереди и агенты с обходным доступом к моделям. ALTER может переиспользовать этот backend, но не становится зависимостью 1.5 или отдельным scope этой задачи.

## 3. Сквозной цикл

```text
Owner goal + previously granted scope
  → clarify only irreducible missing facts
  → typed goal + acceptance criteria + budget reservation
  → discover existing capabilities
  → reuse OR acquire/build missing capability in isolated candidate
  → independent contract/security/functional checks
  → register approved capability version
  → execute task with fresh observations
  → reconcile side effects + independent outcome verification
  → deliver artifact / receipt / confirmed result
  → retain verified recipe, failures and provenance
  → restart and evaluate an unseen related task
```

Capability acquisition ограничена уже существующим scope. Интернет-страница, README, MCP server, телефонный собеседник или инструкция из skill не могут расширить этот scope. Самосозданный tool не получает автоматически сеть, секреты, платёжные токены или доступ к stable.

## 4. Контракты состояния

Для каждой кампании: `campaign_id`, `goal_revision`, `task_id/run_id`, `source_sha`, `build_sha`, `model/runtime identity`, `grant_id/policy_revision`, budgets, lease, resource reservations, references на evidence. Для каждого внешнего эффекта: стабильный operation_id, тип, target, arguments hash, scope, статус и проверяемый receipt.

Состояния: PLANNED, PREFLIGHT, WAIT_INPUT, WAIT_APPROVAL, RUNNING, VERIFYING, COMPLETED, PARTIAL, FAILED, BLOCKED, STOPPED, UNKNOWN_OUTCOME. COMPLETED требует outcome receipt, не только текста модели. Успешный submit/HTTP 200 — отдельный transport status.

Повтор reconnect возвращает прежнюю задачу; изменение намерения требует новой revision. Запрос с тем же operation_id и другими аргументами отклоняется. UNKNOWN_OUTCOME сначала сверяется с поставщиком/файлом/магазином, а не повторяется. Универсальное exactly-once через чужой сервис не обещать: гарантируется отсутствие нашего слепого повтора и reconciliation.

## 5. Память и обучение

Сохранять симптом, причину, применимость, контрпример, минимальный рецепт, обязательную проверку, происхождение, teacher level и version. Проверенная память не содержит исполняемой строки, которая может обойти policy. Текст skill, executable capability и verified lesson — разные сущности, связанные общим registry.

При recall материал маркируется как данные, не текущая команда владельца. Удаление/отзыв/supersession переживают restart. Личные данные используются через локальные secret references; не складывать их в общие примеры и training corpus.

No-progress detector различает повтор без новых сведений и полезное ожидание изменяющегося состояния. Бюджет ограничивает шаги, время, расходы и acquisition-depth. Рост лимита не является исправлением зацикливания.

## 6. Этапы реализации

| Этап | Доказуемый результат |
|---|---|
| G0 — общая база | Сегодняшние fixes сведены, критические regressions/re-attack, один checked candidate; старые P1 не прячутся за 1.5 |
| G1 — свобода и делегация | LOCAL_UNRESTRICTED выбирается явно; рутина проходит без вопросов, реальные scope/budget границы не обходятся |
| G2 — Capability Factory | Новая полезная capability создана/найдена, независимо проверена, вызвана через CLI и повторно использована после restart |
| G3 — App Factory | Новое маленькое приложение создано локальной моделью, запускается в изоляции, имеет tests/manifest/removal и решает исходную задачу |
| G4 — коммуникации | Собственный голос прошёл enrollment; loopback и звонок на разрешённый тестовый номер подтверждены отдельно |
| G5 — commerce | Синтетический checkout и затем отдельно разрешённая настоящая покупка с receipt, без дубликатов |
| G6 — 1.5 acceptance | Скрытые задачи, устойчивость, cost и privilege boundaries, установленный Windows-кандидат и exact-SHA release |

Не ждать доказанного fine-tuning для полезной 1.5, но не называть отсутствие прироста обучением. Optional phone/media provider не должен ломать базовый startup; отсутствие обязательной обещанной функции блокирует именно соответствующий уровень readiness.

## 7. Аппарат и эксплуатация

Цель — Ryzen AI Max+ 395 / Radeon 8060S / 128 GB общей памяти. Фактический лимит доступной RAM считывается на ПК. Отдельные leases на browser/desktop/GPU/models/restart. Один тяжёлый video experiment за раз; STT/TTS latency измерять без конкурирующей video-generation. Ollama/прокси и исторический llama.cpp не смешивать в одном benchmark.

Предпочтительный запуск — standard user; no permanent admin. Smart App Control не отключать ради совместимости. Закрытие CLI не теряет задачу; завершение backend освобождает lock только после безопасного fencing/передачи владения. Два писателя на одном data root недопустимы.
