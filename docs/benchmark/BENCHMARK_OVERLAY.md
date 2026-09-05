# Benchmark Overlay (пассивный) — интеграция drop-in v2 в репозиторий

Пакет: `bossman-core/bossman_v3/benchmark_overlay/`. Тесты: `bossman-core/tests/test_v3_benchmark_overlay.py`,
`bossman-core/tests/test_v3_org_benchmark.py` (пять стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner).

```
РЕАЛЬНАЯ СИСТЕМА
  ↓ durable-истина: org store · fleet flights/journal · TaskJournal (подписанные шаги)
adapters.py  (только чтение; событие ≠ доказательство)
  ↓
BenchmarkCollector → BenchmarkScorer (10 осей) → HardFailGate (9 hard fail'ов) → BenchmarkReport
  ↓ write_reports → benchmark-report.json / .md
scripts/update_readme_scorecard.py --from-benchmark <report.json>
  ↓ hard fail'ы понижают связанные оси (cap 6.0, снимают VERIFIED/ATTESTED), счётчики копируются
docs/benchmark/current-scorecard.json → README (блок BOSSMAN_LIVE_SCORECARD_*)
```

## Контракт пассивности

Overlay не меняет решения исполнения, политику, вердикт верификатора, ретраи и маршрутизацию. Он не
вызывается из рантайма — адаптеры читают durable-хранилища после факта. Проверка «адаптер ничего не
изменил» — в `test_adapters_read_only_from_real_organization_and_feed_scorecard`.

Агрегат `total_score_secondary` — вторичный; авторитетны 10 независимых осей. При нулевой стоимости
метрики «за доллар» = `None`, а не деление на ноль. Live-SLA восстановления (1.5 с) не заявляется:
`resume_sla_claimable=False` до измерения на интегрированном рантайме.

## Adoption table (ZIP → репозиторий)

| Компонент ZIP | Эквивалент в репо | Решение | Куда | Почему |
|---|---|---|---|---|
| `benchmark_overlay/models.py` | — | ADAPT | `bossman_v3/benchmark_overlay/models.py` | + `source` у события; `total` объявлен вторичным |
| `collector.py` | `bossman.benchmark` (SHA-bound V1 бенчмарк) — другой слой | ADAPT | `collector.py` | пассивный; + `extend` |
| `hard_fail.py` | — | ADAPT | `hard_fail.py` | те же 9 hard fail'ов; + `signature_valid=False` ⇒ stale_evidence_accepted (EH-01) |
| `scorer.py` | — | EXTEND | `scorer.py` | + FALSE_SUCCESS_RATE, RECOVERY_SUCCESS_RATE, TEAM_OVERHEAD_RATIO, MODEL_ESCALATION_RATE, LOCAL_EXECUTION_RATE, CLOUD_AVOIDANCE_RATE, TOKEN_VALUE_METRIC; `verification_truth` = 0 при любом неподтверждённом результате |
| `org_evaluator.py` (async) | — | ADAPT | `evaluator.py` (sync) | сценарии гоняют реальный рантайм и возвращают факты; SLA не заявляется |
| `baseline.py` | `bossman.benchmark.engine.compare_reports` (другой отчёт) | ADAPT | `baseline.py` | новый hard fail всегда CRITICAL |
| `report.py` | `bossman.benchmark` пишет свои отчёты | ADAPT | `report.py` | `benchmark-report.json/.md`; N/A вместо деления на ноль |
| — | — | EXTEND | `adapters.py` | события ТОЛЬКО из durable-истины (org store, fleet flights, TaskJournal) |
| — | `scripts/update_readme_scorecard.py` | EXTEND | `--from-benchmark` | детерминированный мост отчёт → scorecard |
| `tests/benchmark_overlay/*.py` | — | ADAPT | `tests/test_v3_benchmark_overlay.py`, `tests/test_v3_org_benchmark.py` | над реальными фикстурами, не над словарями |
| `scripts/validate_benchmark_overlay.py`, `.pytest_cache/*` | — | SKIP_DUPLICATE | — | валидатор самого ZIP / мусор |
| `docs/benchmark/SCORING_SPEC.md`, `V3_ORG_FLEET_STRESS.md`, `INTEGRATION_ORDER.md`, `SWARM_BENCHMARK_ADDENDUM.md` | — | MERGE | этот файл | содержимое сведено сюда |
| `bossman.benchmark` (V1, `python -m bossman.benchmark`) | существует | KEEP_CURRENT | — | SHA-bound бенчмарк способностей V1; overlay его не заменяет |

## Стресс-бенчмарки (детерминированные, `test_v3_org_benchmark.py`)

| # | Бенчмарк | Что доказывает | Метрики |
|---|---|---|---|
| A | CompoundFailureBenchmark | 6 обязательных детей, у одного эффект без требуемой улики → 5 VERIFIED, родитель НЕ COMPLETE; hard fail'ов нет | children, parent_completed |
| B | CrossDepartmentLeakProbe | факт `department:trading` не виден `department:research`; экспорт вида не из allowlist → `ExportBlocked` | blocked, leaked |
| C | LongHorizonResumeBenchmark | 10 шагов, падение на s7, рестарт: s1–s6 не переигрываются, duplicate_side_effect_count=0 | replayed_steps, measured_elapsed_s (SLA не заявляется) |
| D | FleetTopologyStress | laptop 8 ГБ / AI MAX 128 ГБ / cloud: память, способность, приватность соблюдены; private+200 ГБ → нет узла (не облако) | rejections, blocked_reason |
| E | TokenValueMetric | Quality × Reliability / Cost; N/A при нулевой стоимости | — |

Hard fail'ы: `false_success`, `duplicate_side_effect`, `privacy_violation`, `permission_bypass`,
`parent_success_with_failed_child`, `stale_evidence_accepted`, `review_bypass`, `scope_leak`, `treasury_overrun`.

Запуск: `cd bossman-core && pytest tests/test_v3_benchmark_overlay.py tests/test_v3_org_benchmark.py`;
затем `python scripts/update_readme_scorecard.py --from-benchmark <benchmark-report.json>`.
