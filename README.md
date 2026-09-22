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

**Единая карта завтрашнего прогона:** [Tomorrow Operator Runbook](docs/owner/TOMORROW_OPERATOR_RUNBOOK.md) · [короткий checklist](docs/owner/TOMORROW_CHECKLIST.md). Если старые handoff-документы расходятся по порядку действий, используйте runbook как sequencing-layer, а фактический remote HEAD/certifier/artifact — как источник технической истины.


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
| Как проверить, чему Qwen реально научился на ремонтах | [Local Qwen Apprentice Benchmark](docs/evo/LOCAL_QWEN_APPRENTICE_BENCHMARK.md) |
| Как завтра пройти всё в правильном порядке | [Tomorrow Operator Runbook](docs/owner/TOMORROW_OPERATOR_RUNBOOK.md) |
| Как Claude должен экономно работать поверх Qwen | [Qwen-first master prompt](docs/owner/TOMORROW_MASTER_PROMPT_QWEN_FIRST.md) |

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
- **Last evidence SHA:** `a2790632feed52b6b6ec21f017d85fb5151ca4f3` · **Current HEAD SHA:** `1b7cf6d924fd` · **Evidence freshness:** PARTIALLY_STALE
- **Last scorecard update:** 2026-09-22
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 8.0/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

</details>
