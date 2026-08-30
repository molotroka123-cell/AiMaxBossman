# PLUGINS LIVE ACCEPTANCE

Дата: 2026-08-30 · HEAD: `3d04e66` (интеграция поверх)

Статусы: PASS · FAIL · SKIP_HOST · SKIP_EXTERNAL_SERVICE · SKIP_EXTERNAL_CREDENTIAL · NOT_TESTED.
SKIP в PASS НЕ превращается. Внешний LIVE без evidence не заявляется.

## Окружение (определяет SKIP)
- Docker daemon: DOWN → нет живого control-plane под Postgres.
- Ollama: не установлен; платформа Linux (нет Windows GUI).
- Внешние OAuth (GitHub/Gmail/Google): в приёмке не используются (риск побочных эффектов).
- OPENROUTER_API_KEY / TELEGRAM_BOT_TOKEN присутствуют в env, но реальные платные/
  отправляющие вызовы НЕ выполняются осознанно (деньги / сообщения живым людям).

## Матрица

| PLUGIN | CAPABILITY | STATUS | EVIDENCE | SIDE EFFECT | APPROVAL |
|---|---|---|---|---|---|
| http | get | PASS | unit: SSRF-safe fetch, literal+DNS+redirect блок | нет | auto |
| monitor | feed | PASS | тот же safe_get | нет | auto |
| sql | read | PASS | unit: read-only guard (12 write-форм denied) | нет | auto |
| sql | write | PASS (deny) | capability не существует → DENY | нет | — |
| obsidian | read | PASS | unit: path confine + symlink escape denied | нет | auto |
| obsidian | write | PASS (policy) | ask + path confine | под ASK | ask |
| mcp | tool_list | PASS (policy) | reuse mcp_runtime | нет | auto |
| mcp | tool_call | PASS (policy) | ask; unknown tool → DENY | под ASK | ask |
| ollama | chat | SKIP_HOST | нет Ollama; boundary: cloud_policy=never (декларация+тест) | — | auto(local) |
| openrouter | chat | SKIP_EXTERNAL_CREDENTIAL | Cost Governor enforcement 24 теста PASS (bossman-core); платный вызов не делаем | — | ask |
| github | repo_read | SKIP_EXTERNAL_CREDENTIAL | адаптер готов; без OAuth live не гоняем | нет | auto |
| github | issue_create | SKIP_EXTERNAL_CREDENTIAL + policy PASS | reject → 0 side effect (нет креда → SKIP) | нет (доказано) | ask |
| gmail | search | SKIP_EXTERNAL_CREDENTIAL | — | нет | auto |
| gmail | send | SKIP_EXTERNAL_CREDENTIAL + policy PASS | нет креда → SKIP, письмо НЕ отправлено (unit) | нет (доказано) | ask |
| calendar | search/create | SKIP_EXTERNAL_CREDENTIAL | — | нет | auto/ask |
| drive | search/write | SKIP_EXTERNAL_CREDENTIAL | — | нет | auto/ask |
| telegram | status | SKIP_EXTERNAL_CREDENTIAL | — | нет | auto |
| telegram | send | policy PASS; LIVE не выполняется | ask + reuse канала; сообщение живым людям не шлём | нет | ask |
| n8n | workflow_list | SKIP_EXTERNAL_CREDENTIAL | — | нет | auto |
| n8n | workflow_run | SKIP_EXTERNAL_CREDENTIAL + policy PASS | ask; url через SSRF-валидацию | нет | ask |
| browser | open | PASS (reuse) | существующая browser-подсистема | нет | auto |
| browser | form_submit | PASS (policy) | sensitive submit → ASK | под ASK | ask |

## Негативные доказательства (reject → zero side effect)
- `gmail.send` без креда → `SKIP_EXTERNAL_CREDENTIAL`, письмо не отправлено (unit-тест).
- `http.get` на `169.254.169.254` → blocked до сети (unit-тест).
- external без креда → SKIP, побочного эффекта нет.

## Local LLM / Windows gate
LOCAL_LLM: SKIP_HOST · NOTEPAD_LIVE: SKIP_HOST · CLOUD_CALLS: 0 (декларация ollama cloud_policy=never;
Stage13/allowlist unit-тесты в bossman-core зелёные).

## Cost Governor
COST_RESERVE/COMMIT/RELEASE/BUDGET_STOP/UNKNOWN_PRICE_STOP/CLOUD_POLICY_NEVER: **PASS**
(24 теста bossman-core). Реальный платный вызов: SKIP_EXTERNAL_CREDENTIAL.
