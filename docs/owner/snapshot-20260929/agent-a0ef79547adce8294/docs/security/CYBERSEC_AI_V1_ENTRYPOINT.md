# CYBERSEC AI V1 — ENTRYPOINT (handoff, НЕ реализация)

Этот документ описывает ЧИСТЫЕ точки интеграции для будущего слоя CyberSec AI.
Правило №1: CyberSec-слой **оборачивает существующие авторитеты**, а НЕ создаёт
второй конкурирующий Policy/Gateway/Approval/Registry/Secret/Memory. Он подключается
как наблюдатель/фильтр/советник на уже существующих швах.

## Инвариант интеграции
`intent → typed action → [CyberSec pre-filter] → policy/scopes → approval →
executor → fresh observation → [CyberSec post-verify] → verification → audit`.
CyberSec-компоненты добавляют ХАРДЕНИНГ и ДЕТЕКТ, но не могут ослаблять policy,
повышать себе права, отключать approvals или обходить Cost Governor.

## Компоненты и их швы (existing hook points)

| CyberSec V1 компонент | Оборачивает / слушает (существующее) | Точка входа |
|---|---|---|
| **AI Security Guardian** | Gateway (Stage 3) — вход/выход модели | pre/post-hook вокруг `llm`/gateway перед провайдером; читает Cost Governor |
| **Prompt Injection Firewall** | все untrusted-входы: webpage/repo/memory/Telegram | фильтр на границе context_engine ingest + Telegram webhook + plugin `http.get` |
| **Agent Behavior IDS** | EventBus/audit (`events`/`obs`), correlation-id | подписчик на шину; аномалии по последовательностям typed action |
| **Credential/Secret Guardian** | Vault (`bcc/secrets`) + `settings`/env + redaction | расширяет существующую redaction; следит за утечкой в content/logs |
| **Sandbox/Blast-Radius Controller** | Stage 8 sandbox + `decide_effect` side-effect класс | усиливает admission/lease; ограничивает IRREVERSIBLE |
| **Cyber Recovery** | V3 Recovery Kernel + `failure_memory` | loop/watchdog/rollback-советы на уже существующих чекпоинтах |
| **Supply Chain Guardian** | plugin manifest + MCP registry + skill factory promotion | проверка происхождения tool/skill/MCP перед регистрацией |
| **Security Benchmark Lab** | `bcc/features/benchlab` + `eval_scorecard` | НЕ дублировать бенч-архитектуру — расширить существующую |
| **Defensive Red-Team Lab** | V3 Self-Improvement Lab (proposal-only) | генерит атаки-гипотезы, гоняет изолированно, отдаёт предложения (не мержит) |

## Текущий периметр (что CyberSec наследует как «уже защищено»)
- SSRF: literal + DNS-resolve pinned IP, no auto-redirect, per-hop revalidation, `max_bytes` streaming.
- SQL: read-only `mode=ro` + modifying-CTE gate (fail-closed).
- Path/symlink confinement; LSP workspace confinement (allowed_roots/_within).
- Telegram webhook: constant-time secret + chat/user allowlist; approvals — single-use callback.
- Remote Client: device-tokens + scopes, fail-closed; core-роуты под scope.
- Shell: argv-only везде; V3 computer_agent/skill_factory отвергают raw-shell.
- Secrets: Vault (Fernet) + маски; вычистка в логах.

## Явные НЕ-цели V1 (границы)
CyberSec НЕ реализуется в этой эпохе. НЕ вводить второй Policy/Gateway. НЕ давать
CyberSec-слою прав на самоповышение/самомерж. Любая авто-реакция сильнее «alert/
degrade/require-approval» — только через существующий approval-путь.

## Статус пред-условий (обновлено)
- ~~OPEN P0 working_memory schema~~ — **ЗАКРЫТО**: typed view над каноничной схемой.
- ~~REAL POSTGRES GATE (SKIP_HOST)~~ — **ЗАКРЫТО**: 24/24 PASS на живом PostgreSQL 16.13.
- Live-провайдеры/бенчмарк — по-прежнему требуют owner hardware.

## Реализация
Этот документ — handoff. Сам слой реализован: см. `CYBERSEC_AI_V1.md`
(модули и карта авторитетов), `CYBERSEC_V1_ZIP_DELTA.md` (что починено
относительно эталонного пакета) и `FUTURE_RED_BLUE_STRESS_TEST.md`
(замороженный стресс-тест).
