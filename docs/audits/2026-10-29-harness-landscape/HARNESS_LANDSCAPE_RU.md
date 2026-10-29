# Кто сейчас лидирует в agent harness и как с ними соотносится Bossman (29.10.2026)

Вопрос владельца: «Кто щас топ в разработке harness, сравни с нашим по минимум 20 пунктам».

**Как сделано.** Данные о лидерах взяты из веба 29.10.2026: документация вендоров, лидерборды бенчмарков, страницы GitHub. Ссылки на источники собраны в разделе 6. Bossman оценён только по файлам этого worktree
(`wt-bossman-20-argon`): README, `bossman_20.py`, `pc_agent.py`, `terminal_control.py`, `astra_sandbox.py`,
`tools/coding_swarm.py`, `tools/resource_governor/`, квитанции в `docs/audits/` за 24–29.10, `KNOWN_LIMITATIONS.md`,
`CLAIMS_NOT_PROVEN.md`. Чаты, личные данные, строки `bcc.db`, vault, ключи и фото не открывались.

**Ограничения.** Ячейки про конкурентов опираются на документацию, а не на наш прогон. «н/д» означает, что
первоисточник мы не проверили; так помечено вместо догадки. Цифры с агрегаторов (Codesota, Morph, Firecrawl)
вторичны и помечены отдельно.

---

## 1. Кто лидирует сейчас

### 1.1. Бенчмарки, где оценивается связка harness + модель

| Бенчмарк (дата среза) | Топ | Что из этого следует |
|---|---|---|
| **Terminal-Bench 4.0**, официальный лидерборд tbench.ai / Snorkel (записи до 29.09.2026) | 1. **Claude Code** + Opus 5.5: 64.8% ±3.1; 2. Claude Code + Sonnet 5.5: 61.8%; 3–4. **Codex** + GPT-6 Astra / GPT-6.1 Sol: 58.2%; 10. Grok Build + Grok 4.7: 37.6% | В топ-15 только три harness: Claude Code, Codex и Grok Build. Claude Code с чужими моделями тоже в списке: GLM-5.3 даёт 41.8%, GLM-5.3-Flash 35.8%, Qwen3.8-Max 27% [S1] |
| **Terminal-Bench 4.0**, Vals AI, один harness mini-SWE-agent (27.10.2026) | Opus 5.5: 65.15%; Sonnet 5.5: 64.14%; **Kimi K3.1 Pro: 63.64% при $4.57 за тест**; Gemini 4 Carbon: 63.13% | При одинаковом harness разрыв между моделями мал, а цена различается в 3–4 раза. Kimi K3.1 Pro у нас уже подключён как агент 47 [S2] |
| **Terminal-Bench 2.0** (агрегатор Codesota, срез 27.04.2026) | Codex + GPT-5.5: 82.0%; ForgeCode + GPT-5.4: 81.8%. Лучший открытый вариант, OpenHands + Opus 4.5, набрал 51.9% | Агрегатор называет это «harness gap ~30 п.п.». Обёртка вокруг модели заметно влияет на результат [S3] |
| **OSWorld-Verified**, computer use (01.10.2026) | Intelligence-Indeed Agent: 90.19%; Claude Fable 5: 86.0%; Opus 5: 83.4%. Базовый уровень человека 72.36% | Агенты для GUI уже превзошли человека на этом наборе [S4] |
| **OpenHands Index**: SWE-bench Verified, Commit0, SWE-bench Multimodal, SWT-Bench, GAIA (май 2026) | Opus 4.7: 68.2%; Opus 4.6: 66.7%; GPT-5.5: 65.9% | Это открытый многоосевой индекс. Ни одна модель не лидирует на всех осях [S5] |

SWE-bench Verified для сравнения harness почти бесполезен: он насыщен, а в выдаче разных агрегаторов от
октября 2026 цифры противоречат друг другу [S6].

### 1.2. Восемь лидеров, взятых в сравнение

| # | Harness | Почему он в списке | Лицензия / популярность |
|---|---|---|---|
| 1 | **Claude Code + Claude Agent SDK** (Anthropic) | №1 на Terminal-Bench 4.0. Самые развитые hooks, субагенты, workflows, auto mode, Remote Control [S1][S7–S12] | проприетарный |
| 2 | **Codex** (OpenAI: CLI, приложение, облако) | №1 на TB 2.0, №3–4 на TB 4.0. Нативная песочница на Windows. Более 3 млн разработчиков в неделю на апрель 2026 [S3][S13–S15] | Apache-2.0 (CLI), 131.5k★ |
| 3 | **Antigravity CLI / 2.0** (Google) | Заменил Gemini CLI: бесплатный тариф переехал туда 18.06.2026. Общий harness у CLI, IDE и десктопа [S16][S17] | Gemini CLI: Apache-2.0, 108.6k★; Antigravity: н/д |
| 4 | **Cursor** (агент и облачные агенты) | Лидер среди IDE. Облачные агенты по событиям, асинхронные деревья субагентов [S18][S19] | проприетарный |
| 5 | **OpenHands** (Agent Canvas + SDK) | Главный открытый harness для SWE, ведёт собственный публичный индекс, Docker-песочница [S5][S20] | MIT, 92.2k★ |
| 6 | **OpenCode** | Самый популярный открытый кодинг-агент 2026 года, работает с 75+ провайдерами [S21][S22] | MIT, 222.9k★ |
| 7 | **Hermes Agent** (Nous Research) | Лидер по самообучению: навыки создаются из опыта и улучшаются. Telegram и другие мессенджеры, cron [S23] | MIT, 258.4k★ |
| 8 | **OpenClaw** | Самый популярный local-first персональный агент: 20+ мессенджеров, нативные приложения [S24] | MIT, 393.3k★ |

Профиль Bossman (локальный оператор ПК, Telegram-пульт, мобильный пульт) ближе всего к **OpenClaw и Hermes**.
По механике harness (права, песочница, верификация) ближе всего к **Claude Code и Codex**. Поэтому в таблице все восемь.

---

## 2. Таблица сравнения: 31 критерий

Обозначения: ✅ есть, по первоисточнику; ◐ частично или с оговоркой; ❌ нет; н/д — не проверено.
У Bossman в скобках статус по репозиторию: **P** = PROVEN (живая квитанция), **B** = BETA (код и тесты, но живой путь
не закрыт), **NP** = NOT_PROVEN.

Колонки: **CC** Claude Code · **CX** Codex · **AG** Antigravity · **CU** Cursor · **OH** OpenHands · **OC** OpenCode ·
**HE** Hermes · **OW** OpenClaw · **BM** Bossman.

| # | Критерий | CC | CX | AG | CU | OH | OC | HE | OW | **BM (статус, улика)** |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Цикл агента (tool loop) | ✅ [S7] | ✅ [S13] | ✅ [S16] | ✅ [S18] | ✅ [S20] | ✅ build/plan [S21] | ✅ [S23] | ✅ [S24] | ✅ **P**: PC-Agent P0–P5 (`2026-10-26-argon-pc-agent-master`) |
| 2 | Модель прав | ✅ режимы manual/plan/acceptEdits/auto/bypass, allow/ask/deny, managed settings [S7] | ✅ on-request/never/гранулярно [S14] | ◐ trusted folders в Gemini CLI [S17] | ✅ доступ к сети и файлам [S19] | ◐ н/д | ◐ plan read-only + запросы [S21] | н/д | ◐ pairing неизвестных отправителей [S24] | ✅ **P**: уровни T0 (авто), T1 (авто с откатом), T2 (кнопка владельца), T3 (никогда) + автономия L0–L3, пин конституции (`pc_agent.py`) |
| 3 | Одобрение привязано к содержимому действия | ◐ правила по аргументам, разбор составных команд [S7] | ◐ auto-review [S14] | н/д | н/д | н/д | н/д | н/д | н/д | ✅ **B→P**: одобрение одноразовое, привязано к task/run/step и **sha256 payload** (`_compute_step_payload_sha256`, коммит `c27c6d39`), CAS. Живой e2e через пульт не показан |
| 4 | Песочница на уровне ОС (файлы) | ✅ Seatbelt / bubblewrap; **нативная Windows без песочницы** [S8] | ✅ Seatbelt, Landlock+bwrap, **Windows (MXC/legacy)** [S14][S15] | ◐ «изолированные окружения» [S16] | ✅ [S19] | ✅ Docker [S20] | н/д | ✅ Docker/SSH/Modal/Daytona/Vercel Sandbox [S23] | ◐ по настройке; по умолчанию инструменты на хосте [S24] | ❌/**B**: настоящей ФС-изоляции нет. Job Object ограничивает процессы, RAM и время (`astra_sandbox.py`). Выполнение кода модели в self-modify **заблокировано** до появления ОС-изоляции (`2026-10-27-redteam-self-mod`) |
| 5 | Контроль сетевого выхода | ✅ прокси + allowlist доменов [S8] | ✅ по умолчанию выключено, allowlist, защита от DNS rebinding [S14] | н/д | ✅ [S19] | ◐ Docker | н/д | ◐ через backend | н/д | ◐ **P** для облака: облачные модели только для чата, без доступа к ПК (`2026-10-24-cloud-no-pc`). Allowlist сети для команд **нет** |
| 6 | Kill switch / STOP | ◐ прерывание сессии | ◐ | н/д | ◐ | ◐ | ◐ | н/д | н/д | ✅ **P**: глобальный STOP fail-closed для всех пультов. Сбрасывает доверие T2, убивает дочерние процессы, отменяет задачи (`2026-10-26-integrator-stop-unification`) |
| 7 | Очистка дерева процессов | ✅ через ОС-песочницу [S8] | ✅ [S14] | н/д | н/д | ✅ контейнер | н/д | ✅ контейнер | н/д | ◐ **B**: Job Object с `KILL_ON_JOB_CLOSE` в `pc.run_command` (`a19ecd49`). В README: «часть блоков ещё не подключена везде». Орфаны проверены только юнит-тестом |
| 8 | Верификация, постусловия | ✅ hooks PostToolUse / Stop [S9] | ◐ | ◐ hooks [S16] | ◐ hooks в плагинах [S18] | ◐ | н/д | н/д | н/д | ◐ **P**: `execute-step` проверяет постусловие на ФС (`file_exists`/`file_sha256`/`file_contains`). Неизвестный тип — fail-closed. Но проверка идёт **после** шага, и поведение (тест) не проверяется |
| 9 | Квитанции, журнал аудита | ◐ OpenTelemetry, логи сессий [S12] | ◐ логи | н/д | н/д | ◐ | н/д | ◐ траектории | н/д | ✅ **P (сильнейшая сторона)**: sha256-квитанция на шаг, JSON-квитанции с `runtime_sha`, exact-SHA сертификация CI. `audit.jsonl` защищён в 20 из 20 сценариев (это **B**) |
| 10 | Чекпоинты и откат файлов | ✅ /rewind, checkpointing [S10] | ◐ git | ✅ checkpointing в Gemini CLI [S17] | ✅ | ◐ | ◐ | н/д | н/д | ✅ **P**: бэкап с `sha256_before` перед T1/T2, корзина, откат self-modify + 5-минутный health-guard |
| 11 | Контекст: компакция, бюджет | ✅ авто-компакция, повторная подгрузка правил, 1M контекст [S11] | ✅ | ✅ кэш токенов [S17] | ✅ | ✅ condenser | ✅ | ✅ сводка сессий LLM-ом [S23] | н/д | ◐/**NP**: преамбула из последних ходов (`conversation_context.py`), `context_os` с бюджетом токенов. Авто-компакция не доказана |
| 12 | Долговременная память | ✅ CLAUDE.md + auto memory [S11] | ✅ memory (апрель 2026) [S15] | ✅ GEMINI.md [S17] | ◐ rules | ◐ | ◐ | ✅ постоянная память + FTS по сессиям [S23] | ✅ на своём железе [S24] | ✅ **P**: SQLite (facts/decisions/procedures/failures) + Obsidian, секреты только как `[REDACTED]` (`2026-10-24-functions`) |
| 13 | Обучение на шагах, самоулучшение навыков | ◐ auto memory | ◐ «учится на прошлых действиях» [S15] | н/д | ❌ | ❌ | ❌ | ✅ **навыки создаются и уточняются сами** [S23] | ◐ | ◐ **P/NP**: урок пишется после каждого шага (живая квитанция `harness_coder`: +4 записи каждого типа). Recall — поиск по подстроке (ILIKE). **Прирост качества не измерен** (KNOWN_LIMITATIONS: LOCAL_LEARNING_GAIN_NOT_MEASURED) |
| 14 | Субагенты, параллелизм | ✅ субагенты, agent teams, dynamic workflows [S9] | ✅ [S15] | ✅ мультиагентность «first-class» [S16] | ✅ асинхронные деревья субагентов [S19] | ✅ | ✅ general [S21] | ✅ изолированные субагенты [S23] | н/д | ◐ **B**: рой `coding_swarm` (автор + кросс-семейные ревьюеры + интегратор, квитанция `2026-10-29-t5-swarm`). Изолированного контекста субагента внутри рантайма нет |
| 15 | Маршрутизация между моделями | ◐ в основном Claude; чужие модели через gateway [S1] | ◐ OpenAI | ◐ Gemini | ✅ | ✅ любой LLM [S20] | ✅ 75+ провайдеров [S22] | ✅ [S23] | ✅ [S24] | ✅ **P**: локальная → бесплатная → платная с fallback. Free-модель после лимита уходит на локальную, а не на платную. Голосование трёх моделей (**B**) |
| 16 | Контроль стоимости, жёсткие лимиты | ◐ учёт и аналитика [S12] | ◐ | н/д | ◐ | ◐ | н/д | н/д | н/д | ✅ **P**: жёсткие лимиты в $ на провайдера. `billing_unknown` → fail-closed, сверку делает владелец (`67305d9d`). `SpendFirewall` включается по желанию |
| 17 | Очистка секретов и PII перед облаком | ◐ запрет и маскирование учётных данных в песочнице [S8] | н/д | н/д | н/д | н/д | н/д | н/д | ◐ «входящее = недоверенное» [S24] | ✅ **B+**: `ContextSanitizer`: allowlist = `git ls-files`, скан секретов, редакция дома, e-mail, LAN/tailnet IP, защита holdout. 57 тестов. Стоит перед **каждым** облачным вызовом роя |
| 18 | Local-first, офлайн-модели | ◐ | ◐ | ❌ | ❌ | ✅ | ✅ локальные провайдеры | ✅ | ✅ «state/credentials on your hardware» [S24] | ✅ **P (лидерство)**: Ollama, локальное зрение, sd-cli на Vulkan, Whisper. Облако только для чата |
| 19 | Computer use (GUI) | ✅ из CLI [S12] | ✅ фоновый, свой курсор [S15] | н/д | н/д | ◐ | ❌ | н/д | н/д | ✅ **P (Windows)**: UI Automation без ловушки Alt, окна (`argon-pc-agent-master`). Живой прогон 12/12 был на прежней линии (scorecard 22.09) |
| 20 | Работа с браузером | ✅ Chrome [S12] | ✅ встроенный браузер [S15] | ✅ | ✅ | ✅ | н/д | н/д | н/д | ✅ **P**: `/browser`, `browser.active_tab_info` (только чтение, только loopback) |
| 21 | Безопасность самоизменения | — (harness себя не правит) | — | — | — | — | — | ◐ правит только навыки [S23] | — | ◐ **P/NP**: предложение → T2 с одобрением владельца, неизменяемое ядро, откат, health-guard (**P**). Полный цикл самопочинки D-036 **NP**: локальные воркеры патч не дали |
| 22 | Публичные бенчмарки | ✅ TB 4.0 №1 [S1] | ✅ TB 2.0/4.0 [S1][S3] | ◐ модель Gemini, не harness [S2] | ◐ | ✅ свой индекс [S5] | н/д | ❌ | ❌ | ❌ **нет ни одного публичного числа** |
| 23 | Внутренний eval с закрытым holdout | н/д | н/д | н/д | н/д | ✅ OpenHands Index | н/д | н/д | н/д | ◐ **B**: eval500 (350 dev + 150 holdout, запечатан sha256 до любых улучшений). Базовый прогон есть, улучшение по нему не показано |
| 24 | Удалённый, мобильный доступ | ✅ Remote Control: телефон/браузер к локальной сессии [S12] | ✅ приложение и облако | ✅ десктоп + CLI | ✅ облачные агенты | ✅ веб | ◐ десктоп | ✅ мессенджеры | ✅ iOS/Android [S24] | ◐ **P (Chrome)**: мобильный пульт :8840 с push-to-talk. На **iPhone не доказано** |
| 25 | Мессенджеры (Telegram и др.) | ◐ channels через MCP [S12] | ❌ | ❌ | ◐ Slack [S19] | ◐ Slack/GitHub/Linear [S20] | ❌ | ✅ Telegram/Discord/Slack/WhatsApp/Signal [S23] | ✅ 20+ каналов [S24] | ✅ **P**: Telegram-пульт (управление) + Jeff (участник), голос |
| 26 | MCP, плагины, экосистема | ✅ MCP + плагины + маркетплейс [S12] | ✅ 90+ плагинов [S15] | ✅ плагины [S16] | ✅ плагины [S18] | ✅ | ✅ | ✅ MCP [S23] | ✅ ClawHub [S24] | ◐ **B**: MCP-клиент + каталог из 10 серверов с закреплёнными версиями, санитизация описаний (`mcp_catalog.py`). Экосистема мала |
| 27 | Фоновая автономия, расписание | ✅ routines, scheduled tasks [S12] | ✅ «ongoing work» [S15] | ✅ асинхронные фоновые агенты [S16] | ✅ подписки на события [S19] | ✅ Automation Server [S20] | н/д | ✅ cron [S23] | н/д | ◐ **B**: задачи планировщика `BossmanAgent-*`, ночные прогоны. Суточный soak **не доказан** |
| 28 | Управление ресурсами и GPU | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ **P (уникально)**: аренда памяти по P95, fencing-токены, вытеснение по приоритету, memory guard, восстановление после рестарта (`resource_governor/leases.py`, квитанция `music_ondemand_receipt`) |
| 29 | Платформы | macOS/Linux/WSL2, Windows без песочницы [S8] | macOS/Linux/Windows [S13] | н/д | все | Docker, все | все [S21] | Linux/VPS/облако [S23] | все + мобильные [S24] | ❌ **только Windows 11, одна машина владельца** |
| 30 | Открытость, сообщество | закрыт | Apache-2.0, 131k★ | н/д | закрыт | MIT, 92k★ | MIT, 223k★ | MIT, 258k★ | MIT, 393k★ | ◐ AGPL-3.0 в LICENSE, но сообщества и внешних контрибьюторов нет |
| 31 | Многопользовательский режим, политики организации | ✅ managed settings [S7] | ✅ Enterprise | ✅ Enterprise | ✅ команды | ✅ self-hosted | ◐ | ◐ | ◐ осторожно с удалённым gateway [S24] | ❌ один владелец, по замыслу |

---

## 3. Где Bossman впереди

1. **Доказательность.** Правило «нет квитанции — `NOT_PROVEN`», sha256-квитанция на каждый шаг, exact-SHA
   сертификация, честные отзывы прежних заявлений (Video Studio «7 из 7» на деле 0 из 7, отзыв QLoRA-турнира). Похожего
   публичного «права на заявление» у лидеров нет: у них логи и телеметрия, а не квитанции.
2. **Привязка одобрения к sha256 payload, одноразовость, CAS.** У лидеров правила по шаблонам команд и
   классификатор (auto mode, auto-review). Одобрения точных байтов действия мы у них не нашли.
3. **Аренда GPU и памяти.** Ни у одного лидера нет арбитража памяти между LLM, видео и музыкой на одной машине.
4. **Local-first и жёсткие денежные лимиты.** Цепочка локальная → бесплатная → платная, без тихого перехода на
   платную модель. `billing_unknown` → fail-closed, сверку делает владелец.
5. **Санитайзер контекста перед облаком.** Allowlist по `git ls-files` + PII/секреты + защита holdout. У лидеров
   есть маскирование учётных данных в песочнице, но не фильтр исходящего промпта.
6. **Единый STOP для всех пультов** и контур самоизменения с неизменяемым ядром, откатом и health-guard.

## 4. Где Bossman отстаёт (честно)

1. **Нет публичного бенчмарка.** Ни Terminal-Bench, ни OSWorld, ни SWE-bench. Сравнить себя с лидерами числом
   мы сейчас не можем. eval500 внутренний, и улучшение по holdout не показано.
2. **Нет настоящей ОС-песочницы и контроля сети для команд.** Это главный технический пробел: Codex
   изолирует команды даже на нативной Windows, у Claude Code и OpenHands есть изоляция ФС и сети. У нас только Job Object
   (лимиты и kill), а выполнение кода модели в self-modify честно заблокировано.
3. **Самообучение не меняет результат.** Уроки пишутся, но recall — поиск по подстроке, а прирост качества не
   измерен. Hermes превращает опыт в исполняемые навыки, у нас уроки остаются текстом.
4. **Управление контекстом.** Нет доказанной авто-компакции и изоляции контекста субагентов. У лидеров это
   базовые функции.
5. **Верификация после факта.** `execute-step` проверяет файл после шага (`file_contains` по коду), а не
   поведение (тест) и не перед действием. Hooks уровня PreToolUse/PostToolUse нет.
6. **Самопочинка не доказана** (D-036: локальные воркеры за 15–21 шаг не записали патч).
7. **Охват.** Только Windows 11, один пользователь, одна машина. Экосистема MCP и плагинов мала, сообщества нет.
   Мобильный пульт доказан в Chrome, на iPhone нет. Суточный soak не проведён.
8. **Часть защиты Windows в статусе BETA** (Job Object «подключён не везде», орфаны проверены только юнит-тестом).

## 5. Топ-5 того, что стоит взять у лидеров (сначала open source)

| # | Что взять | Откуда (лицензия) | Зачем и как проверить |
|---|---|---|---|
| 1 | **ОС-песочница для `pc.run_command` и self-modify** с запретом сети по умолчанию и allowlist доменов | Песочница Windows из **Codex CLI** (Apache-2.0, `openai/codex`) [S14][S15]. Как запасной вариант — WSL2 + bubblewrap или Docker `--network=none`, по образцу runtime **OpenHands** (MIT) [S20]. Уже готовый детектор в `astra_sandbox.py` | Закрывает пробел №2 и снимает блок `verification_blocked_unisolated`. Проверка: escape-тесты (canary вне каталога, исходящая сеть, дочерний процесс после выхода) должны падать **до** включения |
| 2 | **Публичный замер на Terminal-Bench** | Открытый harness и задачи tbench.ai [S1]. Baseline — mini-SWE-agent, как у Vals [S2] | Первое внешнее число. Прогнать harness Bossman с локальной Qwen и с Kimi K3.1 Pro против mini-SWE-agent с той же моделью: так измеряется **наш** вклад harness, а не модели. Публиковать вместе с квитанцией |
| 3 | **Навыки из опыта вместо текстовых уроков** | Цикл skills в **Hermes Agent** (MIT) [S23] | Урок → исполняемый навык (скрипт или процедура с тестом), допуск только через holdout eval500. Это прямой ответ на измеренное «recall 100%, а результат не изменился» |
| 4 | **Hooks жизненного цикла + проверка до действия** (PreToolUse / PostToolUse / Stop) и трассировка OpenTelemetry | Модель hooks из **Claude Code** как спецификация [S9]; hooks в **Gemini CLI / Antigravity** (Apache-2.0) [S16][S17]; стандарт OpenTelemetry [S12] | `execute-step` перестаёт быть только post-hoc. Постусловие типа «тест прошёл» (pytest node id) вместо `file_contains`. Трейсы вместо разрозненных логов |
| 5 | **Компакция контекста и изоляция субагентов** | **OpenCode** (MIT) [S21] и condenser **OpenHands** (MIT) [S20]. Таблица «что переживает компакцию» у Claude Code — готовая спецификация [S11] | Длинные задачи PC-агента и роя без потери правил: после компакции заново подгружаются конституция, STOP и права. Проверка: длинная задача на eval500, чтобы правила T3 срабатывали и после компакции |

Ещё стоит рассмотреть: классификатор одобрений для рутинных T1/T2 по образцу auto mode у Claude Code и auto-review у Codex
[S7][S14], чтобы владелец получал меньше запросов. И паттерн каналов-мессенджеров из **OpenClaw** (MIT) с pairing
неизвестных отправителей [S24] для Jeff.

**Итог одной строкой.** По честности статусов, привязке одобрений, локальности и управлению ресурсами Bossman
сильнее лидеров. По песочнице, публичным бенчмаркам, управлению контекстом, самообучению с измеримым эффектом и охвату
платформ заметно слабее. Первые два пункта плана (песочница и Terminal-Bench) дают наибольший эффект.

---

## 6. Источники

- [S1] Terminal-Bench 4.0, официальный лидерборд: https://snorkel.ai/leaderboard/terminal-bench/ (также https://www.tbench.ai/leaderboard/terminal-bench/2.0)
- [S2] Vals AI, Terminal-Bench 4.0 (mini-SWE-agent, 27.10.2026): https://www.vals.ai/benchmarks/terminal-bench-4
- [S3] Codesota, Terminal-Bench 2 (агрегатор, срез 27.04.2026): https://www.codesota.com/benchmark/terminal-bench
- [S4] OSWorld-Verified: https://www.llmreference.com/benchmark/osworld-verified , https://yutori.com/leaderboards/osworld-verified.md
- [S5] OpenHands Index: https://openhands.dev/blog/introducing-the-openhands-index , https://benchlm.ai/md/benchmarks/openhandsindex.md
- [S6] SWE-bench (агрегаторы): https://www.developersdigest.tech/benchmarks/swe-bench , https://aicodereview.cc/blog/swe-bench-scores-leaderboard
- [S7] Claude Code, права и режимы: https://code.claude.com/docs/en/permissions
- [S8] Claude Code, песочница: https://code.claude.com/docs/en/sandboxing
- [S9] Claude Code, hooks / субагенты / workflows: https://code.claude.com/docs/en/hooks.md , https://code.claude.com/docs/en/sub-agents.md , https://code.claude.com/docs/en/workflows.md
- [S10] Claude Code, checkpointing: https://code.claude.com/docs/en/checkpointing.md
- [S11] Claude Code, контекстное окно и компакция: https://code.claude.com/docs/en/context-window.md
- [S12] Claude Code, индекс документации (Remote Control, computer use, Chrome, MCP, плагины, routines, мониторинг OTel, channels): https://code.claude.com/docs/llms.txt , https://code.claude.com/docs/en/remote-control.md
- [S13] Codex CLI, GitHub: https://github.com/openai/codex
- [S14] Codex, approvals и sandbox: https://learn.chatgpt.com/docs/agent-approvals-security , https://learn.chatgpt.com/docs/security
- [S15] OpenAI, «Codex for (almost) everything» (16.04.2026): https://openai.com/index/codex-for-almost-everything/
- [S16] Antigravity 2.0 / CLI: https://www.analyticsvidhya.com/blog/2026/05/google-antigravity-2-0/
- [S17] Gemini CLI, GitHub (статус переезда на Antigravity): https://github.com/google-gemini/gemini-cli
- [S18] Cursor 2.5 (плагины): https://cursor.com/changelog/2-5
- [S19] Cursor, облачные агенты, песочница, асинхронные субагенты: https://cursor.com/changelog/08-19-26 , https://cursor.com/changelog/page/8
- [S20] OpenHands, GitHub: https://github.com/OpenHands/OpenHands
- [S21] OpenCode, GitHub: https://github.com/anomalyco/opencode
- [S22] Обзор кодинг-агентов (вторичный): https://www.firecrawl.dev/blog/best-ai-coding-agents , https://www.truefoundry.com/blog/best-open-source-agent-harness
- [S23] Hermes Agent, GitHub: https://github.com/NousResearch/hermes-agent
- [S24] OpenClaw, GitHub: https://github.com/openclaw/openclaw
- Goose (Apache-2.0, AAIF / Linux Foundation, 55.9k★) рассмотрен, но в восьмёрку не вошёл: https://github.com/block/goose

**Улики Bossman (в этом worktree):** `README.md`; `command-center/bcc/features/bossman_20.py` (стр. 805–871);
`command-center/bcc/features/pc_agent.py` (T0–T3, `_compute_step_payload_sha256` стр. 5779+, rollback стр. 910+);
`command-center/bcc/v2/terminal_control.py`; `command-center/bcc/security/astra_sandbox.py`; `tools/coding_swarm.py`;
`tools/resource_governor/leases.py`; `docs/audits/2026-10-29-harness-trio/` (harness_coder_receipt.json,
codex_harness_report.md); `docs/audits/2026-10-29-t5-swarm/receipt.json`; `docs/audits/2026-10-28-self-repair-d036/`;
`docs/audits/2026-10-27-redteam-self-mod/`; `docs/audits/2026-10-25-eval500-baseline/`; `KNOWN_LIMITATIONS.md`;
`CLAIMS_NOT_PROVEN.md`.
