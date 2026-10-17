# ADR-001: изолированное приложение, FastAPI + SQLite + статический интерфейс

Статус: принято 17.10.2026. Источник требований: `Bossman_War_Terminal_v0_1/docs/TECHNICAL_SPEC_RU.md`.

## Решение

`apps/war-terminal` — отдельный процесс с версионированным API. Ядро Bossman не переписывается; связь — через `/api/v1` и тонкий прокси
`command-center/bcc/features/war_terminal.py`.

## Отклонения от ТЗ и почему

| ТЗ | Сделано | Причина | Путь к ТЗ |
|----|---------|---------|-----------|
| Next.js/React + TypeScript | статический HTML/JS без сборки | локальный Windows-старт без Node-сборки, работает офлайн, проще аудит | заменить `ui/` на Next.js, API не меняется |
| PostgreSQL/PostGIS + Alembic | SQLite, схема в `store.py` | one-box Windows владельца, нет сервера БД; гео в MVP — простые lat/lon | слой `Store` изолирован; миграция на PostgreSQL до production |
| Redis + Celery/Arq | синхронный ingest по запросу | один пользователь, нет очереди | планировщик Боссмана вызывает `/ingest` по расписанию |
| OIDC + RBAC + MFA | токены ролей + RBAC | один владелец, loopback | OIDC перед любым внешним доступом |
| MapLibre GL | SVG-карта без внешних тайлов | оффлайн и без лицензий на тайлы | MapLibre + лицензированная подложка |

## Поток данных

```
Official API -> connector -> immutable snapshot (sha256) -> normalizer + dedupe (key = jurisdiction:notice_id, version) -> SQLite
   -> REST /api/v1 -> UI | Bossman proxy (analyst) | model tools (read-only)
   -> rules (hard gates) + scoring (explainable fit) + finance (Decimal) -> approvals ledger + hash-chained audit
```
Факты источника, вывод модели и ввод владельца хранятся раздельно (`tenders`, `assessments.ai`, `kv/profile|fx|sanctions`).

## Версионирование
API `v1`; изменения обратно совместимы. Тендер: новая версия при изменении содержимого уведомления (продление срока и т.д.), дубликат ничего не создаёт.
