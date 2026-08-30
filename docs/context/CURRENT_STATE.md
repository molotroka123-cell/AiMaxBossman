# CURRENT STATE — канонический источник (single source of truth)

> Это канонический документ текущего состояния. Все исторические отчёты — в
> `docs/archive/`. README ссылается сюда.

- Ветка: `claude/bossman-control-v03-43igbk`. NO force push.
- Эпоха: **POLISH / FINAL** — доводка уже построенного Bossman до законченного
  состояния перед вечерним REAL E2E (Windows + Ollama). Без новой архитектуры.

## Две связки (два приложения над общими инвариантами)
1. **bossman-core** (`bossman-core/bossman/`) — агентная ОС: канонический цикл
   `intent → typed action → scopes/policy → approval → executor → fresh result → audit`.
   Gateway (Stage 3), Cost Governor, Resource Brain, Search, Remote Client,
   Video Factory, Sandbox core, Dev Factory, AI Lab, Stage 13 Computer Operator,
   Pythia (только intelligence, НЕ authority).
2. **command-center** (`command-center/bcc/`) — FastAPI dashboard/control plane:
   единый `REGISTRY` (ToolSpec) + `decide_effect` (AUTO/ASK/DENY) + approvals +
   Vault (Fernet) + EventBus. Фичи авто-подхватываются (`bcc/features/`).

## Инварианты (никогда не нарушать)
Никакого второго Gateway / Tool Registry / Policy / Approval / Secret Store /
Event Bus / Browser / Telegram / MCP / Memory / Cost / Session engine.
Запрещено `LLM → произвольная команда → shell`. Cloud LLM:
`Agent → Stage3 Gateway → Cost Governor → Provider`. Local LLM:
`Agent → Stage3 Gateway → Ollama` при `cloud_policy=never` (0 облачных вызовов).
Секреты — только по ссылке/маске, никогда в логи/коммиты.

## Command Center — подсистемы и фичи
- `bcc/tools.py` — `ToolSpec`/`ToolRegistry`/`decide_effect`/`args_hash` (anti-replay),
  deny-by-default (`resolve(allowed)` при пустом → ничего).
- `bcc/permissions.py` — фиксированный словарь прав + DANGEROUS + needs_approval.
- `bcc/secrets.py` — Vault (Fernet-at-rest, `mask()` → «…last4»).
- `bcc/features/plugins.py` — 13 коннекторов-адаптеров поверх REGISTRY (см. ниже).
- `bcc/plugin_security.py` — SSRF (resolve+redirect, pinned IP), path+symlink confine, redaction.
- `bcc/lsp_bridge.py` + `bcc/features/code_intel.py` — LSP JSON-RPC мост (argv-only,
  capability negotiation, Location/LocationLink normalization, bounded, graceful).
- `bcc/coding_session.py` — `CodingWorktreeManager` (git-worktree изоляция сессии,
  base→SHA, durable JSON, conflict-aware merge, orphan cleanup) + `diff_aware_review`.
- `bcc/eval_scorecard.py` — агрегатор бенчмарка (summarize/compare) для A/B.
- `bcc/forks.py` — lineage чекпоинтов (остаётся авторитетом; coding sessions его расширяют).

## Плагины (truth matrix — детали в PLUGIN_POLISH_REPORT.md)
13 capability `plugin:<id>.<cap>`, каждая — типизированный контракт + `default_effect`
(auto/ask/deny) + credential-gate. Без креда → честный `SKIP_EXTERNAL_CREDENTIAL`
(не падение). SQL read — **реальное исполнение** read-only (`sqlite mode=ro`,
single-statement guard), не только валидация.

## Тесты
- Полные наборы гоняются каждой волной POLISH. Точные счётчики и PASS/FAIL/SKIP —
  в `POLISH_FINAL_REPORT.md` (обновляется перед push).
- Метки скипов честные: `SKIP_HOST` / `SKIP_EXTERNAL_SERVICE` /
  `SKIP_EXTERNAL_CREDENTIAL` / `NOT_TESTED_LIVE`. Никаких скрытых скипов.

## Что требует реального хоста (вечерний REAL E2E)
Ollama→Gateway→Planner→Stage13→Windows→Notepad→fresh observation; live-плагины с
кредами; браузер; A/B Bossman vs OpenCode. Матрица — `EVENING_LIVE_ACCEPTANCE.md`.

## Ключевые env
- `BOSSMAN_SANDBOX_ENABLED` (дефолт OFF), `BOSSMAN_GATEWAY_URL`,
  `BOSSMAN_GATEWAY_CORE_KEY`, `TELEGRAM_WEBHOOK_SECRET` (иначе вебхук 403),
  `BOSSMAN_TEST_CHROMIUM`, `BOSSMAN_UNSAFE_LOCAL_EXEC` (только dev).
- Command Center: `SQL_PLUGIN_DSN` (только sqlite read-only DSN в адаптере),
  `LSP_SERVERS`, плюс per-plugin креды (без них — SKIP). Доступ к API — токеном.
