# Bossman 1.7 — Personal Identity Training / Jeff

**Единый общий контракт 1.7.** Эта ветка изолирована от 1.5/1.6 до отдельной convergence-проверки.

## 0. Главная идея

Jeff — отдельный Telegram AI-assistant на базе того же Bossman. Для участника он выглядит как универсальный умный чат-помощник с web, vision, file understanding, code reasoning и персональной памятью.

Каждый Telegram ID начинает с **zero-start**: никакой памяти владельца, global Bossman memory, чужих проектов или других участников.

## 1. Всё работает ЧЕРЕЗ BOSSMAN

Никакого второго самостоятельного AI-приложения.

Обязательная цепочка:

Telegram
→ Bossman transport
→ PIT participant policy
→ Bossman/Jev router
→ Bossman provider registry / web / vision brokers
→ answer
→ Bossman memory-learning pipeline.

Telegram handler не должен напрямую становиться отдельным OpenRouter/Claude/GLM клиентом в обход Bossman.

**Owner launch contract:** после завершения стартовой интеграции пользователь запускает 1.7 из обычного CMD через единый entry point:

`bossman pit start`
`bossman pit status`
`bossman pit doctor`
`bossman pit stop`

GLM завтра обязан реализовать этот subcommand поверх существующего `bossman` CLI/Command Center, а не выдавать пользователю отдельный Python/script launcher как основной путь.

## 2. Только бесплатные модели

1.7 в эксперименте работает **free-only**.

Разрешены:
- локальные модели;
- remote endpoint, который live provider catalog подтверждает как zero-cost/free;
- бесплатный web/search path.

Запрещено:
- silent paid fallback;
- endpoint с неизвестной стоимостью;
- модель, которая «обычно бесплатная», но runtime pricing этого не подтвердил;
- автоматическое списание денег.

На ноутбуке основной configured route = GLM-5.3, если его текущий endpoint удовлетворяет free-only policy. Fallback — только другие owner-allowlisted zero-cost модели.

После AI Max: `local_first_auto`, Jev сам выбирает локальную модель без owner click на каждый ответ.

## 3. Локальное хранение — отдельная папка

Все PIT-данные отделены от остальной памяти Bossman:

`<BOSSMAN_DATA_DIR>/pit-v1.7/personalities/<person_key>/`

`person_key` — HMAC Telegram ID + локальная соль, не raw Telegram ID.

Внутри:
- consent/profile;
- raw lab events, если включены;
- facts/memory candidates;
- questions/corrections;
- memory outcomes;
- summaries;
- derived rebuildable indexes;
- `security/risk.json` — локальная defensive telemetry.

PIT не импортирует старые owner/guest profiles автоматически.

## 4. Модель НЕ имеет прямого доступа к хранилищу

Ни локальная LLM, ни remote model не получают:
- filesystem path PersonaVault;
- `persona.read_own`;
- произвольные `files.*` / filesystem tools;
- risk ledger;
- список других IDs;
- raw local database.

Bossman сам читает storage и, если policy разрешает персонализацию, передаёт модели только маленький transient relevant subset.

То есть **модель не может сама открыть сохранённую папку личности**.

Для remote model отдельная policy решает, можно ли вообще передавать selected persona context. При запрете remote-personalization remote LLM видит только текущий запрос/разрешённые данные текущего turn.

## 5. Jeff public identity/privacy

Публичное имя: **Jeff**.

Jeff не раскрывает:
- текущую модель;
- provider/endpoint/backend/router;
- internal fallback chain;
- PIT/1.7 internal stage/branches/handoffs;
- любые личные данные владельца;
- другие participant profiles.

На model/meta/privacy probing сначала срабатывает deterministic `public_guard`, а не LLM.

О публичном Bossman Jeff может рассказывать доброжелательно и точно и давать:
https://github.com/molotroka123-cell/AiMaxBossman

Незавершённую возможность нельзя выдавать за готовую.

## 6. Location/device = отсутствуют

PIT participant model не получает GPS/IP/device/location tools.

Jeff не знает скрыто:
- где находится пользователь;
- где находится владелец;
- адрес;
- device metadata.

Если location нужен для рекомендации, Jeff просит человека назвать город/страну обычным сообщением.

## 7. Risk score

Некоторые privacy/meta probes дают локально:

`risk_score += 1`

Примеры:
- попытка узнать внутреннюю модель/provider;
- попытка получить данные владельца;
- попытка получить чужую память;
- попытка узнать скрытую геолокацию;
- запрос внутренних PIT/1.7 деталей.

Risk:
- хранится только в `security/risk.json`;
- не экспортируется через persona export;
- не передаётся LLM;
- не является «психологической характеристикой»;
- не даёт модели дополнительных tools;
- не уменьшает privacy.

В collection-first режиме risk может только **слегка увеличить вероятность одного обычного добровольного non-sensitive discovery-вопроса**. Лимит остаётся максимум один follow-up; sensitive-risk candidate всё равно блокируется.

## 8. Поведенческие шкалы

Кроме однонаправленного risk-score у каждого participant локально есть две обратимые operational шкалы 0–100:

### Engagement
Начинается с 50.

Растёт, когда человек:
- отвечает на optional discovery;
- сам сообщает предпочтения/цели;
- полезно исправляет Jeff;
- продолжает контекст.

Падает, когда человек:
- систематически пропускает/отклоняет discovery;
- не хочет персонализироваться.

Engagement влияет только на частоту добровольных уточнений. Низкий engagement = Jeff спрашивает меньше.

### Profile Stability
Начинается с 50.

Растёт при:
- повторном подтверждении факта;
- согласованных наблюдениях;
- полезном retrieval.

Падает при:
- исправлении памяти;
- противоречиях;
- смене предпочтений;
- устаревании.

Profile Stability влияет только на минимальный confidence памяти перед тем, как Bossman добавит её в transient context. Низкая стабильность = чаще уточнить, а не считать старую память фактом.

Обе шкалы:
- хранятся локально в `security/behavior.json`;
- могут расти и падать;
- не передаются LLM;
- не экспортируются как persona;
- не дают никаких новых прав.

## 9. Religion / politics

Jeff может обсуждать религию или политику **только если сам participant уже поднял эту тему**.

Он не должен:
- начинать политический/религиозный profiling ради заполнения persona;
- выводить взгляды из косвенных сигналов;
- использовать политические сведения для убеждения/агитации.

Если participant сам говорит о своей религии или политической позиции, это можно использовать в текущем разговоре. Durable memory возможна только при отдельном sensitive-memory opt-in и явном утверждении самого пользователя.

## 10. Role-play / parody

Jeff поддерживает opt-in режим:
- `/roleplay ...`
- `/parody ...`

Role-play включается только если participant согласен.

Режимы:
- PARODY — дружеская преувеличенная пародия;
- CHARACTER — согласованный персонаж;
- MIRROR_STYLE — лёгкое отражение манеры речи без выдачи себя за человека.

В role-play Jeff может в игровой форме узнавать **обычные/умеренно личные** предпочтения: вкусы, бытовые привычки, бюджетный диапазон, формат поездок, график, цели, уровень опыта, preferred communication style.

Role-play не является обходом privacy: secrets и чувствительные темы не вытягиваются скрытно; politics/religion — только если participant сам уже поднял тему.

## 11. Full learning mode

«Полное обучение» на первом этапе означает, что каждый содержательный turn после разрешённого onboarding проходит полный learning pipeline:

answer
→ memory extraction
→ provenance/confidence/time
→ high-recall candidate collection
→ correction/contradiction tracking
→ later retrieval outcome
→ discovery outcome
→ usefulness/noise label
→ future sorter dataset.

Это **не означает**, что мы уже переписываем веса модели после каждого сообщения.

Сначала учим:
1. что собирать;
2. что потом реально пригодилось;
3. что было шумом;
4. что устарело;
5. что пользователь исправил;
6. какой retrieval улучшил ответ;
7. какие prompts работают лучше.

Потом:
- garbage sorter;
- personalized retriever/ranker;
- DSPy/GEPA prompt optimization;
- только после доказанного baseline возможен LoRA/QLoRA на общем поведении персонализации, а не на сырых фактах конкретных людей.

## 12. Collection-first data

Собираем максимально широко допустимые NORMAL signals:
- язык/стиль/словарь;
- длина/формат ответов;
- знания/навыки;
- обучение;
- работа/проекты/workflow;
- инструменты;
- интересы;
- media taste;
- shopping criteria;
- travel style;
- food preferences;
- decision patterns;
- assistant-use habits;
- corrections;
- goals;
- self-reported relationship context;
- temporal patterns;
- device/software preferences;
- successful/rejected recommendations.

Secrets не становятся persona memory.

Sensitive durable categories остаются отдельно контролируемыми.

## 13. ChatGPT-like participant capability target

Jeff должен закрывать безопасную chat-surface функциональность современного универсального assistant:

**Обязательно на laptop/первом этапе**
- general multilingual chat;
- web search/read + sources;
- safe uploaded-file understanding для поддерживаемых не-image документов;
- code writing/review/explanation без host execution authority;
- calculator/data reasoning;
- summarization;
- translation;
- memory/personalization;
- follow-up questions;
- source-aware current answers.

**После переезда на AI Max добавить как обязательный media-блок**
- локальный fast vision по присланным JPEG/PNG/WebP;
- background visual-memory enrichment после ответа;
- Qwen image editing последнего/выбранного собственного фото через Bossman Studio;
- настоящая local image generation после live PASS.

**После стартовых модулей добавить**
- Telegram voice input → STT;
- optional voice answer → TTS;
- richer document parsing;
- better tables/data analysis;
- multi-image conversation;
- longer attachment workflows.

**Никогда для participant**
- host computer control;
- shell;
- admin console;
- owner approvals;
- trading/payment authority.

### Image generation

До переезда на AI Max image generation не подделывать.

На запрос генерации изображения laptop-mode отвечает ровно:

**«Скоро научусь, малышка 😊»**

После AI Max включается настоящий local image-generation path и этот placeholder исчезает только после live PASS.

## 14. Vision/file boundary

Vision получает только image bytes, которые прислал текущий participant.

File understanding получает только текущий user-upload через broker; модель не получает произвольный путь на диске.

Проверять:
- size limits;
- MIME/magic bytes;
- malformed files;
- archive/path traversal;
- prompt injection внутри документов как untrusted content.

## 15. Discovery Engine

Collection-first означает активное, но не бесконечное знакомство.

После полезного ответа можно выбрать максимум один optional вопрос с высоким expected information gain.

Не задавать sensitive/secret questions.
Skipped question снижает шанс повтора.
Risk score может только понизить threshold для benign discovery; лимит one-question сохраняется.

## 16. Existing Bossman reuse

Переиспользовать:
- Command Center;
- existing Telegram transport/inbox/idempotency;
- Jev;
- provider/model registry;
- cost governor;
- web adapters;
- secret Vault;
- evidence/STOP/restart patterns.

Не создавать:
- второй task engine;
- второй provider registry;
- второй owner console;
- параллельный canonical Bossman brain.

## 17. Уже написанный фундамент

`command-center/bcc/pit/`:
- identity.py
- models.py
- vault.py
- collector.py
- context.py
- participant_context.py
- router.py
- policy.py
- companion_profile.py
- discovery.py
- public_guard.py
- presentation.py
- risk.py
- behavior_scores.py
- behavior_controller.py
- moderate_discovery.py
- topic_policy.py
- roleplay.py
- roleplay_commands.py
- capabilities.py
- categories.py
- secret_filter.py
- telegram_contract.py

Tests:
`command-center/tests/test_pit_foundation.py`

CI:
`.github/workflows/v17-pit-ci.yml`

## 18. Что обязательно добавить после стартовых модулей

После первого работающего Telegram answer GLM не должен сразу переходить к косметике. Закрыть:

1. **CMD integration** — `bossman pit start/status/doctor/stop`.
2. **Crash/restart recovery** — no duplicate answer/effect.
3. **Schema migrations** — обновления persona формата без потери данных.
4. **Encrypted-at-rest review** — PIT content/backup strategy.
5. **Per-user quotas/rate limits** — messages, files, web calls, context.
6. **Circuit breakers** — broken free provider не создаёт retry storm.
7. **Attachment hardening** — size/type/traversal/decompression limits.
8. **Web prompt-injection defense** — web text всегда untrusted evidence.
9. **Context budget** — raw vault никогда целиком не грузится модели.
10. **Observability** — route/latency/errors без содержимого личных данных.
11. **Backup/restore** — локальный PIT folder восстанавливается и проверяется.
12. **Delete verification** — authoritative + derived + backup retention semantics.
13. **Consent version migration**.
14. **Cross-user fuzzing** на synthetic personas.
15. **Failure honesty** — нет vision/web/model → честный status, не fake answer.
16. **Citation integrity** для web answers.
17. **Language parity** — RU/EN и язык собеседника.
18. **Cold-start UX** — Jeff полезен даже до первой накопленной памяти.
19. **Abuse/flood protection** без утечки данных.
20. **Exact-SHA evidence** для laptop и потом AI Max.

## 19. OSS / datasets

Смотри:
- [OSS + datasets](OPEN_SOURCE_AND_DATASETS_20260925.md)
- [Pinned source revisions](contracts/source-refs.json)
- [Garbage-sorter dataset plan](DATASET_AND_GARBAGE_SORTER_RU.md)

Основные reference направления: Graphiti, LangMem, Mem0, Letta, DSPy/GEPA, LaMP/LongLaMP, PRISM, PersonaHub.

## 20. Завтра начать отсюда

[START_TOMORROW_GLM_RU.md](START_TOMORROW_GLM_RU.md)

[GLM implementation master](GLM_5_3_IMPLEMENTATION_MASTER_RU.md)

[Jeff public behavior/privacy](JEFF_PUBLIC_BEHAVIOR_RU.md)

[Latest 1.5 base alignment](BASELINE_1_5_20260925.md)

## 21. Tomorrow target

Финальный laptop-статус:

`PIT_LAPTOP_SHADOW_READY`

Только если:
- CMD path работает через Bossman;
- реальный Jeff Telegram answer;
- free-only route;
- web minimum surface;
- local PIT storage;
- two-ID isolation;
- risk local-only;
- direct-vault tools absent;
- restart/idempotency;
- memory inspect/export/delete;
- no open P0.

После первого успешного laptop-run тот же runtime/data contract переносится на AI Max и переключается в `local_first_auto`.


## 22. Photo / Vision после AI Max

Фото-функции намеренно **не являются laptop blocker**: локальной vision/image-edit модели на ноутбуке нет.

После переноса на AI Max:

`Telegram photo → Bossman verified ingest → Qwen fast vision → Jeff answer → background visual-memory analysis`.

Deep visual-memory не задерживает ответ. При занятости foreground/background enrichment ждёт или пропускается.

Редактирование:

`own verified photo → Bossman Studio reference → local Qwen image-edit job → independently verified output → Telegram`.

PIT не запускает diffusion/shell напрямую.

Использовать:
- `qwen_vision.py`
- `photo_pipeline.py`
- `studio_image_edit.py`
- `photo_edit.py`

Полный контракт: [PHOTO_PIPELINE_AI_MAX_RU.md](PHOTO_PIPELINE_AI_MAX_RU.md)

Совместимость с текущей 1.6: [COMPAT_1_6_MEDIA_20260925.md](COMPAT_1_6_MEDIA_20260925.md)

На ноутбуке:
- анализ фото честно сообщает, что функция появится после AI Max;
- генерация/редактирование: **«Скоро научусь, малышка 😊»**.
