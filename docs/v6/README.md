# Epoch 6 — Bossman Velocity

**Статус:** PLANNED / NOT IMPLEMENTED  
**Назначение:** post-freeze системная оптимизация производительности, отзывчивости и ресурсоэффективности AiMaxBossman.  
**Условие старта:** только после честного V4/V5 freeze candidate на exact SHA и закрытия текущих P0/P1 correctness/release blockers.

Epoch 6 не является новой пачкой функций. Это этап, на котором уже существующий Bossman должен стать быстрее, плавнее, экономнее по CPU/RAM/GPU/unified memory и предсказуемее под нагрузкой — **без ослабления execution truth, verification, owner control, permissions, security, durability и crash recovery**.

## Канонический пакет документации

1. [EPOCH_6_CHARTER.md](EPOCH_6_CHARTER.md) — границы этапа, цели, что входит и не входит.
2. [ARCHITECTURE_AND_WORKSTREAMS.md](ARCHITECTURE_AND_WORKSTREAMS.md) — архитектурные направления оптимизации и hot paths.
3. [METRICS_BASELINE_AND_BENCHMARK.md](METRICS_BASELINE_AND_BENCHMARK.md) — exact-SHA baseline, метрики, сценарии и A/B правила.
4. [ACCEPTANCE_AND_FREEZE_GATES.md](ACCEPTANCE_AND_FREEZE_GATES.md) — Definition of Done и release/performance gates.
5. [HARDWARE_MEMORY_AND_MODEL_RESIDENCY.md](HARDWARE_MEMORY_AND_MODEL_RESIDENCY.md) — RAM/VRAM/unified-memory учёт, Ollama/model residency и target Strix Halo.
6. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — порядок Phase 0→3, зависимости, приоритеты P0/P1/P2.
7. [ROLLBACK_RISK_AND_SAFETY.md](ROLLBACK_RISK_AND_SAFETY.md) — что запрещено ускорять ценой корректности, rollback и kill criteria.
8. [MULTI_MODEL_AUDIT_SYNTHESIS.md](MULTI_MODEL_AUDIT_SYNTHESIS.md) — как объединять Astra/Perplexity/Grok/другие аудиты без превращения прогнозов в факты.
9. [OPUS_IMPLEMENTATION_HANDOFF.md](OPUS_IMPLEMENTATION_HANDOFF.md) — готовый handoff единому интегратору после freeze.
10. [DESIGN_REFRESH_WORKSTREAM.md](DESIGN_REFRESH_WORKSTREAM.md) — Workstream J: обновление дизайна как вторая половина задачи об отзывчивости.

## Исходные документы, которые Epoch 6 собирает в единую систему

- `docs/optimization/NEXT_WAVE_OPTIMIZATION_2026-09-07.md`
- `docs/optimization/PERFORMANCE_OPTIMIZATION_MASTER_TZ_2026-09-07.md`
- `docs/optimization/PERFORMANCE_BASELINE_SPEC_2026-09-07.md`
- `docs/optimization/AUDIT_INDEX_2026-09-07.md`
- `docs/audits/2026-09-07__astra-runtime-responsiveness__audit__v1.md`
- `docs/audits/2026-09-07__perplexity-total-performance__audit__v1.md`
- новые независимые аудиты (например Grok) добавляются в индекс после появления их exact file/SHA.

## Смысл этапа в одной строке

**V4/V5 доказывают, что Bossman делает работу правильно; Epoch 6 доказывает, что тот же самый правильный Bossman делает её быстрее и экономнее.**

Воспринимаемая скорость — часть той же задачи, поэтому в Epoch 6 входит и
обновление дизайна (Workstream J): интерфейс, который честно показывает состояние,
прогресс и контроль владельца. Дизайн подчиняется тем же принципам, что и
производительность, и не имеет права выглядеть увереннее системы.

## Текущий статус

- Implementation: **0%** (включая Workstream J — дизайн: 0%).
- Performance baseline frozen candidate: **NOT_RUN**.
- Exact Bossman idle RAM/VRAM footprint: **UNKNOWN** до измерения process tree на frozen SHA.
- Wide optimization changes before freeze: **BLOCKED BY POLICY OF THIS PLAN**.
- Measurement/instrumentation before freeze: разрешено только если не меняет поведение и помогает release evidence.
