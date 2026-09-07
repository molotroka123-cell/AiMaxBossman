# Epoch 6 Charter — Bossman Velocity

## Mission

Сделать уже существующий AiMaxBossman быстрее и плавнее на реальных пользовательских задачах, уменьшить лишнюю работу CPU/RAM/GPU/unified memory и убрать ресурсную конкуренцию между UI, model runtime, Computer Use, Video Studio, Fleet и background services.

## In scope

- cold/warm startup и readiness sequencing;
- Command Center rendering/polling/API responsiveness;
- backend hot paths, blocking I/O, DB/query/serialization costs;
- Computer Use observe/plan/dispatch/verify latency;
- model residency, reloads, context/KV/cache tuning;
- Video Studio preview/export resource contention;
- Web Studio responsiveness and save/reload path;
- Fleet/background loops and resource admission;
- observational telemetry batching where safe;
- prompt/tool/skill context efficiency;
- process-tree RAM, GPU memory and unified-memory accounting;
- concurrency/soak and p95/p99 tail latency.
- обновление дизайна и визуальной системы как часть воспринимаемой отзывчивости
  (Workstream J, см. `DESIGN_REFRESH_WORKSTREAM.md`): состояния интерфейса,
  прогресс/ожидание, видимость owner control, читаемость доказательств,
  иконки, доступность — БЕЗ смены фреймворка и без новых возможностей.

## Out of scope

- новые пользовательские приложения;
- новый UI framework и переписывание фронтенда (обновление дизайна в Workstream J
  делается внутри существующего стека);
- «упрощение» интерфейса, скрывающее отказ, деградацию или незавершённость;
- новые архитектурные эпохи возможностей;
- ослабление approvals, postcondition verification, evidence, journal durability или fencing;
- замена реального benchmark synthetic-only цифрами;
- aggressive model quantization без quality gate;
- hardware claims для Ryzen AI Max+ 395 без измерения на этой машине;
- включение standing autonomy ради throughput.

## Immutable principles

1. **Correctness before speed.** Быстрое ложное `COMPLETED` хуже медленного verified результата.
2. **Exact SHA evidence.** Benchmark принадлежит реально проверенному source/tree.
3. **Same workload / same model / same config** для A/B, если меняется только orchestration.
4. **p95 важнее красивого среднего.** Оптимизация, которая ускоряет median и создаёт долгие stalls, отклоняется.
5. **Owner control has priority.** Stop/Pause/deny/revoke должны оставаться отзывчивыми даже под тяжёлой нагрузкой.
6. **Unified memory is one physical budget.** На Strix Halo нельзя двойным счётом складывать «RAM + VRAM» как независимые объёмы.
7. **Hypothesis != measured fact.** Числа из аудита становятся baseline только после воспроизводимого run.

## Entry gate

Epoch 6 implementation начинается после:

- одного зафиксированного V4/V5 candidate SHA;
- `OPEN_P0=0` и `OPEN_P1=0` для correctness/release blockers на этом candidate;
- owner-visible startup path работает;
- точные оставшиеся NOT_RUN/INSUFFICIENT_EVIDENCE записаны, а не скрыты;
- benchmark harness может привязать измерение к source SHA/tree и environment metadata.

До entry gate допускается только документация и measurement instrumentation, которая не меняет поведение продукта.

## Exit statement

Epoch 6 нельзя закрыть фразой «стало быстрее». Закрытие требует сохранённого baseline, candidate, delta report, exact-SHA CI/acceptance и доказательства, что safety/truth/recovery показатели не ухудшены.
