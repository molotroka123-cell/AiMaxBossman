# EVENING LIVE ACCEPTANCE — REAL E2E (Windows + Ollama)

Выполняется на реальной машине (Windows GUI + локальный Ollama) в день приезда.
Здесь — точная последовательность и критерии PASS. Никакого fake-green: если шаг
не выполнен на живом сервисе — метка `NOT_TESTED` / `SKIP_HOST` /
`SKIP_EXTERNAL_SERVICE` / `SKIP_EXTERNAL_CREDENTIAL`.

## Предусловия
- Ollama запущен локально, модель загружена.
- `cloud_policy=never` (локальная политика запрещает облако).
- Bossman-core и Command Center подняты; токен устройства Stage 6 выдан.
- Секреты только в env/Vault; в логах — маски (`…last4`), не сырьё.

## A. Локальный ИИ → действие на ПК (главный сценарий)
Поток: `Ollama → Stage 3 Gateway → Planner → Stage 13 Computer Operator →
Windows → Notepad → fresh observation → audit`.
1. Команда: **«Открой Блокнот»**.
   - PASS: Notepad реально открылся; наблюдение — свежее (скрин/окно), не выдуманное.
2. Опционально: **«Напечатай BOSSMAN-POLISH-LIVE»**.
   - PASS: текст реально в Notepad.
3. Инварианты во время A:
   - `CLOUD_CALLS == 0` (счётчик Gateway); ни одного облачного вызова.
   - Небезопасный запуск приложения (не из allowlist) → **DENY** (не молча).
   - Каждый шаг — в аудите с correlation id.

## B. Надёжность / восстановление
4. Рестарт FactStore/Bossman в середине задачи → состояние восстановлено
   (durable), задача продолжается, дубля побочного эффекта нет.
5. Рестарт Command Center → coding-сессии из `sessions.json` видны; активная
   не потеряна; merge не запускается повторно.
6. Прерывание/повтор события approvals → anti-replay (`args_hash`) не даёт
   второго побочного эффекта.

## C. Контекст (deterministic long-session)
7. Большая история → компакция → чекпоинт → рестарт → resume.
   - PASS после восстановления: агент знает цель, инварианты, решения, задачу,
     файлы, состояние тестов, провалы, блокеры, следующий точный шаг. Измеримо.

## D. Плагины (live, с кредами)
8. По `PLUGIN_POLISH_REPORT.md`: github(repo_read), ollama.chat, telegram.status,
   mcp.tool_list, browser.open — реальные вызовы.
   - Мутации/отправки (issue_create, gmail.send, telegram.send, drive.write,
     n8n.workflow_run) → **ASK**, подтверждение оператора, ровно один эффект.
   - `sql.read` с настоящим read-only DSN → реальные строки; `sql.write` → DENY.

## E. Провайдеры / стоимость
9. Cloud LLM (если разрешат облако осознанно): `Agent → Gateway → Cost Governor →
   Provider`. Cost Governor реально считает/ограничивает. Провал провайдера →
   честный degrade, не fake-success.

## F. Браузер / Computer Operator
10. Реальная навигация (allowlist), approvals на ввод/submit, свежее наблюдение.

## G. Хаос
11. Provider down / Ollama down / browser down / Pythia down / plugin cred missing
    → честный degraded-статус в UI (`OLLAMA OFFLINE`, `PLUGIN CREDENTIAL MISSING`,
    `PYTHIA OFFLINE`, `BROWSER DOWN`, `PROVIDER FAILED`), не fake-green.

## H. Безопасность (red team на живом хосте)
12. SSRF приватный IP/hostname/redirect → блок; path/symlink escape → блок;
    secret leakage → нет; SQL write → DENY; unknown MCP capability → DENY;
    unsafe n8n URL → блок; approval replay → нет второго эффекта; arbitrary shell
    → отсутствует (argv-only); unsafe APP_LAUNCH → DENY.

## I. Полная регрессия на хосте
13. bossman-core + command-center + плагины + LSP + memory + approvals + Gateway +
    Cost Governor + Stage13 + Pythia + browser + MCP + security + secret scan +
    compileall + JS syntax. Скипы — только с честными метками.

## J. Бенчмарк (если OpenCode доступен)
14. 10 coding-задач, одна машина/модель/репо/задача; метрики success/tests-green/
    interventions/time/tokens/cost/retries/bad-edits/security-violations/resume.
    Агрегатор — `bcc/eval_scorecard.py`. Если OpenCode не поднимается → `NOT_TESTED`.

## Критерий приёмки вечера
Все A-инварианты выполнены (Notepad открыт, `CLOUD_CALLS=0`, unsafe→DENY);
B/C восстановление доказано; D/E/F/G честны; H без открытых P0/P1; I зелёная.
Только после этого — **FREEZE** и переход к отдельным приложениям поверх Bossman.
