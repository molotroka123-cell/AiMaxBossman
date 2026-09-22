# Bossman 1.1 — инструкция Claude для интеграции

Актуальное закрытие этой ветки: **[docs/evolution/V1_1_FINAL_HANDOFF.md](docs/evolution/V1_1_FINAL_HANDOFF.md)**.
Три основные локальные модели: **[docs/evolution/LOCAL_CHAMPIONS_88GB.md](docs/evolution/LOCAL_CHAMPIONS_88GB.md)**.

## Главное: сначала закончить 1.0

Владелец уточнил: **не вносить этот модуль в `release/bossman-owner`, пока там собирается 1.0**.
Вся эта работа находится в **`codex/bossman-v1.1-evolution`**. Не переключать рабочую
копию другого Claude, не переписывать его коммиты, не делать force-push.
Работать с v1.1 через отдельный Git worktree. Переносить изменения в общую сборку
после закрытия текущего кандидата 1.0 и повторной приёмки объединённого SHA.

Задача: довести уже написанный модуль до живого owner-прогона на
Ryzen AI Max+ 395 / Radeon 8060S / 128 GB. Не переписывать Bossman и не сканировать
все ветки. Прочитать этот файл, перечисленные ниже файлы и необходимые интеграционные точки.

## Что уже реализовано

| Файл | Назначение |
|---|---|
| `bossman-core/bossman_v3/self_improvement/runner.py` | Выбор проваленного сценария, предложение точечной правки, отдельный worktree, сравнение тестов, повторная проверка, сохранение кандидата и опыта |
| `bossman-core/bossman_v3/self_improvement/campaign.py` | Турнир кандидатов, повторные baseline, бюджет proposer/reviewer и checkpoint |
| `bossman-core/bossman_v3/self_improvement/protocol.py` | Ранжирование, привязка независимого ревью к патчу, receipts доказательств |
| `bossman-core/bossman_v3/self_improvement/validation.py` | Проверка целостности и однократный отдельный holdout |
| `tools/bossman_evolve.py` | Команды `assess`, `run`, `report`, `verify`, `validate`, `export` |
| `config/evolution/owner-v1.1.json` | Исполняемый набор: контекст/точные байты, честность кэша, восстановление задач, сохранность памяти и верификации |
| `config/evolution/Dockerfile` | Отдельное окружение тестов; запуск без сети, без ключей владельца, с ограничениями ресурсов |
| `config/evolution/sources.lock.json` | Три проверенных upstream-источника и точные SHA |
| `tests/test_evolution_runner.py` | Проверки реальных Git/pytest-экспериментов с явно тестовым поставщиком патчей |
| `tests/test_evolution_metrics.py` | Запрет продвижения по NaN, бесконечным/невалидным метрикам или незакрытым security failures |
| `bossman-core/bossman_v3/self_improvement/lab.py` | Исправлен приём невалидных числовых доказательств |

Цикл: baseline → проваленный train-сценарий → модель возвращает JSON с точечными
заменами → разрешённые исходники изменяются в отдельной копии → весь фиксированный
набор проверяется дважды → при строгом улучшении сохраняется ветка `evo/candidate-*`.
До 5 альтернатив сравниваются с одним baseline. Победитель выбирается по числу
исправленных проверок и минимальному размеру изменений, затем проходит отдельную
модель ревью без объяснения builder и его test verdict. CLI по умолчанию требует
явную отличающуюся модель reviewer; `--candidate-only` оставляет ревью незавершённым.
Следующая итерация продолжает лучший экспериментальный кандидат. Производственная
ветка автоматически не продвигается: её принятие остаётся в существующем LearningGuard.

Память использует существующий `learning.trace.LearningStore` с его валидацией,
редактированием секретов и версиями. Экспериментальные записи сохраняются как
`PARTIAL` / `FAILED_EXPERIMENT`, а не как доказанные навыки. Неудачные попытки
возвращаются в контекст следующей попытки с соответствующей пометкой.

Есть сохранение бюджета до вызова провайдера, продолжение после перезапуска,
проверка SHA/набора тестов/runtime, ограничение числа попыток и времени, файл `STOP`.
Модель не получает shell/editor/MCP-инструменты. Локальный adapter допускает только
явный loopback `/v1`, запрещает redirect и proxy. Claude требует `--allow-cloud`;
при активном `LOCAL_ONLY` этот путь заблокирован.

## Что проверено и что ещё требуется

Предыдущая поставка: **106 тестов прошли на Linux / Python 3.12**.
Итог текущей поставки указан в `docs/evolution/V1_1_FINAL_HANDOFF.md`. Есть одно прежнее предупреждение
`SyntaxWarning` в анализируемом исходнике теста контекста. Команда:

```bash
PYTHONPATH=.:bossman-core:command-center PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_evolution_runner.py tests/test_evolution_metrics.py tests/test_evolution_protocol.py tests/test_context_bytes_checkpoint.py tests/test_context_slice.py tests/test_cache_observation.py tests/test_cache_intelligence.py tests/test_learning_store_authority.py tests/test_audit_p0_trace_identity.py bossman-core/tests/test_v3_compound_resume.py bossman-core/tests/test_v3_self_improvement.py
```

Проверены реальные временные Git-репозитории, падающий тест до исправления,
проходящий после исправления, отклонение регрессии, сохранность исходной ветки,
бюджет после ошибки провайдера/перезапуска, запрет правок тестов и выхода за пути.
Модель в этих проверках — fixture, **это не живое измерение интеллекта**.

В текущем облачном окружении отсутствовали Docker и Claude CLI. Контейнерный запуск,
реальные вызовы Claude/local LLM, целевой Windows, скорость AMD и полная сборка
**ещё не проверены**. Не писать READY/production/self-trained на основании этих 106 тестов.

## Порядок работы завтра

1. Сохранить работающий 1.0. Создать отдельный worktree v1.1. Проверить Python ≥3.11,
   Git и Docker Desktop с Linux containers. Не ставить второй gateway или второй memory engine.
2. Собрать тестовый образ и выполнить baseline. Образ скачивается только при сборке;
   сами прогоны используют `--pull=never`, `--network=none`, read-only root/source,
   unprivileged uid, 2 CPU / 4 GB / 128 PID. Image ID фиксируется на всю кампанию.
3. Если baseline BLOCKED — исправить конкретную среду/зависимость; не тратить вызовы
   модели и не исключать проверки. `--executor host` допускается только для
   диагностики доверенных исходников командой `assess`, не для модельных правок.
4. Запустить небольшую реальную кампанию. Для local указать реально загруженный
   model ID из имеющегося runtime. Для Claude использовать уже разрешённую авторизацию
   владельца; не отключать permission checks и не извлекать ключи из файлов.
5. Если все сценарии зелёные — `NEEDS_NEW_SCENARIOS` является нормальным результатом.
   В отдельном инженерном проходе превратить подтверждённые owner-дефекты в новые
   неизменяемые тесты. Зафиксировать тесты до эксперимента и начать новую кампанию.
   Не придумывать баги и не улучшать цифры удалением тестов.
6. По каждому победителю выполнить ревью и независимую проверку ниже. Для `validate` предоставить отдельный зафиксированный suite с role=`holdout`: пути
   тестов не пересекаются с train/regression, editable пустой. Один запуск потребляет
   holdout навсегда для этой кампании, включая ошибку; дальнейший run запрещён. Не называть
   два повторения одних тестов независимым holdout. Использовать существующие
   LearningGuard / AutonomyTrainer / evidence ledger для продвижения и rollback.
7. После закрытия 1.0 перенести нужный проверенный commit в интеграционную копию,
   связать CLI с существующим Self Improvement Lab UI/API и собрать установленный
   Windows-продукт. Упаковать инструменты, конфиги и инструкцию: одного wheel
   `bossman-core` недостаточно для repo-level CLI. Повторить штатные owner/CI gates
   на одном итоговом SHA. Не менять версию продукта только ради надписи «1.1».

### Команды из корня отдельной копии v1.1

```text
docker build -t bossman-evolution:1.1 config/evolution
python tools/bossman_evolve.py assess --executor docker
python tools/bossman_evolve.py run --backend claude --model ACTUAL_CLAUDE_MODEL_ID --allow-cloud --review-backend local --review-model ACTUAL_REVIEWER_MODEL_ID --review-url http://127.0.0.1:8081/v1 --iterations 3 --candidates 3 --max-usd 2 --proposal-usd 0.5 --max-seconds 1800
python tools/bossman_evolve.py report
```

Для локальной модели, после её загрузки существующим runtime:

```text
python tools/bossman_evolve.py run --backend local --local-url http://127.0.0.1:8080/v1 --model ACTUAL_LOADED_MODEL_ID --review-model ACTUAL_REVIEWER_MODEL_ID --review-url http://127.0.0.1:8081/v1 --iterations 3 --candidates 3 --max-seconds 1800
```

Заменить все `ACTUAL_*_MODEL_ID` и порты на фактические. Builder/reviewer — разные
модели; runtime должен возвращать именно запрошенную модель. Конфигурация ролей
не доказывает независимость сервера, который игнорирует поле `model`. Не скачивать каталог моделей
целиком и не обещать ROCm-ускорение по одному названию модели. Claude adapter использует
официальные `--safe-mode`, `--tools ""`, `--json-schema`, `--max-budget-usd`:
если установленный CLI их не поддерживает или запрещает вложенный запуск, честно
зафиксировать блокер и запускать поддерживаемым штатным способом, не обходить ограничения.

По умолчанию состояние находится в `~/.bossman/evolution/<base-sha>/`. Можно задать
`--work ABSOLUTE_DIRECTORY_OUTSIDE_REPO`. Повторный запуск использует тот же резерв
расходов; этот предел нельзя сбрасывать автоматическим созданием новой кампании.
Остановить следующие итерации: создать `STOP` в каталоге кампании. Текущий шаг
ограничен timeout. После аварийного завершения оставшийся worktree сохранить для
разбора и удалить штатно только после проверки принадлежности этому эксперименту.

`report.json`, `state.json` и `runs/*` содержат SHA, результаты, расходы, причины
отказа и ветку кандидата. `review-request.json` у победителя перечисляет обязательные
ревью со статусом **PENDING**. `export` выгружает только независимо VERIFIED-записи
разрешённых train-задач с непустым `teach_local_model`. Для новой кампании ноль
примеров — ожидаемый честный результат. Это подготовка данных, а не обучение весов.

## Три P0-кандидата: подключить реальными адаптерами

Полные SHA находятся в `config/evolution/sources.lock.json`. Пока это проверенные
источники и задания на интеграцию, **не установленные и не пройденные reviewers**.

### 1. Cloudflare Security Audit Skill

Источник: https://github.com/cloudflare/security-audit-skill

Изучить закреплённый `skills/security-audit/` и LICENSE. Заимствовать pipeline
recon → coverage ledger → isolated hunters → fresh falsification verifier →
findings → independent record verification. У каждой находки должны быть реальный
source trace, воспроизводимость, затронутая граница и статус
`confirmed` / `needs_validation` / `rejected`.
Выполнять штатные `validate-coverage-ledger.cjs` и `validate-findings.cjs` на
закреплённой версии. Не выдавать schema-valid JSON за доказательство отсутствия багов.
Запуски кода и PoC — только в OS sandbox. Ограничить область изменёнными файлами и
соседними границами доверия; весь монорепозиторий для каждого патча не сканировать.

Проверенный upstream HEAD датирован **14.09.2026**, поэтому не приписывать этому
снимку выпуск в интервале 16–23 сентября.

### 2. Alibaba Open Code Review

Источник: https://github.com/alibaba/open-code-review

Это **code review**, не OCR распознавания документов. Официальный CLI: `ocr`,
пакет `@alibaba-group/open-code-review`; README требует Git ≥2.41.
Перед установкой сверить release/версию/лицензию с закреплённым commit. Не запускать
непроверенный install-script. Модель и бюджет задавать через существующую политику
Bossman; `ocr` сам по себе не наследует лимит нашего proposer.

Документированная команда для отдельной копии кандидата:

```text
ocr review --from BASE_SHA --to CANDIDATE_SHA --format json --output REVIEW_FILE.json
```

Сначала заменить placeholders точными SHA. Сохранить сырые результаты, их hash,
версию инструмента, охваченные файлы/строки, model ID и стоимость. Ненулевой exit,
обрезанный результат, неверный SHA, отсутствие покрытия или недоступная модель —
BLOCKED, а не «замечаний нет». `ocr delegate preview` / `ocr delegate rule` доступны
для host-agent режима, но повторный ответ того же builder не становится независимым ревью.

### 3. Hermes patterns

Источник: https://github.com/NousResearch/hermes-agent

Перенять нужные подходы: сохранение состояния между cron-запусками, небольшой
scope памяти, запрет дублирующих запусков, остановка/steering, лимиты и отчёт.
Не переносить второго агента целиком и не дублировать gateway, секреты, Telegram
или memory authority Bossman. Написанный runner уже сохраняет состояние и бюджеты;
следующий шаг — подключить его к имеющемуся scheduler после первого живого прогона.
Не обещать «30 экспериментов / 4 победителя»: количество победителей измеряется.

## Критерий принятия кандидата в общую сборку

Точный baseline/candidate SHA, неизменяемые тесты, доказанное улучшение, отсутствие
регрессий, свежие независимые ревью, отдельный holdout по реальным задачам,
существующие LearningGuard-гейты, проверенный rollback и повторные CI/owner gates.
Если reviewer не запущен, результат остаётся кандидатом. Результат автора патча
не удостоверяет сам себя. Не добавлять новый путь обхода approvals/budget/LOCAL_ONLY.

Для первого коммерческого пилота после стабилизации выбрать одну задачу владельца:
отчёт по сайту Fresh Vibes, подготовка рекламных материалов или внутренний отчёт
SwapMe. Измерять качество готового артефакта, время ручных исправлений, стоимость
вызовов и фактическую полезность. Публикации, сообщения клиентам и финансовые
операции остаются в штатной политике разрешений. Рост результатов за неделю и
выручка не гарантированы самим наличием learning loop.

## Дополнительные исследовательские опоры

- GEPA: https://github.com/gepa-ai/gepa — оценка кандидатов и отражение ошибок в следующем предложении; зависимость пока не установлена.
- Darwin Gödel Machine: https://sakana.ai/dgm/ — архив кандидатов и исследование альтернатив; опубликованные gains не переносятся на Bossman.
- autoresearch: https://github.com/karpathy/autoresearch — фиксированный измеритель, ограниченный бюджет и журнал экспериментов.
- Agent Lightning: https://github.com/microsoft/agent-lightning — разделение исполнения и обучения; RL не включён.
- mini-SWE-agent: https://github.com/SWE-agent/mini-swe-agent — компактный исследовательский исполнитель; ядро Bossman не заменено.
- Официальный Claude CLI: https://code.claude.com/docs/en/cli-reference

Jev, Paper2Agent, HF GGUF/AMD и Hypit остаются отдельными research-кандидатами из
предложения владельца: здесь их совместимость, даты релизов и качество не проверены.
Не добавлять их в обязательные зависимости сборки по непроверенным описаниям.

## Что вернуть владельцу

Ветка + точный SHA, путь/URL сборки, факт живого запуска proposer, baseline →
candidate результаты, расходы, статусы reviewers, ограничения Windows/Docker/local
runtime и короткая команда следующего запуска. Если есть блокер — его точная причина.
Не заявлять, что веса модели обучились, если выполнялись только ремонт кода и запись опыта.
