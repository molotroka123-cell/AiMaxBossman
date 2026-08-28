# AiMaxBossman

Домашний ИИ-сервер Bossman: приватные локальные агенты, ваш интерфейс,
облако — только осознанно и на виду.

## 🎯 Цель сессии до приезда компьютера

**Собрать BOSSMAN AI Command Center** — локальный dashboard и control plane для
управления моделями, агентами, задачами и автоматизациями. Обязательные документы:

- [`docs/BOSSMAN_CLAUDE_CODE_SETUP.md`](docs/BOSSMAN_CLAUDE_CODE_SETUP.md) —
  master setup brief: правила безопасности (обязательны всегда), порядок настройки
  железа в день приезда, фазы 1–3, lead-pipeline, цены, fail-safe.
- [`docs/COMMAND_CENTER_SESSION_GOAL.md`](docs/COMMAND_CENTER_SESSION_GOAL.md) —
  полное ТЗ Command Center: 70 разделов, MVP (раздел 62), Definition of Done (64–65).
- [`docs/COMMAND_CENTER_ARCHITECTURE.md`](docs/COMMAND_CENTER_ARCHITECTURE.md) —
  архитектура, выбранная для MVP.
- [`docs/COMMAND_CENTER_PLAN.md`](docs/COMMAND_CENTER_PLAN.md) — план реализации.

Критерий готовности сессии: открыть dashboard → подключить локальную и облачную
модель → создать агента → дать задачу (сейчас или по расписанию) → закрыть браузер →
вернуться и увидеть результат, логи и состояние машины. Всё, что требует реального
железа (hardware audit, benchmark, Phase 1 setup-брифа), выполняется на самой машине
в день приезда по `BOSSMAN_CLAUDE_CODE_SETUP.md`.

**Статус: V2.2 — текущий этап закрыт.**
Control plane — настоящий агентный рантайм: модель реально вызывает инструменты
(терминал, браузер, MCP, память, код, OpenCode) через один канонический цикл с
правами AUTO/ASK/DENY. V2.2 добавила масштабирование памяти, производные
хранилища в снапшотах, петлю самообучения с ограниченными переходами и изоляцию
рабочей области агента.

- Итог этапа: [`docs/V2_2_CURRENT_PHASE_FINAL_REPORT.md`](docs/V2_2_CURRENT_PHASE_FINAL_REPORT.md).

- Оценки по направлениям: [`docs/V2_1_FINAL_SCORECARD.md`](docs/V2_1_FINAL_SCORECARD.md)
  — **10 DONE, 7 PARTIAL, 0 FAILED**; что не проверено — названо прямо.
- Доказательства сквозных прогонов: [`docs/V2_1_E2E_PROOF.md`](docs/V2_1_E2E_PROOF.md).
- Безопасность доступа: [`docs/V2_1_SECURITY_REPORT.md`](docs/V2_1_SECURITY_REPORT.md).
- Что и как сделано: [`docs/V2_1_IMPLEMENTATION_REPORT.md`](docs/V2_1_IMPLEMENTATION_REPORT.md).
- Рабочий контекст волны: [`docs/V2_1_RUNTIME_CONTEXT.md`](docs/V2_1_RUNTIME_CONTEXT.md).

Тесты: **на коммите `78b843b`: 321 passed, 1 skipped** за 92 с; на текущем
HEAD ветки — **323 passed, 1 skipped** (плюс два теста гейта «Code root safety»)
(`cd command-center && timeout 900 python -u -m pytest -q`).
Пропуск — намеренный: реальный smoke по `opencode serve`, бинаря в этом
окружении нет, и он честно не засчитан.
Число всегда называется вместе с коммитом: иначе через неделю непонятно, к
какому состоянию оно относится.

Запуск: `cd command-center && pip install -e . && bcc` → http://127.0.0.1:8800
(токен печатается в консоли; в браузере он меняется на HttpOnly-сессию).
Ветка разработки: `claude/bossman-control-v03-43igbk`.

Более ранние срезы — [`docs/V2_FINAL_SCORECARD.md`](docs/V2_FINAL_SCORECARD.md)
и [`docs/COMMAND_CENTER_REPORT.md`](docs/COMMAND_CENTER_REPORT.md) — помечены
как исторические: числа в них относятся к своим коммитам.

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
