# Профили доступа к чату (мульти-пользователь) — feature

Аккаунты для доступа к чату на твоём сервере, у каждого — свои переключатели
доступа, которые РЕАЛЬНО влияют на ИИ, и отдельная main-папка профиля, где
копятся знания. Построено ПОВЕРХ существующей authority (Stage 6 device-identity,
computer_operator, context_engine) — без второго движка авторизации/памяти.

## Где живёт
`bossman-core/bossman/profiles/` — новая подсистема (`critical=False`), роутер
`/profiles`, регистрируется в lifecycle (`api.py`).

- `models.py` — `Profile` + словарь переключателей `TOGGLES` + карта
  `CAPABILITY_TOGGLE` (capability → тумблер).
- `gate.py` — `decide(profile, capability)` / `enforce(...)`: единственный источник
  истины «можно ли». Fail-safe, deny-by-default.
- `store.py` — durable JSON-стор (переживает рестарт), CRUD + поиск по device/telegram.
- `memory.py` — отдельная папка знаний профиля + namespace для context_engine.
- `service.py` — процессный `ProfileService` + колбэк `computer_access_check`.
- `router.py` — admin-API управления аккаунтами.
- `subsystem.py` — подъём стора/сервиса.

## Переключатели доступа (по умолчанию всё ВЫКЛ = deny-by-default)
| Тумблер | Что разрешает | Пример capability |
|---|---|---|
| `computer_control` | управление компьютером/терминалом | `computer.control`, `terminal.run` |
| `internet` | браузер / HTTP | `browser.read`, `http.get` |
| `messaging` | отправка во внешние каналы | `channel.send` |
| `filesystem_write` | запись в ФС | `filesystem.write` |
| `personal_data` | доступ к личным данным | `personal.read` |
| `cloud_llm` | облачные модели | `cloud.llm` |
| `code_execution` | выполнение кода/dev-задач | `code.execute` |

Неизвестная capability → gate возвращает DENY. Выключенный профиль → DENY всего.

## Что уже РЕАЛЬНО enforced
- **«Нет доступа к управлению компом никаким образом»**: тумблер
  `computer_control=false` ⇒ `ComputerOperatorManager.create_task` бросает
  `CapabilityDenied` ДО создания desktop-задачи (провод в
  `computer_operator/subsystem.py`, no-op для локального хозяина). Доказано тестом
  `test_manager_create_task_blocked_by_profile`.
- **Личные данные / остальные тумблеры**: gate готов и выставлен в API
  (`GET /profiles/{id}/access/{capability}`); исполнители (browser/http/channel)
  подключаются к тому же `gate.decide` тем же паттерном — это следующий небольшой шаг.

## Отдельная main-папка профиля (накопление знаний)
- Корень: `workspace_dir/_profiles/<id>/`, знания — в `knowledge/` (создаётся по
  требованию, путь confined — побег за workspace невозможен, `safe_id`).
- Namespace памяти профиля = `profile:<id>` (кладётся в колонку `project`
  context_engine) — знания копятся изолированно на аккаунт, без изменения схемы БД.

## API (все — под scope `admin`, как remote_client; гость права не расширяет)
```
GET   /profiles/_vocabulary              # словарь тумблеров с дефолтами
POST  /profiles                          # создать аккаунт {name, device_id?, telegram_user_id?, toggles?}
GET   /profiles                          # список
GET   /profiles/{id}                     # один
PATCH /profiles/{id}/toggles             # изменить переключатели {toggles:{...}}
POST  /profiles/{id}/enabled             # включить/выключить {enabled:bool}
POST  /profiles/{id}/bind                # привязать device_id / telegram_user_id
GET   /profiles/{id}/access/{capability} # решение gate (для UI)
```
Секреты не хранятся и не отдаются — только идентификаторы привязки и тумблеры.

## Привязка к Telegram (текущий статус — честно)
- Профиль хранит `telegram_user_id`, стор умеет `by_telegram(...)` — когда добавим
  inbound-роутинг Telegram-текста в задачи, профиль резолвится по автору сообщения
  и его тумблеры применяются к сессии.
- Сегодня Telegram — канал подтверждений (approve/deny), inbound-текст → задача пока
  не реализован (greenfield). Это следующий шаг фичи, не входит в текущий слайс.

## Тесты
`bossman-core/tests/test_profiles.py` — 21 passed: стор CRUD/durability/lookup,
gate (unknown→deny, computer_control, personal_data, disabled, none, enforce),
per-profile knowledge (create+confine, escape rejected, namespace), service
(block/allow/strict), enforcement в менеджере (block+unblock, обратная совместимость).
Полная регрессия bossman-core: 927 passed / 4 skipped, 0 регрессий.

## Инварианты (соблюдены)
Один authority-цикл (scopes/policy/approval/executor/audit); профиль — расширение
device-identity, не второй auth. Deny-by-default. Секреты только по ссылке. Локальный
хозяин не ограничивается, пока профиль не привязан к устройству.
