# BOSSMAN — PRE-CYBERSEC FREEZE (baseline for CyberSec AI V1)

База для следующей эпохи. Только проверяемые факты, без маркетинга.

## Архитектура и авторитеты (канон, не дублируется)
Цикл: `intent → typed action → policy/scopes → approval → executor → fresh
observation → verification → audit`. Запрещено `LLM → произвольный shell`.
- **Gateway** (Stage 3) + **Cost Governor** — единственный путь к моделям.
- **Policy/Approval/Scopes** — core `perimeter`/`approvals`; CC `tools.decide_effect` + `permissions`.
- **Secret Store** — CC Vault (Fernet) + env; только маски.
- **EventBus/Audit** — `events`/`obs` (секреты вычищаются).
- **Computer Operator** (Stage 13) — единственный desktop-исполнитель (allowlist).
- **ПАМЯТЬ — ОДНА АВТОРИТЕТНОСТЬ** (закрыто в этом проходе):
  `db/schema.sql` (единственный DDL) → `bossman.db` пул (jsonb-кодек, авто-схема)
  → typed views: `WorkingMemory`, `decision_memory`, `failure_memory`.
  `context_engine` — retrieval/RAG-индекс, НЕ конкурирующий durable-store.

## REAL POSTGRESQL GATE — ДОКАЗАН
Живой PostgreSQL **16.13** (локальный кластер, порт 5433, pgvector) — не мок.
С ЧИСТОЙ БД: схема применяется (`ON_ERROR_STOP=1`, exit 0) → 14 таблиц, 27 индексов.

| Проверка | Результат |
|---|---|
| schema/migrations на чистой БД | PASS |
| working memory create/update | PASS |
| optimistic concurrency (конфликт + отсутствие записи при конфликте) | PASS |
| checkpoint / restore / append-only версии | PASS |
| decision create / query / supersede (история сохранена) | PASS |
| failure record / query / resolve | PASS |
| JSONB хранится настоящим JSON (`@>` containment работает) | PASS |
| process restart → state restore | PASS |
| clean shutdown | PASS |
**GATE: PASS (24/24)**; как постоянный тест — `tests/test_pg_memory_gate.py`
(5 тестов; без `BOSSMAN_TEST_PG_DSN` — честный SKIP_HOST).

## Скорость (измерено, не оценка)
Против ЖИВОГО Postgres и на чистых модулях:

| Метрика | p50 | p95 |
|---|---|---|
| MEMORY_LOOKUP (реальный PG round-trip) | 0.338 ms | 0.467 ms |
| DECISION_LOOKUP (реальный PG) | 0.376 ms | 0.759 ms |
| CONTEXT_OPTIMIZER (Guardian, 200 items) | 0.185 ms | 0.263 ms |
| MODEL_ROUTER (M0–M7, кэш-scorecards) | 0.016 ms | 0.027 ms |
| SKILL_RELIABILITY_LCB | 0.187 ms | 0.256 ms |

**FAST_PATH суммарно ≈ 0.5–1.5 ms p95** — на порядки меньше инференса модели
(сотни ms…секунды). Закон «модули не замедляют обычные решения» выполнен с
доказательством. Роутер детерминирован и **не зовёт LLM, чтобы выбрать LLM**
(проверено: `cloud_allowed=False` → облачные M5–M7 отклонены `cloud disabled`).

## V3 7-Pack (feature-gated, adapter-only)
`bossman-core/bossman_v3/`, всё ВЫКЛ по умолчанию (`BOSSMAN_V3_ENABLED` + пофичевый флаг).
Пакет не создаёт второй Gateway/Policy/Approval/Registry/Memory/Executor — только
Protocols + тонкие адаптеры. Guardian / Computer Agent / Visual State / Self-Healing /
Skill Factory+Beta-LCB / Recovery Kernel / Self-Improvement Lab.

## 8 mini-modules — статус
| Mini | Статус |
|---|---|
| 01 Working Memory | **PROVEN on real PG** (typed view, версии, concurrency) |
| 02 Decision Memory | **PROVEN on real PG** (supersede + история) |
| 03 Failure Memory | **PROVEN on real PG** (record/query/resolve, JSONB queryable) |
| 05 Context Optimizer | context_engine + context_os + V3 Guardian; p95 0.263 ms |
| 22 Evidence Confidence | `model_intelligence.Confidence` + verifier (не авторизует) |
| 29 Skill Reliability | Beta-LCB (было raw success_rate); off-hot-path |
| 30 Skill Factory | verified-trace stage-gate (EXPERIMENTAL→SHADOW→PRODUCTION), OFF |
| 32 Model Intelligence | `model_router` + scorecards; p95 0.027 ms, без LLM |

## Безопасность
- Self-Improvement Lab — **PROPOSAL-ONLY**, доказано тестами: нет методов
  merge/push/deploy/promote/grant/set_policy; нет subprocess/сети/записи;
  security-регрессия и падение verified-success блокируют promotable.
- V3 Computer Agent отвергает raw shell (`shell/exec/cmd/powershell/shell.*`).
- Guardian не выбрасывает P0/P1/protected даже при переполнении бюджета; обе
  стороны конфликта сохраняются.
- 285 security/perimeter/approval/scope тестов зелёные.

## Тестовая база (воспроизведено в этом проходе)
- bossman-core (с живым PG): **975 passed, 5 skipped, 0 failed**
- bossman-core (без PG): 964 passed, 10 skipped, 0 failed (гейт честно SKIP_HOST)
- command-center: **610 passed, 2 skipped, 0 failed**

## Открытые пункты
- Live-провайдеры (Ollama/облако), Windows Stage13 foreground, браузер — требуют
  реального хоста → SKIP_HOST.
- Полный A/B бенчмарк (AAF, IntelligenceRetention на реальных задачах) — не
  измерялся: нужны реальные модели и задачи. Числа НЕ выдумываю.

## Поверхность атаки (для CyberSec V1)
Telegram webhook (secret+allowlist), Remote Client device-tokens (scopes),
HTTP plugin GET (SSRF + pinned DNS + streaming max_bytes), SQL read (mode=ro +
modifying-CTE gate), браузер (allowlist), MCP (unknown→deny), Postgres (единственный
durable store). Секреты — Vault/маски. Внешние тексты — untrusted.
