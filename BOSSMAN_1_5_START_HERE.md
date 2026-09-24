# Bossman 1.5 — начать здесь

Дата решения владельца: 2026-09-23; обновление реализации: 2026-09-24.

**Статус: IMPLEMENTED ON DEDICATED 1.5 BRANCH / OWNER LIVE ACCEPTANCE PENDING.**

Каноническая ветка 1.5: `feat/bossman-1.5-economy-orchestrator-20260924`.
Bossman 1.0 остаётся отдельной release-линей до своего freeze/tag. Не переносить 1.5 feature-коммиты в 1.0 ради удобства.

## Что хочет владелец

«Описываю результат через Bossman CMD. Bossman сам исследует интернет, находит или пишет недостающие приложения/скиллы, проверяет их, выполняет задачу, исправляет свои баги и сохраняет применимый опыт. Рутинные вопросы не нужны. Локальная модель должна быть максимально свободной по тематике, без ненужных отказов. Хочу свой записанный голос, звонки от моего имени и заказ товаров с доставкой».

Подтверждённые направления: `LOCAL_UNRESTRICTED`, `AUTO_BUILD`, `AUTO_OPERATE`, `OWNER_BOUNDARY`. Свобода ответа модели и разрешение процесса совершить действие — разные механизмы. Не заменять технические границы слоганом «без ограничений». Нельзя гарантировать решение любой задачи или отсутствие всех отказов у любых весов.

## Что уже является продуктом 1.5

- persistent autonomy kernel: роли, skill/memory refs, operating graph и cost/quality resource routing;
- free-first economy orchestration;
- runtime code-failure → durable repair inbox;
- isolated local repair candidate branches;
- executable-test requirement before coding worker can claim DONE;
- scientific promotion gate with verifier + security non-regression + unseen transfer;
- Telegram missing-form-data loop → browser runtime fill;
- unified **Bossman 1.5** Command Center page;
- unified `Bossman-1.5.cmd start|status|stop`;
- Twitch OI/CVD verified collector + Telegram;
- YouTube trading-learning ingestion with anti-lookahead and quarantine;
- provider-pool onboarding that forbids automatic signup/quota evasion.

Главный контракт: [AUTONOMY_SELF_REPAIR.md](docs/v1.5/AUTONOMY_SELF_REPAIR.md).

## Проверенный указатель на старт

До данного documentation commit release был `e0bf948dea18a02a5fc577a0c2de98fedbfdb8b6`. Свежий прочитанный owner/fix HEAD: `1a29d85a4a471b549f274d18922e4c5485ed409f`. Evidence HEAD: `010d19076951d2911dc1bade449bbdb509b4a31c`. Это снимок указателей, не команда откатить новые коммиты. Перед работой обязательно fetch.

Последний evidence отмечает известные P1 исправленными в коде, но оставляет live retests, независимую повторную атаку, полный regression, настоящий Windows-100, exact-SHA CI и ZIP. Нельзя выдавать `FIXED` за `OWNER_LIVE_PASS`. Ранее опубликованный Windows-100 описан в свежем evidence как print-loop, а не реальный стресс-тест: проверить исполнение, не название workflow.

Ориентир обучения из owner-run: `SELF_REPAIR_SINGLE_CYCLE_PASS (coached)`; перенос улучшения — `NO_MEASURED_GAIN`. Это исторический результат конкретной конфигурации, не сертификат нового HEAD.

Полный индекс и границы пакета: [docs/v1.5/README.md](docs/v1.5/README.md).

## Завтра: закрытие 1.5

Завтра не начинать новый coding sprint. Сначала выполнить focused 1.5 CI/target tests, затем один owner run:

`Bossman-1.5.cmd quick-test → start → status`

Параллельно должны работать:
1. self-improvement / runtime self-repair;
2. YouTube learning/economy;
3. Twitch verified market collection;
4. Telegram owner console + missing form inputs.

Затем planted defect → isolated repair candidate → verifier → unseen transfer.

Canonical directive: [docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md](docs/owner/CODEX_BOSSMAN_1_5_RUN_20260925.md).
