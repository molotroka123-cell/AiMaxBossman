# OpenCode / GLM 5.3 — непротестированное + основной регресс

Это задание на реальный прогон и исправления, не сертификат завершённых эпох.
Репозиторий: `molotroka123-cell/AiMaxBossman`.
Кандидат: `integration/continuity-steward-closure-20260906`.
Основную `claude/bossman-control-v03-43igbk` и ветки Claude не перезаписывать.

## 0. Продолжить ровно отсюда

Claude исчерпал лимиты. Ты — исполнитель завершающего тестового прохода.
Сначала fetch, сравни текущие refs, сохрани незакоммиченные изменения пользователя.
Работай в отдельной ветке/worktree `glm/residual-acceptance-<date>` от кандидата.
Не делай reset --hard, clean -fd, force-push, массовые merge старых веток.
Не устанавливай все пакеты и модели заново. Проверь уже доступное окружение.

Кандидат объединяет опубликованные Fable hardening `9d3f82ac` и Steward `3f8c7171`
через `945b8f86`. Интеграция не доказывает приёмку. Прочитай:
`docs/testing/CLOSURE_CHECKPOINT_20260906.md`,
`docs/v4/EPOCH_4_PLAN.md`, `docs/v5/V5_RELEASE_SCORECARD.md`.
Исторические PASS не переносить на HEAD. Reference-код из handoffs НЕ runtime.
Не повторяй уже исправленные FFmpeg/Fleet/DOM задачи без новой репродукции.

Через доступный список моделей OpenCode найди реальный provider/model ID GLM 5.3.
Не придумывай ID и не подменяй молча модель. Запиши фактическую версию, провайдера,
параметры генерации и инструменты. При недоступности GLM — BLOCKED_MODEL;
детерминированные проверки можно продолжать. Доступ к API-ключу не равен
разрешению тратить: используй только уже согласованный лимит и разрешённые данные.

## 1. Аккуратно подготовить компьютер

Цель — убрать остатки тестов, а не выключить всё подряд.

Сначала сделай read-only снимок ОС, Python/Node, CPU, RAM, GPU/VRAM, свободного
диска, загрузки, открытых портов и процессов (PID, время создания, имя,
память, родитель). Не пиши в отчёт env, токены или полные секретные argv.
Определи реальное железо; не предполагай, что AI Max уже установлен.

Не завершай системные процессы, антивирус/Defender, firewall, драйверы, VPN,
SSH/удалённую сессию, OpenCode, текущий терминал, редакторы или браузеры с
пользовательскими данными, БД и модельные сервисы, необходимые текущему прогону.
Не меняй приоритеты ОС, swap/pagefile, питание, автозапуск и обновления.
Никаких taskkill/Stop-Process/pkill по маске python/node/chrome/ffmpeg.

Можно завершать только процессы, доказанно принадлежащие твоему тестовому запуску
или явно одобренному списку старых тестовых запусков. Перед сигналом проверь
PID + creation_time + владельца + происхождение. Сначала штатное завершение,
затем ожидание, затем принудительное завершение только того же экземпляра.
Неизвестные тяжёлые процессы оставить и вывести как OWNER_CONFIRMATION_REQUIRED.
Не сохранять/закрывать чужие документы автоматически. Не удалять проекты,
БД, модели или журналы. Очищать только созданный этим прогоном временный каталог.

`tools/pytest_watchdog.py` завершает только наблюдавшиеся дочерние процессы
своего запуска. Это не универсальный очиститель ПК и не гарантия отслеживания
демонов, которые успели переподчиниться до наблюдения. Неизвестные процессы
после прогона фиксировать, а не уничтожать.

## 2. Один реестр пробелов вместо повторного аудита всего репозитория

Составь `acceptance-matrix.json`: capability, canonical_path, new_since_base,
last_tested_sha, test_tier, previous_evidence, current_result, blocked_reason.
Сверь diff и последние журналы. Статусы:
UNTESTED / STALE_EVIDENCE / PARTIAL / FAIL / PASS / EXTERNAL_BLOCKER.

Обязательный основной smoke выполнять всегда. Остальную глубину направить на
новые/изменённые/непройденные функции и взаимодействия. Стабильные старые части
не переписывать ради стиля. Любой реальный регресс добавлять в тот же реестр.
Перед релизом всё равно выполнить полные обязательные CI-команды на одном SHA.

## 3. Изоляция и логирование

Никаких тестов на рабочей БД, пользовательских проектах, настоящих рассылках,
платежах или биржевых ордерах. Временные проекты, отдельные БД, тестовые аккаунты,
loopback/контейнеры и фальшивые внешние эффекты — с явной маркировкой MOCK.
V5 observers/admission не включать на пользовательских данных до N0/V4 acceptance.
Не превращать EXPIRED/REVOKED/DRAFT цели в ACTIVE ради демонстрации.

Журналы — вне tracked дерева, например ../bossman-acceptance/<UTC>-<SHA>/.
Обязательные файлы: manifest.json, acceptance-matrix.json, commands.jsonl,
results.json, junit/, process-before.json, process-after.json, resources.jsonl,
failures.md, OWNER_REPORT_RU.md. Для каждого запуска — code SHA, tree hash,
dirty status, окружение, команда, время, exit code, pass/fail/skip/xfail, ссылки
на артефакты и фактический уровень LIVE/IN_PROCESS/MOCK/NOT_RUN.

Ресурсы измерять с ограниченной частотой, например раз в 2 секунды: RSS, дочерние
процессы, threads/handles или FD, CPU, свободная RAM, доступные GPU метрики,
размер очереди и БД. Недоступные метрики = null/NOT_AVAILABLE, не ноль.
Не логировать скрытые рассуждения, prompts, секреты, cookie, пациентские данные.
Сырые локальные логи не коммитить. Перед публикацией отдельный secret/PII scan.
Скриншоты только тестового профиля. Логи ротировать и ограничить размер.

## 4. Основные сценарии

Холодный старт → вход → чат/миссия → выбор executor → tool call → approval при
необходимости → реальный пост-фактум результат → артефакт открывается.
Проверить отказ, отсутствующий executor/model, PRIVATE без локальной модели,
лимит бюджета, timeout, restart/resume и правдивый blocked_reason.

Файлы, память, browser, terminal, MCP, coding и медиа должны проходить общий
policy/Treasury/finalizer. Зеленый ответ модели — не доказательство.
Проверить один канонический Video Studio и Web Designer, русские подписи,
клавиатуру, масштаб 125/150/200%, back/forward и смену активной страницы.
Не выдавать headless API-тест за реальный desktop acceptance.

## 5. Новое V3/V4/V5 — приоритетные проверки

V3 hardening: context memory authority, tool pruning, crash matrix, Fleet node
revocation/replay, video chaos. Тест `test_fable_context_ablation.py` находится
в `bossman-core/tests`, поскольку импортирует Core runner. Не возвращать его
в root suite и не прятать import failure через skip.

V4 Continuity: Mission IR, authenticated journal, re-observe перед действиями,
preflight, verified skills, сохранение obligations при replanning, invalidation
старых recipes и privacy. Не придумывать новые ядра или архитектуру.

V5 Steward: ObjectiveSpec rehydration/revision, persistence/CAS, observers,
world-state freshness, dedup/reserve/admission, mission adapter, reconciliation,
revoke/expire на границе эффекта, conflict/cooldown, context и improvement gates.
Проверить H01–H10 на реальных файлах; отдельно проверить подключение production
ports. In-process тесты с fake Treasury/policy не доказывают их интеграцию.
У healthy неизменённой цели должно быть 0 лишних model calls. UNKNOWN != health.
Создание UI-инспектора не доказывает работу планировщика/наблюдателей.

Video: реальные системные ffmpeg/ffprobe, encode/probe/decode preflight, CFR и
последний кадр по пикселям, trims/duration/NaN, concurrent exports, cancel,
restart и верифицированный download. Ошибки codec и нехватку места моделировать
на ограниченных fixtures, не заполнять системный диск.

Web: source fidelity, doctype/comments/JSON-LD/scripts/SVG/templates/Unicode,
stale revisions, конфликт сохранений, preview origin/sandbox, path/symlinks,
PRIVATE routing и канонический бюджет. Старые «7 P1» перепроверить, не повторять.

Fleet: реальные loopback TLS/RPC тесты, чужой сертификат, revoke, replay,
протухший lease/fence, подмена work/owner/node, reconnect/restart и resource
admission. Remote production readiness оставить EXPERIMENTAL до полноценной
многоузловой приёмки; локальный TLS socket != проверенная production-сеть.

## 6. Команды — использовать конфигурацию каждого пакета

Установить зависимости по текущим pyproject/workflow в отдельное venv, не в
системный Python. Не менять lock/версии ради обхода теста без репродукции.
Примеры после проверки наличия файлов в текущем дереве:

```bash
python -m pytest tests/test_pytest_watchdog.py tests/test_exact_sha_certify.py -q
python -m pytest tests/test_epoch4_mission_ir.py tests/test_epoch5_objective_spec.py -q
python -m pytest tests/test_v5_*.py -q
# Из bossman-core:
python -m pytest tests/test_fable_context_ablation.py tests/test_fable_crash_matrix.py tests/test_fable_fleet_attack.py tests/test_epoch4_golden_foundation.py tests/test_epoch4_preflight.py tests/test_epoch4_verified_skills.py -q
# Из command-center:
python -m pytest tests/test_fable_video_chaos.py tests/test_video_native_finalize_contract.py tests/test_web_designer.py tests/test_web_designer_source_fidelity.py -q
```

На PowerShell glob может не раскрыться: получи список точных путей и передай
аргументами, не считай отсутствие собранных тестов PASS. Каждый pytest запуск
получает свой --junitxml. Shell-specific команды из Linux не копировать слепо.
При зависаниях используй watchdog из правильного рабочего каталога; настрой
дедлайн через BOSSMAN_WATCHDOG_TIMEOUT_SECONDS. Отсутствующий psutil — setup
blocker, не повод делать небезопасный fallback. Timeout exit=124, interrupt=130.
Полный root/Core/Command Center и обязательные workflows запускай перед freeze.
Пороги coverage не снижать, xfail/skip для реальных регрессий не добавлять.

## 7. Стресс — ступенями, без перегрузки машины

Сначала smoke и 1 worker, затем 2 и 4 только при наличии ресурса.
Сценарии: 50 действий → 100 → 200, повторные миссии, 1000 bounded duplicate
событий, смена model availability, отмена, DB lock, смерть только тестового
worker/browser/FFmpeg, crash на dispatch/effect/journal/verification.
Не жди необоснованно 24 часа в короткой сессии: 15 минут → 60 минут при бюджете;
24h требует реально прошедших 24h и отдельного журнала, иначе NOT_RUN.

По умолчанию прекращай нарастание нагрузки при RAM >85%, свободной RAM ниже
max(1 GiB, 15% total), свободном диске ниже max(5 GiB, 10%), температурном
throttling или потере управления. Это начальные защитные лимиты, уточни по
железу до теста. Не выгружай нужную модель/БД посреди доказательного запуска.
Никакого stress-to-OOM на машине владельца. Disk-full и вредные network cases
инъектировать в fixtures. Все попытки, включая ошибки и отмены, остаются в отчёте.
После каждой ступени сверять процессы/ресурсы, отсутствие утечек и повторных
необратимых эффектов. Дубликат внешнего эффекта или обход политики = STOP/P0.

## 8. Intelligence Preservation — реальные измерения, а не подставленный JSON

Замерять одну и ту же модель/config/quantization на одних задачах в RAW, SYSTEM,
CONTEXT, FULL. Для reasoning сравнение без tools; для tool tasks одинаковые
доступные реальные affordances и budget. Router сначала зафиксировать; его
выбор разных моделей оценивать отдельно. Минимум категорий reasoning, coding,
structured output, tool selection/arguments, memory, adaptation.

Порог retention >=0.98 — относительный, не «минус 2 процентных пункта».
Не скрывать регрессию категории средним. Парные повторы, доверительные интервалы,
held-out задачи, все timeout/failed attempts, отдельно стоимость/латентность.
20 примеров — smoke, не доказательство 2% неухудшения. Если данных/денег/модели
нет — INSUFFICIENT_EVIDENCE. Не менять веса модели и не подделывать измерения.

Замеры хранить как артефакт для фиксированного code SHA. Не коммитить файл,
который должен содержать хеш собственного ещё не созданного коммита: это
самоссылочный тупик. Если текущий workflow требует tracked evidence для своего
же HEAD, исправить доставку через SHA-bound artifact + digest/provenance и тест
на stale/missing evidence; не ослаблять проверку --expect-sha.

## 9. Исправления, push, завершение

Один реестр, reproduce → причина → минимальный fix → regression → hostile test.
Не добавлять V6, косметический rewrite и новые фичи вместо закрытия пробела.
Каждый законченный checkpoint — commit/push своей ветки; перед push fetch и
semantic reconciliation. Не менять ветки других исполнителей. Новые работы
через PR в кандидат. При возобновлении Fable он следует этому же протоколу.

В конце заморозить один SHA: full CI + выбранные live/chaos/GLM сценарии именно
на нём. PR merge SHA и head SHA различать. Отчёт после freeze — CI artifact или
PR comment, чтобы docs-only commit не менял сертифицированный SHA.

Финальный отчёт владельцу на русском: START_SHA, FINAL_SHA, branch/PR, включённые
и НЕ включённые ветки, сколько функций проверено впервые, какие основные
повторены, исправленные баги с репродукциями, commands/run IDs, pass/fail/skip,
что было реальным, что mock, ресурсы/остаточные процессы, затраты, открытые P0/P1,
owner-only блокеры и честный вердикт. Не объявлять V3/V4/V5_COMPLETE по числу тестов.
