# Terminal Run 1.2 — приёмка и следующий прогон владельца

Status: REQUIREMENTS / NOT_RUN until evidence is attached. Все примеры новых команд — целевой интерфейс из MASTER; Claude обязан сначала реализовать и проверить его, затем записать точный фактический синтаксис в существующий `docs/owner/OWNER_RUN_NEXT.md` и --help.

## Что считается успехом

Человек открывает обычный Windows CMD, запускает Bossman, разговаривает по-русски, поручает задачу и видит реальный результат. Claude Code может управлять той же задачей через JSON-команды. Дашборд и Telegram видят те же IDs, файлы и состояние. Нет второй памяти, отдельной терминальной сборки продукта или ручного переноса данных.

Самоисправление использует тот же контролируемый lab/candidate механизм, что и другие каналы. Не вводить в заблуждение владельца: общие файлы проекта не означают разрешение ученику менять stable напрямую.

## Минимальная матрица

| ID | Что проверяем | Доказательство и критерий |
|---|---|---|
| TR-01 | Чистый Windows ZIP | CMD/ConHost запускает клиент без checkout, системного Python, Node и WSL; offline import smoke |
| TR-02 | Второй terminal host | Windows Terminal с профилем CMD: input, resize, scrollback, цвета; отсутствие TTY даёт plain/JSON, а не падение |
| TR-03 | Старый CLI | Существующие serve/task/project/models и их help/exit contracts не сломаны |
| TR-04 | Кириллица/пути | Русский запрос и файл, пути с пробелами/кириллицей, CRLF/LF, UTF-8 input-file; без mojibake |
| TR-05 | Общий instance | CLI/UI/Telegram показывают одинаковый instance/data-root/project/task ID; другой root не создаётся молча |
| TR-06 | Общие файлы | CLI -> изменить разрешённый fixture -> UI читает те же байты/ревизию; затем UI -> изменить -> CLI читает новую версию; без copy/sync |
| TR-07 | Общая память | Verified lesson, записанный общим API, доступен из CLI и задачи; restart; deletion/supersession не воскресает из history/cache |
| TR-08 | Настоящий chat | Много ходов, контекст/файлы, tools; реальная модель отдельно от scripted fixture; ответ не идёт в обход Gateway |
| TR-09 | Structured exec | Без TTY, один валидный JSON/JSONL-поток, ограниченный output, stdout чистый; stderr не раскрывает секреты |
| TR-10 | Replay/reconnect | Разрыв после submit и повтор request ID дают одну задачу; event replay не повторяет мутацию; нет busy polling |
| TR-11 | Close/detach | Закрыть CMD во время backend-owned задачи -> продолжить мониторинг через UI/Telegram/новый CLI; не убить чужие процессы |
| TR-12 | STOP/cancel/resume | Global STOP из Telegram блокирует CLI и наоборот; restart сохраняет STOP; cancel acknowledgment честный; Resume заново наблюдает |
| TR-13 | Approval | Отказ, expiry, изменённые args, replay и одновременный approve из двух каналов; эффект не дублируется |
| TR-14 | Terminal injection | Вывод с ANSI/OSC52/CR/markup/поддельным prompt/RTL не меняет clipboard/терминал и не выдаётся за owner instruction |
| TR-15 | Файловые границы | traversal, junction/reparse, protected path, файл поменялся между чтением и записью; отказаться или согласовать новую ревизию |
| TR-16 | Coding/evolution | Через CLI -> API -> local sidecar -> файл -> regression -> independent diff/verifier; плохой patch отклонён, stable неизменен |
| TR-17 | Браузер/Computer Use | Задача из CLI реально открывает/читает/скачивает в тестовом scope и проверяет файл; lease, focus, fresh observation и STOP |
| TR-18 | Медиа | CLI передаёт задачу существующему Studio; общий артефакт и provenance; фото/короткое видео отдельно от fixture; cancel/restart |
| TR-19 | Все capabilities | Registry-backed parity matrix покрывает исходные функции; у неподключённых есть причина, а не фальшивое ENABLED |
| TR-20 | Нет модели/сети/прав | Таймаут/ошибка -> понятное состояние и recovery, без тихого облачного fallback, пустого PASS или лишних расходов |
| TR-21 | Производительность | Интерактивный ввод не блокируется, idle не запускает модель и не грузит CPU; измерен same-task GUI/CLI overhead |
| TR-22 | Teacher и память | Учитель управляет через exec/status/events; помощь помечается; lesson/restart/unseen transfer разделены |

Linux/mock tests — CONTRACT_PASS, не Windows owner PASS. Проверка redirected subprocess не заменяет ConHost/ConPTY interaction. Где облачный Windows runner не имеет интерактивного desktop, пометить NOT_RUN и оставить конкретный короткий owner test. Скриншот дизайна никогда не evidence.

## Сценарий 1 — старт и разговор

До запуска: source SHA/ZIP hash совпадают; backup настроек и данных; действующий owner scope; runtime не подменён. Запустить из пути с пробелами, проверить --help, версию и instance. Сказать: «Какой проект открыт, какие локальные модели действительно доступны и какие задачи идут?» Сравнить с backend, а не с mock fixture.

Продолжить разговор, сослаться на небольшой разрешённый файл. Закрыть клиента, открыть сессию снова. Открывать дашборд требуется только один раз для cross-channel приёмки, не на каждой учебной итерации.

## Сценарий 2 — одни данные через разные каналы

Создать в разрешённом тестовом проекте `terminal-parity.txt` с уникальным маркером через CLI. UI должен показать тот же artifact/path и содержимое. Изменить через UI, прочитать через CLI. ID, ревизии и bytes включить в evidence; конфликт одновременных правок не решать молчаливым overwrite.

Создать задачу из CLI, посмотреть её в Telegram, выполнить pause/resume по существующей политике. Убедиться, что появился один run, а не три записи. Все memory/skills/settings читаются из действующих общих stores.

## Сценарий 3 — Bossman улучшает Bossman

В выделенном lab выполнить одну настоящую ограниченную задачу через Coding path. Claude наблюдает через machine-readable CLI. Ученик исследует, правит, тестирует и возвращает evidence. Независимый verifier читает исходную задачу и diff, проверяет regression/соседей и отказ плохому patch.

Требуемый результат: полезный кандидат и test evidence, затем применимый lesson. Автономное применение в stable не является критерием успеха. После полного restart дать новую аналогичную задачу без ответа учителя. NO_TRANSFER допустим как честный измеренный исход, не как заявленная готовность самообучения.

## Сценарий 4 — реальная работа через терминал

Через CLI поручить официальное исследование MVČR и подготовку пакета по существующему `tests/owner_scenarios/OWNER_HARDWARE_FIRST_RUN.md`. В облаке только синтетические данные; локально лишь owner-approved документы. Не выдумывать даты, право на ПМЖ, платёжные реквизиты или способ подачи. Проверить загруженные PDF и заполненный draft, собрать MISSING_DATA и SOURCES, остановиться в WAIT_APPROVAL. Ничего не отправлять, не подписывать, не оплачивать, не проходить BankID/Data Box за владельца.

Отдельно короткая генерация изображения и, в разрешённом бюджете, TestRun видео через тот же Studio. Путь, actual dimensions/frames/fps/duration, hashes и decode — из настоящего результата. Большие модели/длинное видео не блокируют проверку самого CLI.

## Сценарий 5 — сравнение накладных расходов

Гипотеза владельца: Claude тратит меньше времени/токенов на управление и обучение через CLI, чем нажимая элементы дашборда.

Сравнить: A — управление задачей через GUI; B — ту же задачу через CLI/JSON. Один backend/model/quant/runtime, одинаковые tools/policies, baseline, memory snapshot, context/input, limits и criteria. Порядок чередовать; warm/cold загрузку учитывать отдельно; минимум несколько пар при доступном бюджете. Не передавать ответ прошлого прогона следующему.

Измерять отдельно: запуск клиента; время dispatch; UI/CLI overhead; prefill/generation; test execution; teacher interactions; screenshots/UI actions; terminal tool calls; объём переданного контекста; wall-clock до verified result; resource peak. Ускорение от другой модели не приписывать CLI. Результат: MEASURED_OVERHEAD_REDUCTION или NO_MEASURED_GAIN. Маленькая выборка — пилот, не универсальная гарантия.

## Отладка без ручной пересборки владельцем

Если не работает: сохранить текущий SHA, command, exit code, task ID, sanitized stderr и последний event cursor. Документировать, остановилась только видимость событий или реальная задача. Не чинить установленный ZIP копированием Python-файла из checkout; новый build = новый SHA/архив и повтор затронутых тестов.

## Что интегратор передаёт

`docs/owner/OWNER_RUN_NEXT.md` содержит проверенную команду для chat и для Claude/headless, где лежит task input и как получить events/result, как остановить и возобновить, где общий data root, как вернуться к прежней сборке. Команды в этом документе генерируются/проверяются по --help, не додумываются.

Evidence report: TR-01…22 с PASS/FAIL/PARTIAL/NOT_RUN, exact code/build/runtime/model, logs/артефакты и отрицательные контроли. Статусы readiness CLI, self-repair, transfer и revenue независимы: красивый shell не повышает автоматически уровень North Star.
