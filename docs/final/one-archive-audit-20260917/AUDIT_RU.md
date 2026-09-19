# Независимый целевой аудит готовности одного Windows-архива

Дата аудита: 17 сентября 2026. Это аудит выпуска/упаковки и достоверности приёмки, а не полное повторение всех тестов репозитория.

## Область и ограничения

Свежие чтения через GitHub connector: ветка, отчёт CURRENT_STATE, PR-triggered CI, Windows jobs/logs/artifacts, INSTALL, build_windows_bundle.py, bundle_evening_test.py, astra6_freeze.py, windows-bundle.yml, live_openrouter_owner.py, intelligence_preservation_run.py, releases.

Локально выполнены 13 изолированных синтетических проверок двух настоящих stdlib-only модулей. Копии получены из прочитанного connector-ответа, перед импортом проверены Git blob SHA. Никаких настоящих model API, секретов, внешних записей, изменения репозитория или перезапуска автоматизаций.

Здесь НЕ выполнялись заново Windows-продукт, все тысячи тестов, приёмка целевого Ryzen, скачивание/перехеширование 759-МБ бинарного архива или антивирусный аудит. Windows-факты сверены по доступному журналу CI. Синтетические результаты проверяют строгость валидаторов и не являются модельными/аппаратными уликами.

## Актуальные точки

- Ветка: `claude/bossman-final-convergence-hu2702`.
- HEAD: `c3f41cc687734f75c2a479c232fba4edf61b6b4d`, последний коммит 16 сентября 15:17:58 UTC.
- Проверенный Windows-кандидат: `5364d2cd31ed380b5d37e8f75bf586888fe4d17c`.
- Windows run 35110167729: bundle / owner-experience / freeze jobs success.
- Реальный журнал owner-experience 104845485517: 40 passed, 0 failures/errors/skips, 308.76 s. Порог отдельного JUnit-check — 40.
- UI sweep: PASS, 30 pages, суммарно 118 классифицированных нажатий; это не означает 118 полноценных модельных действий. 16 — request_accepted, 8 — disabled_reason и т.д.
- История файлов после рестарта: 1437 ms при 5000 ms.
- Live model: OWNER_REQUIRED; env BOSSMAN_OPENROUTER_API_KEY пуст в этом CI job.
- В полученной PR-CI матрице HEAD: 11 workflow success, Intelligence Preservation failure. Полные повторные прогоны локально не запускались.
- GitHub Releases API вернул пустой список.

Артефакт `bossman-windows-5364d2cd…`, ID 10452158323: 759002595 bytes; digest `ebba2294ffda18aa1cc1dfce77194d44497fd97285ba034a8262d465a659ddf9`. Это digest ВНЕШНЕГО GitHub artifact ZIP. Он не выдаётся здесь за хеш внутреннего application ZIP. Время истечения артефакта по API: 16 октября 2026.

## Что изменилось после предыдущего аудита

BL-057 теперь закрыт, а не остаётся отсутствующим тестом: кнопки Images/Video действительно включены в installed Windows acceptance. Закрыты новые runtime/поставочные проблемы отмены Images (BL-066), недостающей зависимости webcam (BL-067) и Windows-механизма тестирования остановки FFmpeg (BL-068). CURRENT_STATE и реальный список исполняемых файлов/40 результатов согласуются.

Поэтому команда «заново написать тест Стоп» уже устарела. Правильная команда — сохранить реализованное и включать его во все соответствующие следующие проверки.

BL-063 остаётся OPEN. Последний коммит проверяет выбранный тег, диагностирует открытые диалоги и делает повторный клик в helper. Найден воспроизводимый сценарий похожего симптома: выбран body/html → подтверждение замены содержимого → изменяющий запрос не отправлен. Причина исторического CI-отказа этим не доказана; first-click и exact-node correctness нужно проверять отдельно от retry helper.

## Новые выводы аудита

Обозначения OA-* относятся к ЭТОМУ аудиту и не претендуют на свободные BL-номера репозитория. Приоритеты — оценка риска выпуска, не утверждение о критичности боевой уязвимости.

### OA-01 — P1: диагностика допускает ложный общий PASS (ВОСПРОИЗВЕДЕНО)

`tools/bundle_evening_test.py`, blob `d8aeb6d3c136c182431b1ee7a01b5b784844fb5e`.

Функция `_doctor` возвращает пустой список blocking при NOT_SHIPPED/UNREADABLE и игнорирует process returncode. Финальный `main` решает по `blocking`, не по статусу проверки.

Получены:

| Условие | doctor | общий результат |
|---|---|---|
| файла проверки нет | NOT_SHIPPED | PASS, rc 0 |
| результат не JSON | UNREADABLE | PASS, rc 0 |
| rc 9, JSON `{}` | OK | PASS, rc 0 |
| `--skip-doctor` | SKIPPED | PASS, rc 0 |

Положительный контроль текущего формата проходит; BLOCKED-check не превращается в PASS. Эти исходы доказаны на реальном `main` с синтетическими `_run` результатами и временной раскладкой, а не простым рассуждением о фрагменте кода.

Исправить обязательность, schema, exit-code mapping, свежесть результата и явные PARTIAL/OWNER_REQUIRED/FAIL. Повторно проверить на установленном архиве.

### OA-02 — P1: final freeze contract неполный (ВОСПРОИЗВЕДЕНО)

`tools/astra6_freeze.py`, blob `44b8d2affe740d8e8341268c554a689be27896a9`.

Негативные входы и действительный ответ `build_manifest`:

| Вход | Ответ |
|---|---|
| 13 успешных JUnit cases вместо текущих обязательных 40 | FROZEN |
| шесть копий одной model/case строки вместо 2×3 | FROZEN |
| JUnit properties содержат чужой source_sha | FROZEN |

Контроли: 12 cases отвергаются; JSON с чужим SHA отвергается; live status FAIL блокирует. Таким образом, функция не просто «всегда говорит да» — неполны конкретные проверки.

ВАЖНО: текущий реальный workflow отдельно требует 40 и действительно их выполнил. Здесь не установлено, что конкретный кандидат принят по 13 или по подложным отчётам. Обнаружено расхождение независимого агрегатора с upstream acceptance и возможность принять некорректные synthetic evidence.

Нужно единое описание обязательных cases; связывание XML и остальных отчётов с source/payload/harness/run; точное множество 2 models × 3 cases; строгий publish gate. Успешное создание JSON с BLOCKED может оставаться успешной технической операцией, но не разрешением выпуска.

### OA-03 — P1 для закрепления выпуска: нет фиксированного полного набора build inputs (СТАТИЧЕСКИ ПОДТВЕРЖДЕНО)

Workflow скачивает FFmpeg через mutable `latest`. `install_packages` делает обычный pip install --upgrade --target без release-lock/require-hashes. Embedded Python берётся из build-интерпретатора, а setup-python задаёт minor 3.12.

Это не доказательство вредоносных бинарников или текущей поломки. Это отсутствие гарантии, что повторная сборка того же source SHA даст тот же набор зависимостей. Постфактум записанные хеши идентифицируют полученное, но не фиксируют входные данные.

Закрепить точные версии и проверяемые digest, обеспечить wheelhouse и установку без внешнего resolver; продвигать проверенный ZIP, а не собирать заново после acceptance. В release-профиле отсутствующий FFmpeg должен ронять сборку, а не становиться «required_download» при обещании готового standalone продукта.

### OA-04 — P1 по обещанию «один архив»: завершение приёмки ещё зависит от clone (ПОДТВЕРЖДЕНО КОДОМ И INSTALL)

INSTALL прямо требует clone для полноценного target_hardware_acceptance и retention. Builder переносит target_hardware_acceptance только как helper аппаратного определения; его комментарий подтверждает, что полный прогон ссылается на scripts/ в репозитории. Live OpenRouter runner вызывается CI из tools/ checkout и не входит в SUPPORT_SCRIPTS.

Это не отменяет факта запуска настоящего installed продукта: интерпретатор и приложение действительно из архива. Но владельцу этим не доставлен самодостаточный путь завершения всего тестирования из ZIP.

Нужно поставить минимальные standalone owner runners и необходимые ресурсы; source identity из manifest/runtime, без вымышленного git. Не копировать весь dev-repository. Веса/credentials отдельно и явно; никакого скрытого pip/npm для обязательного запуска.

### OA-05 — модельная готовность: ключа и ПК недостаточно (ПОДТВЕРЖДЕНО CURRENT INSTALL/PREFLIGHT CODE)

Нужны раздельно: реальный smoke, агентная задача с инструментом, target-PC memory/driver run и same-model retention.

Для retention текущий корпус содержит по документации 220 задач — по 20 на 11 метрик. Production preflight задаёт достаточную минимальную ёмкость 189 на метрику даже при идеальном парном исходе; реальный результат может потребовать больше. Следовательно, нужен независимо отрецензированный более крупный корпус, а не только credentials.

Это репозиторная работа по корпусу плюс внешняя работа по измерению. Не подгонять задания под модель, не снижать пороги, не выдавать smoke из шести задач за сохранность интеллекта.

### OA-06 — выпуск: отсутствует единый прямой release asset (ПОДТВЕРЖДЕНО)

INSTALL просит войти в GitHub, найти Actions artifact и распаковать ZIP дважды. Releases API пуст. Для владельца требуется прямая ссылка на проверенный application ZIP, без нового rebuild/repack, плюс опубликованный hash именно этих bytes и короткая инструкция.

Финальные доказательства, содержащие hash архива, сохраняются снаружи архива. Добавить их внутрь после теста означало бы изменить испытанные bytes. Метка «Астра 6» не равна Authenticode.

## Решение

**Кандидат пригоден для продолжения тестирования, но полностью закрытый выпуск пока не подтверждён.** Главный новый результат — найдены и воспроизведены два устранимых дефекта доверия к диагностике/заморозке. Поэтому утверждение «в репозитории больше нечего править, остался только ключ» сейчас неверно.

Порядок: OA-01/OA-02 → закреплённая самодостаточная поставка → аккуратная проверка BL-063 → тот же ZIP в Windows → model smoke при доступе → прямой release/RC с честными границами. Retention corpus/target hardware остаются отдельными измерениями, не оправданием для новых функций или отказа закончить упаковку.

## Источники

- https://github.com/molotroka123-cell/AiMaxBossman/tree/claude/bossman-final-convergence-hu2702
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/docs/final/CURRENT_STATE.md
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/35110167729/job/104845485517
- https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/35114398687
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/tools/bundle_evening_test.py
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/tools/astra6_freeze.py
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/tools/build_windows_bundle.py
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/.github/workflows/windows-bundle.yml
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/INSTALL.md
- https://github.com/molotroka123-cell/AiMaxBossman/blob/c3f41cc687734f75c2a479c232fba4edf61b6b4d/tools/intelligence_preservation_run.py
- https://api.github.com/repos/molotroka123-cell/AiMaxBossman/releases?per_page=5

Изолированные воспроизведения: `python reproduce_findings.py`; результат — `reproduction_results.json`. Это диагностические контрпримеры валидатора, не release evidence.
