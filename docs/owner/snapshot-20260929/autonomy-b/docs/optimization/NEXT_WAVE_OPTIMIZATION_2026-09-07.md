# AiMaxBossman — NEXT WAVE: PERFORMANCE OPTIMIZATION

**Статус:** PLANNED / NOT_STARTED  
**Дата:** 2026-09-07  
**Ветка планирования:** `plan/performance-optimization-20260907`  
**Условие старта реализации:** только после честного V4/V5 freeze candidate и закрытия текущих P0/P1 correctness/release blockers.

## Что это за волна

Следующая волна разработки Bossman — не новая эпоха функций, а **системная оптимизация уже существующего продукта**: быстрее запуск, меньше лагов Command Center, ниже idle CPU/RAM, меньше ненужных model reloads, более быстрый verified Computer Use, отзывчивый Video Studio во время экспорта и контролируемая конкуренция ресурсов.

Главное правило: **скорость не покупается ценой execution truth, verification, owner control, permissions, security или crash recovery.** Если оптимизация делает систему быстрее, но ослабляет доказательство результата, она не принимается.

## Порядок работ

1. **FREEZE FIRST** — текущая Claude/Opus линия закрывает продуктовые дыры и собирает exact-SHA evidence.
2. **BASELINE** — на frozen SHA измеряем cold/warm start, UI/API p50/p95, process-tree RAM, unified-memory/VRAM accounting, model residency, observations/action, verified actions/min, media contention.
3. **MULTI-AUDIT SYNTHESIS** — сводим независимые аудиты Astra/GPT, Perplexity, Grok и другие в один backlog. Совпадение нескольких моделей повышает приоритет, но не заменяет measurement.
4. **QUICK WINS** — только низкорисковые изменения с A/B before/after.
5. **MEDIUM OPTIMIZATIONS** — event/delta updates, DB/query work, resource admission, context selection.
6. **POST-FREEZE ARCHITECTURE** — unified scheduler, residency-aware routing, более глубокий event-driven runtime — только после измерений.

## Главные метрики

- `cold_start_to_ui_ready_ms`
- `warm_start_to_ui_ready_ms`
- `ui_interaction_p50_ms`, `ui_interaction_p95_ms`
- `local_api_p50_ms`, `local_api_p95_ms`
- `time_to_first_useful_response_ms`
- `verified_action_p50_ms`, `verified_action_p95_ms`
- `verified_actions_per_minute`
- `observations_per_verified_action`
- `bossman_process_tree_ram_hwm_mb`
- `model_resident_memory_mb`
- `unified_memory_hwm_mb` / `dedicated_vram_hwm_mb` where the platform exposes them meaningfully
- `model_load_count`, `model_reload_count`
- `idle_cpu_median_pct`
- `preview_click_to_playable_ms`
- `export_runtime_s`
- `interactive_api_p95_during_export_ms`
- `recovery_to_safe_state_ms`

## Реалистичные цели

Цели задаются **от измеренного frozen baseline**, а не от прогнозов аудитов:

- cold start: `>=30%` быстрее;
- warm start: `>=25%` быстрее;
- background request/render work: `>=30%` меньше при сохранении freshness;
- TTFR на той же модели/конфигурации: `>=20%` быстрее, если качество не падает;
- verified Computer Use p50: `>=20%` быстрее при неизменных stale/approval/postcondition gates;
- ноль avoidable model reloads в 30-минутном scripted workload;
- во время тяжелого export интерактивный local API p95 не хуже `2x` idle p95;
- снижение RAM HWM — только если измеренный hot path показывает реальный выигрыш, без выноса обязательных state/security компонентов из памяти.

## Что уже сделано и НЕ надо дублировать

- GatewayClient на актуальной рассмотренной линии уже держит reusable `httpx.AsyncClient`; не писать второй connection-pool без профилирования конкретного path.
- Computer Operator уже получил reuse verified post-action observation там, где это безопасно.
- Windows owner acceptance уже нашёл `/v1`, observer dependencies, planner schema/token и Unicode path/runtime проблемы; это не надо заново выдавать за performance discovery.
- Video Studio уже имеет реальный FFmpeg/browser acceptance infrastructure — оптимизацию строить поверх него.
- Task/effect truth и signed evidence не переводить в lossy async queue.

## Что пока НЕ является фактом

До baseline не считать доказанными:

- `Bossman idle = 3–4 GB RAM`;
- `idle CPU = 15–20%`;
- `cold startup = 8–12 s`;
- конкретные tokens/s будущего Ryzen AI Max+ 395;
- точный VRAM/RAM split на unified-memory машине;
- `40–60% total speedup`;
- `+30–40% intelligence` от orchestration.

Эти значения могут быть полезными гипотезами для измерения, но не release claims.

## Связанные документы

- `docs/optimization/PERFORMANCE_OPTIMIZATION_MASTER_TZ_2026-09-07.md`
- `docs/optimization/PERFORMANCE_BASELINE_SPEC_2026-09-07.md`
- `docs/optimization/AUDIT_INDEX_2026-09-07.md`
- `docs/audits/2026-09-07__astra-runtime-responsiveness__audit__v1.md`
- `docs/audits/2026-09-07__perplexity-total-performance__audit__v1.md`

**Implementation status: 0%.** Эта ветка пока является техническим планом и сборником независимых аудитов.