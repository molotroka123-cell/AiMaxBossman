# Epoch 8 — Bossman Studio: свой Higgsfield, лучше и честнее

**Статус:** PLANNED / NOT IMPLEMENTED (реализация 0 %).
**Записано:** 2026-09-17, после приёмки кандидата `e39610de` (первая сборка по
замку входов, `WINDOWS_RC_READY_OWNER_REQUIRED`, см. `docs/final/CURRENT_STATE.md`).
**Повод:** Higgsfield открыл свои инструменты сторонним разработчикам (API и MCP:
Seedance 2.0/2.5, MiniMax H3, Z-Image, Soul, Marketing Studio и остальные
продукты), а `wide-trace/open-higgsfield` за три недели собрал 1,7 тыс. звёзд
на «открытой альтернативе». Распоряжение владельца: сделать свою студию
генерации — лучше и круче.

## Смысл этапа в одной строке

**V7 доказал, что Bossman честно делает работу и что один Windows-архив
проходит приёмку; Epoch 8 превращает уже существующие движки (Images, Video
Studio, Web Designer, маршрутизатор моделей, MCP-хаб, provenance) в одну
студию генерации изображений, видео и звука — с любыми провайдерами, включая
Higgsfield, но с байтами, уликами и деньгами под контролем владельца.**

## Что здесь считается «лучше Higgsfield» (проверяемо, не лозунг)

| # | Свойство | Higgsfield / open-higgsfield | Bossman Studio (V8) |
|---|---|---|---|
| 1 | Где живут байты | на CDN платформы; история open-higgsfield «может пережить срок жизни CDN и показывать дыры» | всегда локально, sha256 у каждого результата, галерея переживает провайдера |
| 2 | Провайдеры | одна платформа (open-higgsfield — тонкий клиент к API Higgsfield с ключом `id:secret`) | несколько: локальный ComfyUI, OpenRouter (уже используется скриптами репозитория), платформенный API Higgsfield, MCP Higgsfield — каждый через полосу приёмки, ни один не «по умолчанию» |
| 3 | Что записано о прогоне | prompt, модель, настройки, URL (open-higgsfield: 60 записей в IndexedDB браузера) | полный provenance: модель/провайдер/настройки/seed/входы (sha256)/выход (sha256, байты, размеры)/время/стоимость/request_id/причина отказа — по образцу `bcc/run_provenance.py` |
| 4 | Честность отказов | «failed/nsfw/canceled» плиткой; нет ключа → модалка | отсутствующий ключ, 429, недоступная модель, отказ по контенту и неверный ответ — **пять разных результатов** (словарь `bcc/model_health.py` + `bcc/reality/recovery.py`), без скрытых повторов |
| 5 | Деньги | кредиты платформы, подписка | бюджет на прогон и на день, бесплатные маршруты по умолчанию, платный fallback запрещён политикой (`GovernedAdapter` пересчитывает цену на каждом вызове) |
| 6 | Приёмка | в open-higgsfield нет ни одного теста | каждая возможность — тест на установленном Windows-архиве, вердикт `PASS / FAIL / OWNER_REQUIRED / PARTIAL`, привязка к `source_sha / archive_sha256 / run_id` (контракт v2) |
| 7 | Связь с работой | генерация ради генерации | результат сразу становится клипом Video Studio, картинкой Web Designer, вложением задачи агента — под тем же гейтом «наблюдение ≠ улика» |
| 8 | Лицензия и код | open-higgsfield: **файла LICENSE нет** (проверено на `b16a0efe`) — копировать код нельзя | свой код под своей лицензией; идеи (каталог — источник истины, роли медиа, reuse) — заимствуются с указанием |

## Канонический пакет документации

1. [EPOCH_8_CHARTER.md](EPOCH_8_CHARTER.md) — миссия, границы, неизменяемые принципы, входной гейт.
2. [HIGGSFIELD_PARITY_MATRIX.md](HIGGSFIELD_PARITY_MATRIX.md) — что реально умеет Higgsfield (98 моделей из каталога MCP), что такое open-higgsfield на самом деле, что есть у Bossman сегодня, и матрица возможностей с колонкой «как измерить».
3. [ARCHITECTURE_AND_WORKSTREAMS.md](ARCHITECTURE_AND_WORKSTREAMS.md) — устройство студии поверх существующих модулей: каталог, провайдеры, очередь, галерея, связка с движками; модель данных и API.
4. [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) — фазы 0→5, приоритеты P0/P1/P2, зависимости, что делается первым (тесты и негативные контроли).
5. [ACCEPTANCE_AND_FREEZE_GATES.md](ACCEPTANCE_AND_FREEZE_GATES.md) — Definition of Done, строки вердиктов, условия жёсткого отказа, как студия входит в контракт приёмки v2 и заморозку.
6. [ROLLBACK_RISK_AND_SAFETY.md](ROLLBACK_RISK_AND_SAFETY.md) — риски (деньги, ключи, эгресс медиа, дрейф провайдеров, «15 демонов»), откат, kill-критерии.
7. [MASTER_PROMPT_V8_RU.md](MASTER_PROMPT_V8_RU.md) — готовый мастер-промт для исполняющего агента.

## Источники, на которых стоит пакет (проверены 17.09.2026)

- Каталог Higgsfield через подключённый MCP-сервер: `models_explore list` — 98 моделей
  (video 41, image 34, 3d 17, audio 6), схема параметров и ролей медиа; 16 бандл-воркфлоу
  (`get_workflow_instructions`); список MCP-инструментов сессии.
- `wide-trace/open-higgsfield` @ `b16a0efe4d7e2707b56f8ccb02387fd2a9d2eddf` (shallow-клон,
  только чтение): README, `.env.example`, `src/generation/{platform,to-platform,poll,credentials,actions}.ts`,
  `src/generation/catalog/*`, `src/openhiggsfield/history.ts`.
- Код Bossman на `e39610de`: `command-center/bcc/features/images.py`, `bcc/v2/images_runtime.py`,
  `bcc/oss/comfyui.py`, `bcc/video_studio/*`, `bcc/v2/model_router.py`, `bcc/model_health.py`,
  `bcc/reality/recovery.py`, `bcc/v2/openrouter_*`, `bcc/provider_governance.py`, `bcc/secrets.py`,
  `bcc/v2/mcp_hub.py`, `bcc/v2/mcp_runtime.py`, `tools/mcp_acceptance.py`, `bcc/run_provenance.py`,
  `tools/hailuo_rescue.py`, `tools/seedance_shorts.py`, `command-center/ui/pages/*`.
- Правила проекта: `docs/final/MASTER_PROMPT_NEXT_RUN.md` (§0, §2, §7, §8, §10),
  `docs/final/one-archive-audit-20260917/MASTER_PROMPT_RU.md` (§1, §7, §9),
  `docs/final/SAFETY_INVARIANTS.md`, `docs/final/FAILURE_MATRIX.md`,
  `docs/final/CONNECTOR_INTEGRATION_REPORT.md`, `docs/oss/README.md`.

## Текущий статус

- Реализация: **0 %**. Ни одной строки кода студии не написано; этот пакет — контракт, а не отчёт.
- Провайдер генерации в продукте: **нет** (`bcc/video_studio/analysis.py::generation_status()` →
  `BLOCKED: No verified image/video generation provider is connected to Video Studio`;
  Images исполняет только `mock-image` и `comfyui`).
- Ключ Higgsfield у владельца: **неизвестно**; ключ OpenRouter в Actions Secrets: **пуст**
  (прогон 116). До ключей все живые проверки студии — `OWNER_REQUIRED`.
- Условие старта: см. входной гейт в уставе. Кандидат `e39610de` его выполняет по
  репозиторной части; остаётся решение владельца по ключам и бюджету.
