# CURRENT STATE — канонический источник (single source of truth)

> **PRE-HARDWARE FREEZE** (`docs/context/PRE_HARDWARE_FREEZE.md`)
> Closure audit: connectivity matrix built (`FINAL_CONNECTIVITY_MATRIX.md`),
> 1 P0 found+fixed, 4 P1 found+fixed, 2 P1 documented+deferred with reason.
> Предыдущие точки: CYBERSEC V1 (`docs/security/CYBERSEC_AI_V1.md`), затем
> BOSSMAN V1 FROZEN, PRE-CYBERSEC (`BOSSMAN_PRE_CYBERSEC_FREEZE.md`, PARTIAL).

- Ветка: `claude/bossman-control-v03-43igbk`. NO force push.
- Эпоха: **PRE-HARDWARE FREEZE** — code freeze, не final production acceptance.
- Вердикт: **BOSSMAN PRE-HARDWARE FREEZE PASS** (это code freeze; A/B
  бенчмарк, live-провайдеры и RED vs BLUE стресс-тест остаются на реальное
  железо — см. `docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md`).
- Working/Decision/Failure Memory и Prompt Injection Firewall теперь
  production-wired (`runner.py`) — были доказаны на живом PG/написаны, но не
  вызывались; это закрыто в этом проходе, см. connectivity matrix.
- Стресс-тест RED vs BLUE: **НЕ ЗАПУСКАЛСЯ**, подготовлен и заморожен
  (`docs/security/FUTURE_RED_BLUE_STRESS_TEST.md`).
- Next Phase: реальное железо — `REAL_HARDWARE_FINAL_ACCEPTANCE.md` целиком.

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

## CyberSec AI V1 (`bossman-core/bossman/cybersec/`)
Слой ПОВЕРХ существующих авторитетов, не вторая система: ни один модуль не выдаёт
разрешений, только ужесточает решение / детектит / требует approval. Карта
«модуль → усиленный авторитет» — `bossman.cybersec.LAYERED_OVER`.
10 модулей: injection firewall, behavior IDS, secret guardian, repo scanner,
blast radius, supply chain, cyber recovery, security memory (вид над каноничной
`failure_memory`), security benchmark, red-team lab (только типизированный
`AttackIntent`, каталог 14 сценариев L0–L5).
Гейты: `BOSSMAN_CYBERSEC_V1_ENABLED`, `BOSSMAN_CYBER_LAB_ENABLED`,
`BOSSMAN_CYBER_LAB_ACK` + `SandboxFacts` (одноразовая песочница, без продакшн-
секретов и продакшн-сети). Умолчания `SandboxFacts` небезопасны → fail-closed.
Обучение: PROPOSED → BENCHMARKED → SHADOW → VERIFIED → PROMOTED, причём
PROMOTED — только по явному решению владельца. Детали:
`docs/security/CYBERSEC_AI_V1.md`, `CYBERSEC_V1_ZIP_DELTA.md`.

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
- CyberSec: `BOSSMAN_CYBERSEC_V1_ENABLED` (дефолт OFF), `BOSSMAN_CYBER_LAB_ENABLED`
  (дефолт OFF), `BOSSMAN_CYBER_LAB_ACK` (иначе лаборатория заморожена).
- Command Center: `SQL_PLUGIN_DSN` (только sqlite read-only DSN в адаптере),
  `LSP_SERVERS`, плюс per-plugin креды (без них — SKIP). Доступ к API — токеном.
