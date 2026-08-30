# PLUGINS PACK V1 — INTEGRATION REPORT

Дата: 2026-08-30 · HEAD: `4e72785` · Пакет: `bossman_plugins` (в bundle V1)

## Статус: NOT_INTEGRATED (обоснованное решение) + AUDIT DONE

Собственные тесты пакета: **10 passed** (включая `test_ollama_gateway_boundary`,
`test_sql`, `test_security`, `test_registry`, `test_manager`). Разбор: см.
SKILLS_PLUGINS_FINAL_BUG_CHECK.md.

## Что в пакете
Плагины-адаптеры: github, gmail, calendar, drive, telegram, n8n, obsidian, browser,
monitor, sql (read-only), ollama, openrouter, mcp. Инфраструктура: `PluginRegistry`,
`PluginManager` (ALLOW/ASK/DENY + approval + DEGRADED), `manifests` (capability→scopes→risk),
`security` (redaction + SSRF), `audit`, `retry` (bounded), `types`.

## Ключевые границы (проверены на уровне кода)
- **Ollama** → делегирует в инъектируемый `gateway_adapter.chat(provider="ollama",
  cloud_policy="never")`; своего LLM-клиента НЕ создаёт. Тест boundary — PASS.
- **OpenRouter** → делегирует в `gateway_adapter.chat(provider="openrouter")`; авторитет по
  облаку (Cost Governor) остаётся в адаптере ядра; chat=ASK.
- **MCP** → делегирует в `bridge`; unknown tool → `registry.resolve` бросает → DENY;
  tool.call=ASK. MCP-метаданные не дают авторитета.
- **PluginManager** fail-closed: DENY→PermissionError (audited), ASK→approval-обязателен,
  выключенный плагин mid-flight→RuntimeError, ошибка→DEGRADED.
- **Health-check**: спецификация (`docs/PLUGIN_MANAGER_UI_SPEC.md`) требует non-destructive
  health — не слать письмо/telegram/calendar/drive/n8n/merge/billable LLM. Соблюдать при
  реализации UI-хелсчека в Command Center.

## Почему НЕ вендорим как есть
`PluginRegistry`/`PluginManager`/`security`/`audit` пакета — параллельные аналоги того, что
ядро уже имеет (scopes/approvals/secret-broker/event-bus/MCP-runtime/browser). Внести целиком
= поднять вторые системы (прямой запрет). Правильная интеграция = связать адаптеры пакета с
существующими seam'ами ядра; её нельзя LIVE-проверить здесь (нет живых сервисов/кредов),
а фальшивый PASS запрещён.

## Адаптерный план (правильная интеграция)
1. `gateway_adapter`, передаваемый в ollama/openrouter — это СУЩЕСТВУЮЩИЙ клиент Stage 3
   Gateway ядра (`bossman.llm`/gateway client). Никаких прямых httpx к провайдеру.
2. `bridge` для MCP — существующий `command-center/bcc/v2/mcp_runtime`, не второй MCP-стек.
3. browser-plugin — мост к существующей browser/Playwright-подсистеме; параллельного браузера
   не создавать; sensitive submit/download → ASK.
4. telegram-plugin — использовать существующий notifications/telegram-транспорт ядра; второго
   Telegram-сабсистема не поднимать; message.send=ASK.
5. Креды — reference из secret-broker ядра (`github.token`, `google.oauth`,
   `telegram.bot_token`, `n8n.api_key`, `openrouter.api_key`, `database.dsn`); UI показывает
   configured/missing/expired, никогда сам секрет.
6. Сетевые плагины (monitor/n8n) — усилить SSRF до уровня skills-web (F1/F2 bug-check).
7. Plugin Manager UI — в Command Center только если чисто ложится на существующую архитектуру;
   health-check строго non-destructive.

## Верификация, которую потребует интеграция (сейчас недоступна)
- Ollama live (`cloud_policy=never`, доказать CLOUD_CALLS=0) → SKIP_HOST (нет Ollama).
- OpenRouter через Gateway+Cost Governor, budget-deny до сети → enforcement PASS на моках;
  реальный платный вызов → SKIP_EXTERNAL_CREDENTIAL (деньги не тратим).
- github/gmail/calendar/drive/telegram/n8n live-мутации → SKIP_EXTERNAL_CREDENTIAL; негативный
  инвариант (REJECT → ноль побочных эффектов) обеспечен fail-closed manager'ом.

## Вывод
Границы (Gateway/Cost Governor/MCP-deny) в пакете спроектированы верно; критических дефектов
нет. Готов к адаптерной интеграции на способном хосте; вендоринг как есть — нарушение правил и
недоказуем здесь.
