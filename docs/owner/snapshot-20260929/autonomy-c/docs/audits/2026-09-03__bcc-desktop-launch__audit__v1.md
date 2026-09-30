# Аудит: ярлык BOSSMAN на рабочем столе — «нажимаю, не открывается» + шум ПК

Дата: 2026-09-03 · Ветка: `claude/bossman-control-v03-43igbk` · Код НЕ менялся (только диагностика)

## TL;DR

**Причина найдена и воспроизведена:** `UnicodeEncodeError` в `bcc/auth.py:40` —
`TokenAuth.announce()` делает сырой `print()` с русским текстом в `sys.stdout`.
При кодировке консоли cp1252 падает весь стартовый цепочник
`create_app() → Services → TokenAuth` ещё до поднятия сервера. Коммит `c691853`
(«fix(desktop): never crash on console output») починил кодировку только в
`desktop.py run()` (`out.reconfigure`), но НЕ в `auth.py` — окно запуска умирает
молча под `pythonw` (stdout/stderr = None, трейсбек некуда писать).

**Доказательство (воспроизведение, без правок кода):**

```text
C:\Python314\python.exe -c "from bcc.desktop import _BackgroundServer; ..."
→ UnicodeEncodeError: 'charmap' codec can't encode characters (cp1252)
  File "bcc\auth.py", line 40, in announce
    print(f"[bcc] {head} доступа: {self.token} ...", flush=True)
  File "bcc\api.py", line 68, in Services.__init__
  File "bcc\api.py", line 310, in create_app
  File "bcc\desktop.py", line 190, in _BackgroundServer.__init__

тот же запуск с PYTHONIOENCODING=utf-8:
→ [bcc] токен доступа: ...
→ SERVER STARTED: True
```

## Хронология вечера (по файлам, все времена локальные)

| Время | Событие | Источник |
|---|---|---|
| 19:22:20 | Последняя успешная сессия Command Center (login) | `bcc.db` таблица `sessions` |
| 21:12 | Запущен OpenCode Desktop (7+ процессов) | Win32_Process |
| 21:22–21:23 | Запись в `bcc.db-wal` (сервер или тесты) | mtime файла |
| 21:26–21:27:35 | Серия pytest-запусков desktop-тестов (порты 53399/53404/53249/60532/60537/50063/56958 — случайные, НЕ 8800) | `desktop-run.log` |
| **21:28:22** | **Клик пользователя: `start pid=18544 url=http://127.0.0.1:8800/` — и СТРОКИ ЗАВЕРШЕНИЯ НЕТ** | `desktop-run.log` |
| 21:30 | Процесса `pythonw` нет, порт 8800 закрыт, окна нет | Get-Process / Test-NetConnection |

Отсутствие строки `browser-exit` после `start` = процесс умер МЕЖДУ логом старта
(`desktop.py:282`) и запуском окна: в `_BackgroundServer.__init__` / `started.start()`
— ровно там, где воспроизводится краш `auth.py:40`. Под `pythonw` исключение
уходит в stderr=None → полная тишина для владельца.

## Почему ПК «зашумел и всё нагрузилось»

Одновременно работали: OpenCode Desktop (GPU/renderer/utility процессы с 21:12),
его сессия гоняла pytest по этому репо (см. лог тестовых запусков 21:26–21:27),
плюс клик поднимал uvicorn-сервер + Chromium-окно. Суммарная CPU-нагрузка
(в момент замера 34%) и дала шум кулеров. Отдельного «злого» процесса не
обнаружено: никаких посторонних python/node/uvicorn в системе нет.

## Сопутствующий риск (зафиксировать, НЕ чинить в этой сессии)

1. `desktop-тесты` пишут в **живую** базу владельца: тесты не переопределяют
   `BCC_DATA_DIR`, поэтому `bcc.db`/`bcc.db-wal` в `command-center/data`
   обновлялись во время pytest (21:22–21:23) — гонка с реальным сервером.
2. Любое падение под `pythonw` невидимо: нет ни консоли, ни журнала. В рабочей
   копии есть НЕЗАКОММИЧЕННАЯ правка `desktop.py` (+69/−3: run-log + lock) —
   она уже пишет `desktop-run.log` (это помогло расследованию), но не покрывает
   исключения в `auth.py` и не перенаправляет `sys.stdout/stderr` процесса.
3. Токен доступа печатается в stdout (`auth.py:40`) — при будущем
   перенаправлении в файл-лог токен попадёт в файл в открытом виде.

## Рекомендуемые фиксы (НЕ применены — по указанию владельца)

1. `bcc/auth.py:40`: заменить `print(...)` на encoding-safe вывод
   (`sys.stdout` guard + `errors="replace"`) или `logging`.
2. `bcc/desktop.py run()` (pythonw-ветка): перенаправить `sys.stdout/sys.stderr`
   всего процесса в `desktop-run.log`/devnull, а не только локальную `out`.
3. Тестам задать изолированный `BCC_DATA_DIR` (tmp_path), чтобы не трогать
   `command-center/data`.
4. Не печатать токен целиком: маскировать, кроме первых 4 символов.

## Что проверить после фикса

`python -m bcc.desktop --host 127.0.0.1 --port 8800` из консоли → сервер
поднимается (воспроизведено с utf-8), затем двойной клик ярлыка → окно
открывается, в `desktop-run.log` появляется пара `start` + `browser-exit`.
