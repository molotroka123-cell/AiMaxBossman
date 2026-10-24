# Документация Bossman — карта

Короткий вход: [README](../README.md) → правила для AI-участников [AGENTS.md](../AGENTS.md) → квитанции
[docs/audits/INDEX.md](audits/INDEX.md). Документ сам по себе ничего не доказывает и ничего не разрешает: готовность
определяется только квитанциями и тестами на точном SHA.

## Начать здесь

| Документ | Зачем |
|---|---|
| [README.md](../README.md) | что такое Bossman, что доказано, быстрый старт, порты |
| [AGENTS.md](../AGENTS.md) | обязательные правила: безопасность, бюджет, приватность, приёмка |
| [INSTALL.md](../INSTALL.md) | установка Windows-сборки |
| [OWNER_ACCEPTANCE.md](../OWNER_ACCEPTANCE.md) | приёмка владельцем HW-01…HW-13 |
| [KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md), [CLAIMS_NOT_PROVEN.md](../CLAIMS_NOT_PROVEN.md) | что не работает или не доказано |
| [LICENSING.md](../LICENSING.md) | лицензии кода, сторонних компонентов и моделей |

## Доказательства

- [audits/INDEX.md](audits/INDEX.md) — все квитанции `docs/audits/<дата>-<тема>/` с вердиктами.
- [evidence/](evidence/) — старые улики прогонов; [benchmark/](benchmark/) — источник Live Scorecard.
- [testing/](testing/) — сценарии и прогоны испытаний.

## Архитектура и контракты

- [ARCHITECTURE.md](../ARCHITECTURE.md) — обзор системы.
- [architecture/](architecture/) — контракты (память single-writer, code-mode facade, MP21), реестр дерева
  возможностей [`tree-registry.json`](architecture/tree-registry.json),
  [PRODUCT_EVOLUTION_CONTRACT.md](architecture/PRODUCT_EVOLUTION_CONTRACT.md).
- [constitution/](constitution/BOSSMAN_CONSTITUTION.md) — конституция (пин меняет только владелец).
- [security/](security/) — модели угроз, red-team, гейты безопасности.

## Текущая работа владельца и интеграторов

- [owner/](owner/) — актуальные поручения, North Star, мастер-промпты 22–23.10, продакшен-план
  [production-20261021](owner/production-20261021/README_RU.md), 2× производительность
  [BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md](owner/BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md).
- [agents/](agents/2026-10-22-foundation/README_RU.md) — основа делегирования агентам.
- [north-star/](north-star/), [evo/](evo/), [evolution/](evolution/), [learning/](learning/) — самоулучшение и обучение.

## Направления

[media/](media/), [video-studio/](video-studio/), [genjutsu/](genjutsu/), [music/](music/) — медиа;
[trading/](trading/) — торговые эксперименты (без живой торговли); [telegram-calls/](telegram-calls/) — звонки;
[ux/](ux/) — интерфейсы; [features/web-research](features/web-research/) — веб-поиск; [skills/](skills/) — навыки;
[operations/](operations/) — настройка Claude Code и защита веток.

## Версии

[v1.5](v1.5/README.md) · [v1.6](v1.6/) · [v1.7](v1.7/) · [v1.8](v1.8/) · [v1.9](v1.9/) · [v2.2-20261013](v2.2-20261013/README.md) ·
[v3](v3/) · [v8](v8/) — спецификации прошлых этапов; действующие правила — в AGENTS.md.

## Архив

[archive/INDEX_RU.md](archive/INDEX_RU.md) — перенесённые (не удалённые) документы: прежние README
([полный README до 24.10](archive/root-history/README_full_20261024.md)), handoff-ы, планы и дневники. Всё в архиве
относится к дате файла и не является текущим указанием.
