# TZ-03 — Инструменты, ОС, приложения (7 → 10)

Находки: TL-01..TL-04. Инварианты: INV-1, INV-2, INV-5.

## 1. Текущее состояние

- Реальные исполнители: Playwright (`tools_browser.py`), терминал с политикой (`tools_terminal.py`), запуск приложений через `subprocess.Popen(argv)` (`apps_control.py:379`, `tools_apps.py`), код/MCP/OpenClaw/OpenCode/память/факты — по одному модулю на семейство.
- Роутинг способности: только browser получает автоматическое evidence (`action_router.py`); остальные семейства блокируются честно, если у агента нет инструмента (`action_contract._before_run`).
- Цикл инструментов: `max_steps` агента (по умолчанию 4), таймаут на хук, `tool_calls` как receipt.
- CI: только `ubuntu-latest`; 358 тестовых файлов содержат Windows-ветвления.

## 2. Требования

### 2.1 Windows в CI (TL-01) — MUST
1. В `command-center-ci.yml` и `bossman-core-ci.yml` добавить job `windows-latest` (py3.12) с маркером `-m "windows or not linux_only"`; тесты, требующие Chromium/Playwright, — под `BCC_REQUIRE_BROWSER`.
2. Ввести маркеры `pytest.mark.windows`, `pytest.mark.linux_only`, `pytest.mark.live`; централизовать в `conftest.py`.
3. Минимальный набор для Windows-job: `apps_control` (Notepad smoke), `tools_terminal` (argv, `hard_deny_reason` для `del /f /s /q`, `rd /s`, `format`, `reg delete`), пути (`Path.resolve`, диски, UNC), `watchdog` (`os.kill` семантика на NT — комментарий `watchdog.py:50`).

### 2.2 Capability Manifest (TL-04) — MUST
Единый декларативный реестр `bcc/capabilities.py` (одна запись на исполнитель):
```
CapabilitySpec(
  name="apps.launch", tool_sources={"apps"}, platforms={"win32","linux"},
  effect="ask"|"auto"|"deny", idempotency="key:app_id", verification="post_state:process",
  max_duration_s=60, retry_policy=…, receipt_fields=[…])
```
`action_contract.Capability`, `ToolSpec` и верификаторы TZ-01 читают ЭТОТ реестр; дублирующие знания удаляются. Неизвестная способность → `CAPABILITY_UNAVAILABLE` (INV-6).

### 2.3 Автоматическое предоставление инструмента (TL-02) — MUST
Обобщить `action_router._before_run`: для классифицированной способности, если политика агента (`permissions`) и governor допускают, роутер добавляет в `task.meta.allowed_tools` инструменты семейства **на этот run** (а не навсегда), пишет событие `capability.selected`, и прикрепляет ожидаемое состояние (`meta.review.evidence`) из `CapabilitySpec.verification`. Если политика — `ask` → `waiting_approval` до выдачи; `deny` → `blocked`.

### 2.4 Observe → Decide → Act → Verify как контракт цикла (TL-03) — MUST
1. Каждый шаг цикла инструментов пишет `observation_before` и `observation_after` (структурный отпечаток: для browser — URL+title+hash DOM-скелета; для ФС — `stat` целевых путей; для apps — PID/окно).
2. Останов «нет прогресса»: если `k=3` шага подряд `observation_after == observation_before` и нет receipt с `succeeded` — прерывание с `failed/no_progress` вместо сжигания `max_steps`.
3. Таймаут на вызов инструмента по `CapabilitySpec.max_duration_s` (сейчас — только на хуки).
4. Bounded blind sequence: не более 1 действия между наблюдениями для `write/send/exec`.

### 2.5 Локальные модели без нативного tool-calling — SHOULD
Детерминированная обёртка: модель выдаёт JSON-план (`[{tool, args}]`) по фиксированной схеме; оркестратор валидирует по `CapabilitySpec` и исполняет сам. Уже есть зачатки в `action_router`; вынести в `bcc/planner_wrap.py`, тест на qwen-подобном фейке (`FakeAdapter` с JSON-ответом).

### 2.6 Desktop-наблюдение на Windows — MAY (в границах V2)
Только `psutil` + заголовки окон через `ctypes.windll.user32.EnumWindows`; UIA/pywinauto — отложить (V3 Visual State Engine).

## 3. Математика
- Ограничение шагов: ожидаемое число вызовов при вероятности успеха шага `p`: `E[steps] = 1/p`; при `p=0.5` и `max_steps=4` вероятность не уложиться `= (1−p)^4 = 6.25 %`. Правило «3 шага без прогресса» останавливает раньше, чем `max_steps`, если `p→0`, экономя ≥ 25 % вызовов на неисполнимых задачах (оценка по логу тестовой сессии: 2 из 4 действий были неисполнимыми).
- Отпечаток наблюдения: `h = sha256(url || title || skeleton)`; коллизии при изменении страницы ≈ 2⁻¹²⁸ — «нет прогресса» ложно не фиксируется.

## 4. Приёмка
1. Windows job зелёный на exact-SHA; ≥ 40 тестов с `@pytest.mark.windows` исполняются.
2. `test_capability_manifest_is_single_source` — каждая `Capability` в `action_contract` ссылается на запись реестра; `ToolSpec` без записи → ошибка загрузки.
3. `test_router_grants_terminal_tool_per_run` — агент без `tools`, промпт «создай файл через терминал», политика auto → инструмент выдан на run, receipt подписан, задача `completed`; тот же промпт с политикой deny → `blocked`.
4. `test_no_progress_stops_loop` — `FakeAdapter`, три одинаковых наблюдения → `failed/no_progress`, вызовов ≤ 3.
5. `test_tool_call_timeout` — инструмент спит дольше `max_duration_s` → `error/timeout`, run не завис.

## 5. Чек-лист 10/10
- [ ] windows-latest job
- [ ] `CapabilitySpec` — единый источник
- [ ] Выдача инструмента на run по политике
- [ ] Observe→Act контракт с «нет прогресса» и таймаутами
- [ ] Обёртка для моделей без tool-calling
