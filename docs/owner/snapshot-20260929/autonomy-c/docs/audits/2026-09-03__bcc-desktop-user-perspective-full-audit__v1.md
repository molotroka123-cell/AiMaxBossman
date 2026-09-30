# Полный аудит от лица пользователя: «Кликаю BOSSMAN — не открывается»

Дата: 2026-09-03 · Ветка: `claude/bossman-control-v03-43igbk` · Код НЕ менялся · 10 параллельных агентов, лимит 15 минут

> Написано «от первого лица пользователя»: что я вижу на экране, что крашится, с воспроизведением и доказательствами.

## 1. ЧТО Я ВИЖУ КАК ПОЛЬЗОВАТЕЛЬ

1. Двойной клик по ярлыку «BOSSMAN» на рабочем столе.
2. Иногда открывается окно Chrome с адресом `http://127.0.0.1:8800/` — но страница НЕ загружается (ERR_CONNECTION_REFUSED).
3. Иногда окно закрывается **мгновенно** (lifetime=0.0s), и ничего не происходит.
4. Консольная команда `bcc --help` не работает: `No module named bcc.__main__`.
5. Комп периодически «шумит» — кулеры на повышенных оборотах, в основном в моменты запусков и тестов.

## 2. ЧТО НАЙДЕНО (10 агентов, с воспроизведением)

| # | Проверка | Результат | Доказательство |
|---|---|---|---|
| 1 | Ярлык BOSSMAN.lnk | Таргет `C:\Python314\python.exe` (консольный, не pythonw) + `-m bcc.desktop --host 127.0.0.1 --port 8800`, WorkDir = корень репо | чтение через WScript.Shell |
| 2 | Клик ровно как по ярлыку | **Окно Chrome --app открылось, НО сервер-бэкенд на 8800 НЕ слушает** (порт закрыт, python-процесс жив 40 с, CPU 0.1 с) → страница мёртвая | desktop-run.log: `start pid=... url=...:8800/` без `browser-exit`; Test-NetConnection = False |
| 3 | Воспроизведение краша | `UnicodeEncodeError: 'charmap' codec can't encode` в `bcc/auth.py:40` — сырой print русского текста в stdout при cp1252; с `PYTHONIOENCODING=utf-8` → `SERVER STARTED: True` | трейсбек (приложение A) |
| 4 | Короткоживущие окна | Механизм single-instance Chrome: второй `--app` на том же профиле молча выходит с code=0; двойной клик быстрее жизни первого → «само закрылось» | 16 записей, 4 шт. `browser-exit code=0 lifetime=0.0s` |
| 5 | CLI bcc | `bcc --help` = EXIT 1 (нет `bcc/__main__.py`), консольных скриптов `Scripts\bcc*` нет (entry-points не установлены), `python -m bcc.app --help` **запускает сервер** вместо справки | EXIT-коды и трейсбеки |
| 6 | Тесты bossman-core | Память/состояние зелёные: 7 + 9 + 8 passed (скипы — Postgres/live) | вывод pytest |
| 7 | Импорты бэкенда | 5/6 OK; **`bossman.cognitive` — пустой пакет (только `__pycache__`)** — работа из предыдущей сессии на feature-ветке не смержена | ModuleNotFoundError |
| 8 | Gateway | импорт OK, CLI OK, сервер стартует на 8765; `--port` CLI игнорируется (порт всегда из конфига) | живой запуск 3 с |
| 9 | Системная нагрузка | CPU 20%, свободно 3.8 ГБ, python/node-процессов нет, крашей Windows нет. Шум = OpenCode (7 процессов, CPU ~1335 с с 21:12) + Edge (8) + разовые пики (uvicorn + Chrome + pytest одновременно) | Get-CimInstance, event-лог |
| 10 | Инфра надёжности | bcc.db: integrity=ok, WAL чист. Профиль браузера: 0 процессов, 0 локов; chrome_debug.log = 0 байт (бесполезен). `.env` не создан (только .env.example). `*.log` НЕ в .gitignore. Рабочее дерево чистое, синхрон с origin | sqlite PRAGMA, git status |

## 3. ПОЧЕМУ ЭТО ПРОИСХОДИТ (диагноз от «щёлчка» до «мёртвого окна»)

```
Клик → python.exe -m bcc.desktop (запущен из проводника: stdout=None/редирект cp1252)
     → _BackgroundServer(8800) → bcc.app.create() → Services → TokenAuth.__init__
     → auth.py:40 print("... русский текст ...") → UnicodeEncodeError
     └─ путь А (кодировка сломала создание сервера):
          сервер не поднялся → окно Chrome всё равно открывается
          → страница ERR_CONNECTION_REFUSED → python висит (ждёт закрытия окна)
          → «не открывается»
     └─ путь Б (двойной клик, single-instance Chrome):
          2-й --app на том же профиле → handoff → code=0, lifetime=0.0s → «само закрылось»
```

**Дефекты (по влиянию на пользователя):**

1. **CR-1 (критичный UX)** — бэкенд 8800 стартует ненадёжно: `auth.py:40` делает сырой `print()` в stdout; под cp1252/без консоли это роняет `create_app()`. Фикс `c691853` починил только `desktop.py` (`out.reconfigure`), но не `auth.py`. Воспроизведено: без `PYTHONIOENCODING` → UnicodeEncodeError; с ним → `SERVER STARTED: True` (identity 200 OK, version 0.1.0).
2. **CR-2 (критичный UX)** — повторный/двойной клик мгновенно закрывает окно (Chrome single-instance на общем профиле), а uvicorn-поток (daemon) умирает вместе с main-процессом → порт закрывается.
3. **CR-3 (функциональный)** — CLI-обвязка нерабочая: скрипт `bcc` не установлен, `python -m bcc` падает (нет `__main__.py`), `python -m bcc.app --help` игнорирует аргументы и запускает сервер.
4. **CR-4 (гигиена/шум)** — шум кулеров создают одновременные запуски OpenCode (7 процессов) + Edge (8) + uvicorn + Chrome + pytest; постоянно «нагруженного» процесса нет (CPU 20%, крашей нет).

## 4. ВЛИЯНИЕ И МЕТРИКИ

| Что проверил пользователь | Статус | Метрика |
|---|---|---|
| Ярлык открывает приложение | ❌ CR-1 | страница мёртвая при «успешном» окне |
| Клик повторно не роняет окно | ❌ CR-2 | lifetime=0.0s у 4 из 16 запусков |
| CLI-инструмент bcc | ❌ CR-3 | `bcc --help` = EXIT 1 |
| Память/тесты базово работают | ✅ | 24 теста зелёные (7+9+8) |
| Система стабильна после запусков | ✅ | 0 крашей Windows, bcc.db integrity=ok |

## 5. РЕКОМЕНДАЦИИ (НЕ применены — по указанию «код не меняй»)

1. **CR-1**: в `auth.py` заменить `print()` на кодировко-безопасный вывод (`errors="replace"` / logging); в `desktop.py` при GUI-запуске перенаправить `sys.stdout/sys.stderr` процесса в файл-лог.
2. **CR-2**: перед запуском окна проверять жив lock и убивать зомби-процессы на целевом порту; после запуска **ждать готовности сервера** и открывать окно только на живой URL.
3. **CR-3**: установить entry-points (`pip install -e command-center`), добавить `bcc/__main__.py`, починить обработку `--help` в `bcc.app`.
4. Добавить `*.log` и `command-center/data/desktop-run.log` в `.gitignore`.
5. Создать `.env` из `.env.example` (сейчас нет ни одного).
6. Проверить раздачу статики UI: `command-center/ui/static` отсутствует (UI лежит в `ui/`).

## 6. Приложение A: воспроизведение краша кодировки

```text
$ python -c "from bcc.desktop import _BackgroundServer; s=_BackgroundServer('127.0.0.1', 8899); s.start(...)"
  File "bcc\auth.py", line 40, in announce
      print(f"[bcc] {head} доступa: {self.token}", flush=True)
  UnicodeEncodeError: 'charmap' codec can't encode characters in position 6-10
  File "bcc\api.py", line 68, in Services.__init__
  File "bcc\api.py", line 310, in create_app
  File "bcc\desktop.py", line 190, in _BackgroundServer.__init__

$ PYTHONIOENCODING=utf-8 python -c "<то же>"
  [bcc] токен доступа: M4w7...
  SERVER STARTED: True
```

## 7. Статус

Найдено 4 дефекта (2 критичных UX, 1 функциональный CLI, 1 гигиенический).
Рабочее дерево чистое, код не менялся, аудит запушен. Следующий шаг — фиксы по
рекомендациям раздела 5, затем повторный прогон тех же 10 проверок.