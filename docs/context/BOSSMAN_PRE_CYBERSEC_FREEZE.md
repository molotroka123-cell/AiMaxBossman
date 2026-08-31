# BOSSMAN — PRE-CYBERSEC FREEZE (baseline for CyberSec AI V1)

Этот документ — база для следующей эпохи (CyberSec AI V1). Он фиксирует
архитектуру, авторитеты, поверхность атаки, тестовую/ресурсную базу и открытые
пункты. Никакого маркетинга — только проверяемые факты.

## SHAs
- START_REMOTE_SHA (база сверки): `ac044f6`
- FINAL_LOCAL_SHA / FINAL_REMOTE_SHA: см. `V3_PRE_CYBERSEC_FINAL_REPORT` (обновляется при push)

## Архитектура и авторитеты (канон, не дублируется)
Канонический цикл: `intent → typed action → policy/scopes → approval → executor →
fresh observation → verification → audit`. Запрещено `LLM → произвольный shell`.
- **Gateway** (Stage 3) + **Cost Governor** — единственный путь к облачным/локальным моделям.
- **Policy/Approval/Scopes** — bossman-core `perimeter`/`approvals`; command-center
  `tools.decide_effect` (AUTO/ASK/DENY) + `permissions`.
- **Secret Store** — command-center Vault (Fernet); bossman-core `settings`/env; только маски.
- **EventBus/Audit** — `events`/`obs` (секреты вычищаются при логировании).
- **Computer Operator** (Stage 13) — единственный desktop-исполнитель (allowlist APP_LAUNCH).
- **Memory authority** — `context_engine` (Stage 2.222) канонична; LLM-V2
  `working/decision/failure_memory` — новая asyncpg-прослойка (см. «Открытые пункты»).
- **Profiles** (мой предыдущий вклад, если влит отдельной веткой) — доступ по
  device-identity + тумблеры; не второй auth.

## V3 7-Pack — интеграция (feature-gated, adapter-only)
Вендорнут как `bossman-core/bossman_v3/` (пакет матчит `bossman*` в pyproject).
Все фичи ВЫКЛ по умолчанию: `BOSSMAN_V3_ENABLED=0` + пофичевые флаги. Пакет
НЕ создаёт второй Gateway/Policy/Approval/Registry/Memory/Executor — только
Protocols (`contracts.py`) и тонкие адаптеры (`adapters/bossman_core.py`).

| Компонент | Статус | Примечание |
|---|---|---|
| Context/Data Guardian | INTEGRATED (off) | анти-context-starvation; P0/P1 защищены даже сверх бюджета (тест) |
| Universal Computer Agent | INTEGRATED (off) | raw-shell отвергается ДО policy (тест); дублирует существующий manager → не на hot-path |
| Visual State Engine | INTEGRATED (off) | structured > vision; shadow-only |
| Self-Healing | INTEGRATED (off) | EV+Beta выбор стратегии; не повторяет опровергнутое |
| Skill Factory + Reliability | INTEGRATED (off) | verified-trace → EXPERIMENTAL→SHADOW→PRODUCTION; Beta-LCB; raw-shell отвергается |
| Recovery Kernel | INTEGRATED (off) | loop/watchdog/budget; FileCheckpointStore — только demo |
| Self-Improvement Lab | INTEGRATED (off) | **proposal-only**: не мержит/деплоит/повышает права |

Тесты пакета: 7 (pack) + 10 (инварианты: флаги OFF+master-gating, shell-reject,
guardian critical-survival, conflict-preservation) — все зелёные.

## 8 intelligence mini-modules — статус (честно)
| Mini | Наличие | Статус |
|---|---|---|
| 01 Working Memory | `working_memory.py` (asyncpg-класс) | код есть; **схема рассинхронизирована (OPEN P0)**; логика покрыта fake-pool тестами |
| 02 Decision Memory | `decision_memory.py` | asyncpg-контракт ПОЧИНЕН; покрытие детерминированное |
| 03 Failure Memory | `failure_memory.py` | asyncpg-контракт ПОЧИНЕН (async-for/status/double-pool); покрыт |
| 05 Context Optimizer | `context_engine/retrieval.py` + `context_os` + V3 Guardian | есть; MMR отсутствует (dedup есть); Guardian добавляет анти-starvation |
| 22 Evidence Confidence | `bcc/v2/model_intelligence.Confidence`, `computer_operator/verifier` | есть; V3 `VerifierPort` — тонкая обёртка |
| 29 Skill Reliability | V3 `skill_factory/reliability` (Beta LCB) | добавлен статистически корректный LCB (был raw success_rate) |
| 30 Skill Factory | `bcc/features/skills` + V3 `skill_factory` | typed-trace stage-gate добавлен пакетом (off) |
| 32 Model Intelligence | `bcc/v2/model_intelligence` + `model_router` | есть и протестирован; V3 второй роутер НЕ добавляли |

## Быстродействие (честные микробенчи, не end-to-end)
Детерминированные микробенчи V3-модулей (без модели/БД, 2000–20000 итераций):
- `ContextDataGuardian.select` (200 items): **p50 0.21ms, p95 0.30ms**.
- `reliability_lcb` (Beta LCB): **p50 ~0.19ms**.
Вывод: интеллект-модули добавляют суб-миллисекундный оверхед — пренебрежимо на фоне
инференса/исполнения инструментов. Полные end-to-end router/memory-lookup латентности
против реальных провайдеров/Postgres — **host-limited, здесь НЕ измерены** (не выдумываем).

## Тестовая база (после правок)
- bossman-core: **962 passed, 1 failed (HOST_SPECIFIC Windows), 4 skipped**.
- command-center: см. FINAL_REPORT (collection-блокер устранён).
- Новое: memory asyncpg-контракт (6), working_memory host-honest (5+1 SKIP_HOST),
  V3 pack+инварианты (17).

## Открытые пункты (P0/P1/DEFERRED)
- **OPEN P0** — `WorkingMemory` (asyncpg) ждёт схему `project_id` + таблицу
  `working_memory_versions`; в `db/schema.sql` их нет, плюс дубль таблицы
  `working_memory` (строки 146/230). НЕ патчил вслепую (нет live PG; риск сломать
  boot). Требует ревью-миграции на реальном Postgres. Модуль сейчас orphaned (не в проде).
- **SKIP_HOST** — REAL POSTGRES GATE, live-провайдеры, полный speed/AAF-бенчмарк,
  Windows Stage13 foreground.
- **HOST_SPECIFIC baseline fail** — `test_stage13_windows_adapter` (нужен Windows).

## Поверхность атаки (текущая, для CyberSec V1)
Внешние входы: Telegram webhook (secret + allowlist), Remote Client device-tokens
(scopes), HTTP plugin GET (SSRF-защита + pinned DNS + max_bytes), SQL read
(mode=ro + modifying-CTE gate), браузер (allowlist), MCP (unknown→deny). Секреты —
Vault/маски. Все внешние тексты (webpage/repo/memory) — untrusted.

## Feature-gates (сводно)
`BOSSMAN_V3_ENABLED`(+пофичевые), `BOSSMAN_LOW_MEMORY`, `BOSSMAN_SANDBOX_ENABLED`,
`BOSSMAN_UNSAFE_LOCAL_EXEC`, `TELEGRAM_WEBHOOK_SECRET`, `SQL_PLUGIN_DSN`,
`BOSSMAN_TEST_PG_DSN` (для real-PG gate).
