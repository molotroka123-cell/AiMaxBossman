---
name: atomicity-boundary
description: Применять, когда запись в базу и вызов внешнего порта (реестр, API, файловая система, освобождение блокировки) идут подряд и не могут разделить одну транзакцию. Не делать вид, что транзакция общая: записать исход попытки, сделать незавершённое состояние ВИДИМЫМ и уборку — повторяемой и идемпотентной.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: reliability
---

# Atomicity Boundary

Между `commit` в базе и `port.release(...)` есть щель. Падение в щели — это не
редкий случай, а состояние, которое обязано быть представимым в данных.

## Признак, что это твой случай

В одной функции подряд идут: запись в durable-хранилище И вызов чего-то внешнего,
и нет общей транзакции. Если ты пишешь `return ok=True` в конце — ты соврал.

## Порядок

1. **Не сворачивай два исхода в один флаг.** Результат хранит исход базы и исход
   порта РАЗДЕЛЬНО. Один `ok` обещает атомарность, которой нет.
2. **Запиши исход попытки** во внешний вызов в уже закоммиченную запись:
   `OK` / `UNRESOLVED` / `KEYS_NOT_RECORDED`.
3. **Сделай незавершённое видимым**: запрос вида `store.pending_releases()`
   перечисляет ровно те записи, у которых внешний шаг не закончен.
4. **Сделай уборку повторяемой**: `resolve_pending_releases()` дочищает и
   опустошает список. Повторный вызов ничего не ломает.
5. **Контракт порта: release идемпотентен и no-op для ключа, который держит кто-то
   другой.** Иначе поздняя уборка украдёт свежую аренду.
6. **Читай владельца и ключи из ДОЛГОВЕЧНОЙ записи, не из аргумента.** Аргумент —
   это утверждение вызывающего; освободить чужую аренду по такому утверждению —
   ровно тот отказ, который ты предотвращаешь.
7. **Освобождай то, что было ВЗЯТО**, а не то, что называет текущая спецификация:
   ревизия могла поменять `conflict_keys` после допуска.
8. **Молчание — не успех.** Отсутствие записи о ключах — это `KEYS_NOT_RECORDED`,
   а не «всё хорошо».

## Реализация в этом репозитории

`bossman_shared/objective_admission.py` (коммиты `0d732c7` → `15f666d`):

- `RELEASE_UNRESOLVED = "UNRESOLVED"`, `RELEASE_UNKNOWN_KEYS = "KEYS_NOT_RECORDED"`;
- `store.pending_releases()` (`bossman_shared/objective_store.py`) — список
  незавершённых;
- `AdmissionKernel.resolve_pending_releases(...)` — повторяемая уборка;
- `settle` больше НЕ принимает `objective_id` — параметр удалён, это закреплено
  тестом по сигнатуре.

Тесты — `tests/test_v5_fairness.py`, по одному на каждый разобранный случай:

| тест | что доказывает |
|---|---|
| `test_the_owner_comes_from_the_record_never_from_the_caller` | владелец берётся из записи |
| `test_an_unknown_reservation_is_refused_before_anything_is_released` | отказ до любого действия |
| `test_a_second_settle_is_refused_and_does_not_release_twice` | конкурентный settle, порт не зовут дважды |
| `test_an_unresolved_release_is_visible_and_repeatable` | таймаут → UNRESOLVED, ключ виден, retry дочищает |
| `test_repeatable_cleanup_never_steals_a_lease_taken_since` | поздняя уборка не крадёт свежую аренду |
| `test_cleanup_is_idempotent` | повтор → один вызов release суммарно |
| `test_a_legacy_reservation_without_recorded_keys_says_so` | KEYS_NOT_RECORDED вместо «успеха» |
| `test_the_keys_released_are_the_recorded_ones_not_the_current_spec` | освобождается взятое |

## Историческая причина

До `0d732c7` единственный вызов `conflicts.release` стоял на пути отказа. Успешный
допуск возвращался, ДЕРЖА ключи, — правильно, пока миссия идёт, и неправильно
навсегда после. Замер: `releases == 0`, obj-b получает `conflict_held` навсегда.
Никакая справедливость планировщика этого не лечит: `admit` отказывает
состарившемуся проигравшему при любом ранге.
