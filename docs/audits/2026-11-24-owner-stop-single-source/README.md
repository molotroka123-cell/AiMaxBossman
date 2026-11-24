# STOP владельца: один источник истины и шлюзы в ядре (24.11.2026)

Статус: **дефект класса 1 воспроизведён и исправлен в исходниках; тесты PASS; живой прогон на ПК владельца NOT_RUN.**

## Дефект (воспроизведён на `75f7b76` до правки)

Глобальный STOP владельца (`<data>/computer/STOP`: палитра, `bossman stop --all`, Telegram /stop) **не держал Боссмана
остановленным**. `stop --all` гасил работу, которая уже шла, но сам движок агентов STOP не читал. Пока STOP был включён,
новая задача из пульта/чата:

```
claim → вызов модели → инструмент ВЫПОЛНЕН → status=completed
```

Тест-воспроизведение: `command-center/tests/test_owner_stop_engine_gate.py` — на старом коде 3 теста шлюзов FAIL,
на новом PASS (проверено подменой `engine.py`/`tools.py` на базовые).

Корень — архитектурный: минимум 8 независимых проверок STOP с разными правилами.

| Где | Какие файлы | При ошибке чтения |
|---|---|---|
| движок агентов (`engine.py`) | **никакие** | — |
| `features/evolution.py` | computer | fail-open (`exists()`) |
| `features/photo_edit_delivery.py` | computer + autonomy | исключение наружу |
| `direct_gen/service.py` | computer + autonomy | **fail-open** (`return False`) |
| `features/telegram_calls.py` | computer (+ состояние в памяти) | по флагу |
| `features/coding_tasks.py`, `v2/terminal_control.py` | computer в нескольких каталогах-кандидатах | пропуск кандидата |
| `features/pc_agent.py` | свой `STOP` + `BOSSMAN_STOP_FLAG_PATH`, по умолчанию `C:\Users\asd\…` | — |

## Новая архитектура

```
                    bcc/owner_stop.py  (один модуль, без зависимостей, только чтение, fail-closed)
                      owner_stop(data_dir, include_autonomy=False) -> OwnerStop(active, sources, reason)
                                         │
   ┌─────────────────────────────────────┼──────────────────────────────────────────┐
   │ ЯДРО                                │                                          │ ПЛОСКОСТИ (зона не изменена)
   ├─ TaskEngine.claim()  — очередь держится: задачи ждут, модель не зовётся, $0     ├─ evolution, coding_tasks, terminal
   ├─ TaskEngine._check_interrupt() — STOP посреди работы = task «stopped»         ├─ direct_gen, photo_edit_delivery (+autonomy)
   │     (как `stop --all`, checkpoint сохранён)                                    ├─ telegram_calls (watcher не fail-closed по замыслу)
   └─ tools.execute_tool() — последний шлюз перед ЛЮБЫМ эффектом инструмента          └─ learning.youtube
```

Правила: `computer/STOP` — глобальный, останавливает всё, включая движок. `autonomy/STOP` — STOP цикла автономии;
плоскости, которые его уже читали, читают его и дальше (`include_autonomy=True`), движок — нет (тест подтверждает,
что `bossman autonomy stop` не замораживает задачи владельца; зона не расширена без решения владельца).
Неподдающийся проверке STOP (нет прав, ошибка ввода-вывода) = STOP включён. Снимает STOP только владелец там, где ставил.

После снятия STOP очередь продолжает сама (событие `engine.resumed_after_owner_stop`); пока держит —
`engine.held_by_owner_stop` (одно событие на эпизод, без спама).

## Доказательства (Linux-песочница, не ПК владельца)

- `test_owner_stop_engine_gate.py` — 10 PASS: модуль (нет/есть/нечитаемый/битые настройки/зона autonomy), шлюз
  инструментов с негативным контролем, задача ждёт под STOP и выполняется после снятия, STOP посреди работы — второй
  эффект и второй вызов модели не происходят, негативный контроль без STOP, autonomy-STOP не морозит задачи владельца.
- Широкий прогон command-center (`-k stop/engine/tool/terminal/coding/direct_gen/evolution/photo/call/computer/
  control_plane/owner/autonomy/v21/approval`): **3546 passed, 17 failed**. Те же 16 красные на базе `75f7b76`
  (sidecar coding, approval replay, telegram store, motion UI, perf import и др.) — не от этой правки. 17-й,
  `test_owner_safe_runtime_drill_end_to_end` (включая его шаг STOP), падал только из-за грязного дерева
  (`build_sha=None`) и проходит на закоммиченном SHA. **Новых красных: 0.**

## Не сделано / не проверено

- Живой прогон на ПК владельца через пульт: поставить STOP → отправить задачу из пульта → убедиться, что она «в очереди»,
  снять STOP → выполнена. NOT_RUN.
- `pc_agent.py`: путь по умолчанию `C:\Users\asd\Bossman\handoff\os-agent\board\STOP` зашит под конкретную учётку —
  на другом ПК этот STOP молча не читается. Не трогал (нужен ответ владельца, где board живёт); отдельная находка.
- Индикатор «очередь держит STOP» в UI пульта — событие есть, отрисовки нет.
