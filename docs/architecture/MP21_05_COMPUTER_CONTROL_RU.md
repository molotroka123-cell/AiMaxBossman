# 05. Управление компьютером (сверено 11.10.2026)

Код: `command-center/bcc/features/tools_computer.py` (2153 строки), `bossman-core/bossman/computer_operator/*`
(адаптеры, `policy.py`, `verifier.py`, `loop_guard.py`, `secret_executor.py`), панель `command-center/ui/pages/control.js`.
Доказательство работоспособности: 98 passed (rc 0) в `docs/owner/runs/MP21_A_CHECKS_20261011.md`, строка 1.
**Важно:** эти тесты подменяют Windows-адаптер заглушками. Реальный рабочий стол в этом проходе НЕ управлялся.

## 1. Цикл

| Шаг | Реализация | Статус |
|---|---|---|
| Снимок экрана | `tools_computer.observe(..., screenshot=True)`, `adapters/screenshot.py` | works (заглушки) |
| Элементы UI | дерево UIA на окно, allowlist окон, привязка к HWND/PID задачи | works (заглушки) |
| Мышь/клавиатура | `adapters/windows.py` через pyautogui с `FAILSAFE=True`, бюджеты ввода/хоткеев; ввод в поле пароля (UIA `IsPassword`) и в цели с токенами `password/пароль/...` не исполняется ни с каким одобрением (`tools_computer.py`, `CREDENTIAL_TARGET_TOKENS`, `credential_target`) | works (заглушки) |
| Проверка после действия | `tools_computer.verify(obs, expect)`; запуск засчитан только с проверенным окном | works |
| Передача управления владельцу | `POST /api/computer/stop`, затем `POST /api/computer/resume`; resume обнуляет очередь и требует свежего наблюдения | works |
| Аварийный STOP | файл `<data_dir>/computer/STOP` (переживает рестарт), прерывает ожидания и подшаги; кнопка в панели; угол экрана pyautogui | works |

Тесты: `test_computer_use_tools.py`, `test_computer_verify_and_approval.py`, `test_action_contract_verified_computer_launch.py`,
`test_computer_stop_race.py` (10 тестов, в т.ч. `stop_survives_restart`, `stop_and_resume_are_not_model_callable`).
STOP и resume не вызываются моделью.

## 2. Что отсутствует

| Пункт | Статус |
|---|---|
| Глобальный hotkey аварийной остановки | missing: в репозитории нет `RegisterHotKey`/keyboard hook; владелец должен дотянуться до панели или угла экрана |
| Автономная многосайтовая миссия (браузер, загрузка, процесс) | design only, не доказано вживую |
| Прогон на живом рабочем столе | не выполнялся (видео-рендеры имеют приоритет, живой клик-цикл занял бы мышь) |

## 3. Точный следующий шаг

`tools/cu_acceptance` (`run_cu_acceptance.py`, `run_cu_local_qwen.py`, `scripted_cu_model.py`) на тихом рабочем столе, при
владельце, с сохранённым junit. До этого статус живого управления: NOT PROVEN.

## 4. Правила безопасности, уже заложенные в коде

* только окна из allowlist и приложения, привязанные к задаче;
* секреты не уходят в модель: заранее привязанную форму входа заполняет детерминированный `secret_executor.py`, а модель ввод в поля паролей не выполняет (`tools_computer.py`);
* любое последствие (платёж, отправка, удаление) идёт через одобрение владельца (`test_computer_verify_and_approval.py`);
* `loop_guard.py` останавливает зацикливание действий.
