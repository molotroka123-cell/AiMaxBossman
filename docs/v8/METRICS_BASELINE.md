# Studio phase 0 — реальные замеры

База: `d1b96687f02b6c17f829c671cfe4a4987d00c489`. Платформа: `Linux-6.18.44-x86_64-with-glibc2.39`, Python 3.12.14.

| Метрика | До | После |
|---|---:|---:|
| import bcc.app, ms | 448.499 | 392.234 |
| RSS before 60s idle, MiB | 101.516 | 98.098 |
| RSS after 60s idle, MiB | 103.180 | 100.137 |
| Idle CPU, % of one core | 0.783 | 0.733 |
| First GET /api/images/models, ms | 140.817 | 143.387 |
| Services.start to first response, ms | 223.064 | 208.018 |
| Idle observation, seconds | 60.000 | 60.000 |
| Process count | 1.000 | 1.000 |

Метод: `python docs/v8/measure_idle.py` в окружении с зависимостями проекта.
Временная SQLite, настоящий `create_app` и `Services.start(start_workers=True)`;
Images tick 0.7 s активен в обоих замерах, ошибок tick нет. RSS — psutil RSS
процесса Linux, не Windows working set и не измерение целевого Ryzen.
GET идёт через HTTPX ASGITransport с настоящей авторизацией и маршрутом,
**без TCP**. CPU — разность user+system CPU time за 60 секунд / wall time.
Числа одиночные, не статистический benchmark. Второй замер шёл одновременно
с корневым набором тестов; это ограничивает сравнение задержек. Фаза 0 не
импортируется продуктом и не меняет его очередь/маршруты/фоновые задачи.
Первый ответ отличается на +2.57 ms (+1.83%); вывод о регрессии из этого
не следует. Фазовый допуск для наблюдения: +10% по import/first answer/RSS,
+0.5 процентного пункта CPU; это диагностический допуск, не ослабление теста.

Исходные JSON: `evidence/baseline-before.json`, `evidence/baseline-after.json`.
Архив: NOT_CAPTURED — в фазе 0 новый Windows-архив не собирался и не измерялся.
Windows installed acceptance / UI sweep: NOT_RUN, новых продуктовых маршрутов нет.
Аппаратная приёмка владельца: OWNER_REQUIRED.
