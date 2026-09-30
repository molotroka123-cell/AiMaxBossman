# TZ-08 — Наблюдаемость, телеметрия, CEO Control Plane (5 → 10)

Находки: OBS-01..OBS-05, UX-05. Инварианты: INV-5.

## 1. Текущее состояние
- Шина: `EventBus.emit` → таблица `events` + WebSocket (`command-center/bcc/events.py:34-72`); 105 видов событий; нет ротации.
- Логи run: `run_events` (`engine._log`), корреляция `contextvars` (`bossman/correlation.py`), flight recorder на стороне чтения (Postgres, `flight_recorder.py`).
- Метрики: psutil CPU/RAM/диск + GPU best-effort (`metrics.py`); нет экспорта.
- Testing period: журнал UI-действий с редакцией (`testing_period.py`, `ui/testing.js`), dead/rage-click детекторы.
- CEO-снимок организации есть как функция (`control_plane.py`), не как API.

## 2. Требования

### 2.1 Ретеншн событий (OBS-01) — MUST
1. `events`: хранить 14 дней или 200 000 строк (что раньше); `run_events`: 90 дней для `failed/blocked` run'ов, 14 — для `completed`; чистка — задача планировщика раз в час, батчами по 5 000 (`DELETE … WHERE id IN (SELECT id … LIMIT 5000)`), чтобы не держать WAL-лок.
2. Перед удалением — агрегат в `events_daily{day, kind, count, p50_ms, p95_ms}` (истории хватает для трендов).
3. Тест: 300 000 событий → после чистки ≤ 200 000, агрегаты совпадают с суммой.

### 2.2 Серверные задержки и SLO (OBS-02, OBS-04) — MUST
1. ASGI-middleware: гистограмма `http_request_duration_seconds{route,method,status}` с бакетами `[0.05,0.1,0.25,0.5,1,2,5,10,30]`; экспорт `GET /metrics` в формате Prometheus (без внешних зависимостей — текстовый формат прост) + `GET /api/latency` (JSON p50/p95/p99 по маршрутам за 15 мин).
2. SLO (первая версия, из наблюдений сессии `20783913fa36`):
   - `/api/apps` warm p95 ≤ 2 с; cold (первый вызов после старта) ≤ 15 с — отдельная метка `cold=1`.
   - `/api/models`, `/api/agents`, `/api/missions`, `/api/system` p95 ≤ 1 с warm.
   - `task.completed` для INFORMATIONAL p95 ≤ 60 с локально.
3. Error budget: 30-дневное окно, бюджет `1 − SLO` (например, 5 % запросов вне цели); при сгорании > 50 % бюджета за 7 дней — событие `slo.burn_rate` и баннер в UI. Burn-rate `= (наблюдаемая доля ошибок)/(1−SLO)`; алерт при `burn ≥ 2` на окне 1 ч и ≥ 1 на 6 ч (мульти-оконное правило Google SRE).

### 2.3 Трейсы (OBS-04) — SHOULD
OpenTelemetry-совместимые span'ы без обязательной зависимости: `trace_id = run_id-based uuid5`, span'ы `task/run/step/tool_call/verify/finalize` с `parent_span`; экспорт — JSONL в `data/traces/` + опциональный OTLP, если установлен `opentelemetry-sdk`. Flight recorder читает те же span'ы.

### 2.4 Жизненный цикл действия (INV-5) — MUST
Обязательная цепочка событий для каждой задачи с сайд-эффектом (имена — существующие конвенции `<subject>.<verb>`):
`request.classified{mode,capabilities,confidence}` → `capability.selected{tools,route}` → `permission.checked{effect,policy}` → `action.started{capability,target,fence}` → `action.result{succeeded,error}` → `action.verified{verified,verifier,sig}` → `task.finalized{status,reason}`.
Тест «цепочка полна»: для каждого run с `tool_calls` набор видов ⊇ обязательный, порядок по `id` монотонен, `trace_id` один.

### 2.5 CEO Control Plane (OBS-03, UX-05) — MUST
`GET /api/control-plane` — единый машиночитаемый ответ:
```
{ now, organization: control_plane.snapshot(),      # миссии, блокеры, ожидание владельца, failing_agents
  queue: {queued, leased, running, waiting_approval}, # engine
  treasury: {fable: ledger.remaining(), envelopes: treasury.snapshot(), burn_rate_usd_per_h, eta_exhaustion},
  fleet: {nodes:[…], placements:[…]},                 # TZ-05, пусто при флаге OFF
  slo: {routes:[{route,p95,target,budget_left}]},
  attention: [{kind, ref, since, why}] }              # то, что требует владельца, отсортировано по возрасту
```
Кэш 2 с; всё берётся из durable-источников (одинаково после рестарта). UI-страница — TZ-10 §2.3.

### 2.6 Dead-click детектор (OBS-05) — MUST
`viewFingerprint()` → `sha1(view.innerText.length || view.querySelectorAll('*').length || aria-busy count || disabled count)`, плюс `MutationObserver` на `#view` с `attributes+characterData+childList`: любой замеченный mutation в окне `DEAD_MS` = «отклик был». Проверить на реальном кейсе `#bcc-testing-publish` (меняет `textContent`) — не должен быть dead.

### 2.7 Приватность — сохранить, добавить тест
Grep-тест: ни один `bus.emit`/`_log` не передаёт поля `messages`, `prompt`, `system_prompt`, `api_key`, `cookie`; редакция `redact_secrets` вызывается для `url`/`args` (уже частично есть в `status()` браузера).

## 3. Математика
- Гистограмма с 9 бакетами: p95 оценивается интерполяцией внутри бакета; ошибка ≤ ширины бакета — для целей 1–2 с достаточно.
- Burn-rate: при SLO 95 % бюджет 5 %; `burn=2` за 1 ч означает расход 2 × (1/720 бюджета/ч) — за 30 дней сгорит 2 бюджета; порог выбирается так, чтобы алерт срабатывал за ≥ 15 дней до исчерпания.
- Ретеншн 200 000 строк × ~400 байт ≈ 80 МБ — комфортно для SQLite WAL.

## 4. Приёмка
1. `test_events_retention_and_daily_aggregate`.
2. `test_latency_histogram_and_slo_endpoint` — 100 запросов, p95 в `/api/latency`.
3. `test_burn_rate_alert_event`.
4. `test_lifecycle_chain_complete_for_side_effect_task` (с `FakeAdapter` + инструмент).
5. `test_control_plane_survives_restart` — снимок до/после рестарта приложения совпадает по `organization/queue/treasury`.
6. `test_dead_click_not_fired_on_text_change` (Playwright, testing_period включён).
7. `test_no_private_fields_in_events` (grep + runtime).

## 5. Чек-лист 10/10
- [ ] ретеншн + агрегаты
- [ ] гистограммы, `/metrics`, `/api/latency`, SLO + burn-rate
- [ ] span'ы с общим trace_id, flight recorder на них
- [ ] обязательная цепочка INV-5 с тестом
- [ ] `/api/control-plane`
- [ ] детектор dead-click на мутациях
- [ ] тест приватности
