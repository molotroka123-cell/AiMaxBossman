# V2 — Final Scorecard (исторический срез)

> **Это оценка волны V2 на коммите `24d24eb` (110 тестов).** Актуальное
> состояние системы — в [`V2_1_FINAL_SCORECARD.md`](V2_1_FINAL_SCORECARD.md):
> там 267 passed / 1 skipped и другие оценки, потому что в V2.1 появился
> канонический tool-loop, а часть функций из «DONE» здесь была DONE только
> как UI поверх API, без вызова моделью.


Оценки честные: **DONE** = backend + persistence + тесты (unit/integration/failure)
проверены лидом; **PARTIAL** = работает, но часть требует внешней зависимости
(бинарь/железо) или UI ещё дорабатывается; **FAILED** = не работает.

Дата: 28.08.2026. База тестов: **110 pytest passed** (`cd command-center && python -m pytest -q`).
UI: 15 feature-страниц + Home V2 + Mobile — построены, QA в Chromium пройден (0 ошибок консоли, 0 h-scroll на 320-1440px).

| # | Feature | Backend | Persistence | Failure handling | Tests | UI | Итог |
|---|---------|:------:|:-----------:|:----------------:|:-----:|:--:|------|
| 01 | Autopilot / Missions | PASS | PASS | PASS | PASS (5) | **DONE** | **DONE** |
| 02 | Smart Model Router (+OpenRouter meta) | PASS | PASS | PASS | PASS (5) | **DONE** | **DONE** |
| 03 | AI Governor | PASS | PASS | PASS | PASS (5*) | **DONE** | **DONE** |
| 04 | Model Benchmark Lab | PASS | PASS | PASS | PASS (4) | **DONE** | **DONE** |
| 05 | Replay / Fork Session | PASS | PASS | n/a | PASS (2) | **DONE** | **DONE** |
| 06 | Visual Agent Map | PASS | PASS | n/a | PASS (2) | **DONE** | **DONE** |
| 07 | Worktree+Terminal+OpenCode | PASS (terminal) / PARTIAL (opencode) | PASS | PASS | PASS (8) | **DONE** | **PARTIAL** |
| 08 | Automatic Reviewer Gate | PASS | PASS | PASS | PASS (3*) | **DONE** | **DONE** |
| 09 | Browser Live View | PASS | PASS | PASS | PASS (5) | **DONE** | **DONE** |
| 10 | Skills + Skill Forge + MCP Hub | PASS | PASS | n/a | PASS (7) | **DONE** | **DONE** |
| 11 | NL Orchestration | PASS | PASS | n/a | PASS (5) | **DONE** | **DONE** |
| 12 | Resource Brain | PASS | PASS | PASS | PASS (7) | **DONE** | **DONE** |
| 13 | Mission KPI | PASS | PASS | PASS | PASS (в 01) | **DONE** | **DONE** |
| 14 | Self-Healing | PASS | PASS | PASS | PASS (1) | **DONE** | **DONE** |
| 15 | Mobile Command Mode | n/a | n/a | n/a | PASS (QA) | **DONE** | **DONE** |

\* Governor/Reviewer тесты в общих файлах test_feat_governor_review.py; счётчики примерные.

## Пояснения к PARTIAL

- **07 OpenCode**: клиент `opencode serve` (health/attach/abort/diff) готов и
  протестирован на «недоступно» (honest 503, не падение). Полный цикл
  (create/fork/diff session) требует установленного бинаря `opencode` на машине —
  в этом окружении его нет. Terminal (3 режима, AUTO/ASK/DENY) + UI — DONE.

## Ядро (не входит в 15, но критично)

| Компонент | Итог |
|---|---|
| Worker Pool (N параллельных run'ов, BCC_WORKERS) | **DONE** (тесты) |
| Hard Cancel (Stop рвёт активный inference) | **DONE** (тесты) |
| Хуки engine (pick_model/before_run/on_step/gate_completion/on_failure/after_run) | **DONE** |
| Пак-интеграция (bcc/v2 + tables + skills) | **DONE** (48→110 тестов) |
| Схема БД V2 (12 таблиц + миграции колонок) | **DONE** |

## Кросс-сценарии (§39–41 мастер-промпта)

| Сценарий | Статус | Примечание |
|---|---|---|
| Persistence/Reboot (long task переживает рестарт) | **DONE** | test_persistence + core-1 |
| Failure: endpoint down → healing → router fallback → resume | **PARTIAL** | компоненты по отдельности протестированы (healing degraded→recovered, router fallback, engine retry); полный цепной E2E — на реальных моделях |
| Cross-feature: автономное улучшение репозитория (20 шагов) | **PARTIAL** | компоненты готовы; полный прогон требует реальной coding-модели/opencode |
| Mobile 390px (командный режим) | **DONE** | Пульт: миссия, approvals, agents, health, Quick Task — QA 320–430px |

## Итого

На момент V2: **14/15 функций DONE (backend+UI+QA), 1 PARTIAL (07 — OpenCode-часть требует
бинаря `opencode serve`; Terminal этой же функции — DONE).**
Ни одной FAILED. 110 автотестов зелёные. Полные кросс-сценарии §39–41 (цепной
E2E на реальных LLM) — PARTIAL: компоненты протестированы по отдельности + mock,
финальный прогон на боевой машине с GPU-моделями. Всё PARTIAL честно ограничено
внешней зависимостью — не «fake done».

## UI (добавлено после сборки страниц)

15 feature-страниц + Home V2 (overview) + Mobile Command (пульт) построены поверх
готовых API. **Browser QA в Chromium**: все 19 страниц desktop 1440px + mobile
320/375/390/430px — **0 ошибок консоли, 0 горизонтального скролла**. Сквозные
потоки проверены на живом сервере (создание/запуск миссии, терминал-политика
AUTO/DENY, NL-парсинг оркестра, approvals). Fake-кнопок нет — нереализованное
показано честным empty-state. Скриншоты: `docs/V2_PROOFS/shots/ui-*.png`.

Итог по UI на момент V2: **13/15 DONE (backend+UI+QA)**; 07 OpenCode-часть и полные
кросс-сценарии §39-41 — PARTIAL (внешние зависимости: бинарь opencode,
реальные GPU-модели).
