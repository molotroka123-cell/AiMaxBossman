# Bossman 1.5 — Universal Operator: документация

**SPECIFICATION, 2026-09-23.** Target: `release/bossman-owner`. Данная публикация — только документация. Тестовые статусы ниже описаны как будущие критерии, не как выполненные работы.

| Документ | Назначение |
|---|---|
| [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) | Цель, общая архитектура и этапы реализации без второго ядра |
| [AUTONOMY_AND_UNRESTRICTED.md](AUTONOMY_AND_UNRESTRICTED.md) | Максимально свободный локальный профиль, рутина без вопросов, отдельная authority |
| [CAPABILITY_FACTORY.md](CAPABILITY_FACTORY.md) | Поиск/создание/тест/установка apps, skills, MCP и adapters |
| [VOICE_AND_PHONE.md](VOICE_AND_PHONE.md) | Собственный голос, запись, STT/TTS, телефония, живой тест |
| [SHOPPING_AND_IDENTITY.md](SHOPPING_AND_IDENTITY.md) | Покупка кроссовок, адрес, финальная цена, платёж, receipt и доставка |
| [INTERNET_MEDIA_AND_MODELS.md](INTERNET_MEDIA_AND_MODELS.md) | Интернет/Computer Use/Jev, hybrid video, модельные профили |
| [GAME_STUDIO.md](GAME_STUDIO.md) | Unreal/Game Studio: автономная разработка и проверка оригинального 15-минутного AAA-FPS vertical slice |
| [MODEL_STACK_REFRESH_2026-09-23.md](MODEL_STACK_REFRESH_2026-09-23.md) | Свежая ревизия локального стека, Xing4 challenger, маршрутизация и owner-hardware A/B |
| [ACCEPTANCE_AND_LEARNING.md](ACCEPTANCE_AND_LEARNING.md) | 20 классов неизвестных задач, safety-гейты, измерение обучения |
| [ECONOMY_ORCHESTRATOR.md](ECONOMY_ORCHESTRATOR.md) | Jev-managed free-first swarm: 3× Nemotron, Ling tester/coder, capped paid GLM |
| [CODEX_OWNER_RUN_20260925.md](CODEX_OWNER_RUN_20260925.md) | Завтрашний owner-run: Codex как интегратор, дешёвые модели делают bulk work |
| [MERGE_TODAY.md](MERGE_TODAY.md) | Пошаговое безопасное сведение сегодняшних delta и exact-SHA |
| [CLAUDE_MERGE_MASTER.md](CLAUDE_MERGE_MASTER.md) | Исполняемый handoff локальному Claude, 5 агентов |
| [SOURCES_AND_BASELINE.md](SOURCES_AND_BASELINE.md) | Проверенные источники, snapshot и неизвестное |
| [contracts/source-refs.json](contracts/source-refs.json) | Зафиксированные remote-указатели, которые нужно обновить после fetch |
| [contracts/capability-manifest.schema.json](contracts/capability-manifest.schema.json) | Проект формата регистрируемой capability |
| [contracts/owner-grant.schema.json](contracts/owner-grant.schema.json) | Проект формата делегирования прав на кампанию |
| [contracts/autonomy-profile.example.json](contracts/autonomy-profile.example.json) | Неактивный пример профиля, НЕ действующее разрешение |

## Обязательное различие

`SPECIFICATION → IMPLEMENTED → CONTRACT_PASS → LOCAL_LIVE_PASS → OWNER_LIVE_PASS → RELEASE_CERTIFIED`.

Отдельно: авторство `LOCAL_STUDENT / TEACHER / FIXTURE`; результат `PASS / FAIL / BLOCKED / NOT_RUN / UNKNOWN_OUTCOME`; помощь `UNASSISTED / COACHED / TEACHER_PATCH`.

Документация не подменяет код. Mock не доказывает звонок, покупку, живую генерацию или исправление локальной моделью. Новый docs commit не сертифицирует новый runtime. Факт наличия функции в другой ветке не доказывает её интеграцию.

## Разрешение владельца

Подготовка пакета и сведения сегодняшней согласованной работы разрешена. Без дополнительных вопросов допустимы обычные локальные инженерные действия внутри уже разрешённого проекта: читать, писать, тестировать, исправлять минимальный баг и публиковать безопасный код/evidence. Эти документы не разрешают реальные звонки, оплаты, выпуск голоса, публикацию рекламы или расширение доступа к личным файлам. Для таких задач заранее оформляется конкретная делегация; внутри неё повторные вопросы не нужны.

Существующие `never/ask/allowed`, Cost Governor, STOP, approvals, privacy и rollback не заменяются параллельной системой. Новые schemas — проект расширения существующих контрактов, до реализации их нельзя принимать как authority.

## Что считать завершением подготовки

Пакет доступен из целевой ветки, все внутренние ссылки разрешаются, JSON валиден, нет секретов/адресов/голосовых образцов, master prompt называет тот же target и сохраняет сегодняшние fixes. Реализация и слияние продукта имеют отдельные доказательства.
