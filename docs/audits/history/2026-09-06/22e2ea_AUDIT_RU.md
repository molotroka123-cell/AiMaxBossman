# Bossman — аудит стадии и границ, 6 сентября 2026

AUDITED_SHA=22e2ea304bcc2f4f51b24af41c881f0f9353d827
BRANCH=claude/bossman-control-v03-43igbk
TREE_SHA=96399dac247bf24f50e1d65ec7a7aa988738a98d
VERDICT=INTEGRATION_ALPHA_NOT_RELEASE_READY
V4_ACCEPTED=NO
V5_ACCEPTED=NO
HUMAN_LEVEL_COMPUTER_USE=NOT_PROVEN

## Что проверено

Свежие remote refs, метаданные и diff PR31, PR25/29/30, текущие документы V4/V5,
актуальные GitHub Actions на SHA выше, полный исходный архив этого SHA и JUnit
из CI. Углублённо проверены operator manager/store/router/verifier, browser
adapter, learning evidence ledger, V5 admission/workspace и профиль скорости.
Это НЕ построчное ревью всех 2316 файлов и НЕ полный UI-прогон на машине владельца.
Изучена структура полного снимка, но динамические репродукции ограничены указанными
компонентами. Никакой новый код продукта в этой аудиторской сессии не исправлен
и не запушен; данный пакет содержит тесты и доказательства аудита.

## Прогресс

PR31 действительно слит 2026-09-06 15:53:17 UTC. Он объединяет 36 коммитов,
110 файлов, +13669/-291 строк, включая ранее написанный V5 и тесты. Сравнение
предыдущей основной 400799a42075eab1f5f780180de30a87e636ecc5 с текущей:
40 коммитов впереди, 0 позади. Эти числа НЕ равны числу новых функций или
скорости verified engineering throughput.

В main интегрированы V5 store/CAS/observers/world-state/admission/reconciliation,
контекстный фильтр, workspace и hostile tests; SQLite lifetime, управление
Pause/Stop/Take-control во время шага; оптимизация Windows UIA, параллельное
наблюдение/снимок экрана, retention кадров; Ollama-host normalization и
corpus provenance / promotion ledger. Это существенное объединение реализации,
а не только документация. Но общая приёмка не завершена.

PR29 с action snapshot, effect-time authorization и дополнительными V4 DAG/
approval исправлениями остаётся отдельным draft PR. Browser fastpath fb6591e
тоже отсутствует в текущем main: там сохранён старый playwright_browser.py.
PR30 с автоматической записью real-workload samples остаётся draft/conflicting;
наличие telemetry.py в main пока не равнозначно постоянному сбору каждой миссии.

## Новые воспроизводимые результаты

Два независимых pytest запуска на Python 3.13.5/Linux завершились exit 1:
ledger: 3 failed + 2 passed; operator: 3 failed + 1 passed.
Всего 6 нарушенных safety assertions и 3 положительных контроля.
Это шесть сценариев, а не шесть обязательно независимых первопричин:
три относятся к одной системе single-use evidence.

Полный реестр: FINDINGS.json. Ключевые случаи:
AT-01: модель возвращает COMPLETE, файл не создан, исполнитель не вызывался,
но standalone ComputerOperatorManager пишет COMPLETED. Это существующий обход
сквозной семантики завершения, не утверждение о дефекте каждого BCC finalizer.
AT-02: восстановление повторно открытого store меняет PAUSED на RECOVERING.
Не тестировалось убийство реального desktop процесса; доказана сама смена
сохранённого owner-state в production recovery routine.
AT-03: внешний экран изменяется после verified step, не меняя task generation;
оптимизированный цикл передаёт следующему действию старое состояние. Контроль
с отключённым reuse проходит. Проверка выполнена с детерминированным desktop
fixture, не на настоящем чужом приложении.
AT-04a/b/c: эвикция, повреждение JSON и конкурирующие экземпляры ledger позволяют
повторно принять измерение для другого кандидата. Доказан дефект ledger,
но не фактическая публикация вредоносного навыка на пользовательском компьютере.
AT-05: свежий root CI прерывает сбор двух тестов: Core package init импортирует
FastAPI, отсутствующий в declared root environment. Exit 2, обе Python-матрицы
FAIL. Это repository-local packaging/CI boundary, не проблема ноутбука.

## Exact-SHA CI

Root run34043712462: FAIL collection (2 errors в py3.12), py3.11 также FAIL.
Container/shared-package job: PASS, он не компенсирует падение root pytest.
Intelligence run34043712445: contract PASS, measured job FAIL на шаге требования
current same-model evidence; evaluator не выполнялся. Это НЕ доказательство
падения интеллекта, а отсутствие допускающего измерения.
Media/Web/Fleet run34043712461, job101514904021, Python3.12: PASS.
Его JUnit независимо прочитан: 3 preflight + 87 Fleet + 299 media/web +48
interaction = 437 passed; 11 media/web skipped. Пропуски не считаются пройденными.
Python3.11 той же workflow ещё QUEUED при последней проверке.
Core матрица ещё не завершена; Command Center full pytest ещё выполняется,
при этом Windows-path и security/JS jobs прошли. ASTRA Windows portable job
прошёл, что не заменяет owner desktop/live acceptance.

## Человеческая скорость

В main есть реальные оптимизации расходов исполнения: bounded UIA walk,
одно разрешение foreground, параллельный capture и меньше повторных наблюдений.
Но operator_step_profile.py использует CostedObserver/Planner/Adapter со
стоимостью, которую задаёт вызывающий. Это измерение framework overhead,
не производительности локальной модели и Windows.
Старый browser fastpath результат в сообщении коммита (177.08→19.88ms,
p95=98.78ms) относится к другой ветке и локальному fixture. Сырые измерения
для этого числа в текущем аудите не перепроверены. В основной ветке patch отсутствует.
Самый быстрый unsafe вариант не проходит приёмку: AT-03 сначала закрывается,
затем latency меряется снова. Цель <100ms — инженерный SLO для определённого
этапа, не доказательство универсального человеческого уровня.

Нужны отдельно input dispatch, observation, model decision, verified action,
время чужого сервиса и recovery; p50/p95/p99, cold/warm, все ошибки, paired human
workflow baseline на одной машине. Higgsfield server generation ~минуты не
может считаться агентной задержкой. Новая оптимизация не меняла веса модели.

## Стадии

V3: реально работающие слои, но standalone completion/recovery и общий CI
мешают сквозному закрытию. V4: инженерная интеграция Generation A, M0/M10/M11
не приняты, адаптивные стратегии B и композиция C не имеют актуального допуска.
V5: N1-N3 компонентно реализованы, N4-N8 частичны. N0 не принят. Workspace
явно STANDING_AUTONOMY_ENABLED=False, observers_running=False,
admission_enabled=False. Это существующий каркас, не включённая автономная ОС.
Текущий V5_RELEASE_SCORECARD прямо содержит V5_NOT_COMPLETE; его отдельные PASS
исторические и не заменяют новую SHA-сертификацию. Заявленные там нули P0/P1
нельзя переносить на наши новые отрицательные сценарии.

## Что делать первым

1. Закрыть AT-01/02/03/04 с независимыми регрессиями; не превращать красные
   assertions в skip/xfail и не ослаблять требование результата/актуальности.
2. Развести root/Core import boundaries и прогнать exact-SHA CI целиком.
3. Семантически интегрировать оставшиеся PR29/browser/telemetry, без потери
   уже влитого V5. Не менять незавершённый owner UI worktree.
4. Реальный GUI/Windows/same-model прогон с метриками, smoke и негативными кейсами.
5. N4-N8, fair scheduling, production ports, wizard/evidence UX, canary,
   rollback и реальный soak; затем формальное решение о допуске, не заранее.

## Первичные ссылки

https://github.com/molotroka123-cell/AiMaxBossman/pull/31
https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34043712462
https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34043712461
https://github.com/molotroka123-cell/AiMaxBossman/actions/runs/34043712445
https://github.com/molotroka123-cell/AiMaxBossman/blob/22e2ea304bcc2f4f51b24af41c881f0f9353d827/docs/v5/V5_RELEASE_SCORECARD.md
https://github.com/molotroka123-cell/AiMaxBossman/blob/22e2ea304bcc2f4f51b24af41c881f0f9353d827/bossman-core/bossman/computer_operator/manager.py
https://github.com/molotroka123-cell/AiMaxBossman/blob/22e2ea304bcc2f4f51b24af41c881f0f9353d827/bossman-core/bossman/learning_guard/evidence_ledger.py
