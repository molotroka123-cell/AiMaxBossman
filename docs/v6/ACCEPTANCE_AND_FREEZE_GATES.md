# Epoch 6 — Acceptance & Freeze Gates

## Entry gate

Before implementation:

- V4/V5 frozen candidate defined;
- current correctness/release `P0_OPEN=0`, `P1_OPEN=0` on that candidate;
- startup path and owner UI work;
- baseline harness ready;
- remaining external `NOT_RUN` evidence explicitly listed.

## Performance acceptance gates

A candidate can be called `EPOCH6_VERIFIED` only if:

1. baseline and candidate are exact-SHA bound;
2. target metrics improve as declared or are explicitly `NO_CHANGE` with rationale;
3. p95/worst do not reveal new severe stalls;
4. no new OOM/paging behavior;
5. Stop/Pause/deny/revoke latency does not materially regress;
6. Computer Use stale/freshness/approval/postcondition regressions remain green;
7. Video preview/export correctness remains green;
8. restart/recovery and duplicate-effect protections remain green;
9. same-model held-out quality/verified-success does not regress;
10. CI and owner evidence use the candidate actually being released.

## Design acceptance gates (Workstream J)

Дополняют performance gates, не заменяют их. Изменение дизайна принимается, только если:

1. ни один существующий UX / owner-control / editors тест не ослаблен, не
   помечен skip/xfail и не удалён;
2. каждое новое состояние (loading / empty / partial / error / denied / stopped /
   paused / offline / degraded) имеет тест, что оно ПОКАЗЫВАЕТСЯ, когда система
   действительно в нём находится;
3. Stop / Pause / Take Control достижимы и отзывчивы ПОД НАГРУЗКОЙ, а не только
   на спокойном экране; human-speed контракт не смягчается;
4. нет регрессии frame/interaction latency по метрикам Workstream B;
5. `prefers-reduced-motion` уважается, анимация не конкурирует с model runtime и
   экспортом видео;
6. контраст, фокус и размеры целей нажатия ИЗМЕРЕНЫ, а не заявлены.

## Hard fail conditions

Дизайн-специфичные (Workstream J):

- результат показан как готовый БЕЗ верификации — «зелёная галочка» без улики;
- Stop/Pause стал менее доступен или менее отзывчив, чем до изменения;
- отказ (deny/blocked) показан без причины;
- долгая операция не показывает ни прогресса, ни возможности отмены;
- «улучшение» потребовало ослабить существующий тест.

Immediate `NO-GO` if any optimization:

- allows model text to substitute verified external effect;
- skips durable intent/evidence/receipt writes;
- retries an ambiguous irreversible effect blindly;
- hides errors as empty data;
- weakens permission/owner checks;
- increases duplicate effects;
- makes Stop/Pause lose authority;
- causes OOM/paging under a workload the baseline handled;
- disables/xfails existing safety tests to become green;
- claims live/hardware/model results that were not run.

## Minimum test pyramid

- changed-module unit/regression;
- negative-control tests;
- root/core/command-center affected suites;
- Windows-specific affected tests;
- browser/editor acceptance where UI changed;
- performance benchmark same workload before/after;
- stress/concurrency scenario;
- restart/recovery scenario;
- exact-SHA CI + owner run where required.

## Release labels

- `PLANNED` — docs only;
- `BASELINED` — frozen baseline exists;
- `IN_PROGRESS` — implementation started;
- `CANDIDATE` — target metrics met locally/CI but full evidence pending;
- `VERIFIED` — exact-SHA performance + correctness gates pass;
- `REJECTED` — speed gain causes correctness/safety/resource regression.

No percentage-complete score may replace these gates.