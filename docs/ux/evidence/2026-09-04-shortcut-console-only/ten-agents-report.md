# 10-агентный прогон базовых функций (2026-09-04, машина владельца, сервер :8800)

Метод: 10 параллельных read-only агентов (auth, browser, terminal, tasks,
models, skills, desktop-упаковка, гигиена, UI-статика, data-dir). Тяжёлое не
трогали: POST /api/browser/sessions НЕ вызывался, браузеры не запускались,
сервер не перезапускался. Секреты нигде не печатались.

## Зелёное (9/10 чисто)

| Агент | Проверки |
|---|---|
| auth | identity OK; 401 без токена; 200 с `X-BCC-Token`; login ставит HttpOnly-cookie + CSRF |
| browser | `/health`: `available=true`, sessions=0; `GET /sessions` 200 (видна строка id=1 `created` — остаток краша владельца); путь `BrowserUnavailable` в коде есть |
| terminal | `GET /api/terminal/sessions` 200 (пусто); `roots` 200 (1 корень) |
| tasks | `/api/tasks`, `/missions`, `/forks?task_id=1`, `/taskxchange/queue` — все 200, 500 нет |
| models | `/providers/kinds` (2), `/models` (0), `/router/rules` — все 200 |
| skills | `/skills` 200 (18 штук), evaluations, retrospectives — 500 нет |
| desktop | `--print-launcher` exit 0; оба `BOSSMAN.lnk` на месте; `bossman.ico` есть; в логе токена нет; `--enable-logging` и `desktop.lock` в коде |
| hygiene | secret scan PASS; compileall exit 0; дерево чистое |
| ui | `/` 200 HTML; все файлы UI на месте; `/manifest.json` 200 |

## Найденные ошибки (3)

1. **P1 — протухший `desktop.lock` блокирует окно.** Убитые `Stop-Process`
   запуски не проходят `finally` → замок `{"pid": 7088}` остался лежать при
   мёртвом процессе. Пока на порту висел чужой живой сервер, guard считал
   первое окно открытым и отказывался открывать второе. Замок удалён вручную;
   guard корректно отработал stale-путь только после чистки. Вывод: нужен
   PID-liveness check в дополнение к probe порта (задача в бэклог).
2. **P2 — `/ui/manifest.json` 404** (манифест отдаётся с `/manifest.json`).
   Если `index.html` ссылается на `/ui/...` — сломана установка PWA. Проверить ссылку.
3. **P1 (открыта) — POST /api/browser/sessions не воспроизведён здесь.**
   Строка `id=1 status=created` в БД подтверждает: создание сессии владельца
   упало между INSERT и `running` (исключение вне `BrowserUnavailable`/
   `BrowserPolicyDenied` → 500 или смерть воркера). Эндпоинт ловит только 2 типа
   ошибок — любой другой взрыв Playwright (память, GPU, missing browser) идёт
   наружу необработанным. Требуется: catch-all → 500 с `status=failed` + тест.

## Зачистка после прогона (мои остатки)

- Убиты detached-остатки убитых вызовов агента: `python -m bcc.app`
  (pid 14324, затем pid 20248) — оба держали :8800 без открывателя окна и ломали
  UX ярлыка («только консоль»). Порт проверен: слушателей 0.
- Удалён протухший `desktop.lock` (pid 7088).
- Машина оставлена чистой для замера VRAM владельцем.
