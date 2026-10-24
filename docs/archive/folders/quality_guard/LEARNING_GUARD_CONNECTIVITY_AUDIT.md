# LEARNING QUALITY GUARD — CONNECTIVITY AUDIT

**Репозиторий:** molotroka123-cell/AiMaxBossman
**START_REMOTE_SHA:** `3c47010c844f3d34c2938b3afcd8c6ec85f38210`
**Метод:** grep всех ссылок на `learning_guard` вне самого пакета/тестов/доков —
**на старте: 0 production call-sites**. Слой был чистой протестированной
библиотекой. Принцип аудита: библиотека существует ≠ WIRED; unit-тест ≠
production integration.

В этом проходе добавлен ТОЛЬКО один очевидно-недостающий тонкий адаптер
(Secret Holdout на границе durable learning-evidence). Архитектура не
переписывалась, дубли движков не создавались, A/B-числа не выдумывались.

## Классификация по 10 пунктам (с точными call-site)

| # | Гейт | Статус | Точный production call-site / почему нет |
|---|---|---|---|
| 1 | **Secret Holdout перед durable learning evidence** | **WORK** | `bossman/runner.py:_learning_excluded()` вызывается перед `decision_memory.create_decision` (`runner.py:~301`) и `failure_memory.record_failure` (`runner.py:~356`). Holdout-задача НЕ попадает в learning-корпус. No-op, пока holdout не задан (`learning_guard.get_holdout()` → None). Тест: `test_runner_holdout_exclusion.py` |
| 2 | **QualityGate перед promotion (memory/skill/context/router/self-improvement)** | **UNWIRED** | В production НЕТ триггера «learned-improvement → release». `context_engine.memory.promote` — это низкоуровневый статус-переход (ACTIVE/DISPUTED), не релиз обучения, и у него 0 production-вызовов (только тесты). `cybersec.learning`, `bossman_v3/skill_factory`, `bossman_v3/self_improvement` — proposal-only/frozen, тоже без prod-вызова. `learning_guard.guard_promotion` не вызывается нигде в проде. Логика есть и протестирована |
| 3 | **ContextRetentionGate в raw-vs-filtered A/B** | **UNWIRED** | В production нет A/B-пути (raw vs filtered прогон одной модели) — это benchmark-харнесс на реальных моделях/железе, которого нет. `learning_guard.context_fallback_to_raw()` — чистая функция без prod-вызова |
| 4 | **RouterQualityGate перед routing-policy release** | **UNWIRED (нет такого пути)** | Маршрутизация (`gateway/router.py`) — статическая конфигурация alias→backend; в production НЕТ «release routing-policy» promotion-потока, который можно было бы гейтить. Гейтить нечего до появления адаптивного router-release |
| 5 | **PromotionController lifecycle candidate→validation→shadow→verified→owner** | **UNWIRED** | `learning_guard.advance()`/`promote()` реализованы и протестированы, но ни один production-поток не прогоняет жизненный цикл |
| 6 | **Owner approval перед PRODUCTION** | **UNWIRED (enforced-by-construction)** | `promote(owner_approved=…)` структурно не даёт OWNER_PROMOTED без владельца (тест есть), но prod-вызывающего нет |
| 7 | **Bossman self-score отклоняется как достаточное доказательство** | **UNWIRED (enforced-by-construction)** | Гейты используют ТОЛЬКО `*_verified`; `bossman_self_score` — audit-only и доказано не влияет (`test_self_score_is_not_evidence`). Гарантия на уровне библиотеки, но prod-триггера нет |
| 8 | **Rollback metadata записывается перед promotion** | **UNWIRED (enforced-by-construction)** | `promote()` требует `RollbackInfo` (тест есть); prod-вызывающего нет |
| 9 | **Per-task-class degradation gate** | **UNWIRED** | В `evaluate_ab` (тест `test_per_class_regression...`); без A/B-прогона в проде не вызывается |
| 10 | **Security regression hard-block** | **UNWIRED (enforced-by-construction)** | `assert_no_security_regression()` (тест есть); без promotion-потока в проде не вызывается |

**Итог: 1 × WORK, 9 × UNWIRED** (из них 4 — enforced-by-construction: инвариант
гарантирован конструкцией библиотеки, но prod-триггера ещё нет; 1 — пути в
принципе нет: router-release). NOT_TESTED — ни одного: вся логика покрыта unit-
тестами; разрыв именно в production-триггере/wiring, а не в тестах. DEAD — нет.

## Что реализовано в этом проходе (только очевидный адаптер)
`bossman/runner.py`: `_learning_excluded(task_id)` + два call-site (decision,
failure). Secret Holdout теперь реально исключает задачи из durable learning-
корпуса. Fast path не тронут (по умолчанию holdout=None → no-op). Операционная
`working_memory` (state/restore задачи) под исключение НЕ подпадает — она нужна
самой задаче, это не cross-task learning.

## Почему остальные 9 НЕ подключены (и почему это правильно сейчас)
Пункты 2–10 требуют **production-триггера, которого не существует**: реального
same-model A/B (raw vs Model+Bossman) прогона, потока продвижения learned-
improvement и/или router-release. Всё это — benchmark/owner-hardware время.
- Создавать такие prod-потоки сейчас = «rewrite architecture / duplicate engines»,
  что задача прямо запрещает.
- Заявлять улучшение/прогонять promotion без A/B evidence = fake-green, что тоже
  запрещено.
Поэтому честный статус — библиотека-ворота готова и протестирована, но
активируется только когда появится реальный A/B-харнесс и поток продвижения
(owner hardware). Точки интеграции — в `docs/context/LEARNING_QUALITY_GUARD.md`.

## Remaining gaps (для будущего prod-wiring, когда будет A/B-харнесс)
- benchmark-харнесс, наполняющий `list[ABResult]` реальным same-model raw-vs-guarded;
- вызов `guard_promotion(...)` в потоке продвижения memory/skill (пп.2,5,6,8,9,10);
- вызов `context_fallback_to_raw(measured_retention)` в context-сборке (п.3) — только
  под измеренный retention, не на hot-path каждого запроса;
- (п.4) появится, только если будет адаптивный router-release; сейчас маршрут статичен.

## Проверки (на FINAL_REMOTE_SHA)
```
focused learning_guard + holdout:  19 passed (+4 pg-gated skips)
bossman-core (живой PG 16.13):     1127 passed, 5 skipped, 0 failed
command-center:                    619 passed, 2 skipped, 0 failed
compileall: PASS · secret scan: PASS
NEW_P0=0 · NEW_P1=0 · NEW_REGRESSIONS=0
```

## VERDICT
Ровно 1 из 10 гейтов реально подключён в production-путь (Secret Holdout);
остальные — протестированная библиотека без production-триггера (который сейчас
нельзя создать честно). Поэтому:

**LEARNING QUALITY GUARD PARTIAL**
