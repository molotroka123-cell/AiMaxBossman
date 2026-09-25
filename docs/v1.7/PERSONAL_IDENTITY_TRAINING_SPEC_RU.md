# Bossman 1.7 — Personal Identity Training

## Статус

Отдельный экспериментальный поток. Пока 1.5 и 1.6 закрываются на AI Max, 1.7 пишется и тестируется на ноутбуке с удалёнными моделями. Слияние — только после отдельной convergence-проверки.

## Главная цель

Сделать Telegram-ассистента для владельца и небольшой группы добровольных тестеров. Он должен быть полезен как универсальный чат-ассистент, уметь пользоваться веб-поиском, выбирать модель через Jev и постепенно собирать локальную персональную память каждого участника.

Первый этап — collection-first: высокая полнота сбора полезного контекста важнее идеальной фильтрации. Позже отдельный garbage-sorter/retention model учится отделять полезную память от шума на данных добровольных тестеров.

## Consent boundary

До долговременной памяти пользователь видит короткий onboarding: бот может локально запоминать предпочтения и контекст для персонализации; память администрируется владельцем Bossman; её можно посмотреть, исправить, приостановить, экспортировать и удалить.

Скрытая персонализация без этого onboarding не входит в 1.7.

## Архитектура

Telegram update
→ identity + allowlist
→ consent gate
→ intent/privacy classifier
→ persona retrieval
→ Jev route
→ optional web/tool
→ model answer
→ verifier
→ send answer
→ memory candidate extraction
→ policy filter
→ append to collection spool
→ optional discovery question
→ periodic compaction into persona snapshot

## Laptop profile

Пока тяжёлые локальные модели недоступны:
- local models disabled by config;
- primary remote slot = GLM 5.3 through an owner-approved provider;
- fallback = allowlisted zero-cost/free cloud endpoints;
- web search enabled for current/fresh facts;
- paid route disabled unless explicitly enabled by owner policy.

Переход на local-first позже должен быть конфигурационным: Telegram, PersonaVault и memory schemas не переписываются.

## Jev decision contract

На каждый запрос Jev выдаёт структурированное решение:
- intent
- needs_web
- needs_vision
- needs_code
- sensitivity
- persona_keys
- model_candidates
- selected_model
- fallback_chain
- expected_quality
- expected_latency
- expected_cost
- privacy_risk
- reason_code

Score:

predicted_quality - a*latency - b*cost - c*privacy_risk + d*local_bonus

Local bonus не имеет права выбирать модель, которая не умеет выполнить задачу.

## Identity isolation

Каждый новый Telegram ID начинает с пустого персонального профиля. PIT context builder использует только память текущего person_key и его собственные turns/files. Профиль владельца, общая память Bossman и профили других участников в PIT model context не входят. Внутри этого отдельного бота даже владелец машины рассматривается как participant; административные возможности остаются вне conversational surface.

## Telegram capability boundary

У Telegram-пользователя структурно отсутствуют:
- computer.*
- shell/terminal
- Windows control
- owner approvals
- payment/trading effects
- secret management
- доступ к другим persona folders
- unrestricted filesystem

Разрешаются:
- normal chat
- web search/read
- calculator
- code reasoning without local execution effects
- own uploaded files
- vision over own files/images
- own persona memory
- isolated per-user workspace

## Storage layers

### L0 — raw event spool
Высокополный журнал сообщений и метаданных для лабораторного режима. Имеет retention policy и никогда автоматически не подмешивается целиком в prompt.

### L1 — memory candidates
Максимально широкий набор кандидатов после каждого содержательного ответа. Здесь допускается шум: цель первого этапа — не терять потенциально полезные сигналы.

### L2 — canonical persona
Только нормализованные факты/предпочтения с provenance, confidence, temporal state и contradiction tracking.

### L3 — derived indexes
Embeddings, retrieval indexes, clusters, summaries. Полностью rebuildable.

## Категории данных collection-first

### Общение
- основной язык
- дополнительные языки
- предпочитаемая длина ответа
- формат: текст/список/таблица/код
- стиль: формально/неформально
- терпимость к техническим деталям
- нужны ли примеры
- нужны ли источники
- предпочитаемая степень инициативы ассистента
- отношение к уточняющим вопросам
- любимая структура объяснений
- характерный словарь и сленг
- частые сокращения
- формат дат, времени, валют
- предпочтение голос/текст

### Знания и навыки
- известные языки программирования
- профессиональные области
- инструменты, которыми пользователь умеет пользоваться
- темы, где пользователь эксперт
- темы, где пользователь новичок
- предпочитаемый уровень объяснения
- часто исправляемые заблуждения
- уже объяснённые понятия
- проверенные навыки
- обучаемые навыки
- цели обучения

### Работа и проекты
- активные проекты
- роли в проектах
- текущие задачи
- дедлайны, если пользователь сам их дал
- используемые приложения/сервисы
- предпочитаемый workflow
- типичные документы
- повторяющиеся рабочие процессы
- критерии «готово»
- требования к качеству
- любимые инструменты
- нежелательные инструменты
- текущие блокеры
- принятые решения и причины

### Интересы
- хобби
- спорт
- технологии
- игры
- музыка
- фильмы/сериалы
- книги
- автомобили
- путешествия
- дизайн
- фотография
- наука
- бизнес-темы
- коллекции
- часто обсуждаемые темы

### Медиа-вкусы
- жанры
- авторы/исполнители
- предпочитаемый темп/тон
- визуальные стили
- любимые примеры
- нежелательные стили
- предпочтение реализм/стилизация
- формат видео
- формат изображений
- привычные платформы

### Покупки и выбор
- важные критерии выбора
- цена/качество
- премиальность
- чувствительность к цене
- любовь к сравнению вариантов
- отношение к брендам
- новые vs проверенные продукты
- скорость решения
- гарантия/сервис как критерий
- любимые магазины/платформы
- экосистема устройств

### Путешествия
- любимый формат поездки
- предпочитаемый транспорт
- комфорт vs приключение
- темп маршрута
- интерес к еде/музеям/ночной жизни/природе
- предпочтение центра/тихих районов
- тип жилья
- багажные привычки
- отношение к пересадкам
- часто посещаемые города/страны, если сам сообщает

### Еда и бытовые предпочтения
- кухни
- любимые блюда
- продукты, которые пользователь не любит
- стиль ресторанов
- кофе/чай
- формат доставки/готовки
- бытовые привычки, которые реально влияют на рекомендации

### Решения
- какие критерии пользователь обычно ставит первыми
- любит ли короткий shortlist или глубокий анализ
- просит ли worst-case
- предпочитает ли консервативный/экспериментальный вариант
- насколько любит обратимые решения
- как относится к неопределённости
- насколько часто просит второе мнение
- предпочитает ли цифры, примеры или аналогии

### Ассистентские привычки
- типичные типы запросов
- время/частота использования
- средняя длина диалога
- насколько часто пользователь уточняет
- насколько часто просит переделать
- что обычно считает ошибкой
- какие ответы сохраняет/возвращается к ним
- какие инструменты чаще нужны
- где web обычно полезен
- где web раздражает
- успешные шаблоны ответа

### Коррекции
- пользовательские исправления фактов
- исправления имени/терминов
- «не делай так»
- «делай всегда так»
- rejected recommendations
- successful recommendations
- изменения предпочтений со временем
- contradiction history

### Цели
- краткосрочные
- долгосрочные
- текущий приоритет
- completed goals
- paused goals
- dependencies
- success criteria
- следующие шаги, явно подтверждённые пользователем

### Социальный контекст
Только то, что пользователь сам сообщил и что нужно для будущей помощи:
- relation labels (friend, colleague, partner)
- имена/псевдонимы, если нужны в текущем контексте
- совместные проекты
- предпочтения коммуникации с этими людьми

Не строить скрытый social graph из чужих данных.

### Temporal patterns
- рабочие/нерабочие часы, если явно проявляются
- типичные сроки ответа
- recurring tasks
- сезонные проекты
- изменение интересов
- freshness каждой памяти
- decay score

### Device/software context
- OS
- телефон/компьютер
- используемые IDE
- браузеры
- мессенджеры
- file formats
- cloud services
- предпочитаемые AI tools

## Sensitive/secret boundary

Никогда не сохранять как persona secret values:
- passwords
- API tokens
- seed phrases/private keys
- 2FA/recovery codes
- full bank/card credentials
- auth cookies/session secrets

Чувствительные категории не профилируются автоматически: здоровье, сексуальная жизнь, точная геолокация, религия, этничность, политические предпочтения и аналогичные категории. Если они нужны для текущего ответа — использовать только в контексте текущего разговора. Durable storage требует отдельного opt-in.

## Memory record

Каждый candidate/fact:
- id
- category
- key
- value
- confidence
- explicit / inferred / confirmed
- sensitivity
- provenance.message_id
- first_seen
- last_seen
- observation_count
- contradiction_count
- supersedes
- ttl
- utility_score
- retrieval_count
- correction_count
- source_model
- extraction_version

## Discovery Engine

Максимум один необязательный вопрос после содержательного ответа.

score = relevance * uncertainty * future_utility - annoyance_cost - sensitivity_risk

Не спрашивать в каждом сообщении. Не повторять skipped question. Не использовать discovery для секретов или скрытого sensitive profiling.

## User commands

/memory
/why_memory
/forget
/pause_memory
/resume_memory
/export_me
/delete_me
/style
/privacy

## Persona snapshot

Это симуляционная модель предпочтений, а не «истина о личности»:
- communication_style
- stable_preferences
- active_goals
- recurring_interests
- decision_preferences
- known_context
- knowledge_map
- tool_preferences
- uncertainty
- contradictions
- evidence_count
- last_refresh

Не выводить психиатрические диагнозы, IQ или другие скрытые ярлыки.

## P0 invariants

- cross-user leakage = 0
- secret persistence = 0
- computer-control exposure = 0
- silent paid fallback = 0
- user deletion removes authoritative + derived data
- LOCAL_ONLY never routes to cloud
- duplicate Telegram update cannot produce duplicate side effects
- persona retrieval always scoped by person_key

## Metrics

- collection recall
- candidate precision
- canonical precision
- cross-user leakage
- secret filter recall
- memory correction success
- deletion success
- retrieval usefulness
- personalization usefulness
- discovery skip rate
- local route share
- remote-free route share
- paid route share
- latency
- cost per user/day
- context tokens per answer
- persona tokens per answer
- garbage ratio
- compaction ratio

## Rollout

1. Unit/contracts on laptop.
2. GLM-5.3 remote profile.
3. Owner-only Telegram shadow.
4. Privacy/adversarial suite.
5. One invited participant.
6. Up to 3–4 invited participants.
7. Gather collection dataset.
8. Train/evaluate garbage sorter on held-out users.
9. Enable local models on AI Max.
10. Converge with post-1.6 stable.
