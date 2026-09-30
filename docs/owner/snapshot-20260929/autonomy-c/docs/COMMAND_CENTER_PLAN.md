# BOSSMAN AI Command Center — план MVP-сессии

Архитектура: [`COMMAND_CENTER_ARCHITECTURE.md`](COMMAND_CENTER_ARCHITECTURE.md).
Полное ТЗ: [`COMMAND_CENTER_SESSION_GOAL.md`](COMMAND_CENTER_SESSION_GOAL.md), MVP — раздел 62, DoD — 64–65.

## Распределение работ (субагенты)

| Работа | Кто | Границы файлов |
|---|---|---|
| Исследование OpenCode (лицензия, API, Path A/B) | research-агент | только отчёт → раздел 9 архитектуры |
| Backend: БД, провайдеры, registry, агенты, task engine, очередь, scheduler, approvals, метрики, события, auth, тесты | build-агент №1 (Opus) | `command-center/**`, кроме `ui/` |
| UI: все страницы MVP по контракту раздела 6 архитектуры | build-агент №2 (Opus) | `command-center/ui/**` |
| Интеграция, e2e-прогон DoD, UI QA со скриншотами, отчёт, commit/push | оркестратор сессии | всё |

## Шаги

1. ✅ Зафиксировать брифы в `docs/`, обновить README (цель сессии).
2. ✅ Архитектура + план (эти файлы).
3. Исследование OpenCode → дополнить раздел 9 архитектуры.
4. Backend MVP (`command-center/`): пакет `bcc`, SQLAlchemy-схема, адаптеры
   `openai_compat` + `anthropic`, worker-очередь с lease/retry/checkpoint,
   scheduler с catch-up, метрики psutil, WS-события, токен-auth, шифрование
   ключей, pytest (adapter, persistence, queue retry, scheduler, API).
5. UI MVP (`command-center/ui/`): Home, Models, Agents, Tasks (+композер,
   +live-лог), Schedules, System, Settings; тёмная/светлая тема; Cmd+K.
6. Интеграция: поднять сервер, пройти Definition of Done (17 пунктов) реальными
   запросами; UI QA в Chromium (desktop + mobile viewport, скриншоты); фиксы.
7. `docs/COMMAND_CENTER_REPORT.md` + обновление README.
8. Commit + push.

## Definition of Done (проверяется в шаге 6)

Открыть dashboard → добавить локальный OpenAI-compatible endpoint → добавить
облачную модель → статусы online/offline → создать агента (модель + system
prompt) → создать задачу → запустить → обновить страницу → задача продолжилась/
завершилась → live/history-логи → задача по расписанию → CPU/RAM/(GPU) →
результаты → остановить активную задачу. Обычные операции — без терминала.

## Явно вне MVP (Phase 2/3 — раздел 63 ТЗ)

Orchestra/teams, smart routing, fallback-цепочки в UI, Playwright-панель,
OpenCode adapter, MCP manager, project memory, длительные автономные objectives,
replanning, evaluator, бюджеты облака с остановкой, второй узел ROG, удалённый
доступ, интеграции уведомлений.

## Примечание о ветке

ТЗ просит ветку `feature/bossman-command-center`; работа этой удалённой сессии
ведётся в выделенной ветке `claude/bossman-control-v03-43igbk` (правило сессии).
На локальной машине ветку можно переименовать/слить по вкусу.
