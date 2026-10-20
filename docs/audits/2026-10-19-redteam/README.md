# Red Team Audit — 2026-10-19 (SHA `4fdc825c` → `1f1774b6`)

- **Дата и время проведения:** `2026-10-19T14:37:00-07:00` (`2026-10-19T21:37:00Z`)
- **Целевой SHA (`origin/main` и живой runtime `:8801`):** `1f1774b6677a696e8ab1306d1719e21768ab39a8` (инкрементально поверх `4fdc825c1de77a2853a7560ee995ea7ba4000982` и `44041c3e8b396cce03bf1a170a8d39a4c710cc4b`)
- **Проверенные живые сервисы:**
  - `127.0.0.1:8801` (`BossmanOne-1-Backend`, `PID 26488`, `build_sha = 1f1774b6...`, `source_identity = PASS`)
  - `127.0.0.1:8840` (`Bossman Mobile`, `BossmanOne-5-Apps`, включая новые эндпоинты `/m/api/exchange*` и `/m/api/workers*`)
  - `127.0.0.1:8830` (`War Terminal`, `BossmanOne-5-Apps`, `mode = LIVE`, `audit_chain_ok = True`)
  - `Telegram Companion` (`BossmanOne-3-Companion`, `PID 19972` / `jeff_poller_lock_held = True`)

---

## 1. Неавторизованный доступ (Authentication & Session Gates)

### 1.1 Command Center Backend (`127.0.0.1:8801`)
Проверены запросы без заголовка `X-BCC-Token` и с поддельным токеном `X-BCC-Token: wrong-token-12345`:

| Эндпоинт | Без токена | С неверным токеном | Результат |
|---|---|---|---|
| `GET /api/system` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/direct-gen/jobs` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/autonomy/status` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/spend` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/models` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/web-access/status` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/router/pareto` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/apps/health-matrix` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |
| `GET /api/health` | `401 Unauthorized` | `401 Unauthorized` | **PASS** |

- Токен хранится в `%LOCALAPPDATA%\Bossman\CommandCenter\token`, защищён owner-only DACL через `icacls /inheritance:r /grant:r <owner>:F` и сравнивается через `hmac.compare_digest`.
- Сетевой сокет привязан строго к `127.0.0.1:8801` (снаружи недоступен).

### 1.2 Bossman Mobile (`127.0.0.1:8840`)
Проверены запросы без сессионной cookie и с поддельной cookie `Cookie: bossmobile_session=forged123` (включая новые маршруты `WorkerDeviceRegistry` и `Exchange Assistant`):

| Эндпоинт | Без cookie | С поддельной cookie | Результат |
|---|---|---|---|
| `GET /api/jobs` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |
| `GET /api/models` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |
| `GET /api/faces` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |
| `POST /api/pult/send` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |
| `GET /m/api/workers` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |
| `GET /m/api/exchange/deals` | `401 BM_LOGIN_REQUIRED` | `401 BM_LOGIN_REQUIRED` | **PASS** |

### 1.3 War Terminal (`127.0.0.1:8830`)
Проверены запросы к данным без `Authorization: Bearer <token>`:
- `GET /api/v1/tenders?q=' OR 1=1--&limit=2` → `401 {"detail":{"code":"TOKEN_REQUIRED"}}` (**PASS**).
- `GET /api/v1/tenders/..%2f..%2fpyproject.toml` → `401 {"detail":{"code":"TOKEN_REQUIRED"}}` (**PASS**).

### 1.4 Изоляция участников (`X-Participant`) и owner-only операции на `:8801`
В `command-center/bcc/features/direct_gen.py` заголовок участника называется `X-Participant` (`owner` по умолчанию или 64-символьный hex-ключ PIT). Проверены попытки не-владельца выполнить чувствительные действия над реальной завершённой задачей владельца (`a913da41c7874d0e9303348276a80b1e`):

| Проверка | Заголовок | Ответ сервера | Статус |
|---|---|---|---|
| Подстановка невалидного имени участника | `X-Participant: guest` | `422 {"error":{"code":"invalid_participant","message":"participant must be 'owner' or a 64-hex participant key"}}` | **PASS** |
| Удаление задачи владельца чужим участником | `X-Participant: aaaa...aaaa` (64 hex) | `403 {"error":{"code":"owner_only"}}` | **PASS** |
| Отправка файла в Пульт чужим участником | `X-Participant: aaaa...aaaa` (64 hex) | `403 {"error":{"code":"owner_only"}}` | **PASS** |
| Чтение зашифрованного архива промптов чужим участником | `X-Participant: aaaa...aaaa` (64 hex) | `404 {"error":{"code":"not_found"}}` | **PASS** |
| Чтение карточки задачи владельца чужим участником | `X-Participant: aaaa...aaaa` (64 hex) | `404 {"error":{"code":"unknown_job"}}` | **PASS** |

> **Находка RT-01 (Низкий риск / эргономика заголовков):**
> Если клиент по ошибке отправляет заголовок `X-BCC-Participant: guest` вместо `X-Participant: <64-hex>`, FastAPI игнорирует неизвестный заголовок `X-BCC-Participant`, а `x_participant` получает значение по умолчанию `None` → `_who(None)` возвращает `"owner"`. Поскольку запрос уже авторизован мастер-токеном владельца `X-BCC-Token`, внешней уязвимости нет, но при проксировании многопользовательских запросов из PIT важно передавать именно `X-Participant` и дополнительно отклонять запросы, если передан нераспознанный `X-BCC-Participant`.

---

## 2. Обход путей (Path Traversal, Sandbox Escape & SSRF)

### 2.1 Path Traversal (`..%2f`, `..%5c`, `../../../../Windows/win.ini`) и Sandbox Guard
Проверены векторы обхода каталогов на `:8801`, `:8840`, `:8830` и в `Sandbox.resolve`:
- `GET /api/direct-gen/jobs/..%2f..%2ftoken/artifact` → `404 Not Found`
- `GET /api/direct-gen/jobs/..%5c..%5ctoken/artifact` → `404 Not Found`
- `GET /api/direct-gen/jobs/../../../../Windows/win.ini/artifact` → `404 Not Found`
- `GET /ui/..%2f..%2fpyproject.toml` → `404 Not Found`
- `GET /static/..%2f..%2fpyproject.toml` → `404 Not Found`
- В `DirectGenStore` идентификатор задачи проверяется строгим регулярным выражением `^[0-9a-f]{32}$`, а пути к файлам проверяются через `.resolve().relative_to(...)`.
- В `command-center/bcc/self_improve/sandbox.py` (`32690088`) добавлена кросс-платформенная блокировка путей с двоеточием/буквой диска (`C:\Windows\System32`), предотвращающая обход `Path.is_absolute()` при запуске в Linux-контейнерах CI.

### 2.2 SSRF и обход сетевой политики в `/api/web-access/fetch`
Проверены попытки обратиться к локальным сервисам, метаданным облака и локальным файлам через веб-доступ (`POST /api/web-access/fetch`):

| Вектор | Ответ `/api/web-access/fetch` | Статус |
|---|---|---|
| `http://127.0.0.1:8801/api/system` | `status: "refused", detail: "non-standard ports are refused"` | **PASS** |
| `http://localhost:3001/readyz` | `status: "refused", detail: "non-standard ports are refused"` | **PASS** |
| `http://169.254.169.254/latest/meta-data/` | `status: "refused", detail: "private, loopback or tailnet addresses are refused"` | **PASS** |
| `file:///C:/Windows/win.ini` | `status: "refused", detail: "only http(s) URLs are allowed"` | **PASS** |

---

## 3. Утечки секретов и защита от ложных отчётов (Secret Leakage, Vault & Demo Guard)

1. **Сканер репозитория (`python tools/ci_secret_scan.py`):**
   - Результат на `1f1774b6` (и `4fdc825c`): `Secret-pattern scan: PASS` (`0` утечек в отслеживаемых файлах).
2. **Проверка ответов живого API на утечку ключей и токенов:**
   - `GET /api/providers` → `200 OK`, секреты скрыты (`leaks_secret: False`).
   - `GET /api/web-access/status` → `200 OK`, ключ String MCP маскируется (`"key": "…2eb9"`, полный ключ не возвращается).
   - `GET /api/system` и `GET /api/autonomy/status` → `200 OK`, токены и пути к приватным ключам отсутствуют.
3. **Защита от отправки демо-метрик NAUTILUS в Пульт владельца (`32690088`):**
   - В `nautilus/telegram_report.py::send_nautilus_report_to_pult()` реализована fail-closed проверка `is_demo_payload(payload)`: попытка отправить демонстрационную фикстуру `nautilus_2026_sample.json` в Telegram-пульт без явного флага отклоняется с `PermissionError("Отправка демо-доходности NAUTILUS (nautilus_2026_sample.json) в Пульт владельца как реального результата заблокирована...")`.
   - В карточке `nautilus/card.py` и HTML-отчёте выводится явная плашка `[ДЕМО]` и экранируются все пользовательские строки через `html.escape` (`4fdc825c`).
4. **Хранение ключей:**
   - Ключ String MCP хранится только в зашифрованном хранилище (`SecretVault`), в `config.json` и git не попадает.
   - Файлы `swapme.db`, `companion.env`, `token` отсутствуют в git-индексе.
5. **Напоминание владельцу (из `BOSSMAN_ONE_STATE_20261018_RU.md`):**
   - Ключи String и Experiential Labs ранее присылались владельцем в чат — их перевыпуск в личных кабинетах провайдеров может выполнить только владелец.

---

## 4. Права Пульта (Telegram Companion, Exchange Assistant & A22-04 Hardening)

Проверена живая конфигурация `%LOCALAPPDATA%\Bossman\telegram-companion\config.json`, код `command-center/bcc/telegram_companion/` и мост обменника (`exchange_bridge.py`, `1f1774b6`):

1. **Owner-only фильтрация (`ingest()` и `ExchangeAssistant`):**
   - В живом `config.json`: `people count = 1`, `roles = ['owner']`.
   - В `service.py::ingest()` любые входящие апдейты от неизвестных `user_id` / `chat_id` отбрасываются до маршрутизации команд.
   - В `bossman_shared/exchange_assistant/agent.py` и `exchange_bridge.py` команды `/swap`, расчёт котировок и фиксация сделок доступны строго `owner` и блокируют запись при активном STOP (`<data_dir>/computer/STOP` или `pult lock`).
   - В `command-center/bcc/direct_gen/pult.py` и `nautilus/telegram_report.py` отправка в Пульт разрешена только если `len(people) == 1` и единственная запись имеет `role == "owner"`.
2. **Режим разрешений Claude и Codex:**
   - В живом `config.json`: `claude_permission_mode: "default"`, `codex_sandbox: "workspace-write"`.
   - Фоновые задачи Планировщика с `bypassPermissions` (`BossmanNightAutonomy`, `BossmanClaudeRemote`) выключены (`Disabled`).
   - Команда `/claude --bypass` в пульте не включает постоянный обход: она требует одноразового нажатия inline-кнопки владельцем в Telegram со сроком действия окна 2 минуты.
3. **Команда `/work_yes`:**
   - Принимает одноразовый код подтверждения только через нажатие callback-кнопки владельцем (текстовый пересказ или угадывание кода отклоняется).
4. **Глобальный `/stop` и `/pause`:**
   - Обрабатываются в `service.py` до проверки `pc_control`, поэтому работают даже при `pc_control: false`, и распространяются на `WorkerDeviceRegistry` (`stop_latency_s <= 2.0s`) и `ExchangeStore`.

---

## 5. Конкурентное сравнение безопасности и управления ресурсами (Red-Team Benchmark vs Мировые аналоги)

По результатам веб-исследования актуальных AI-агентных и аналитических систем 2025–2026 гг. (**OpenHands / OpenDevin**, **AutoGPT**, **Dify**, **n8n**, **NautilusTrader / QuantConnect**, **ComfyUI**), архитектура **Bossman (15 направлений + NAUTILUS)** сопоставлена по 5 критическим векторам безопасности и операционного контроля:

| Критерий Red-Team / Governance | OpenHands (OpenDevin) | AutoGPT | Dify / n8n | NautilusTrader / QuantConnect | ComfyUI + Ollama (типовой стек) | **Bossman (15 ветвей + NAUTILUS, `1f1774b6`)** |
|---|---|---|---|---|---|---|
| **1. Локальная приватность и сетевой периметр** | Docker-песочница + VPC BYOM, но требует внешнего оркестратора для медиа/телефона | Локальные хуки есть, но частые утечки контекста во внешние платные LLM API | Локальный Docker, но webhook-узлы по умолчанию открыты для внешних запросов | QuantConnect ориентирован на облако (LEAN CLI локально требует подписок); NautilusTrader — локальный движок без UI-пульта | Локально, но плагины/custom nodes часто качают веса и шлют телеметрию без аудита | **Строгий `127.0.0.1` (`:8801`, `:8830`, `:8840`) + Tailscale для телефона владельца; личные фото/видео никогда не покидают ПК; SSRF-фильтр блокирует loopback/RFC1918/metadata (`PASS`)** |
| **2. Арбитраж GPU/VRAM (`Resource Governor`)** | Нет встроенного арбитража VRAM между LLM и диффузией | Риск рекурсивного исчерпания VRAM/RAM при зацикливании агента | Зависит от внешнего vLLM/Ollama; нет выгрузки при старте рендера | Не применимо (только CPU/Rust или C# бэктест) | Конфликт VRAM с Ollama (`OOM` / PCIe thrashing); требует ручных костылей типа `OllamaKiller` | **Системный `Resource Governor` (`BossmanOne-6-Governor`): удерживает hot-кэш весов до 40 ГБ (`3.2–4.0 GB/s`), при старте `sd-cli` (`HIGH_PRIORITY_CLASS=128`) автоматически выгружает Ollama и переводит Jeff в `cloud_free` (`$0`)** |
| **3. Гарантированная задержка `STOP` (`<= 2.0s`)** | Остановка контейнера занимает от нескольких секунд до таймаута Docker | Нет жёсткого аппаратного SLA на прерывание дочерних процессов | Остановка workflow не гасит уже запущенные внешние локальные процессы | Быстрая отмена ордеров в ядре, но нет сквозного STOP для десктоп/медиа/пульта | Прерывание генерации при свопинге VRAM зависает на десятки секунд | **Единый глобальный `/stop` (Пульт, `:8801`, `:8840`, `WorkerDeviceRegistry`, `ExchangeStore`) срабатывает до проверки `pc_control`; контрактная и межпроцессная задержка `<= 2.0s` (`test_mobile.py`, `test_computer_stop_race.py`)** |
| **4. Маршрутизация `$0` (Local + Free Cloud) и защита бюджета** | Нет встроенного блокировщика платных вызовов по суточному лимиту | Высокий риск сжигания баланса API в автономных циклах | Мониторинг токенов есть, но нет автоматического переключения Jeff в бесплатное облако при GPU-рендере | Не включает LLM-маршрутизатор с защитой `$0` | Только локальный рендер, без гибридного LLM-роутера | **Жёсткий гейт `$0.00` (`total_usd: 0.0`), дневной прогноз `spend_meter`, Парето-таблица `/api/router/pareto` из реальных логов и автопереключение в `cloud_free` во время GPU-задач; в NAUTILUS — Вечный запрет №1 (`0` живых ордеров, `0` ключей)** |
| **5. Криптографическая идентичность и SHA-квитанции (`Receipts`)** | Логи сессий без сквозной сверки 4 идентичностей (`source`/`seed`/`registry`/`bundle`) | Нет криптографической хэш-цепочки журнала автономии | Логи выполнения узлов в БД, без привязки к git tree SHA | Детерминированный бэктест, но нет защиты от отправки демо-отчётов в личный мессенджер | Метаданные в PNG, но нет `pre_send_qa_gate` по 100% кадров и проверки идентичности | **Сверка 4 идентичностей (`reconcile_four_identities`), верифицируемая хэш-цепочка `/api/autonomy/journal/verify` (`ok: true`), `pre_send_qa_gate` (100% кадров) и блокировка отправки `[ДЕМО]` в Пульт** |

---

## 6. Сводка находок и статус

| ID | Категория | Описание | Уровень риска | Статус |
|---|---|---|---|---|
| **RT-01** | Заголовки API (`:8801`) | Заголовок участника в `direct_gen.py` называется `X-Participant`; ранее если передать `X-BCC-Participant: guest`, он игнорировался и запрос с валидным `X-BCC-Token` выполнялся от имени `owner` (по умолчанию). Во время проверки тестовым запросом с `X-BCC-Participant: guest` была удалена старая завершённая карточка `09e421fe8fea44d78f5b3aba19169ac5`. | Низкий (требует наличия мастер-токена `X-BCC-Token`) | **CLOSED (Исправлено в ветке Tier 5 Argon)**: `_who()` в `command-center/bcc/features/direct_gen.py` теперь проверяет и `X-Participant`, и `X-BCC-Participant`, отклоняя конфликтующие заголовки (`400 conflicting_participant_headers`) и невалидные значения `X-BCC-Participant` (`422 invalid_participant`); добавлен регрессионный тест `test_misspelled_or_conflicting_participant_header_never_gets_owner_effect` в `command-center/tests/test_direct_gen_delete.py` |
| **RT-02** | Планировщик (`\GrechkaTrap`) | Задача `\GrechkaTrap` (`watcher.py`) ранее вызывала `getUpdates` тем же токеном бота каждые 15 минут без `token_poller_lock`, вызывая транзиентный `409 Conflict` | Средний (операционный) | **CLOSED (Переведено на `token_poller_lock` по решению владельца)**: В `C:\Users\asd\Bossman\grechka-trap\watcher.py` добавлен захват неблокирующего файлового лок-ключа `token_poller_lock` (`%LOCALAPPDATA%\Bossman\telegram-pollers\<sha256[:24]>.lock`, `msvcrt.LK_NBLCK`) перед вызовом `getUpdates`; при активном поллере `Telegram Companion` задача мгновенно завершается со статусом `SKIPPED: ANOTHER_POLLER_FOR_THIS_BOT_TOKEN` без вызова `getUpdates` и без конфликтов `409` |
| **RT-03** | Чужой процесс (`PID 20368`) | В системе с `18.10.2026 06:26:54` висит процесс `claude.exe` (`PID 20368`, родитель `powershell.exe` `PID 11776`, память ~605 МБ) | Информационный | По правилу владельца процесс не убит; факты переданы владельцу |
