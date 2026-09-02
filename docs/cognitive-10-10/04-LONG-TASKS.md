# 04 — Длинные задачи 6/10 → 10/10

Код: `bossman-core/bossman/cognitive/tasks.py`
Тесты: `bossman-core/tests/test_cognitive_tasks.py`

## Durable Task Journal

Поля шага: `task_id, run_id, goal, constraints, plan_version, step_id,
dependencies, state, attempt, input_hash, output_hash, effect_id, receipt,
verification, started_at, completed_at, checkpoint_ref, next_action`.

Состояния: `PENDING → READY → RUNNING → {WAITING, RECONCILING,
FAILED_RETRYABLE, FAILED_FINAL}`, `RECONCILING → VERIFIED`.
`COMPLETED` без verifier не существует. `RUNNING → VERIFIED` напрямую запрещён
(`IllegalTransition`) — только через `RECONCILING` + `verifier_id/verification`
(`VerificationRequired`), иначе `FalseCompletion`.

## Checkpoint (`Checkpointer.write` после каждого важного шага)

Подтверждённые результаты, оставшиеся шаги, актуальный DAG, расход бюджета,
внешние эффекты (`effect_id`), approvals, receipts, открытые гипотезы,
последняя проверенная среда (`last_verified_env`).

## Resume после перезапуска (`ResumeRecovery.recover`, 7 шагов ТЗ)

1. Загрузить journal. 2. Сверить среду (`EnvSnapshot.diff`).
3. Найти `RUNNING/RECONCILING/WAITING`. 4. `effect_probe(step) → True/False/None`:
   есть эффект → RECONCILING (ждём verifier), нет → FAILED_RETRYABLE,
   неизвестно → RECONCILING (не повторять вслепую). 5. Без слепых повторов.
6. Working State восстанавливается из checkpoint. 7. Продолжить с первого
   незавершённого (`resume_from`), `lost_verified_steps` считается сверкой
   VERIFIED до/после (`resume_accuracy_ok = lost == 0`).

## Среда изменилась → revalidation

`EnvSnapshot`: `git_sha, browser_tab, ui_digest, file_digest, api_state,
credential_scope, model_price`. Любой diff → `need_revalidation=True`,
план обновляется с новым `plan_version` (см. `add_dependency`).

## Dynamic DAG

- `ready_steps/mark_ready`: только с VERIFIED-зависимостями (блокировка).
- `add_dependency` с `new_plan_version` (версионирование плана).
- `retry_step`: только `FAILED_RETRYABLE → READY`, `effect_id` переиспользуется
  (идемпотентность: `effect_id = stable(task, step, input_hash)` фиксируется
  ДО первого внешнего вызова → `DuplicateExternalEffects = 0`).
- `cancel_branch`, rollback/компенсации — доменные колбэки поверх journal
  (шаблон в INTEGRATION-GUIDE §5).

## Нагрузочный сценарий приёмки (30–100 шагов)

Три перезапуска, отказ инструмента, stale evidence, смена Git SHA, чужое окно
браузера, исчерпание бюджета, конфликт данных, отмена, частичный side effect.
Метрики — `verify.LONGTASK_GATES`: `LongTaskVerifiedSuccess ≥ 0.95,
ResumeAccuracy = 1.00, DuplicateExternalEffects = 0, LostVerifiedSteps = 0,
WrongDependencyExecution = 0, FalseCompletion = 0, BudgetContinuity = 1.00,
RecoverySuccess ≥ 0.95`.
