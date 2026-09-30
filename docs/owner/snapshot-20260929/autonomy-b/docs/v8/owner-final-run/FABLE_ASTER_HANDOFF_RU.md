# Fable / Астер — подготовить владельческий GUI-прогон, не новый аудит

Сначала прочитать `PREPARATION_MEMORY_RU.md`. Единственная ветка `claude/bossman-final-convergence-hu2702`. Новые функции и отдельный fork не добавлять. Владелец хочет приехать и работать; shell/API/pytest не заменяют его GUI-прогон.

## Fable / финальный интегратор

1. PREP-02: исправить Windows discovery без WMIC; CIM/JSON, корректные unknown/foreign/target, таймауты и диагностика, не ложный software-ready при ошибке определения. Проверить actual installed script и Machine-Report, не только copy в repo. Официальные источники: https://learn.microsoft.com/en-us/windows/win32/wmisdk/wmic и https://learn.microsoft.com/en-us/powershell/module/cimcmdlets/get-ciminstance .
2. Прочитать уже закрытые OA-01–04 и не писать их повторно. Новые состояния не должны ослаблять действующие gates. Все known failures остаются в ledger с фактическим статусом.
3. В конечный ZIP положить этот пакет как `app-support/owner-final-run/`: инструкции, локальный checkpoint-template, пример отчёта, подготовленные безопасные fixtures, release-record и нужные очищенные снимки контекста в context/. Нормальный owner-run не требует Git и developer Python. Зафиксировать комплект в manifest/checksums ДО сборки; контракт отсутствие файла → FAIL.
4. Подготовить OpenCode-профиль согласно установленной версии. Не включать глобальный all-tools allow. Доказать независимый GUI-driver через Блокнот; две роли — внешний OpenCode и агент Bossman — не должны подменять друг друга.
5. Проверить обновление/откат двух архивов и автоматические diagnostics. Не открывать новую БД старым runtime без совместимости или backup. Подготовить понятное сообщение при failed startup, не молча закрывающееся окно.
6. После интеграции — полный существующий CI и Windows installed profile не меньше текущих 46 сценариев, без скрытых skips; owner-GUI evidence дополнительное. Выпустить exact tested ZIP и обратную сверку его SHA-256, не rebuild.

## Астер / Studio и отзывчивость

1. PREP-03: `policy.prices[model]=0` — разрешённая оценка владельца, не фактический тариф. До первого POST нужны авторитетные модель/параметры/тариф и корректный cap. При unknown/nonzero и free_only — никаких вызовов генерации. Тест paid-model+local-zero должен наблюдать 0 исходящих POST. Не включать реальное платное тестирование без отдельного согласия.
2. Сохранить existing queue, task-bound cancellation, revoke-egress, exact reference hashes, cloud default-off, no blind resubmission, byte verification/provenance. Отдельно различать импорт, mock, реально сгенерированный asset и проверенный provider.
3. Закрыть GUI перенос живого image/video в редакторы и восстановление после restart при разрешённом provider. Без доступа оставить LIVE_PENDING; продолжать contract/packaging работы, не выдавать их за live.
4. Большая галерея и hash/decode не должны блокировать ввод. Измерить event-loop/UI latency на установленной Windows, полное дерево процессов и безопасный soak. Не вводить фоновые демоны ради одного теста. Сам факт 101 MiB Linux RSS не доказательство всей Windows-системы.
5. Подготовить профили локального model-server/checkpoint без гигантских скачиваний и без меняющихся latest-зависимостей. Указать отдельно LLM, images, video и tool/structured capability. Не обещать локальное видео только по работоспособности ComfyUI text-to-image.

## Общее соглашение о работе

Перед записью fetch и чтение current HEAD; один интегратор, узкие commits, fast-forward. Нельзя стирать результаты соседа или метить старый архив новым SHA. Согласовать общие файлы до правок. Не возобновлять старые мониторы. Проверять рабочую ветку, а не поиск коммитов только default branch.

Все synthetic tests используются для дефектов и негативных контролей. Финальный пользовательский эффект подтверждается настоящим GUI. Если принятие «одного клика» требует retry, первая попытка остаётся failure, пока причина не устранена/не классифицирована.

Обязательный handoff в одном отчёте: выполненные PREP ID, ссылки на commits и проверки, actual ZIP/hash/size, remaining product bugs отдельно от owner inputs, готовность OpenCode+GUI driver, источник fixtures, measured responsiveness, rollback. Не заканчивать «остался ключ», если незакрыты код/комплектация/локальный driver.

Фактическое выполнение задач не начинается от наличия этого файла автоматически. Получивший поручение агент должен отметить CLAIMED с timestamp и current SHA, выполнить работу и записать результат; не приписывать соседу запуск без подтверждения.
