# BOSSMAN

### Один компьютер. Одна программа. Проверяемый результат.

Локальное AI-рабочее пространство: модели, агенты, память, управление компьютером, файлы, сайты, изображения, видео и связь через Telegram. Владелец задаёт результат обычным языком; Bossman должен выполнить работу, запросить нужные разрешения и проверить итог.

> **Текущее направление: общая сборка Bossman, самоулучшение 1.1 и этап 1.2 — Terminal Run.**
> Каноническая линия — **`release/bossman-owner`**; интеграционные PR сверяются по актуальному remote.
> Этот README — карта продукта и направления, **не сертификат готовности**.
> Версия считается принятой только по конкретному TESTED_SHA, Windows-архиву и проверяемым owner-evidence.

[Открыть каноническую ветку](https://github.com/molotroka123-cell/AiMaxBossman/tree/release/bossman-owner) · [Установка](INSTALL.md) · [Первый прогон](OWNER_ACCEPTANCE.md) · [Задание интегратору](CLAUDE_NEXT_ACTION.md)

## Этап 1.2 — тот же Bossman, теперь ещё и в CMD

**Не второй Bossman и не новая база. Только ещё один удобный пульт.** Терминал, дашборд и Telegram должны работать с одним выбранным экземпляром программы: общие проекты и файлы, модели, задачи, навыки, память, разрешения и результаты. Изменение через один интерфейс видно в остальных без переноса папок.

Владелец хочет общаться с Bossman прямо в Windows CMD, как с современным coding-CLI. Claude Code будет обучать и аудировать его через структурированные команды, а не тратить каждый шаг на клики по дашборду. Это должно сделать процесс удобнее; снижение времени и расходов ещё нужно измерить на одинаковых задачах. Терминал сам по себе не ускоряет модель и не заменяет реальную проверку результата.

**Статус этого обновления: задание на реализацию и приёмку, не утверждение, что новый CLI уже работает.** Этап 1.2 не создаёт отдельный engine, memory store или систему самообучения. Изолированные кандидаты для self-repair сохраняются как механизм проверки; stable не переписывается учеником напрямую.

[Мастер-промпт Claude](docs/terminal/TERMINAL_RUN_1_2_MASTER.md) · [Открытые компоненты](docs/terminal/OPEN_SOURCE_REUSE.md) · [Приёмка Terminal Run](docs/terminal/ACCEPTANCE_AND_OWNER_RUN.md) · [Claude Code: учить и аудировать через терминал](docs/terminal/CLAUDE_TEACHER_TERMINAL.md)

## Bossman 1.1 — главный North Star

**После release-critical безопасности и корректности главный продуктовый приоритет — проверяемое непрерывное самоулучшение Bossman.**

Цель следующего этапа: локальный Bossman сам находит ограниченную полезную проблему, исправляет её в изолированной среде, доказывает результат тестами, получает независимую проверку, сохраняет обобщённый урок, переживает перезапуск и применяет опыт к новой аналогичной задаче.

Последнее уточнение владельца — **целевой 4–6-дневный измеряемый learning experiment после первого принятого запуска**: начать получать коммерчески полезные результаты и двигаться к первому заработку за счёт идей, процессов и своего железа. Это обновляет прежний ориентир 4–7 дней, но **не обещает гарантированной прибыли** и не разрешает автономные платежи, торговлю, массовые рассылки или внешние отправки без обычных approval-gates. Подготовленная работа, оценочная экономия и фактическая выручка учитываются отдельно.

Лестница доказательств:

`SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT`

[Полный обязательный контракт Bossman 1.1](docs/evo/BOSSMAN_1_1_NORTH_STAR.md) · [Последнее уточнение владельца](docs/terminal/TERMINAL_RUN_1_2_MASTER.md).

## Состояние основы 1.0 RC — ранее зафиксированный срез

Следующий раздел и scorecard ниже сохраняют прежние evidence; они не подтверждают готовность более новых интеграционных SHA или Terminal Run.

Bossman уже прошёл первый реальный owner-hardware цикл на **Ryzen AI Max+ 395 / Radeon 8060S / 128 GB**. После живого аудита и repair-pass в канонической линии появились исправления browser download, approvals, честной телеметрии локальных моделей и Computer Use. Локальный MAIN/FAST стек и управление Windows были проверены на реальном железе; media-generation путь на sd.cpp/Wan/Z-Image интегрируется и ещё требует финальной установленной приёмки.

**Что уже доказано на owner hardware:** запуск самостоятельного Windows-продукта, встроенные Python/Chromium/FFmpeg, auth/Flight Recorder, локальные Qwen-модели, restart/approval continuity, реальный Computer Use и исправленный download path. **Что ещё не является финальным PASS:** новый exact-SHA Windows-кандидат после всех repair-коммитов, полный owner re-run, AI-video через окончательный product path, coaching/holdout и независимая red-team атака.

Текущий принцип выпуска: **не количество тестов, а один точный SHA → один Windows artifact → реальные owner-сценарии → независимая атака → только затем принятый выпуск.**

<a id="why"></a>
## Для чего нужен Bossman

Не ещё один чат с моделью. Целевая цепочка продукта:

**Задача → нужный контекст → модель и инструменты → действие → проверка → сохранённый результат.**

Примеры: подготовить документы к проверке владельцем; собрать и отредактировать сайт; обработать изображения и видео; разобрать файлы; проверить проект и подготовить исправление. Каждый путь принимается отдельно — универсальная автономность заранее не обещается.

<a id="quick-start"></a>
## Следующий прогон начинаем отсюда

**Единая карта прогона:** [Tomorrow Operator Runbook](docs/owner/TOMORROW_OPERATOR_RUNBOOK.md) · [короткий checklist](docs/owner/TOMORROW_CHECKLIST.md) · [дополнение Terminal Run](docs/terminal/ACCEPTANCE_AND_OWNER_RUN.md). При расхождении старых handoff-документов фактический remote HEAD/certifier/artifact определяет техническое состояние, а последнее решение владельца — scope и порядок. Новые команды CLI публикуются как рабочие только после реализации и проверки.

1. Получить от интегратора **один TESTED_SHA, один Windows-архив, его SHA-256 и результаты обязательных проверок**. Нет этих данных — сборка ещё не передана на приёмку.
2. Скачать именно этот архив по [инструкции установки](INSTALL.md). Не выбирать просто последний зелёный прогон и не использовать архив другой ветки.
3. Распаковать в отдельную тестовую папку, запустить `Start-Bossman.cmd`, настроить одну проверенную локальную модель. Рабочие данные и внешние действия пока не подключать.
4. Запустить поставляемый `Evening-Test.cmd`, затем пройти [HW-01…HW-13](OWNER_ACCEPTANCE.md) и относящиеся к новому клиенту Terminal Run проверки. Базовая диагностика не означает прохождение всех кейсов.
5. Сбой: сохранить задачу, SHA архива, результат и обезличенный лог. Исправление получает новый архив и повторный прогон затронутых сценариев.

<a id="capabilities"></a>
## Что входит в программу испытаний

| Область | Что должен увидеть владелец | Что доказываем отдельно |
|---|---|---|
| Модели и Gateway | Подходящая модель и понятный fallback | Настоящий вызов, правильные tools/JSON, лимит памяти и бюджета |
| Задачи и агенты | Ход работы, остановка, результат | Полная цепочка, отсутствие ложного успеха, восстановление |
| Контекст и файлы | Поиск, редактирование, история | Изоляция проектов, сохранность и удаление после рестарта |
| Браузер и Computer Operator | Наблюдение, действие, передача управления человеку | Правильное окно/цель, проверка эффекта, реальный Windows desktop |
| Web Designer | Проект, предпросмотр, правки и откат | Реальное подключение creative brief к AI-пути, сохранность сайта |
| Image Studio | Импорт, правки, библиотека и экспорт | Нативное редактирование отдельно от генерации реальным провайдером |
| Video Studio | Таймлайн, правка, сохранение и экспорт | Фактический рендер, ffprobe/полное декодирование, ожидаемое изменение |
| Telegram | Поручение, подтверждение, продолжение | Реальная доставка владельцу, идентичность и защита от повтора |
| Terminal Run | Разговор и управление тем же Bossman в CMD | Общие файлы/состояние, structured exec, installed Windows и отсутствие второй очереди |
| Trading Lab | Анализ и симуляция | Только READ-ONLY/PAPER; не разрешение на реальные ордера |

Подробные критерии: [owner-сценарии](tests/owner_scenarios/owner_scenarios.json), [hardware-манифест](tests/owner_hardware/manifest.json), [известные ограничения](KNOWN_LIMITATIONS.md).

<a id="release"></a>
<a id="evidence"></a>
## Как определяется готовность

**Код есть ≠ кнопка работает ≠ сборка принята ≠ железо проверено.**

Готовность к владельческому испытанию требует одного неизменяемого SHA, выполненных обязательных CI-проверок и настоящей установки его Windows-архива вне репозитория. Обязательные проверки задаёт [существующий certifier](tools/exact_sha_certify.py); список нельзя сокращать ради зелёного результата.

Очередь, пропуск, отмена, `action_required`, прогон без выполненных jobs и чужой SHA не считаются PASS. Документационный commit тоже не наследует сертификат предыдущей версии; старый сертификат остаётся действительным только для своей версии.

Локальная модель на целевом AMD, desktop владельца, Telegram и человеческая авторизация имеют отдельные результаты. Отсутствующий код нельзя списать на «ждём железо». Отсутствующее реальное измерение интеллекта не подменяется выдуманным JSON.

<a id="principles"></a>
## Правила работы

`never / ask / allowed` сохраняются на всех путях. Секреты и LOCAL_ONLY не уходят в облако. Платный fallback не включается молча. Подача документов, оплата и опасные внешние действия требуют отдельного подтверждения. Результат без независимой проверки не называется выполненным.

<a id="interface"></a>
<a id="workflows"></a>
<a id="telegram"></a>
## Первый день: результат важнее количества тестов

Сначала безопасные файлы, браузер и перезапуск. Затем фото/видео, агентные цепочки и Telegram в разрешённом тестовом чате. MVČR — подготовить проверяемый пакет и остановиться перед отправкой. Основные критерии находятся в [OWNER_ACCEPTANCE.md](OWNER_ACCEPTANCE.md), дополнительный терминальный путь — в [Terminal Run](docs/terminal/ACCEPTANCE_AND_OWNER_RUN.md).

<a id="performance"></a>
## Модели и производительность

Целевой компьютер: **Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory**. Скорость, память и качество измеряем на нём, а не выводим из названия модели.

[Локальные кандидаты](tests/owner_hardware/MODEL_STACK_2026-09-20.md) и [облачные кандидаты](tests/owner_hardware/CLOUD_STACK_2026-09-20.md) — планы сравнения, не доказанные победители и не обещание доступности. Перед подключением проверяются официальный ID, лицензия, runtime, возможности и актуальный тариф. В первый день не скачиваем весь каталог.

<a id="map"></a>
## Карта репозитория

| Нужен ответ | Документ |
|---|---|
| Как установить конкретный архив | [INSTALL](INSTALL.md) |
| Как проверять на своём ПК | [OWNER_ACCEPTANCE](OWNER_ACCEPTANCE.md) |
| Что инженеру закончить сейчас | [CLAUDE_NEXT_ACTION](CLAUDE_NEXT_ACTION.md) |
| Где искать актуальные доказательства | [CURRENT_STATE](CURRENT_STATE.md) |
| Как устроена система | [ARCHITECTURE](ARCHITECTURE.md) |
| Что ещё не доказано | [KNOWN_LIMITATIONS](KNOWN_LIMITATIONS.md) |
| Что сохранено из старых веток | [CONVERGENCE_DECISIONS](CONVERGENCE_DECISIONS.md), [salvage ledger](docs/final/FINAL_SALVAGE_LEDGER.md) |
| Как разбирать поломку первого прогона | [Hotfix playbook](tests/owner_hardware/HOTSPOTS_AND_HOTFIX_PLAYBOOK.md) |
| Как frontier-модели будут проверять и обучать Bossman | [Frontier Council и тренинг — спецификация](docs/evo/FRONTIER_COUNCIL_AND_TRAINING.md) |
| Как проверить, чему Qwen реально научился на ремонтах | [Local Qwen Apprentice Benchmark](docs/evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md) |
| Как пройти всё в правильном порядке | [Tomorrow Operator Runbook](docs/owner/TOMORROW_OPERATOR_RUNBOOK.md) |
| Как Claude должен экономно работать поверх Qwen | [Qwen-first master prompt](docs/owner/TOMORROW_MASTER_PROMPT_QWEN_FIRST.md) |
| Как Bossman должен помнить опыт между сессиями | [Durable Memory / Always-Remember Contract](docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md) |
| Как сделать терминал без второго Bossman | [Terminal Run 1.2](docs/terminal/TERMINAL_RUN_1_2_MASTER.md) |
| Как Claude Code учит через терминал | [Teacher runbook](docs/terminal/CLAUDE_TEACHER_TERMINAL.md) |

<a id="vision"></a>
## EVO — развитие общей системы

[Предложение EVO](docs/evo/EVO_1_0_PROPOSAL.md) описывает изолированный кандидат → сравнение → независимую проверку → решение владельца → обновление с откатом. Самостоятельная замена рабочего ядра, разрешений или моделей не включается одной документацией.

### Frontier Council — внешние аудиторы и учителя

Следующее описание — архитектурное направление, не сертификат его реализации. Согласованный объём v1.1/Terminal Run определяют текущие [North Star](docs/evo/BOSSMAN_1_1_NORTH_STAR.md) и [последнее уточнение](docs/terminal/TERMINAL_RUN_1_2_MASTER.md).

Локальный Bossman выполняет работу, а разрешённые frontier-модели независимо проверяют обезличенные результаты, находят ошибки и готовят коррекции. Проверяемые тесты, а не авторитет модели или голосование, определяют полезность совета.

**Работа → подтверждённая ошибка → коррекция учителя → повторная попытка Bossman → проверенный урок → новые невиденные задачи → предложение улучшения → решение владельца.**

Обучение разделено: **память/skills**, **учебные задачи и проверенный датасет**, затем отдельно — **экспериментальное LoRA/QLoRA-дообучение весов**. Запись урока не называется fine-tuning. Пользу надо доказать на новых задачах и после перезапуска; помощь учителя учитывается отдельно от самостоятельного успеха.

В проектируемом EVO Lab владелец увидит, чему Bossman научился, какие советы отклонены, сравнение stable/candidate, расходы и откат. Неизмеренные показатели остаются неизвестными. Stable не переписывается автоматически; LOCAL_ONLY не уходит наружу; нет неразрешённого платного fallback.

[Полная спецификация, этапы и критерии приёмки](docs/evo/FRONTIER_COUNCIL_AND_TRAINING.md). Само наличие плана не включает облачные вызовы, расписание аудитов или обучение весов.

<a id="journey"></a>
## Одна линия продукта, история сохранена

Финальная ветка остаётся **`release/bossman-owner`**. Не создаём `final-final`, отдельный Dashboard-Bossman или Terminal-Bossman. Незавершённые полезные PR не закрываем без проверки переноса. GitHub default branch может показывать старую линию — для этого прогона используйте ссылку на каноническую ветку сверху.

Обширные прежние README, INSTALL и handoff сохранены без изменения байтов в [архиве до уборки](docs/archive/owner-preflight-05a1bb02/README.md). Они объясняют историю, но не определяют текущую готовность.

## Проверки интегратора

Владельцу этот раздел не нужен: обычный запуск — из переданного Windows ZIP по INSTALL.
Команды ниже выполняются инженером в рабочей копии; они не заменяют установленный
Windows-продукт, реальную модель или HW-01…HW-13.

<details>
<summary>Повторить проверки README и владельческого launcher</summary>

```bash
python scripts/update_readme_scorecard.py --check
python -m pytest tests/test_readme_commands_are_real.py -q
python -m pytest tests/test_readme_scorecard.py -q
python -m pytest tests/test_owner_acceptance_ps1_contract.py -q
```

После согласованного push задать TESTED_SHA полным 40-символьным SHA и сохранить
в runs.json все страницы GitHub Actions для него, включая push/PR/dispatch.
Не сокращать набор обязательных workflows. Дополнительно сверить реальные jobs,
checkout/build SHA и байты архива: один ответ certifier не заменяет эти улики.

```bash
python tools/exact_sha_certify.py --sha "$TESTED_SHA" --runs-json runs.json --output exact-sha-certification.json
```

</details>

<details>
<summary>Актуальный Live Scorecard — оценка прогресса, не сертификат текущего кандидата</summary>

Источник — docs/benchmark/current-scorecard.json, обновлён 22 сентября 2026 по текущему repair evidence и owner-hardware проверкам. Это инженерная оценка прогресса, а не release-сертификат: exact-SHA CI и финальная установленная приёмка текущих байтов всё ещё требуются. Его отображение восстанавливается штатным scripts/update_readme_scorecard.py.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 9.1/10 | VERIFIED | HIGH | Browser download B4 now persists and verifies real artifacts instead of returning false success; Approval restart matrix is durable and task-bound effects remain exactly-once across hard restart |
| 2 | Security | 9.0/10 | VERIFIED | HIGH | CU-APPROVAL no longer trusts a model-supplied semantic field as proof of approval; CU-VERIFY fails closed on unknown or mistyped expectations; approval and anti-replay boundaries remain enforced |
| 3 | Tooling / OS Integration | 8.6/10 | INTEGRATED | HIGH | Owner hardware live run exercised Computer Use on Windows with 12/12 checks including focus, Cyrillic typing, STOP/resume and coordinate fallback; Local MAIN/FAST models, Chromium, FFmpeg and browser download were exercised on the Ryzen AI Max+ 395 target system |
| 4 | Organization Layer | 7.6/10 | INTEGRATED | MEDIUM | Mission/task orchestration and durable task state remain integrated; Planner/worker/verifier owner scenario still needs the final installed 1.0-RC re-run |
| 5 | Fleet & Resources | 7.2/10 | INTEGRATED | MEDIUM | Lease/fence/resource controls remain in place and owner repair used explicit desktop/backend/GPU leases; Media workers are bounded child processes rather than permanently resident generation daemons |
| 6 | Memory / Context | 7.2/10 | INTEGRATED | MEDIUM | Controlled audit disproved a general memory-loss P1; unique memory writes and restart continuity passed; Coaching/frontier learning design is documented, but holdout transfer is not yet measured |
| 7 | Testing / CI | 8.6/10 | VERIFIED | MEDIUM | Repair pass added targeted regressions for B4, AP-ALL, TEL-001, Computer Use safety, media hashing/cancel and UX settle; Full-suite triage found no confirmed product regression after harness/environment fixes; current exact-SHA mandatory CI is still pending |
| 8 | Observability / CEO Control | 7.9/10 | INTEGRATED | HIGH | Flight Recorder and installed-product telemetry were live on owner hardware; TEL-001 now reports model generation throughput from native/upstream timing rather than short-prompt wall time |
| 9 | Treasury / Cost | 6.9/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain fail-closed and unknown pricing is not treated as free; Frontier-audit/training and media cost-per-verified-result is not yet measured |
| 10 | Mission UX / Command Center | 7.8/10 | INTEGRATED | MEDIUM | Computer Use is wired into the owner product and real Windows interaction has owner evidence; F-17 page-settle race was fixed with a product-owned rendered-page signal |

- **Current bottleneck:** The repair pass is materially ahead of the 2026-09-07 scorecard, but the final repaired exact-SHA Windows artifact still needs mandatory CI, clean-install owner re-run, Video Studio product-path acceptance, coaching/holdout and an independent red-team.
- **Next highest-value fix:** Finish mandatory exact-SHA CI, build one clean Windows 1.0-RC artifact, then run the full owner acceptance and independent red-team on those exact bytes.
- **Last evidence SHA:** `0c1cbe651f5271bbd0bb2d30b467c1206c19f565` · **Current HEAD SHA:** `9e3aa192f513` · **Evidence freshness:** PARTIALLY_STALE
- **Last scorecard update:** 2026-09-22
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** PASS

_Среднее (вторично, не авторитетно): 8.0/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

</details>
