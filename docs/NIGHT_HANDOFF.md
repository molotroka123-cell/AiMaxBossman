# NIGHT HANDOFF — BOSSMAN V2 (обновляется на каждом этапе)

> Если сессия оборвалась по лимиту: новая сессия читает ЭТОТ файл +
> `data/night_tasks.json` + `docs/V2_ORCHESTRATION_STATE.md` и продолжает,
> НЕ пересобирая решения. LOCKED-решения — в `docs/v2-pack/CLAUDE_START_HERE.md`.

## Главная цель

BOSSMAN V2 — проверяемый multi-agent AI control plane по
`prompts` из пака + мастер-промпту 15 функций. Реальные функции, не UI-мокапы.
Definition of Done: §43 мастер-промпта + §11 CLAUDE_START_HERE.

## Ветка и состояние

- Рабочая/push-ветка: `claude/bossman-control-v03-43igbk` (пуш выполняется после
  каждого этапа). Интеграционная метка: локальная ветка `feature/bossman-command-center-v2`
  (переставить на итог перед финалом; пуш её — в самом конце).
- Последний чекпоинт: пак внедрён (bcc/v2, tests/v2, .agents/skills, docs/v2-pack),
  **48 pytest зелёных** (`cd command-center && python -m pytest -q`).
- V2-core уже в коде: таблицы контрактов, bcc/features/ загрузчик, хуки engine
  (pick_model/before_run/on_step/gate_completion/on_failure/after_run),
  bcc/permissions.py, ui/pages/index.js, api.raw. Тесты: tests/test_v2_core.py.
- 15 worktree: /home/user/bossman-wt/NN-* (ветки agent/NN-*) на базе f11a6c2;
  частичная работа после обрыва минимальна (01,02,05,10 — по 1-2 файла).

## Что сделано

1. MVP Command Center (48 тестов, DoD 17/17) + discovery локальных моделей.
2. V2: аудит, контракты, матрица, core с хуками, бриф агентов, 15 worktree.
3. Пак пользователя внедрён и зелёный; LOCKED-решения приняты
   (OpenRouter first-class; browser Playwright DOM-first; terminal 3 режима без
   глобального «весь компьютер»; MCP Hub с AUTO/ASK/DENY; skills в .agents/skills;
   OpenCode = execution engine под BOSSMAN).

## Что осталось (порядок — data/night_tasks.json)

core-1 Worker Pool (params concurrency, сейчас worker последовательный) + Hard
Cancel (stop должен рвать активный HTTP-inference) — делает ЛИД в engine.
core-2 Вайринг Services: browser/terminal/skills менеджеры из bcc/v2 (optional
imports, Playwright/MCP не фатальны) + мосты bcc/v2/tables → bcc/db.
agents-N перезапуск 15 Fable-агентов волнами по 5 с обновлёнными зонами
(02+04: OpenRouter каталог/пробы; 07: worktree+terminal+opencode; 09: готовый
browser_control; 10: skills+MCP Hub) — промпты см. V2_ORCHESTRATION_STATE.
int-1..N интеграция веток по порядку из CLAUDE_START_HERE §4, проверка лидом
каждой функции (не верить отчётам), proofs.
ui-1 UI/UX Lead по ux-references. qa-1 browser QA + persistence/reboot + failure
injection + 3 кросс-сценария. fin-1 scorecard + V2_IMPLEMENTATION_REPORT +
финальный push (+ ветка feature/bossman-command-center-v2).

## Тесты

Проходят: 48/48 (`cd command-center && python -m pytest -q`).
Пак-тесты требуют asyncio_mode=auto — в repo pyproject уже есть.

## Точные следующие команды

```bash
cd /home/user/AiMaxBossman/command-center && python -m pytest -q   # база зелёная?
cat /home/user/AiMaxBossman/data/night_tasks.json                  # взять первую pending
```

## Не делать без владельца

production deploy, финансовые действия, удаление данных, отправка сообщений
клиентам, force push, публикация dashboard в интернет.
