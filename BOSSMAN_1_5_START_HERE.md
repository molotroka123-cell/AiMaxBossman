# Bossman 1.5 — начать здесь

Дата решения владельца: 2026-09-23. **Статус пакета: SPECIFICATION / NOT IMPLEMENTATION / NOT CERTIFICATION.**

Единственная целевая ветка: `release/bossman-owner`. Не создавать вторую final-ветку или второй Bossman. Этот пакет добавляет требования и порядок сведения; сам по себе не включает автономию, не устанавливает модели, не звонит, не покупает и не объявляет код принятым.

## Что хочет владелец

«Описываю результат через Bossman CMD. Bossman сам исследует интернет, находит или пишет недостающие приложения/скиллы, проверяет их, выполняет задачу, исправляет свои баги и сохраняет применимый опыт. Рутинные вопросы не нужны. Локальная модель должна быть максимально свободной по тематике, без ненужных отказов. Хочу свой записанный голос, звонки от моего имени и заказ товаров с доставкой».

Подтверждённые направления: `LOCAL_UNRESTRICTED`, `AUTO_BUILD`, `AUTO_OPERATE`, `OWNER_BOUNDARY`. Свобода ответа модели и разрешение процесса совершить действие — разные механизмы. Не заменять технические границы слоганом «без ограничений». Нельзя гарантировать решение любой задачи или отсутствие всех отказов у любых весов.

## Два последовательных результата

**A. Свести сегодняшнюю работу.** Прочитать [мастер-промпт](docs/v1.5/CLAUDE_MERGE_MASTER.md) и [порядок слияния](docs/v1.5/MERGE_TODAY.md). Использовать существующую owner/fix-линию как место интеграции, сохранить этот пакет, проверить один кандидат и перенести его в `release/bossman-owner` только после обязательных gates. `main` не менять.

**B. Реализовывать 1.5 из общей базы.** [Архитектура](docs/v1.5/PRODUCT_ARCHITECTURE.md), [права и свободный профиль](docs/v1.5/AUTONOMY_AND_UNRESTRICTED.md), [App/Skill Factory](docs/v1.5/CAPABILITY_FACTORY.md), [голос/звонки](docs/v1.5/VOICE_AND_PHONE.md), [покупки](docs/v1.5/SHOPPING_AND_IDENTITY.md), [интернет/медиа/модели](docs/v1.5/INTERNET_MEDIA_AND_MODELS.md), [приёмка](docs/v1.5/ACCEPTANCE_AND_LEARNING.md).

## Проверенный указатель на старт

До данного documentation commit release был `e0bf948dea18a02a5fc577a0c2de98fedbfdb8b6`. Свежий прочитанный owner/fix HEAD: `1a29d85a4a471b549f274d18922e4c5485ed409f`. Evidence HEAD: `010d19076951d2911dc1bade449bbdb509b4a31c`. Это снимок указателей, не команда откатить новые коммиты. Перед работой обязательно fetch.

Последний evidence отмечает известные P1 исправленными в коде, но оставляет live retests, независимую повторную атаку, полный regression, настоящий Windows-100, exact-SHA CI и ZIP. Нельзя выдавать `FIXED` за `OWNER_LIVE_PASS`. Ранее опубликованный Windows-100 описан в свежем evidence как print-loop, а не реальный стресс-тест: проверить исполнение, не название workflow.

Ориентир обучения из owner-run: `SELF_REPAIR_SINGLE_CYCLE_PASS (coached)`; перенос улучшения — `NO_MEASURED_GAIN`. Это исторический результат конкретной конфигурации, не сертификат нового HEAD.

Полный индекс и границы пакета: [docs/v1.5/README.md](docs/v1.5/README.md).

## 2026-09-24 economy implementation

Implemented on `feat/bossman-1.5-economy-orchestrator-20260924`: a free-first Jev-managed worker lane using three independent Nemotron roles, free Ling coding/verifier work, bounded paid GLM finalization, public K1m6a YouTube batch ingestion, distillation quarantine, Bossman API controls, STOP, tests and Windows packaging. Live owner acceptance remains for the 2026-09-25 run.

Start tomorrow with [ECONOMY_ORCHESTRATOR.md](docs/v1.5/ECONOMY_ORCHESTRATOR.md) and [CODEX_OWNER_RUN_20260925.md](docs/v1.5/CODEX_OWNER_RUN_20260925.md).
