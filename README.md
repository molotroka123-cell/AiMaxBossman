# AiMaxBossman

Домашний ИИ-сервер Bossman: приватные локальные агенты, ваш интерфейс,
облако — только осознанно и на виду.

| Часть | Что это | Документы |
|---|---|---|
| [`bossman-infra/`](bossman-infra/) | инфраструктура: LiteLLM, llama-swap, Postgres+pgvector, Redis, Open WebUI, Uptime Kuma | [чек-лист этапа 0 и ТЗ инфраструктуры](bossman-infra/docs/STAGE0_AND_SPEC.md) |
| [`bossman-core/`](bossman-core/) | Bossman Control v0.3: петля агентов, панели, проекты, работа с контекстом | [ТЗ Bossman Control v0.3](bossman-infra/docs/BOSSMAN_CONTROL_TZ.md) |

Порядок внедрения:

1. **До приезда железа** — прогнать `bossman-infra` на ноутбуке (только облако
   или CPU-тест), прогнать `python -m pytest` в `bossman-core`.
2. **День приезда** — чек-лист этапа 0 (~2,5 часа + загрузка моделей).
3. **Поверх инфраструктуры** — подключить `bossman-core/compose.core.yaml`
   (см. [`bossman-core/README.md`](bossman-core/README.md)).

Железо: ACEMAGIC M1A PRO+ (Ryzen AI MAX+ 395, 128 ГБ LPDDR5X, 2 ТБ NVMe).
Доступ — только через Tailscale; ни один порт не открыт в интернет.
