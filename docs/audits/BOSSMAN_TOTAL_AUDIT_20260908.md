# AiMaxBossman — независимый аудит от первого коммита до 8 сентября 2026

Дата проверки: 8 сентября 2026. Аудит выполнен основным проверяющим и двумя независимыми агентами. Исходники не изменялись, push/merge и обращения к платным моделям не выполнялись. Проверка охватывает историю, код выбранных границ, CI на точных SHA и целевые локальные тесты. Это не заявление о построчном прочтении всех файлов или полном испытании машины владельца.

## 1. Главный вывод

Bossman существенно вырос в инженерных возможностях, но актуальный продукт нельзя честно назвать полностью замороженным или production-ready. Обнаружены воспроизводимые дефекты авторизации и завершения, красные CI и завышенная интерпретация синтетических benchmark-результатов. При этом многие реальные исправления находятся в интеграционном кандидате, а не в default-ветке.

Предварительная экспертная оценка по рубрике ниже: **около 7000/10000 для default `da67a63` и около 7600/10000 для кандидата `d021b99`**. Это диагностическая оценка инженерной готовности, не процент готовности, не измеренный интеллект и не рейтинг относительно конкурентов. Точность до десятков баллов была бы ложной; разумная экспертная неопределенность — несколько сотен баллов.

Оценка 7200/10000 восстановлена через память из ответа аудитора от 2 сентября 2026, 20:34 UTC, на `d5480b3`: «репозиторий целиком 7,2/10», безопасность 7,8; UCA 6,2; память/контекст/reasoning 6,8; benchmark 4,5; production NO-GO. Старые веса не сохранены, поэтому разницу с новой рубрикой нельзя считать точным измерением роста. Снижение оценки default отражает обнаруженные дефекты, а не доказанное ухудшение каждого компонента после 2 сентября.

## 2. Что именно считается текущим

| Срез | SHA | Коммитов в достижимой истории | После baseline |
|---|---|---:|---:|
| Первый коммит | `1e6c8c564690b0f9a9701033f7be1ee1417384ea` | 1 | — |
| Прошлый аудит 7,2/10 | `d5480b3dccb367b830b4f0344c94544058483867` | 452 | 0 |
| GitHub default: `claude/bossman-control-v03-43igbk` | `da67a63251ab2ffb5d20a624772d2cb9a0427365` | 776 | +324 |
| Отдельная ветка `main` | `799fc3dd8e4327811be9d8f3e33cc43ce8168977` | 764 | +312 |
| Интеграционный кандидат PR #58, `night/v7-convergence-20260908` | `d021b99c628d9329fa27c896da3fc6dbcbb1d133` | 967 | +515 |

Default и кандидат расходятся: **18 коммитов только в default и 209 только в кандидате**. Это не линейное обновление default. PR #58 остается draft; при проверке GitHub показывал `mergeable=true`, `mergeable_state=unstable`. Старое утверждение отчета об обязательном gateway merge conflict поэтому нельзя переносить на актуальное состояние без проверки.

Ссылки: [baseline](https://github.com/molotroka123-cell/AiMaxBossman/commit/d5480b3dccb367b830b4f0344c94544058483867), [default](https://github.com/molotroka123-cell/AiMaxBossman/commit/da67a63251ab2ffb5d20a624772d2cb9a0427365), [кандидат PR58](https://github.com/molotroka123-cell/AiMaxBossman/pull/58).

## 3. История развития и качество прироста

Первый коммит датирован **27 августа 2026, 23:10 UTC** (28 августа, 01:10 по Праге): инфраструктура и ядро агентов, 65 файлов, 24 Python-файла, 4 файла в тестовых каталогах.

| Период | Что появилось в истории |
|---|---|
| 27–29 августа | Command Center, страницы UI, gateway, инструменты/браузер, sandbox, базовые границы разрешений и первые security hardening |
| 30 августа – 2 сентября | Долговременная рабочая память, контекст, маршрутизация моделей, benchmark/learning guard, recovery и long-horizon freeze foundation; baseline 7200 |
| 3–5 сентября | Универсальный action contract, верификация эффектов, подписи evidence, fencing, Fleet/Organization, казначейство, наблюдаемость, существенная red-team доработка |
| 6 сентября | Доработка crash recovery, запрет повторов необратимых действий, Video Studio/Web Designer, V4 Continuity и V5 objective infrastructure |
| 7–8 сентября, кандидат | V6 lazy loading/telemetry, owner-control и review deadlock fixes, approval coalescing и бюджет миссий, guarded OpenHands, V7 reality/world-state, UI и recovery |
| 8 сентября, default | Trader Apprentice: детерминированная матрица orderflow, корпус правил/кейсов и API; это еще не доказанное обучение модели и не доказанная прибыльность |

| Размер дерева | Baseline | Default | Кандидат |
|---|---:|---:|---:|
| Все отслеживаемые файлы | 1613 | 2364 | 2581 |
| Python | 939 | 1376 | 1501 |
| JavaScript | 31 | 45 | 45 |
| Файлы в тестовых путях | 352 | 567 | 653 |
| Markdown | 407 | 581 | 676 |
| ZIP | 11 | 23 | 4 |

После baseline в default 304 non-merge коммита: 162 меняют production-код, 186 тесты, 142 одновременно production и тесты; 87 — только документы/данные. В кандидате 488 non-merge: 253 production, 285 тесты, 215 одновременно, 140 только документы/данные. Классы production/test пересекаются; это эвристика по путям и расширениям, не оценка ценности каждого коммита. Рост не сводится к документации, но около 29% non-merge изменений — документы/данные. Эти числа не доказывают кратное ускорение разработки или качества.

## 4. Подтвержденные находки

### A. P1: false completion в Computer Operator основной ветки

На `da67a63` прямой `ActionKind.COMPLETE` записывает COMPLETED без свежей проверки результата. Локально воспроизведены два случая: голый COMPLETE при цели создать файл — ни одного действия и ни одного verified history; неудачная TYPE/verification, затем COMPLETE — также COMPLETED. Router реально подключен в API, это отдельный production-путь, а не только тестовая заглушка.

На кандидате `d021b99` этот путь исправлен AT-01/AT-03: те же пробы дают FAILED, а целевые тесты проходят. Исправление нельзя приписывать default.

[Уязвимый путь default](https://github.com/molotroka123-cell/AiMaxBossman/blob/da67a63251ab2ffb5d20a624772d2cb9a0427365/bossman-core/bossman/computer_operator/manager.py), [исправленный путь кандидата](https://github.com/molotroka123-cell/AiMaxBossman/blob/d021b99c628d9329fa27c896da3fc6dbcbb1d133/bossman-core/bossman/computer_operator/manager.py).

### B. P1: текущие разрешения не полностью перепроверяются при возобновлении одобренного инструмента

На default агент воспроизвел через реальные Services, SQLite, API и worker три сценария после ASK→approve: добавление DENY, удаление инструмента у агента, а также revoke в await-интервале до dispatch. Во всех случаях безвредный тестовый исполнитель увеличил счетчик эффекта; task завершилась. В race-сценарии статус approval уже был revoked.

Путь `_resume_pending_tool` использует глобальный registry и ранее прочитанный статус; привязка approval к agent/task сама по себе не заменяет актуальные grants/policy и проверку непосредственно перед эффектом. Безвредный счетчик доказывает достижимость dispatch, а не причинение реального внешнего ущерба.

[Engine default](https://github.com/molotroka123-cell/AiMaxBossman/blob/da67a63251ab2ffb5d20a624772d2cb9a0427365/command-center/bcc/engine.py). Отдельный runtime race-пробник на кандидате в этом прогоне не завершен: его статус NOT_VERIFIED. Нельзя автоматически переносить ни дефект default, ни успешный unit-тест revocation на все async race-сценарии другого SHA.

### C. P1: алгебра policy не сохраняет DENY во всех композициях

В `decide_effect()` разрешение агента может заменить `default_effect=deny` на AUTO до фиксации policy floor. Последующее общее AUTO-правило также может перекрыть ранее совпавшее DENY-правило: floor вычислен до цикла и не повышается при появлении DENY. Это противоречит заявленному DENY⊗X=DENY. Существующие тесты проверяют DENY default при `permission=None`, поэтому не закрывают вариант с выданным permission.

Тот же код присутствует на кандидате. Hook-DENY остается защищен; находка не означает, что абсолютно любой запрет обходится.

[Код policy кандидата](https://github.com/molotroka123-cell/AiMaxBossman/blob/d021b99c628d9329fa27c896da3fc6dbcbb1d133/command-center/bcc/tools.py), [существующие тесты](https://github.com/molotroka123-cell/AiMaxBossman/blob/d021b99c628d9329fa27c896da3fc6dbcbb1d133/command-center/tests/test_policy_algebra.py).

### D. P1 для достоверности release evidence: синтетическая экономия выдана за измеренный эффект

`command-center/scripts/convergence_metrics.py` использует `_Scripted` с заданными `(400,60)` токенами на ответ; terminal handler возвращает `exit_code=0` без записи документа. Benchmark считает `completed=true` и допускает PASS даже при `status='failed'`. Поэтому сравнение исторических 1 295 189 токенов с 1840/920 не является честным same-model A/B тестом выполненной задачи. Это полезный контрактный тест бюджетов/одобрений, но не доказательство кратного улучшения эффективности или интеллекта.

`three-system-qa` использует ASGI smoke; `project_count=0`, apps live=0/control_enabled=false. Его ноль manual interventions нельзя называть успешной автономной работой с реальными приложениями владельца.

[Benchmark-код](https://github.com/molotroka123-cell/AiMaxBossman/blob/d021b99c628d9329fa27c896da3fc6dbcbb1d133/command-center/scripts/convergence_metrics.py), [отчет кандидата](https://github.com/molotroka123-cell/AiMaxBossman/blob/d021b99c628d9329fa27c896da3fc6dbcbb1d133/docs/night/NIGHT_V7_CONVERGENCE_FINAL_REPORT_20260908.md).

### E. CI-дефекты и ограничения

- Default root: точное сравнение float в `test_trader_apprentice.py` — 79350.00000000001 против 79350. Подтверждено и GitHub 3.11/3.12, и локально. Это небольшой численный/test-contract дефект, но release gate красный.
- Кандидат root/Editors: устаревший реестр skip; browser-проверки Editors сами прошли 5/5, падает последующий registry gate. Нельзя описывать это как пять сломанных UI-сценариев.
- Кандидат Core: `test_the_sandbox_may_not_commit_or_move_its_own_head` падает DID NOT RAISE OpenHandsError. Независимая проверка показала зависимость теста от Git identity: без identity `git commit` завершается 128 и HEAD не меняется; с явно заданной identity HEAD меняется и клиент правильно BLOCKS. Это подтвержденный дефект воспроизводимости теста, не доказанный обход OpenHands HEAD-защиты.
- Intelligence Preservation на обоих SHA: отсутствует `docs/benchmark/intelligence-preservation-current.json`, exit 2. Успешный evaluator contract не заменяет измерение сохранения интеллекта.
- Текущие committed scorecard в default и кандидате содержат одинаковые числа, но ссылаются на старые SHA `e26553e` и `413a97a`. Их нельзя считать новой независимой оценкой текущего HEAD.

## 5. CI именно выбранных SHA

### Default da67a63

| Workflow | Результат | Подробность |
|---|---|---|
| [root-ci](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582067) | FAIL | 1 failed, 865 passed, 2 skipped на Python 3.12; аналогичная float ошибка на 3.11 |
| [Core](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582151) | PASS | full coverage job: 2570 passed, 60 skipped; 89,04% в выбранном coverage scope |
| [Command Center](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582042) | PASS | 1997 passed, 17 skipped на каждой версии 3.11/3.12; coverage 77,10%/76,74% |
| [ASTRA](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211581994) | PASS workflow | Windows portable 98+24 passed; real sandbox SKIPPED |
| [Solana](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582043) | PASS | safety обе версии |
| [V2 Auto-Repair](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582001) | PASS | workflow завершен |
| [Fable media/Fleet](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582035) | PASS | py3.11: отдельные группы 3; 88; 299+11 skipped; 48 passed — группы не суммировать как уникальные сценарии |
| [Intelligence Preservation](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34211582140) | FAIL | нет измеренного current artifact |

Command Center 3.12 содержит PytestUnraisableExceptionWarning с `Event loop is closed`, но весь job действительно успешен. Это teardown-долг, а не новое падение workflow. Core 89,04% — покрытие выбранных 6132 statements, не всех 1500 Python-файлов проекта.

### Кандидат d021b99

| Workflow | Результат последней проверки |
|---|---|
| [root-ci push](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221944651) | FAIL: 1 failed, 1154 passed, 10 skipped; stale skips registry |
| [Core push](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221944672) | FAIL: full 1 failed, 2974 passed, 70 skipped; OpenHands test identity |
| [Command Center PR](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221949198) | IN_PROGRESS на последнем чтении; PASS не присвоен |
| [Editors user safety](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948771) | FAIL registry; 5 browser tests passed |
| [ASTRA](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948820) | PASS workflow, sandbox skip не доказательство |
| [Solana](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948942) | PASS |
| [Fable media/Fleet](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948853) | PASS |
| [Internal benchmark](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948860) | PASS workflow; не измеренная реальная intelligence retention |
| [Intelligence](https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34221948759) | FAIL missing current measurement |
| V2 Auto-Repair | на этом SHA в полученных 14 прогонах отсутствует; MISSING, не PASS |

PR workflow run связан с head SHA; его checkout может быть merge ref. Для root/Core дополнительно использованы push-прогоны точного commit. Счетчики разных матриц и пересекающихся suites не складывались в число уникальных тестов.

## 6. Локальные тесты в этом аудите

| Набор | Итог |
|---|---|
| Default root suite Python 3.12 | **863 passed, 3 failed, 2 skipped** за 78,58с |
| Default CC security targeted | **62 passed** |
| Default Core targeted | **86 passed** |
| Default UI Node state tests | **13 passed, 0 skipped** |
| Candidate AT01/AT03, owner control, recovery, Fleet safety/auth/RPC | **88 passed, 0 skipped** |
| Candidate review deadlock, mission budget, approval scope, canary production caller, unmeasured resources | **94 passed, 0 skipped** |
| Candidate OpenHands targeted failing CI case | **1 passed** локально; дополнительная проба объяснила отличие Git identity |
| Независимые негативные probes | false COMPLETE default; policy composition; pending authorization/revoke; сопоставление default/candidate |

Два дополнительных локальных root failure, отсутствующих в GitHub, — packaging isolated wheel import (`bossman_shared` not found) и watchdog child process (sleeping вместо zombie). Причина различия окружения полностью не расследована; они не объявлены доказанными регрессиями production. Float failure подтвержден независимо. Неудобные результаты не удалялись и не превращались в PASS.

Целевые группы могут пересекаться, поэтому общий счет уникальных тестов не заявляется. Модель/внешние инструменты в probes подставные или безвредные, persistence и control flow настоящие. Весь Windows desktop, платные провайдеры и приложения владельца здесь не запускались.

## 7. Что стало реально сильнее

- **Execution/recovery:** effect obligations, post-state evidence, подписи, fencing, write-ahead и безопасное обращение с неизвестным исходом необратимого действия; на кандидате закрыт отдельный Computer Operator COMPLETE-путь.
- **Управление владельца и миссии:** исправления review deadlock, явные исходы blocked-миссий, approval coalescing и token-loop guards; эти детерминированные свойства подтверждаются тестами.
- **Fleet:** lease/fence/auth/RPC/recovery существенно крепче исходного ядра; transport-local проверка не равна многомашинному owner soak.
- **UX/performance:** lazy pages, app-probe single-flight, измерение startup/phase timing, снижение приоритета FFmpeg; это реальные изменения архитектуры, но не доказанные цифры ускорения на компьютере Тимура.
- **Memory/context:** scoped memory, durable checkpoints, сохранение policy/invariant head при truncation, recall/recovery mechanics. Польза семантического retrieval и интеллектуальная retention требуют моделей и A/B, а не только сериализационных тестов.
- **V5/V7:** появился production caller для skill canary; world/reality/contracts и guarded OpenHands. Standing objectives по умолчанию остаются выключенными, а OpenHands live provider acceptance отсутствует.

## 8. Что говорят реальные пользовательские доказательства

В кандидате сохранена summary owner session `6cbb17ce84db`: 6327 записей, 33 errors, 40 dead clicks, 71 refusals. Это агрегированные события 4–7 сентября; исходный raw jsonl удален из текущего дерева, есть summary/digest. Это не подтверждение установленного и принятого `d021b99`.

Более поздняя облачная приемка на `1efb5471`: T1 PARTIAL; T2 artifact PASS, процесс FAIL; T3 PARTIAL с runaway tokens; T4 NOT_COMPLETED. Последующие тестовые исправления полезны, но не заменяют повторение этих задач живым пользователем.

Обход 34 вкладок был headless Chromium 1440×900. Из 24 безопасных controls: 14 PASS, 3 NO_OP, 5 disabled, 2 NOT_RUN. Операции delete/approve/stop/send/save исключались. Поэтому нельзя писать «проверена каждая кнопка» или «все реальные сценарии пройдены».

## 9. Оценка по прозрачной рубрике

Каждая из 10 осей имеет одинаковый вес 10%; оценка 0–10 переводится в /10000. 10 требует интеграции, отрицательных проверок и актуального acceptance; большое число строк/тестов само по себе оценку не повышает. Критические дефекты и missing live evidence остаются veto для production независимо от среднего.

| Ось | Default | Кандидат | Обоснование |
|---|---:|---:|---|
| Execution truth | 6,5 | 8,3 | отдельный false-completion путь default; AT01/AT03 исправлены candidate |
| Security / owner authorization | 6,0 | 6,2 | обнаружены policy/resume пробелы; наличие множества тестов их не компенсирует |
| Tooling / OS integration | 7,0 | 7,5 | Windows CI и runtime connectors есть; owner acceptance неполон |
| Organization / orchestration | 7,3 | 7,8 | durable missions, deadlock/coalescing, но автономные standing objectives выключены |
| Fleet / resources | 7,0 | 7,5 | auth/fencing/recovery и fail-closed resource admission; multi-host live не доказан |
| Memory / context / reasoning | 7,0 | 7,3 | durable/scope/context protection лучше; same-model retention отсутствует |
| Testing / CI | 7,5 | 7,5 | богатые suites/negative tests, но текущие gates красные и есть evidence gaps |
| Observability / control | 7,3 | 8,0 | traces, bounded retention, phase telemetry; synthetic/live требуют разграничения |
| Treasury / costs | 7,0 | 7,8 | ceilings и loop guards есть; заявленная экономия не валидный A/B |
| UX / Command Center / editors | 7,0 | 7,8 | lazy UI, editor/caller fixes; owner end-to-end повторно не принят |
| **Среднее** | **6,96 ≈ 7,0** | **7,57 ≈ 7,6** | экспертный, а не статистический показатель |

По сохраненным старым осям: UCA/OS и память движутся вперед, но новая проверка снизила доверие к безопасности. Интеллектуальный benchmark не заслуживает повышения только за contract tests: прежний пробел 4,5/10 качественно не закрыт. Рост кандидата относительно прежних 7200 — ориентировочно несколько сотен баллов, не «полностью другой класс интеллекта».

## 10. Минимальный путь к честному freeze

1. Свести источник поставки: выбрать интеграционный кандидат и перенести сохраняемые default-only изменения, не теряя ни одной стороны. Переключение имени ветки не исправляет интеграционные расхождения.
2. Закрыть актуальную авторизацию непосредственно перед эффектом при resume, revocation и смене grants; добавить негативные race-тесты. Закрыть обе композиции DENY.
3. Внести корректировки тестов float/Git identity, обновить skips registry; дождаться всех обязательных CI на одном окончательном SHA. Не ослаблять gates и не считать отсутствие V2 PASS.
4. Отдельно назвать synthetic contract benchmarks; real success gate должен требовать реальный post-state и completed, не failed. Выполнить сопоставимое RAW/SYSTEM/CONTEXT/FULL измерение на одной модели и SHA.
5. Повторить четыре пользовательские задачи на выбранном SHA с одним persistent owner Dashboard: файл/браузер, Web Designer, Video import→edit→export→decode→reopen, multi-step Computer Use. Зафиксировать видимый результат, approvals, token/cost/latency/RSS, recovery и stop/revoke.

**Вердикт:** substantial engineering progress; текущий release/freeze **NO-GO**. Нельзя утверждать «остались только внешние измерения»: есть конкретная работа в коде и тестах. Не подтверждено превосходство над ChatGPT/Claude/Vegas, человеческая скорость PC-control, рост интеллекта или прибыльность торговой системы.
