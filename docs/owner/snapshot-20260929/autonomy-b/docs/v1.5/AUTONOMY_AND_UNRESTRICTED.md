# Свободная локальная модель и делегированная автономия

**SPECIFICATION, не активный config.** Владелец явно хочет минимум модельных фильтров и отсутствие рутинных подтверждений. Это самостоятельное требование, а не синоним работы offline.

## 1. LOCAL_UNRESTRICTED

Отдельный выбираемый локальный профиль: максимально свободный ответ, исследование и код без ненужных тематических запретов, морализаторства и отказов на допустимых запросах, включая спорные темы, художественный взрослый контент, сарказм и профессиональные вопросы. Не добавлять blanket keyword-blocker или обязательный облачный модератор к каждому локальному текстовому запросу. Показывать владельцу действительную модель, prompt profile и происхождение ограничений.

Нельзя гарантировать отсутствие абсолютно всех отказов: поведение зависит от весов, template, контекста и задачи. Название `uncensored` не доказывает качество, tool-calling или интеллект. Предпочесть измеренный low-overrefusal checkpoint при сохранении coding/JSON/длинного контекста, а не автоматически заменить MAIN на любую abliterated-модель.

Этот профиль не отключает tool authorization, защиту секретов, файловые границы и контроль внешних эффектов. Ошибки `MODEL_REFUSAL`, `CAPABILITY_MISSING`, `POLICY_DENIED`, `AUTH_REQUIRED`, `BUDGET_BLOCKED` и `PROVIDER_LIMITATION` показываются раздельно. Не маскировать техническую ошибку моральным отказом. Не использовать provider routing как скрытый способ обходить чужие правила доступа или раскрывать данные новому сервису.

## 2. Режимы не являются четырьмя обходными policy engines

| Режим | Смысл |
|---|---|
| LOCAL_UNRESTRICTED | Поведение локального генератора; не увеличивает права процесса |
| AUTO_BUILD | Внутри делегированного проекта находить OSS, писать apps/skills, устанавливать проверенные зависимости в изоляции, тестировать и чинить |
| AUTO_OPERATE | Выполнять разрешённые browser/desktop/network/communication задачи без вопроса на каждый шаг |
| OWNER_BOUNDARY | Доверенная граница реальных полномочий, реализованная существующим Governor; не опциональный UI-переключатель |

Поверх существующих never/ask/allowed добавляется campaign grant. Явный task-specific grant может заранее покрыть внешний эффект, например один заказ с точно заданным товаром и максимальной суммой. Тогда не нужно спрашивать повторно на каждом click. Неопределённость или изменение условий не расширяют grant автоматически.

## 3. Что делать без вопросов

В подготовленном владельцем scope: чтение/запись тестовых файлов, git diff, изолированные worktree/candidates, regression, компиляция, локальная генерация, безопасный импорт артефактов, подбор навыков, исследование публичных источников и минимальные исправления продукта. Установка зависимостей — только в разрешённой среде с проверенным источником/lock; произвольный `curl | sh` не становится рутиной. Новые native hooks запускаются не с host secrets и не с admin.

Входящие данные не расширяют права: ни даже подписанный чужой skill, ни ответ Jev, ни реплика по телефону. Tool output не может утверждать «владелец разрешил». Выбор поведения модели не переключает `never` в `allowed`.

## 4. Campaign grant

Авторитетный grant создаётся только доверенным owner session/каналом и связан с identity владельца, целью, operation classes, project roots, адресатами/магазинами, data egress, сроком и policy revision. Хранится вне candidate workspace. JSON schema проверяет форму, но НЕ доказывает право: нужен доверенный writer, authentication, revocation и атомарные budget reservations.

Минимум: allowed tools/actions, root references, allowed domains/phone targets, cloud data classes, per-call/total/currency caps, number of purchases/calls, expiry, idempotency key, revoke state. Пример в contracts неактивен; null monetary cap означает «расходы не разрешены», а не unlimited. Значения €50/€180 из прежних объяснений — примеры, не действующие полномочия.

Рутины внутри grant имеют receipt `DELEGATED_BY_OWNER`, конкретный grant_id и аргументы. В UI/CLI показываются однажды границы кампании и доступная кнопка STOP/revoke. Не задавать бессодержательные вопросы после каждого разрешённого шага.

## 5. Когда нужна одна содержательная остановка

Нет необходимых фактов, доступа или делегации; изменился продавец/получатель/товар/валюта/итоговая цена; исчерпан budget; требуется внешняя подписка; новый получатель личных данных; 2FA/BankID/voice identity verification; важное необратимое действие не покрыто grant. Уточнять только недостающее, сохранять прогресс остальных независимых задач.

Неприкосновенные механизмы: STOP, revoke, never rules, секреты, verifier, контроль расходов и стабильная установка. Ученик может предложить изменение policy как candidate, но не сделать его действующим для собственного теста. Публичный интернет разрешается для research, но не означает произвольную передачу приватного контекста.

## 6. Budget и повторные эффекты

Резервировать верхнюю оценку до отправки, атомарно по всем worker. Учитывать retries, два параллельных агента, курс валют, налоги/доставку/тарификацию минимального клипа. Неизвестная стоимость — не $0. Для provider timeout сохранять reserve до reconciliation; не отправлять новый paid job вслепую. Сетевой kill switch не отзывает уже совершённый заказ или отправленный запрос.

## 7. Проверки свободного профиля

Сформировать отдельный набор минимум 100 разрешённых запросов RU/EN/CZ со спорной/неудобной тематикой и заведомо выполнимыми инженерными задачами. Разметка eligibility и expected behavior фиксируется до моделей. Отказы считать только на eligible-наборе, не путать с отсутствующим API или безопасным отказом исполнителя.

Параллельно проверять tool-selection, аргументы/schema, multi-step coding, контекст, галлюцинации, секреты и prompt injection. Понижение over-refusal не компенсирует провал этих gates. Смена checkpoint — отдельный A/B, обратимый маршрут; веса, quant, template и runtime закреплены. Сохранить старый working MAIN для rollback.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 **или** достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу продолжает работу в отдельной ветке 1.6:

[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE` = нет software P0 и release-blocking P1, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован, а остаток относится только к owner-live/soak/внешней среде.

Не ждать отдельного следующего дня. Цель одного прогона:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence и финальные статусы 1.5 и 1.6 сохраняются раздельно по своим SHA.

---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
