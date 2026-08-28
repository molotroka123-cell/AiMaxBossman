# V2 — Acceptance Matrix

Статус строки может стать DONE только когда заполнены все релевантные проверки
(файл proof в `docs/V2_PROOFS/` с PASS). Лид заполняет матрицу ТОЛЬКО по итогам
собственной проверки, не по отчётам агентов.

| # | Feature | Unit Test | Integration Test | Browser/UI Test | Failure Test | Persistence Test | Status |
|---|---------|-----------|------------------|-----------------|--------------|------------------|--------|
| 01 | Autopilot Objective | — | — | — | — | — | TODO |
| 02 | Smart Model Router | — | — | — | — | n/a | TODO |
| 03 | AI Governor | — | — | — | — | — | TODO |
| 04 | Model Benchmark Lab | — | — | — | — | — | TODO |
| 05 | Replay / Fork Session | — | — | — | n/a | — | TODO |
| 06 | Visual Agent Map | — | — | — | n/a | n/a | TODO |
| 07 | Worktree Sandboxes | — | — | — | — | — | TODO |
| 08 | Reviewer Gate | — | — | — | — | — | TODO |
| 09 | Browser Live View | — | — | — | — | n/a | TODO |
| 10 | Skill Library | — | — | — | n/a | — | TODO |
| 11 | NL Orchestration | — | — | — | — | n/a | TODO |
| 12 | Resource Brain | — | — | — | — | — | TODO |
| 13 | Mission KPI | — | — | — | — | — | TODO |
| 14 | Self-Healing | — | — | — | — | — | TODO |
| 15 | Mobile Command Mode | n/a | — | — | n/a | n/a | TODO |

Обозначения: PASS / FAIL / n/a (не релевантно, с обоснованием в proof) / — (не выполнялось).

Кросс-сценарии (обязательные, §39–41):

| Сценарий | Статус |
|---|---|
| Cross-Feature: автономное улучшение test-репозитория (20 шагов) | TODO |
| Cross-Feature: отказ endpoint'а → healing → router fallback → resume | TODO |
| Cross-Feature: mobile 390px — 10 действий | TODO |
| Persistence/Reboot (long task переживает рестарт сервиса) | TODO |
