# TZ-05 — Fleet Control Plane и ресурсы (3 → 10)

Находки: FL-01..FL-05. Инварианты: INV-2 (единственность сайд-эффекта), INV-5, INV-6.

## 1. Текущее состояние
- Очередь V2: `engine.claim()` — выбор `queued` run'а с `worker_lease_until ≤ now`, CAS-обновление `status=leased` (`engine.py:449-472`); `_heartbeat` продлевает аренду каждые 30 с на 90 с (`:520-533`); `recover()` возвращает протухшие в очередь (`:474-500`).
- Ресурсы одного узла: `resource_brain` (аренды RAM/диска с TTL, единый пул VRAM), `bcc/features/resources.py` (политика, оценка, резерв/освобождение).
- Реестр устройств: `remote_client` — мобильные клиенты, не исполнители.
- Fleet OS: ZIP `AiMaxBossman_Fleet_OS_Complete_Foundation_DropIn.zip` — 70 файлов `bcc/v5/fleet/{registry,scheduler,leases,work_stealing,dead_letter,resume,retry,privacy,topology,node_agent,health,metrics,control_plane,…}` + тесты; **не влит**.

## 2. P0 — fencing-токен (FL-01) — MUST

### 2.1 Проблема (формально)
Пусть воркер A взял run с арендой до `t₁`. A «замирает» (сон ноутбука, GC, сетевой лаг к БД) на `> lease_seconds`. В `t₁+ε` воркер B (или тот же процесс после `recover()`) берёт run заново и исполняет шаг `s`. A просыпается и тоже исполняет `s`. Журнал V3 не спасает: A читал журнал до заморозки и считает `s` незакрытым. Итог: `duplicate_side_effect_count = 2`.

### 2.2 Требование
1. Колонка `task_runs.fence` (монотонный целочисленный epoch, `+1` при каждом успешном `claim`/`recover`-переквеуе). `claim()` возвращает `(run_id, fence)`.
2. Каждая запись, производящая сайд-эффект или закрывающая шаг (`tool_calls` insert, `TaskJournal.record`, `_finish`, `checkpoint`), MUST нести `fence` и исполняться как условный UPDATE/INSERT: `WHERE task_runs.fence = :fence`. Несовпадение → `FencedOut` → воркер немедленно прекращает run без записи результата.
3. Исполнители с внешним эффектом (browser act, terminal start, apps launch, github push) MUST проверять `fence` **перед** вызовом (`assert_fence(run_id, fence)`), а не только при записи receipt — иначе эффект уже произойдёт.
4. Heartbeat MUST быть условным: `UPDATE … WHERE fence = :fence`; если строк 0 — отменить run-таск (`asyncio.Task.cancel`).
5. Идемпотентность внешних эффектов: ключ `idem = sha256(task_id||step_id||fence_of_first_attempt)` в `tool_calls.args_hash` (уже есть поле) с UNIQUE `(task_id, step_id, idem)`; повтор с тем же ключом возвращает сохранённый результат (как OpenClaw дедуп).

Это модель «fencing token» (Kleppmann, DDIA гл. 8) поверх lease; аренда одна не гарантирует взаимного исключения.

## 3. Реестр узлов и планировщик (FL-02, FL-03) — MUST

### 3.1 Влить Fleet OS за флагом
1. Распаковать ZIP в `command-center/bcc/v5/fleet/` **через ревью**: каждый модуль читается, `__pycache__` отбрасывается, тесты переносятся в `command-center/tests/v5/fleet/`.
2. Флаг `BCC_FLEET_ENABLED` (default OFF). При OFF — поведение идентично текущему single-node.
3. Удалить ZIP из корня после влития (и остальные drop-in ZIP — см. TZ-07 §2.5).

### 3.2 Модель узла
```
Node{ node_id, kind: workstation|laptop|cloud, caps:{gpu:[{name, vram_mb}], ram_mb, cpu, os}, 
      labels:{privacy: local_only|cloud_ok}, health, last_heartbeat, epoch }
```
Heartbeat период `T/3` при TTL `T=30 с`; узел `SUSPECT` после `T`, `DEAD` после `2T` — его аренды возвращаются в очередь с новым `fence`.

### 3.3 Размещение (score с объяснением)
Для run `r` с требованием `req` и узла `n`:
```
fit(n,r)  = 1 if req ⊆ caps(n) else −∞          # VRAM, RAM, ОС, privacy-label
score(n,r)= w₁·fit + w₂·(1 − load(n)) + w₃·locality(n,r) + w₄·reliability(n) − w₅·cost(n)
```
Дефолт `w = (1, 0.4, 0.3, 0.2, 0.1)`; `locality=1`, если данные/сессия браузера уже на узле; `reliability` — Beta-оценка как в TZ-04 §3. Результат `PlacementDecision{node, score, rejected:{node:reason}}` пишется в `run.route` (уже есть колонка) — это и есть `placement_explain` из ZIP.

Privacy-инвариант: run с `privacy=local_only` MUST NOT размещаться на `cloud` (fail-closed при неизвестной метке).

### 3.4 Ёмкость (проверка перегруза)
Закон Литтла: `L = λ·W`. При средней длительности run `W` и допустимой параллельности узла `L_max` (из RAM/VRAM аренд) планировщик ограничивает приём `λ ≤ L_max/W`; превышение → очередь с `ETA = (L − L_max)/ (L_max/W)` в `control-plane`.

### 3.5 Work stealing
Свободный узел забирает `queued` run у перегруженного, если `fit=1` и `locality`-штраф < выигрыша по времени ожидания; перенос — только для run без открытой браузерной сессии (сессия не мигрирует).

## 4. Учёт GPU (FL-05) — SHOULD
`metrics.gpu_info()` → массив `{index, name, vram_total, vram_used, util}`; `resource_brain` резервирует VRAM на модель по `bench.ram_mb`/`vram_mb`; аренда VRAM — часть `Node.caps`. Учёт GPU-секунд → в казначейство (TZ-09 §4).

## 5. Приёмка (chaos-тесты, детерминированные)
1. `test_fence_rejects_zombie_writer` — два `Engine` на одной SQLite: A claim → искусственная пауза → B recover+claim → A пытается `record`/`tool_calls` → `FencedOut`, в БД один receipt.
2. `test_heartbeat_conditional_on_fence` — после перехвата heartbeat A обновляет 0 строк и отменяет свой таск.
3. `test_idempotent_external_effect` — повтор шага с тем же `idem` не вызывает исполнитель второй раз (`duplicate_side_effect_count=0`).
4. `test_node_dead_after_2T_returns_leases`.
5. `test_placement_respects_privacy_label` — `local_only` не уходит на cloud даже при `fit=1` и свободной ёмкости.
6. `test_placement_explain_lists_rejections`.
7. `test_little_law_backpressure` — при `λ > L_max/W` очередь растёт, приём отложен, ETA положительный.
8. Существующие ZIP-тесты `test_fleet_foundation.py`, `test_fleet_complete_extensions.py` проходят в CI под флагом.
9. Регрессия single-node при `BCC_FLEET_ENABLED=0`: полный прогон без изменений.

## 6. Чек-лист 10/10
- [ ] `fence` в run, условные записи, проверка перед внешним эффектом
- [ ] idempotency-ключ шага с UNIQUE
- [ ] Fleet OS влит за флагом, ZIP удалён
- [ ] Реестр узлов, heartbeat, SUSPECT/DEAD
- [ ] Планировщик со score и объяснением, privacy-инвариант
- [ ] Little-law backpressure, work stealing
- [ ] GPU-учёт в аренде и казначействе
