# AiMaxBossman

Домашний ИИ-сервер Bossman: приватная агентная ОС и панель управления к ней.
Модель не выполняет произвольные команды — она формирует **типизированное
намерение**, которое проходит политику, права и (если нужно) подтверждение
владельца, и только потом исполняется. Всё, что происходит, попадает в аудит.
Облако — только осознанно, через единственный шлюз, под счётчиком стоимости.

Ветка разработки: `claude/bossman-control-v03-43igbk` (без force push).
Числа в этом файле измерены на коммите **`c20ed2c`**, 2026-08-31 (см. «Статус»).

## Что это

Два приложения над общим набором инвариантов.

| Приложение | Путь | Роль |
|---|---|---|
| **bossman-core** | `bossman-core/bossman/` | агентная ОС: канонический цикл действия, Gateway (Stage 3), Cost Governor, Resource Brain, Search, Remote Client, Video Factory, Sandbox, Dev Factory, AI Lab, Computer Operator (Stage 13), Pythia (только intelligence, не авторитет) |
| **command-center** | `command-center/bcc/` | FastAPI dashboard / control plane: единый `REGISTRY` (`ToolSpec`) + `decide_effect` (AUTO/ASK/DENY) + approvals + Vault (Fernet) + EventBus; фичи авто-подхватываются из `bcc/features/` |

**Канонический цикл действия**, один и тот же для обоих приложений
(`fresh observation` — обязательный шаг: результат берётся из свежего наблюдения
за миром, а не из того, что модель предположила):

```
intent → typed action → scopes/policy → approval → executor
       → fresh observation → verification → audit
```

**Каноничная память — одна авторитетность** (унифицировано в этом проходе):

```
db/schema.sql (единственный DDL)  →  bossman.db pool (jsonb-кодек, авто-применение схемы)
        ↓                    ↓                     ↓
  WorkingMemory        decision_memory        failure_memory      ← typed views
```

`context_engine` — retrieval/RAG-индекс (documents / chunks / embeddings), а **не**
конкурирующий durable-store. Встроенный DDL из модулей памяти удалён:
`init_*_table()` проверяет каноничную таблицу через `to_regclass` и честно падает,
если её нет, вместо тихого создания второй расходящейся схемы.

## Инварианты

- **Нет второго** Gateway / Policy / Approval / Tool Registry / Secret Store /
  Event Bus / Memory engine. Новый код подключается адаптером к существующему
  авторитету, а не заводит свой.
- **Запрещено `LLM → произвольная команда → shell`.** Везде argv-only.
  Cloud: `Agent → Stage 3 Gateway → Cost Governor → Provider`.
  Local: `Agent → Stage 3 Gateway → Ollama` при `cloud_policy=never` (0 облачных вызовов).
- **Секреты — только по ссылке/маске.** Vault (Fernet at-rest), `mask()` → «…last4»;
  никогда в логи, аудит и коммиты.
- **Deny-by-default** в разрешениях; `resolve(allowed)` при пустом наборе не даёт ничего.
- **Stage 13 Computer Operator** — единственный исполнитель действий на десктопе (allowlist).

## Что уже работает

| Область | Состояние | Доказательство |
|---|---|---|
| Каноничный цикл действия в обоих приложениях | работает | наборы тестов core и command-center |
| `db/schema.sql` на **чистой** БД | применяется | `psql -v ON_ERROR_STOP=1` → exit 0, 15 таблиц, 31 индекс |
| WorkingMemory (create/update, оптимистическая конкурентность, checkpoint/restore, версии), decision_memory (create/query/supersede с историей), failure_memory (record/query/resolve, JSONB реально queryable через `@>`), restart → restore durable state | PASS на живом Postgres | `bossman-core/tests/test_pg_memory_gate.py` |
| Периметр: SSRF (pinned DNS, no auto-redirect, `max_bytes`), SQL read-only (`mode=ro` + modifying-CTE gate), path/symlink confinement, LSP workspace confinement, Telegram webhook (constant-time secret + allowlist), Remote Client device-tokens + scopes | зелёное | 141 тест в core и 90 в command-center по срезу security/perimeter/approval/scope/permission |
| Плагины command-center: 13 капабилити `plugin:<id>.<cap>`, каждая с типизированным контрактом и `default_effect` | работает | без креда — честный `SKIP_EXTERNAL_CREDENTIAL`, не падение |
| V3 Self-Improvement Lab | **proposal-only** | доказано тестами: нет merge/push/deploy/promote/grant, нет subprocess/сети/записи |

**REAL PostgreSQL gate больше не SKIP.** Прогнан на живом PostgreSQL 16.13
(локальный кластер, порт 5433) с чистой базой и проходит. Без `BOSSMAN_TEST_PG_DSN`
тесты честно помечаются `SKIP_HOST` — fake-green нет.

**V3 7-Pack** вендорится в `bossman-core/bossman_v3/` (Guardian, Computer Agent,
Visual State, Self-Healing, Skill Factory + Beta-LCB, Recovery Kernel,
Self-Improvement Lab). **Выключен по умолчанию**: нужен `BOSSMAN_V3_ENABLED=1`
**плюс** пофичевый флаг (`BOSSMAN_V3_COMPUTER_AGENT`, `BOSSMAN_V3_RECOVERY_KERNEL`,
…). Слой adapter-only — Protocols и тонкие адаптеры, второго
Gateway/Policy/Registry/Memory он не создаёт.

## Статус

- **Фаза:** PRE-HARDWARE FREEZE — code freeze после closure-аудита, не
  финальная production-приёмка (`docs/context/PRE_HARDWARE_FREEZE.md`).
- **Вердикт:** `BOSSMAN PRE-HARDWARE FREEZE PASS` — 0 открытых P0/P1. A/B
  бенчмарк (AAF, IntelligenceRetention) и live-провайдеры остаются на
  реальное железо, см. `docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md`.
- **Closure-аудит:** connectivity matrix (`docs/context/FINAL_CONNECTIVITY_MATRIX.md`)
  нашла 1 P0 (обход approval в терминале command-center) и 4 P1 (Prompt
  Injection Firewall и каноничная память были доказаны на живом PG, но не
  вызывались ни из одного production-пути) — все закрыты. 2 P1
  (Context OS, V3 Data Guardian/Skill Factory) сознательно оставлены
  UNWIRED с объяснением, а не тихим пробелом.
- **CyberSec AI V1:** 10 модулей в `bossman-core/bossman/cybersec/`, всё **OFF**
  по умолчанию; тренировочная лаборатория за тройным гейтом + фактами одноразовой
  песочницы. Prompt Injection Firewall теперь реально подключён к границе
  ingest внешних данных в `runner.py`. Реальный стресс-тест RED (Fable через
  OpenCode) vs BLUE (Bossman) **не запускался** — см.
  `docs/security/FUTURE_RED_BLUE_STRESS_TEST.md`.

Тесты, измеренные на коммите `c20ed2c` (2026-08-31):

| Набор | Результат |
|---|---|
| `bossman-core` без Postgres | **1076 passed, 14 skipped, 0 failed** (~21 с) |
| `bossman-core` с живым PostgreSQL 16.13 | **1085 passed, 5 skipped, 0 failed** (~22 с) |
| `bossman-core/tests/test_pg_memory_gate.py` (живой PG) | **5 passed** |
| `bossman-core` CyberSec V1 (unit + интеграция + структурные) | **79 passed** |
| `command-center` | **611 passed, 2 skipped, 0 failed** (~202 с) |

Разница 1076 → 1085 — гейт памяти (5) плюс 4 новых теста wiring-доказательства
на живом PG. Число всегда называется вместе с коммитом; в рабочем дереве
может идти параллельная работа, поэтому пересчитывайте счётчики сами, а не
копируйте эти.

## Быстрый старт

```bash
# тесты (без Postgres гейт памяти честно SKIP_HOST)
cd bossman-core   && python -m pytest -q --timeout=120
cd command-center && python -m pytest -q --timeout=120
python tools/ci_secret_scan.py

# поднять реальный PostgreSQL, чтобы гейт памяти не скипался
initdb -D /tmp/pgdata -A trust -U postgres
pg_ctl -D /tmp/pgdata -o '-p 5433' -l /tmp/pg.log start
createuser -h 127.0.0.1 -p 5433 -U postgres -s bossman
createdb   -h 127.0.0.1 -p 5433 -U postgres -O bossman bossman
export BOSSMAN_TEST_PG_DSN=postgresql://bossman:bossman@127.0.0.1:5433/bossman
cd bossman-core && python -m pytest -q --timeout=120 tests/test_pg_memory_gate.py
```

Запуск command-center: `cd command-center && pip install -e . && bcc` →
http://127.0.0.1:8800 (токен печатается в консоли, в браузере меняется на
HttpOnly-сессию). Точки входа core: `bossman` (CLI) и `bossman-gateway`.
Ключевые переменные окружения:

| Переменная | Значение по умолчанию | Смысл |
|---|---|---|
| `BOSSMAN_DATABASE_URL` | `postgresql://bossman:bossman@postgres:5432/bossman` | каноничный durable-store; без него core честно падает на старте |
| `BOSSMAN_TEST_PG_DSN` | не задана | DSN для REAL-PG гейта; без неё гейт — `SKIP_HOST` |
| `BOSSMAN_V3_ENABLED` | `0` (OFF) | мастер-флаг V3 7-Pack; нужен ещё и пофичевый флаг |
| `BOSSMAN_LOW_MEMORY` | `0` | режим экономии памяти (в т.ч. `low_memory_budget` Guardian) |
| `BOSSMAN_SANDBOX_ENABLED` | `0` (OFF) | Stage 8 sandbox; выключен — менеджер ничего не исполняет |
| `BOSSMAN_CYBERSEC_V1_ENABLED` | `0` (OFF) | защитный слой CyberSec (firewall / IDS / guardian) |
| `BOSSMAN_CYBER_LAB_ENABLED` + `BOSSMAN_CYBER_LAB_ACK` | `0` / пусто | тренировочная лаборатория; нужны ещё и факты одноразовой песочницы |
| `TELEGRAM_WEBHOOK_SECRET` | пусто | без него вебхук отвечает 403 (constant-time сверка + allowlist) |
| `BOSSMAN_GATEWAY_URL`, `BOSSMAN_GATEWAY_CORE_KEY` | — | адрес и ключ Stage 3 Gateway |
| `SQL_PLUGIN_DSN`, `LSP_SERVERS` | — | command-center: SQL-плагин (sqlite read-only DSN) и LSP-мост |

## Структура репозитория

| Путь | Что внутри |
|---|---|
| `bossman-core/bossman/` | агентная ОС: gateway, perimeter, approvals, memory (`working_memory.py`, `decision_memory.py`, `failure_memory.py`), `context_engine`, `computer_operator`, `sandbox`, `dev_factory`, `ai_lab`, `cost_control` |
| `bossman-core/bossman_v3/` | V3 7-Pack, feature-gated **OFF**, adapter-only (`feature_flags.py`, `adapters/`, `contracts.py`) |
| `bossman-core/bossman/cybersec/` | CyberSec AI V1: 10 защитных модулей + замороженный red-vs-blue движок, **OFF** по умолчанию |
| `bossman-core/db/schema.sql` | **единственный DDL** каноничной памяти |
| `bossman-core/tests/` | ~1080 тестов, включая `test_pg_memory_gate.py`, `test_v3_authority_boundaries.py`, `test_cybersec_v1.py`, `test_cybersec_integration.py` |
| `command-center/bcc/` | control plane: `tools.py` (REGISTRY/`decide_effect`), `permissions.py`, `secrets.py` (Vault), `plugin_security.py`, `features/`, `lsp_bridge.py`, `coding_session.py`, `eval_scorecard.py` |
| `bossman-infra/` | инфраструктура: LiteLLM, llama-swap, Postgres + pgvector, Redis, Open WebUI, Uptime Kuma |
| `apps/` | 8 прикладных приложений поверх ядра (манифест + ТЗ; часть уже с `src/`) |
| `docs/context/`, `docs/security/` | канонический статус, отчёты, лог решений/провалов, периметр и handoff CyberSec |
| `tools/` | `ci_secret_scan.py` и утилиты CI |

## Что дальше / цели

Всё, что можно закрыть без реального железа, закрыто в этом проходе
(`docs/context/PRE_HARDWARE_FREEZE.md`). Дальше — только
`docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md` целиком (A–L):

1. **Real-host acceptance** — то, что сейчас честно `SKIP_HOST`: живые провайдеры
   (Ollama и облако через Gateway), Windows Stage 13 foreground и Notepad-E2E,
   браузер, runsc/KVM для сильных рантаймов Stage 8 (проверен только путь отказа).
2. **A/B бенчмарк** — AAF и IntelligenceRetention, RAW vs GUARDED verified-success.
   **Не измерялся**: нужны реальные модели и набор задач.
3. **CyberSec AI V1 под наблюдением** — Prompt Injection Firewall теперь
   реально в проде (пока выключен); включить на реальном хосте и понаблюдать
   за `cybersec.injection_detected`, прежде чем оставлять включённым.
4. **Замороженный стресс-тест RED vs BLUE** — отдельный, последующий гейт
   (только в одноразовой песочнице, без продакшн-секретов и продакшн-сети).
5. Сознательно отложено с объяснением (не блокирует freeze, но и не забыто):
   Context OS (command-center), V3 Data Guardian/Skill Factory — оба
   реализованы, но не подключены; см. `FINAL_CONNECTIVITY_MATRIX.md`.

## Документация

| Документ | О чём |
|---|---|
| [`docs/context/CURRENT_STATE.md`](docs/context/CURRENT_STATE.md) | канонический источник текущего состояния |
| [`docs/context/PRE_HARDWARE_FREEZE.md`](docs/context/PRE_HARDWARE_FREEZE.md) | итог closure-аудита: что закрыто, что сознательно отложено, вердикт |
| [`docs/context/FINAL_CONNECTIVITY_MATRIX.md`](docs/context/FINAL_CONNECTIVITY_MATRIX.md) | WORK/PARTIAL/UNWIRED/DEAD по каждому подсистеме, с доказательствами |
| [`docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md`](docs/context/REAL_HARDWARE_FINAL_ACCEPTANCE.md) | исполняемый чеклист приёмки на реальном железе (A–L) |
| [`docs/context/BOSSMAN_PRE_CYBERSEC_FREEZE.md`](docs/context/BOSSMAN_PRE_CYBERSEC_FREEZE.md) | baseline перед CyberSec: авторитеты, PG-гейт, замеры скорости, поверхность атаки |
| [`docs/context/V3_PRE_CYBERSEC_FINAL_REPORT.md`](docs/context/V3_PRE_CYBERSEC_FINAL_REPORT.md) | итоговый отчёт прохода + автономные инженерные решения (AED) |
| [`docs/context/V3_PRE_CYBERSEC_SYNC.md`](docs/context/V3_PRE_CYBERSEC_SYNC.md) | сверка веток и 5 закрытых P0 с доказательствами |
| [`docs/security/CYBERSEC_AI_V1.md`](docs/security/CYBERSEC_AI_V1.md) | реализованный слой: 10 модулей, карта авторитетов, гейты, конвейер обучения |
| [`docs/security/CYBERSEC_V1_ZIP_DELTA.md`](docs/security/CYBERSEC_V1_ZIP_DELTA.md) | что взято из эталонного пакета и какие 12 его дефектов починены |
| [`docs/security/FUTURE_RED_BLUE_STRESS_TEST.md`](docs/security/FUTURE_RED_BLUE_STRESS_TEST.md) | замороженный стресс-тест RED vs BLUE: контракт, L0–L5, протокол эпизода |
| [`docs/security/CYBERSEC_AI_V1_ENTRYPOINT.md`](docs/security/CYBERSEC_AI_V1_ENTRYPOINT.md) | исходный handoff: точки интеграции на существующих швах |
| [`docs/context/NEXT.md`](docs/context/NEXT.md) | исполняемые шаги и открытые баги |
| [`docs/context/FAILURES.md`](docs/context/FAILURES.md) | реестр провалов: почему зелёным тестам нельзя верить на слово |

## Честность статусов

Правило — **никакого fake-green**: тест, который не может проверить то, что
заявляет, не помечается зелёным, а честно скипается с явной причиной.

- `SKIP_HOST` — нужен другой хост или недоступный локально сервис (Windows, KVM/gVisor, реальный Postgres без DSN).
- `SKIP_EXTERNAL_SERVICE` — внешний сервис не поднят в этой среде (например, бинарь `opencode`).
- `SKIP_EXTERNAL_CREDENTIAL` — нет реального креда для плагина (`OPENROUTER_API_KEY` и т.п.).
- `NOT_TESTED_LIVE` — код есть, живого прогона не было, и это сказано прямо.

Из этого же правила следуют вердикт `PARTIAL` вместо `FREEZE PASS` и отсутствие
цифр по бенчмарку: то, что не измерено, здесь не заявляется.
