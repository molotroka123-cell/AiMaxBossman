# BOSSMAN

### Один компьютер. Одна программа. Проверяемый результат.

Локальное AI-рабочее пространство: модели, агенты, память, управление компьютером, файлы, сайты, изображения, видео и связь через Telegram. Владелец задаёт результат обычным языком; Bossman должен выполнить работу, запросить нужные разрешения и проверить итог.

> **Фаза: закрытие Bossman 1.0 RC перед владельческим прогоном.**
> Каноническая линия — **`release/bossman-owner`**, PR **#67**.
> Этот README — карта продукта и текущего направления, **не сертификат готовности**.
> Версия считается принятой только по конкретному TESTED_SHA, Windows-архиву и проверяемым owner-evidence.

[Открыть каноническую ветку](https://github.com/molotroka123-cell/AiMaxBossman/tree/release/bossman-owner) · [Установка](INSTALL.md) · [Первый прогон](OWNER_ACCEPTANCE.md) · [Задание интегратору](CLAUDE_NEXT_ACTION.md)

## Текущее состояние 1.0 RC

Bossman уже прошёл первый реальный owner-hardware цикл на **Ryzen AI Max+ 395 / Radeon 8060S / 128 GB**. После живого аудита и repair-pass в канонической линии появились исправления browser download, approvals, честной телеметрии локальных моделей и Computer Use. Локальный MAIN/FAST стек и управление Windows были проверены на реальном железе; media-generation путь на sd.cpp/Wan/Z-Image интегрируется и ещё требует финальной установленной приёмки.

**Что уже доказано на owner hardware:** запуск самостоятельного Windows-продукта, встроенные Python/Chromium/FFmpeg, auth/Flight Recorder, локальные Qwen-модели, restart/approval continuity, реальный Computer Use и исправленный download path. **Что ещё не является финальным PASS:** новый exact-SHA Windows-кандидат после всех repair-коммитов, полный owner re-run, AI-video через окончательный product path, coaching/holdout и независимая red-team атака.

Текущий принцип выпуска: **не количество тестов, а один точный SHA → один Windows artifact → реальные owner-сценарии → независимая атака → только затем 1.0.**

<a id="why"></a>
## Для чего нужен Bossman

Не ещё один чат с моделью. Целевая цепочка продукта:

**Задача → нужный контекст → модель и инструменты → действие → проверка → сохранённый результат.**

Примеры: подготовить документы к проверке владельцем; собрать и отредактировать сайт; обработать изображения и видео; разобрать файлы; проверить проект и подготовить исправление. Каждый путь принимается отдельно — универсальная автономность заранее не обещается.

<a id="quick-start"></a>
## Завтра начинаем отсюда

1. Получить от интегратора **один TESTED_SHA, один Windows-архив, его SHA-256 и результаты обязательных проверок**. Нет этих данных — сборка ещё не передана на приёмку.
2. Скачать именно этот архив по [инструкции установки](INSTALL.md). Не выбирать просто последний зелёный прогон и не использовать архив другой ветки.
3. Распаковать в отдельную тестовую папку, запустить `Start-Bossman.cmd`, настроить одну проверенную локальную модель. Рабочие данные и внешние действия пока не подключать.
4. Запустить поставляемый `Evening-Test.cmd`, затем пройти [HW-01…HW-13](OWNER_ACCEPTANCE.md). Базовая диагностика не означает прохождение всех 13 кейсов.
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

Сначала безопасные файлы, браузер и перезапуск. Затем фото/видео, агентные цепочки и Telegram в разрешённом тестовом чате. MVČR — подготовить проверяемый пакет и остановиться перед отправкой. Подробный порядок и критерии находятся в [OWNER_ACCEPTANCE.md](OWNER_ACCEPTANCE.md).

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

<a id="vision"></a>
## После первой стабильной сборки — EVO 1.0

[Предложение EVO](docs/evo/EVO_1_0_PROPOSAL.md) — пока рекомендации: изолированный кандидат → сравнение → независимая проверка → решение владельца → обновление с откатом. Самостоятельная замена рабочего ядра, разрешений или моделей не включается этой документацией.

### Frontier Council — внешние аудиторы и учителя

**Запланировано, не включено этим обновлением.** Локальный Bossman выполняет работу, а разрешённые frontier-модели независимо проверяют обезличенные результаты, находят ошибки и готовят коррекции. Проверяемые тесты, а не авторитет модели или голосование, определяют полезность совета.

**Работа → подтверждённая ошибка → коррекция учителя → повторная попытка Bossman → проверенный урок → новые невиденные задачи → предложение улучшения → решение владельца.**

Обучение разделено: **память/skills**, **учебные задачи и проверенный датасет**, затем отдельно — **экспериментальное LoRA/QLoRA-дообучение весов**. Запись урока не называется fine-tuning. Пользу надо доказать на новых задачах и после перезапуска; помощь учителя учитывается отдельно от самостоятельного успеха.

В проектируемом EVO Lab владелец увидит, чему Bossman научился, какие советы отклонены, сравнение stable/candidate, расходы и откат. Неизмеренные показатели остаются неизвестными. Stable не переписывается автоматически; LOCAL_ONLY не уходит наружу; нет неразрешённого платного fallback.

[Полная спецификация, этапы и критерии приёмки](docs/evo/FRONTIER_COUNCIL_AND_TRAINING.md). Сначала завершение и приёмка 1.0; затем автоматизация council/тренинга. Этот план не расширяет текущий repair-pass и не включает облачные вызовы, расписание аудитов или обучение весов.

<a id="journey"></a>
## Одна линия продукта, история сохранена

Финальная ветка остаётся **`release/bossman-owner`**. Не создаём `final-final`, отдельный Dashboard-Bossman или новую версию ради уборки. Незавершённые полезные PR не закрываем без проверки переноса. GitHub default branch может показывать старую линию — для этого прогона используйте ссылку на каноническую ветку сверху.

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
<summary>Историческая проекция Live Scorecard — не сертификат текущего кандидата</summary>

Источник — docs/benchmark/current-scorecard.json, последнее измерение 7 сентября 2026.
Числа и статусы ниже сохранены из этого источника, а не измерены заново. Они не дают
PASS для текущего TESTED_SHA. Источник не переписывается ради зелёной проверки README;
его отображение восстанавливается штатным scripts/update_readme_scorecard.py.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.8/10 | VERIFIED | HIGH | AT-01 effect obligations and fresh post-state verification are closed with regression coverage; Fencing, anti-replay and recovery invariants remain in force; V6 performance work did not weaken effect-boundary semantics |
| 2 | Security | 8.5/10 | VERIFIED | HIGH | Secret scan and blocking SAST/SCA completed successfully on the V6 code candidate; Approvals, authorization, privacy routing, freshness and fail-closed behavior were explicitly preserved through V6 |
| 3 | Tooling / OS Integration | 7.5/10 | INTEGRATED | MEDIUM | Windows-path CI is green on the current V6 code candidate; Owner-session reconnect, app restart recovery, provider UX and Trading Lab crash paths have repository fixes |
| 4 | Organization Layer | 7.3/10 | INTEGRATED | MEDIUM | Mission/task orchestration remains durable and blocked-only missions now terminate honestly instead of appearing to run forever; Organization/Fleet execution contracts and verified-child completion rules remain covered |
| 5 | Fleet & Resources | 7.0/10 | INTEGRATED | MEDIUM | Lease/fence/queue safety contracts remain covered and V6 does not bypass the canonical execution path; Interactive work is protected from background FFmpeg contention by lowered child-process priority |
| 6 | Memory / Context | 6.5/10 | IMPLEMENTED | MEDIUM | Durable task/recovery memory and scoped context contracts remain intact; Long-session testing now explicitly watches stale context, zombie runs, duplicate subscribers and memory growth |
| 7 | Testing / CI | 8.0/10 | VERIFIED | MEDIUM | Bossman Core full coverage/rest/security/gateway/stage8-14 jobs are green on 413a97a1; ASTRA acceptance, Solana safety, Windows paths and secret/SAST gates are green; Python 3.14 is a hard Command Center lane |
| 8 | Observability / CEO Control | 7.0/10 | PARTIAL | MEDIUM | V6 adds Services.start phase tracing, UI_READY/first-page timing and Computer Use phase_timing; Testing-period evidence from owner session 6cbb17ce84db is retained with corrected dead-click classification |
| 9 | Treasury / Cost | 6.8/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain unchanged and fail-closed during V6 performance work; Unknown pricing is not treated as free and Fable hard-cap accounting remains isolated from tests |
| 10 | Mission UX / Command Center | 6.8/10 | IMPLEMENTED | MEDIUM | V6 lazy pages reduced first-render modules from 42/788 KiB to 14/290 KiB in the measured harness; Owner-session reconnect, blocked mission, app restart and provider/trading error paths received targeted fixes |

- **Current bottleneck:** V6 phase 0/1 is repository-complete pending external validation: Core/ASTRA/Solana/Windows/security evidence is green on the current code candidate, but an uninterrupted full Command Center exact-SHA matrix plus owner Windows/local-model/Video/Web Designer/long-session acceptance is still missing.
- **Next highest-value fix:** Finish one uninterrupted exact-SHA CI certification, then run the second owner Dashboard acceptance on Windows with the configured model/provider and close only reproduced Video Studio, Web Designer and long-session findings.
- **Last evidence SHA:** `413a97a1ce2936f9543fea5d8f0b529256fc1de2` · **Current HEAD SHA:** `UNPROVEN` · **Evidence freshness:** UNPROVEN
- **Last scorecard update:** 2026-09-07
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 7.4/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

</details>
