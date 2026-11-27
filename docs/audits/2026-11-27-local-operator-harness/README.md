# Квитанция: локальный оператор (Ollama → чат + управление ПК) и Bossman Harness — 27.11.2026

Ветка `claude/local-operator-harness-20261127`, база `main` @ `75f7b76`. План: [`docs/agents/LOCAL_OPERATOR_HARNESS_PLAN_20261127_RU.md`](../../agents/LOCAL_OPERATOR_HARNESS_PLAN_20261127_RU.md).

**Общий вердикт: код и тесты — PASS; ПК владельца — NOT_RUN** (компьютер не был подключён к сессии: мост
`remote-devices` ответил «device is not connected»). По правилу «фича доказана, а не обёрнута» в `main` не вливать,
пока шаги 2–5 плана не пройдены на ПК и их квитанции не лежат в этой папке.

## Что добавлено

| Файл | Что делает |
|---|---|
| `command-center/bcc/local_operator/inventory.py` | Только чтение Ollama: `/api/version`, `/api/tags`, `/api/show`; возможности (`tools`, `vision`, `thinking`, `embedding`) — по словам Ollama, без догадок по имени; `:cloud` помечается облаком; запасной порт 11435; `BOSSMAN_OLLAMA_URLS` |
| `command-center/bcc/local_operator/provision.py` | Чистый план: модели в registry (только локальные чат-модели), агенты «Локально · модель», «… · ПК» (только при `tools`), «Bossman Harness» (если нет агента с таким именем; самая крупная локальная модель с `tools`). Существующее не меняется: `exists` / `drift` в отчёте |
| `command-center/bcc/features/local_operator.py` | `GET /api/local-operator/status`, `POST /api/local-operator/sync` (503 `OLLAMA_UNREACHABLE`, если Ollama не отвечает; ничего не создаёт) |
| `command-center/bcc/local_operator/harness_run.py`, `tools/harness_agent.py` | Драйвер Harness: цель → `/api/chat/threads/…/send` → движок → ожидание одобрений (драйвер их только показывает) → независимая проверка командой владельца → вердикт `PASS/FAIL/UNVERIFIED/STOPPED/TIMEOUT/NOT_RUN` → квитанция JSON |
| `command-center/ui/pages/local_operator.js`, `local_operator_model.js` | Страница пульта «Локальные модели»: модели с бейджами, «Взять модели из Ollama», «Чат» / «Чат + ПК» (выбирает агента в окне чата и открывает его), блок Harness |

## Тесты (локально, Python 3.13, этот SHA до коммита)

`tests/test_local_operator.py` + `tests/test_harness_run.py` + `tests/test_local_operator_ui.py`: **41 passed**.

Ключевые доказательства (в т. ч. негативные контроли):

* Инвентарь: модель без списка `capabilities` (старая Ollama) **не** считается умеющей инструменты, но чатится;
  упавший `/api/show` оставляет модель с «возможности неизвестны»; недоступная Ollama — честная ошибка, не пустой список;
  порт 11434 с чужим HTTP → берётся 11435.
* План: `:cloud`, embedding — пропуск с причиной (параметризованно 3 облачных тега); занятый алиас → `-2`;
  агент «· ПК» только у моделей с `tools`; Harness на 30.5B, а не на 8.3B; чужой «Bossman Harness» (агент 80) и
  агент с тем же именем на другой модели не трогаются (`exists` / `drift`).
* Через настоящий API: sync регистрирует и создаёт агентов, повторный sync не создаёт ничего; существующий провайдер
  Ollama владельца переиспользуется (`localhost` ≡ `127.0.0.1`); при упавшей Ollama — 503 и число агентов не меняется.
* Политика на настоящем реестре инструментов: `computer.observe` — AUTO для «· ПК», клик/ввод/запуск — ASK;
  негативный контроль: без права на наблюдение спрашивается даже наблюдение. Чат-агент инструментов не получает.
* Настоящий движок со скриптовой моделью: `computer_act` → задача `waiting_approval`, одобрение с `computer.act` в превью;
  `computer_observe` → без одобрения, результат вернулся модели, задача `completed`; чат-агенту схемы инструментов не уходят.
* Harness e2e через настоящее приложение: цель через чат-тред → «Bossman Harness» → `completed` → проверка
  (настоящий subprocess, код 0) → `PASS`. Действие на экране без решения владельца → `TIMEOUT` с одобрением в квитанции,
  проверка не запускалась. «Агент сказал готово, проверка упала» → `FAIL`. Нет проверки → `UNVERIFIED`, не `PASS`.
* Страница: настоящий модуль отрисован в node (заглушки UI): кнопки, «Чат + ПК» только у модели с инструментами,
  клик пишет `bcc.chat.route = agent:<id>` и открывает `/chat.html`; ошибка API → блок ошибки.

Смежные наборы (не сломаны): `test_v21_tool_loop`, `test_cloud_fallback_no_pc_tools`, `test_computer_use_tools`,
`test_computer_verify_and_approval`, `test_harness_guards`, `test_v2_core`, `test_command_bar`, `test_chat_ui_static`,
`test_buttons_sweep_1001`, `test_v6_lazy_pages`, `test_ux2_pages_sweep::test_every_page_renders_and_buttons_work`,
`test_cu_participant_perimeter`, `test_authorization_at_effect_time` — зелёные.
`test_ux2_pages_sweep::test_every_page_fits_mobile_viewport` красный и на `main` (страница `creator-mode`, 702 > 390) — не эта ветка.

`docs/testing/SKIPS_REGISTRY.md` перегенерирован (`--check` PASS, 543 записи): новый skip «node is not installed» +
сдвиг строк `test_harness_guards.py`, который на `main` уже не совпадал.

## NOT_RUN (нужен ПК владельца)

| Проверка | Как |
|---|---|
| Реальный список моделей Ollama и их возможности | `python tools/harness_agent.py models` (сверить с `ollama list`) |
| Регистрация на живой базе (агент 80 не тронут) | Пульт → «Локальные модели» → «Взять модели из Ollama» |
| Живой computer use локальной моделью | «Чат + ПК» → «Открой Блокнот и напиши: проверка Bossman» |
| Harness с независимой проверкой | шаг 5 плана → `pc_receipt.json` в эту папку |

Чего здесь нет и не заявлено: реальная Ollama и реальные модели не запускались (в облачной песочнице их нет);
качество ответов конкретных локальных моделей в роли оператора не измерено.
