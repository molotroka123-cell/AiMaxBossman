# AiMaxBossman — Multi-Model Performance Audit Index

**Статус:** COLLECTION IN PROGRESS / IMPLEMENTATION NOT STARTED  
**Ветка:** `plan/performance-optimization-20260907`  
**Правило:** сначала закрываем текущие release/freeze дыры, затем делаем единый optimization plan по нескольким независимым аудитам и реальному baseline.

## Уже собранные аудиты

### Astra / GPT-5.6 Sol
`docs/audits/2026-09-07__astra-runtime-responsiveness__audit__v1.md`

Фокус: startup critical path, Command Center responsiveness, model residency, Computer Use observation loop, Video Studio contention, RAM/VRAM/unified-memory accounting, безопасные границы оптимизации.

Главное отличие: всё разделено на `MEASURED`, `CODE-EVIDENCED`, `HYPOTHESIS`. Числа без exact-SHA baseline не выдаются за факты.

### Perplexity
`docs/audits/2026-09-07__perplexity-total-performance__audit__v1.md`

Фокус: lazy startup, polling, Ollama tuning, media concurrency, context/skill selection, telemetry, estimated hardware behavior.

Полезно как источник гипотез, но заявленные startup/RAM/CPU/tokens/s и проценты ускорения пока не считаются measurement. Часть code assumptions уже требует перепроверки: например, current GatewayClient на рассмотренной линии уже переиспользует `httpx.AsyncClient`.

### Hardware/model audit
Исходный Perplexity hardware audit находится в истории `d5187cdf94510b5db9ab02f56cc14015af240844` (`docs/hardware/AI_MAX_PRO_128GB_AUDIT.md`). Его числа для 70B — прогноз для будущей машины, не фактические результаты Bossman.

### Grok
**PENDING DISCOVERY IN THIS LANE.** Владелец сообщил, что Grok также делает аудит. Когда его commit/file появится в этой ветке или будет дан SHA, добавить сюда как отдельный независимый источник, не переписывая предыдущие оценки.

## Правила синтеза

1. Совпадение 2–3 моделей повышает приоритет **только как гипотезы**, но не заменяет profiling.
2. Реальный owner/CI trace важнее уверенного текста модели.
3. Если аудит предлагает уже существующий fix — пометить `ALREADY_IMPLEMENTED`, не дублировать архитектуру.
4. Если аудит предлагает ускорение за счёт ослабления journal/evidence/approval/freshness — отклонить.
5. Любой точный процент ускорения требует before/after на одном workload, одном model/config и привязанном source SHA.
6. RAM/VRAM на unified-memory APU нельзя считать как две независимые ёмкости.
7. Среднее время не достаточно: смотреть p50/p95/worst и stalls.
8. Большое ускорение, которое ухудшает verified success / owner-control latency / recovery, считается регрессией.

## Совпадающие направления уже сейчас

Высокая уверенность, что стоит измерить сразу после freeze:

- startup fan-out и необязательная eager initialization;
- background polling/refresh и broad UI updates;
- local model residency / avoidable reloads;
- FFmpeg/media contention с интерактивной работой;
- prompt/context/skill/tool overhead;
- expensive Computer Use observations/model calls;
- write amplification и blocking I/O на hot paths;
- реальный process-tree RAM / unified-memory high-water mark.

## Уже известные анти-дубли

- GatewayClient reuse уже есть на рассмотренной актуальной линии — искать конкретные другие per-request clients, а не писать второй pool.
- Computer Use уже получил reuse verified post-action observation там, где это безопасно.
- Video Studio уже имеет real FFmpeg/browser acceptance harness.
- Windows `/v1`, observer deps, planner schema/token и Unicode/path проблемы — это уже известные owner-run findings, не новая performance discovery.
- TaskJournal/effect truth нельзя превращать в lossy telemetry queue.

## Следующий документ после freeze

После появления frozen candidate + Grok/других аудитов обновить:

`docs/optimization/PERFORMANCE_OPTIMIZATION_MASTER_TZ_2026-09-07.md`

и сгенерировать final synthesis:

`docs/optimization/PERFORMANCE_AUDIT_SYNTHESIS_<FROZEN_SHA>.md`

В synthesis для каждого пункта должны быть поля:

- audit sources;
- current code path;
- evidence level;
- baseline metric;
- expected gain range;
- implementation complexity;
- regression risk;
- exact test;
- status: `REJECTED / ALREADY_DONE / QUICK_WIN / MEDIUM / POST_FREEZE_ARCH`.

**Текущий статус:** аудиты собираются; optimization code intentionally NOT_STARTED.