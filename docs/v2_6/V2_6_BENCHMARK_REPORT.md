# V2.6 — BENCHMARK REPORT (честные измерения, без выдумок)

## Fast path (модуль 24) — чистая оркестрация задачи

Бенч: pick_agent + system prompt + tool schemas + ContextBuilder init/build +
ВСЕ детерминированные V2.6-хуки (signals → uncertainty → compute level,
flight-recorder no-op), N=300, тот же хост.

| Метрика | ДО V2.6 (4aaa17b) | ПОСЛЕ (Phase 2+) |
|---|---|---|
| p50 | 0.156 ms | 0.125–0.132 ms |
| p95 | 0.267 ms | 0.171–0.185 ms |

Вывод: **оверхед V2.6-контроллеров неизмерим на фоне шума** (детерминированные
функции, микросекунды; регрессии нет). Тест-сторож:
`test_controller_overhead_is_negligible` (1000 итераций signals+uncertainty+
level < 0.5 s).

Дополнительно при `BOSSMAN_ADAPTIVE_COMPUTE=1`: C0-задачи (тривиальные) вообще
не платят за retrieval (embed + hybrid search) — устранение O8 из
FABLE5-аудита; по умолчанию флаг OFF, поведение прежнее.

## Execution cache (модуль E)

Микровыигрыш на hot path: `real_window()` больше не разбирает YAML на каждый
model-call (кэш по mtime). Счётчики hits/misses доступны в
`exec_cache.get_cache().stats()` — измерение реального hit-rate возможно только
на живой нагрузке владельца.

## Измерено дополнительно (см. V2_6_RESOURCE_REPORT.md)

| Метрика | Значение |
|---|---|
| Цена V2.6 по RSS | **+0.8 MB** (все 14 модулей) |
| Стек контроллеров на задачу | **0.013 ms** p50 |
| `real_window()` холодный → тёплый | **9.37 ms → 0.0062 ms** (≈1400×) |
| Разбор registry.yaml без кэша | 8.52 ms p50 — платилось КАЖДОЙ задачей до V2.6 |
| Парсинг CSV 1000 строк / из кэша | 1.61 ms / 0.073 ms |
| Research QUICK (3 источника, обвязка) | 0.27 ms p50 |

## Что НЕ измерено (и почему не заявляется)

- **VerifiedSuccess before/after** = NOT MEASURED: требует реального
  same-model A/B (Raw vs Model+Bossman) на owner hardware с внешней
  верификацией. Learning Quality Guard готов принять эти прогоны; выдумывать
  числа запрещено.
- **IntelligenceRetention** для Personal Context Router = NOT MEASURED —
  поэтому selection выключен по умолчанию (RAW), включение — только после A/B.
- **RAM** = ИЗМЕРЕНО: +0.8 MB на все модули V2.6 (см. RESOURCE_REPORT).
- **VRAM** = на этом хосте GPU физически отсутствует (нечего мерить);
  на железе владельца — впереди.
- «5x efficiency» — не заявляется: unified benchmark не проводился.
