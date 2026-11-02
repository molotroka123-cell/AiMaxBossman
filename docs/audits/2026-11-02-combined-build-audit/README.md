# Независимый аудит общей сборки — 02.11.2026

Аудитор: облачный агент (GitHub-only, **без** доступа к ПК владельца, Roblox Studio и локальному Bossman).
Всё, что требует ПК/Studio, помечено `NOT_RUN`. Ничего не запускалось против mainnet. Секреты не выводятся (только файл:строка и первые 4 символа).

Проверенные SHA:
- AiMaxBossman `origin/main` = `4a8c68f3`, `origin/claude/integration-20261102` = `28e0f5dd` (merge-base `f8d6e5b5`; main впереди на 2 коммита Argon, integration — на 10 коммитов Claude).
- SwapmeGame `origin/roblox/fox-city` = `85df57e` (**нет общего предка с `main`** — это отдельная история; `main` репозитория не трогался).

## 1. Итог: GO / NO-GO

**AiMaxBossman `claude/integration-20261102` → `main`: NO-GO в текущем виде, GO после условий C1–C3.**

Технически слияние чистое: 0 конфликтующих файлов, 180/180 целевых тестов на ветке интеграции, 208 passed / 5 skipped trading-тестов на main, секретов в диффе нет. Причины остановки:

| № | Условие | Почему |
|---|---|---|
| C1 | Вернуть LF в 6 файлах (см. F-01) | Из 6929 «добавленных» строк ~6300 — только смена CRLF. `git diff -w` показывает 17 файлов / +646 −17. В `action_contract.py` (шлюз «сделал без доказательств») реально изменены 4 строки, а blame/ревью/будущий cherry-pick ломаются на 2998 строках. |
| C2 | Исправить квадратичный regex `_DATA_BLOCK_RE` (F-02) | Измерено: 152 КБ текста с незакрытыми `НАЧАЛО …` блокирует вызов на 11.8 с; вызывается синхронно из классификаторов задач. |
| C3 | Живое доказательство по правилу «feature is PROVEN» | `/api/funds`, `/key → vault`, `data blocks` описаны в harness-логе как PROVEN на :8801, но квитанции с SHA и `receipt.json` для них в `docs/audits/` нет; из облака проверить нельзя (`NOT_RUN`). |

**Main (Argon, Pump.fun Launchpad / 5-Wallet Genesis):** реальной отправки транзакций и подписи **нет**, всё `PAPER_TRADING_ONLY`; но дизайн «5 кошельков одного создателя покупают свой токен в блоках 0–2» — полисный риск (F-03), решение владельца обязательно до любого live-пути.

**SwapmeGame `roblox/fox-city`:** к публикации не готова (`check.sh`, Studio — `NOT_RUN`); на слияние AiMaxBossman не влияет.

## 2. Таблица находок

Серьёзность: P0 — прямой ущерб/секрет/деньги; P1 — блокирует слияние/релиз или риск политики; P2 — реальный дефект/пробел доказательств; P3 — улучшение.
P0 не найдено.

| ID | Sev | Файл:строка | Доказательство | Что сделать |
|---|---|---|---|---|
| F-01 | P1 | `command-center/bcc/features/action_contract.py` (1499 строк), `action_router.py` (227), `telegram_companion/adapters.py` (1751), `telegram_companion/service.py` (2388), `v2/memory/lifecycle_wiring.py` (239), `AGENTS.md` (220) | `git show origin/main:…action_contract.py \| file -` → «UTF-8»; на integration → «with CRLF line terminators». `git diff --numstat`: 1499/1499 строк; `git diff -w --ignore-cr-at-eol --stat` → 17 файлов, +646/−17. | Пересохранить 6 файлов в LF, добавить `.gitattributes` (`* text=auto eol=lf` для `*.py *.md`), перепроверить `git diff --stat` ≈ +646/−17 до слияния. |
| F-02 | P2 | `command-center/bcc/conversation_context.py:37-44` (`_DATA_BLOCK_RE`, `without_data_blocks`) | Замер: 500 незакрытых `НАЧАЛО РАСШИФРОВКИ` (9.5 КБ) 0.056 с; 2000 (38 КБ) 0.903 с; 8000 (152 КБ) **11.76 с**. Вызывается из `action_contract.py:260`, `action_router.py:132,144,154` без таймаута. | Заменить на линейный парсер построчно (стек открытых меток) или ограничить длину входа/число маркеров; тест на 200 КБ < 0.2 с. |
| F-03 | P1 (политика/право) | `bossman-core/bossman/trading_learning/pump_project_launchpad.py:775-780` (`FIVE_WALLET_ROLES`), `:864` (`second_offset` шаг 0.35 с), `:784-880` | Пять кошельков одного создателя покупают 100% бюджета в «Block 0..2» траншами по 0.35 с — это схема bundle/coordinated buy собственного токена. Старый `apps/token-launch-helper/src/launch.js:6,20` прямо запрещает «No multi-wallet buys»; `TZ.md` — то же. Новая сборка этому противоречит. Сейчас код только считает (`execution_allowed_live: False`, `:873`), но заготовка подталкивает к live-варианту. | Решение владельца письменно: либо удалить 5-кошельковый сценарий, либо оставить как «чужой снайпинг-анализ». Добавить тест, что модуль не импортирует signer/RPC и что `launch.js` по-прежнему отвергает >1 кошелька. Не подключать к `launch.js`. |
| F-04 | P2 | `pump_project_launchpad.py:~979-1017` (`phases_spec`, `blocked_wash_volume_usd: 0.0`, `wash_trading_allowed: False`); `docs/audits/2026-11-01-pump-launchpad-computer-use/receipt.json` | Объёмы «organic» — зашитые константы (18 500 / 64 000 / 15 000 USD …), в квитанции `cumulative_creator_fee_usd: 6742.5`; `blocked_wash_volume_usd` всегда 0.0; `creator_sold: false` зашито; holder-monitor на 5 собственных кошельках даёт `risk_level: VERY_HIGH`. Цифра выглядит как прогноз дохода, но это фикстура. Handoff Codex (`CODEX_ARGON_PUMP_HANDOFF_20261101_RU.md` п.2) уже называет это. | Пометить поля `DEMO_CONSTANT`, убрать слово «organic» и 6742.5 из квитанции/UI, wash-политика — не словарь, а проверка по данным либо удалить поле. |
| F-05 | P2 | `apps/bossman-mobile/bossmobile/api.py` (`/m/api/trading-lab/pump-launchpad`, ~2562-2586), `routes.py:148-165` | Параметры `project_name/ticker/twitter_handle` без валидации идут в плане/манифесте; `use_demo_fixtures=True` по умолчанию. Fallback `except HTTPException` исполняет код ядра внутри процесса пульта. Аутентификация есть (`Depends(session)`, `require_scope(SCOPE_CHAT)`). | Валидация длины/символов (как в `launch.js:validate`), в ответе всегда `data_source: DEMO_FIXTURE`, тест на `<script>` в имени (preview.html). |
| F-06 | P2 | `command-center/bcc/telegram_companion/service.py:674-699`, `adapters.py:748-765` | `store_keys` записывает ключи, затем `apply_provider_key` вызывается в том же `try`, который ловит только `KeyIntakeError`; неожиданное исключение (напр. `KeyError` на `row['id']`, `int(...)`) пролетает после сохранения → владелец не получает статус, ключ уже записан. Значение не логируется (хорошо); `f"error:{exc}"[:40]` — сейчас только коды, но любое будущее сообщение с текстом ответа может утечь. | Оборачивать `apply_provider_key` в `except Exception → "error:internal"`; тесты: сбой сети, провайдер не найден, `id` не число; негативный тест «значение ключа не попадает в ответ/лог». Также удалять исходное сообщение с ключом из чата Telegram (проверить). |
| F-07 | P3 | `command-center/bcc/features/funds.py:106` | `/funds?refresh=true` обходит кэш 60 с без лимита; каждый вызов делает исходящий запрос к OpenRouter с ключом. Маршрут под токен-auth (`features/__init__.py`), не публичный. | Минимальный интервал refresh (напр. 10 с) независимо от параметра; тест. |
| F-08 | P2 | `tools/jev_kev_game_testers.py` + `docs/audits/JEV_KEV_GAME_TESTERS_20261101.md` | В квитанции `task_id: null`, `status: completed`, а ответы содержат непроверенные утверждения: Jev «TradeService.server.luau, строка 120 — дюп при одновременном обмене», Kev «PurchaseLedger — при `not updated` продолжает исполнение». Фактический код (`SwapmeGame` `PurchaseLedger.luau`) делает `assert(updated)` и возвращает `NotProcessedYet`; обмен выполняется синхронно без yield. То есть «completed» ≠ «верно». | В скрипте проверять, что процитированные строки существуют; квитанция помечает ответы `UNVERIFIED_MODEL_CLAIMS`; не считать это тестом игры. |
| F-09 | P2 | `AGENTS.md` (директива 01.11, «Cloud coding agents inside a project folder») | Облачные модели получают `code.read/write/patch` в папке проекта: исходники уходят провайдеру (OpenRouter/GLM). Приватность (AGENTS: gateway) явно не указана для этого пути; «done» требует Studio F5 — недостижимо для облака. | Дописать: перед отправкой кода проходит Privacy Gateway/сканер секретов; запись только через diff с ревью Kev/Claude; тест на отказ при секрете в файле. |
| F-10 | P2 | `docs/audits/2026-11-02-harness-capability-log/README.md` | Строки со статусом PROVEN ссылаются на задачи/коммиты, но без `receipt.json` и SHA установленного билда (требование CLAUDE.md/AGENTS §«PROVEN, not wrapped»). | Приложить `receipt.json` на каждую PROVEN-строку или понизить до PARTIAL. |
| F-11 | P1 для игры (не для Bossman) | `SwapmeGame` `src/shared/Remotes.luau:8,69-71`, `src/shared/Park.luau:33` | `FlightStart/FlightFinish/ParkState` созданы, но `OnServerInvoke` нигде не задан (grep по `src/`): клиентский `InvokeServer` повис бы навсегда. `TicketService.consume` никем не вызывается — после включения `product ≠ 0` игрок платил бы Robux за билет, который ничего не открывает. `Park.StudioTestMode = true` постоянно. | Пока нет обработчика: убрать Robux-товары билетов (`product = 0` оставить), либо реализовать `FlightStart` с `TicketService.consume`; тест-режим включать только через `RunService:IsStudio()` (уже есть, но флаг лучше `false` в релизе). |
| F-12 | P2 | `SwapmeGame` `src/server/DataService.luau:416-419` (`Update`) | `Update(fn)` вызывает `fn(profile)` на живом профиле, **без отката**; возвращаемое значение `fn` игнорируется. В `PurchaseLedger.luau` исключение внутри `grant` после частичной мутации оставит частично изменённый профиль (`pcall` не откатывает). | Применять `fn` к копии и коммитить при успехе (или явный rollback), тест «grant бросает исключение посередине — профиль неизменен». |
| F-13 | P3 | `SwapmeGame` `src/server/TradeService.server.luau:203` | `#offer > MAX_ITEMS` не ограничивает таблицу с нечисловыми ключами (`#` их не считает), цикл `for _, uid in offer` обходит всё. Сверху ограничено только числом монет игрока и проверкой дубликатов. Также `execute` не проверяет `Config.MaxInventory` получателя. | Считать элементы в цикле (>9 → return), проверять инвентарь получателя перед обменом. |
| F-14 | P3 | `SwapmeGame` `src/server/TradeService.server.luau:155` | Откат B-неудачи делает `data.stats.trades -= 1` и не восстанавливает слоты будки (`unplace` уже снял). Практически недостижим (нет yield между проверкой и `Update`), но не покрыт тестом. | Тест на путь отката или упрощение до единой транзакции на копиях. |
| F-15 | P3 | `SwapmeGame` `src/server/EconomyService.server.luau:109` | В `StateChanged` клиенту уходит `purchases = profile.purchases` (PurchaseId чеков) каждую секунду вместе со всем состоянием. | Не отправлять `purchases`; слать дельту. |
| F-16 | P3 | `SwapmeGame` `src/server/World/ArchitectAndSigns.luau:277-278` | Любой игрок переключает глобальную иллюминацию города без RateLimit (грифинг). | Подключить `RateLimit.allow` и кулдаун. |
| F-17 | P3 | `SwapmeGame` `World/HQ.luau:32-33` (cx=90, cz=0, 40×36) × `World/Streets.luau:28` (кольцо r=90, ширина 10 → x 85..95); `ArchitectAndSigns.luau:236` (Архитектор −84,0, платформа 22×22 → x −95..−73) | Здание HQ (x 70..110, z −18..18) лежит поперёк пешеходного кольца и восточной дорожки; платформа Архитектора перекрывает западную часть кольца; арка Fox Fun Park (`ArchitectAndSigns.luau:369`, x ±10, z 90) стоит на кольце. | Прорезать кольцо вокруг HQ/платформы (или сместить кольцо до r=115), проверить в Studio (`NOT_RUN`). |
| F-18 | P3 | `AiMaxBossman` `command-center/bcc/conversation_context.py:37-44` | Направление отказа «открытый» (fail-open): запрос, спрятанный в паре `НАЧАЛО X … КОНЕЦ X`, не создаёт обязательств в `action_contract` (шлюз `is_dangerous`/права не затронуты — функция используется только для обязательств, `action_contract.py:1066`). | Тест: опасная просьба внутри блока всё равно не получает прав; метка блока допускается только из явного списка (`РАСШИФРОВКА`, `TRANSCRIPT` …). |

Секреты: см. раздел 5 — **не найдены**.

## 3. Результаты тестов

Окружение: Python 3.11.18 (облачный контейнер, не ПК владельца). Зависимости из `pyproject.toml` и `command-center/requirements.lock` поставлены через `pip` (корневой `bossman-shared` — `pip install -e . --no-deps`; потребовалось доустановить `cryptography>=50`, `Pillow`, `numpy`, `asyncpg`, `redis`, `aiohttp` — в окружении не было / были битые системные). Первый прогон до доустановки `numpy/Pillow`: `1 failed, 123 passed, 53 errors` — причина `ImportError: cannot import name '_imaging' from 'PIL'` / `No module named 'numpy'` (окружение, не код).

### A1. integration (`audit/combined-build-20261102` = `claude/integration-20261102`)

```
cd command-center && PYTHONPATH=.:.. python -m pytest -q \
  tests/test_action_contract*.py tests/test_funds_feature.py tests/test_companion_key_apply.py \
  tests/test_facts_snapshot_ranking.py
........................................................................ [ 40%]
........................................................................ [ 80%]
....................................                                     [100%]
180 passed in 56.62s
```
**PASS** (180/180; glob `test_action_contract*.py` включает `test_action_contract_data_blocks.py`).
Замечание: тесты не покрывают F-02 (производительность), F-06 (исключения), F-18.

### A2. main (`origin/main` = `4a8c68f3`, временный worktree)

```
cd bossman-core && PYTHONPATH=.:..:../command-center python -m pytest -q \
  tests/test_trading_*.py tests/test_xiaomi_ocr_and_monetization.py
208 passed, 5 skipped in 7.01s
```
**PASS** (5 skipped — не раскрывались, причина не выяснялась: `NOT_RUN` для них). Первый прогон падал на сборке из-за отсутствия `asyncpg`/`redis` в окружении (после установки — зелёный).

### A3. Конфликты слияния integration → main

```
git checkout -b tmp-merge-check origin/main
git merge --no-commit --no-ff origin/claude/integration-20261102
→ «Automatic merge went well; stopped before committing as requested»
git diff --name-only --diff-filter=U → (пусто)
git merge --abort; ветка удалена
```
**Конфликтующих файлов: 0.** Наборы изменённых файлов не пересекаются: main (Argon) правил только `trading_learning/*`, `bossmobile/api.py`, `tools/monetization_trainer.py`, docs; integration — `command-center`, `AGENTS.md`, `tools/*`, docs. Оговорка: чистое слияние не проверяет семантику; после слияния нужен полный CI (`NOT_RUN` здесь).

### C. SwapmeGame `roblox/fox-city`

| Проверка | Статус |
|---|---|
| `luau-compile` (синтаксис 37 `.luau`) | **NOT_RUN** — бинарь `luau-compile` в окружении отсутствует; скачанный релиз запустить не удалось (запуск внешнего кода заблокирован политикой), я не обходил |
| `selene src` | **NOT_RUN** (нет бинаря; релиз для Linux по тому URL недоступен) |
| `rojo build` → `.rbxlx` | **NOT_RUN** (нет бинаря) |
| `tools/check.sh` | **NOT_RUN** — скрипт жёстко привязан к `/c/Users/asd/Bossman/apps-local/roblox-tools/*.exe` (Windows); из облака непереносим |
| Studio F5 / 2-player trade playtest | **NOT_RUN** (в репо есть `docs/receipts/2026-11-02-trade/README.md` — не перепроверялось) |
| Статический обзор | выполнен, см. раздел 4 |

## 4. Статический обзор fox-city (без запуска)

**PurchaseLedger (`src/server/PurchaseLedger.luau`)** — идемпотентность сделана корректно: `data.purchases[PurchaseId]` проверяется внутри `Update`, `PurchaseGranted` только после `Save`; игрок/профиль/неизвестный продукт → `NotProcessedYet`. Замечания: окно памяти 200 id (`MAX_REMEMBERED`) — очень старый ретрай теоретически выдаст повтор; `grant`-исключение не откатывается (F-12). `MonetizationService` выдаёт только прямые товары (swaps/boost/ticket), ID из `Config` (0 → пропуск).

**Доверие клиенту** — `EconomyService` (BuyVault/Merge/Rebirth) считает всё на сервере, клиент передаёт только строку id; `RateLimit`/`blocked` есть; `PlaceCoin` — в `BoothService` с rate-limit. `TradeService`: все RemoteEvent проверяют `typeof`, длину `uid ≤ 64`, владение (`owns`), сброс подтверждений при любой правке, повторная проверка владения при обмене, countdown по `version`. Дыры — F-13, F-14, F-16. `AntiExploit` только логирует/откатывает позицию (без бана) — нормально.

**DataStore** — `DataService`: `UpdateAsync`, блокировка сессии (`jobId`, таймаут), `BindToClose`, автосохранение, отказ записи при потере lock. Memory-fallback только в Studio. `Update` без отката — F-12. Публикация места/реальный DataStore не проверялись (`BLOCKED` в ROADMAP).

**Порядок модулей `WorldBootstrap`** — `BuildPlaza` (если нет Plaza) → `Polish` → `River, Bridge, HQ, Streets, Props, ArchitectAndSigns, Roads, TramLine, OldTown, Hills, PragueCastle`. `Hills` строится до `PragueCastle` (зависимость `require(Hills)` выполнена); сбой модуля изолирован `pcall`. Terrain: `River` (вода) раньше `Roads` (`FillBlock Asphalt`), `Hills` после — пересечений terrain-заливок нет (см. таблицу). `Props` зависит от `ReplicatedStorage.Assets` (Rojo `assets/rbxm`, файлы есть: `FoxStatue.rbxmx`, `TicketBooth.rbxmx`).

**Пересечения районов (вычислено по константам кода):**

| Пара | Результат |
|---|---|
| River (вода z 110..170, x ±260) × Bridge (z 100..180, x=0) | мост перекрывает реку по замыслу; ОК |
| River × Roads (до z=100, ±160) × OldTown западный ряд (z до ≈98) | зазор ≥ 10 studs; ОК |
| Roads (север z=−150, запад/восток x=±160) × OldTown (фасад z=−163/x=−173, глубина 14) | зазор 3 studs до бордюра; ОК |
| Hills.Castle (центр −212,−212, r 66) × Roads/OldTown | при z=−150 холм занимает x −235..−189, дорога начинается с x=−167; при x=−160 холм z ≤ −171, дорога от z=−143 → **пересечения нет** |
| Hills.Petrin (205,−215, r 52) × дорога x=160 / север z=−150 | нет |
| TramLine (z=−150, по северной дороге) × Roads | совпадает по замыслу; у `ArchitectAndSigns` есть второй трамвай (`PragueVintageTram`) — два разных трамвая, не пересекаются, но стоит свести |
| **HQ (x 70..110, z −18..18) × Streets (кольцо r 85..95)** | **пересечение** (F-17) |
| **ArchitectAndSigns Архитектор (−84,0; 22×22) × кольцо (x −95..−85)** | **пересечение** (F-17) |
| ArchitectAndSigns арка Fox Fun Park (x ±10, z 90) × кольцо / Bridge (z≥100) | арка стоит на кольце, мост начинается с z=100 — приемлемо |
| ArchitectAndSigns Fun Park (деки z 180..252, y 7.4) × Bridge (zEnd=180, deckY 8) | стыкуются по z=180, высота 8.0 vs 8.0 (7.4+0.6) — совпадает; ОК (визуально `NOT_RUN`) |

## 5. Секреты

Паттерны: `sk-or-`, `nvapi-`, `gsk_`, `AIza`, `PRIVATE KEY`, `ROBLOSECURITY`, seed/mnemonic.

- **Дифф integration (f8d6e5b5..integration)**: реальных секретов нет. Совпадения: `command-center/bcc/features/action_contract.py` (регулярка `AIza…|AKIA…|-----BEGIN … PRIVATE KEY-----` — шаблон детектора), `command-center/tests/test_companion_key_apply.py` (фикстура `sk-or-…` из 6 символов — заведомо фальшивая).
- **Дифф main (f8d6e5b5..main)**: совпадений нет.
- **Дерево обеих веток (вне диффа, историческое)**: только тестовые заглушки — `apps/shorts-factory/tests/test_trends_youtube.py:15` (`AIza…`), `command-center/tests/test_b05_cloud_hardening.py:34` (`nvapi…`), `command-center/ui/tests/chat_core.test.mjs:70` (`nvapi…`), PEM-заглушки в `bossman-core/tests/test_search_everything.py:98`, `test_leaf_search_everything_connectors.py:9`, `apps/social-farm/tests/unit/test_media_store.py:151`, `command-center/tests/test_web_research_safety.py:132,501`, `test_cu_task_apps_binding.py:428`. Тело ключей не раскрывалось и не проверялось на живость.
- **SwapmeGame `roblox/fox-city`**: `git grep` по всем паттернам (+`api_key=`) — 0 совпадений.
- `git log` не сканировался на удалённые секреты (`NOT_RUN`); GitHub secret-scanning не запускался.

## 6. Оценка Pump.fun Launchpad / 5-Wallet Genesis Engine (main, только чтение)

Файлы: `bossman-core/bossman/trading_learning/pump_project_launchpad.py` (1008 строк), `routes.py`, `cli.py`, `xiaomi_ocr.py`, `tools/monetization_trainer.py`, мобильный маршрут `bossmobile/api.py`.

| Вопрос | Вывод (доказательство) |
|---|---|
| Реальная отправка транзакций? | **Нет** в новых файлах. `grep` по `sendTransaction|send_raw|solana|solders|nacl|requests|httpx|aiohttp|subprocess` в `pump_project_launchpad.py` — 0 совпадений (кроме слов `jito_tip_lamports` в расчётах); `xiaomi_ocr.py:139` — `urlopen` только на loopback (проверка адреса выше); `monetization_trainer.py:59` — `subprocess.run` локальный (содержимое команды детально не аудировалось). |
| Подпись приватными ключами? | **Нет.** Адреса кошельков — фальшивые строки `BossVault{N}{sha256[:24]}` (`:878`), не ключи. |
| Координированные покупки несколькими кошельками своего токена? | **Да, как симуляция/план**: `FIVE_WALLET_ROLES` (`:775-780`), 12–60 траншей через 0.35 с, 5 кошельков создателя (F-03). Позиционируется как «прозрачно»; это не снимает риск манипуляции рынком/«fair launch» и нарушает политику старого `token-launch-helper`. Live-пути нет. |
| Dry-run по умолчанию? | Фактически **всегда paper**: `safety.py:23-25` — `TRADING_EXECUTION="OFF"`, `PAPER_TRADING_ONLY=True`, `OWNER_APPROVAL_REQUIRED=True`; в выходе `execution_allowed_live: False` (`:873`), `status DRAFT_AWAITING_OWNER_APPROVAL`. Ответ маршрута доступен через `require_scope(SCOPE_CHAT)` (`routes.py:148`) и `Depends(session)` в пульте. Защита — константы, а не отсутствие кода: нужен тест «нет импорта signer». |
| Реальный live-код в main? | **Да, отдельно:** `apps/token-launch-helper/src/launch.js`+`cli.js` — `sendRawTransaction` (`launch.js:82`), подпись `Keypair` из JSON-файла (`cli.js:44`). Защита: по умолчанию DRY RUN (`cli.js:11-15`), один кошелёк, `MAX_DEV_BUY_SOL = 5`, фраза `Я ПОДПИСЫВАЮ ЗАПУСК`, `simulateTransaction` перед отправкой, раскрытие в описании. Это единственный путь к mainnet; в этом аудите **не запускался**. Риск: ключ читается с диска по пути из аргумента; рекомендация — только hardware/отдельный кошелёк, лимит уже есть. |
| Достоверность денег | Цифры комиссий — иллюстрация (F-04), нужны пометки DEMO. |

## 7. Что НЕ проверено

`NOT_RUN`: ПК владельца, установленный билд, Roblox Studio/F5, 2-player playtest, `luau-compile`/`selene`/`rojo`/`tools/check.sh`, полный CI обеих репозиториев, весь тест-сьют (только указанные подмножества), 5 skipped trading-тестов, запуск `token-launch-helper` (даже dry-run — сеть mainnet RPC), сканирование истории git на секреты, живые провайдеры (OpenRouter/NIM), мобильный пульт на :8840.

---

## 8. Промпт-корректировка для Claude

*Инструкция лидирующему агенту Claude (интегратору). Выполнять по порядку.*

1. **Первым делом не мержи.** `claude/integration-20261102` → `main` только после C1–C3 из раздела 1. Сначала нормализуй окончания строк в `action_contract.py`, `action_router.py`, `telegram_companion/adapters.py`, `telegram_companion/service.py`, `v2/memory/lifecycle_wiring.py`, `AGENTS.md` (LF), добавь `.gitattributes`, и убедись, что `git diff --stat origin/main...HEAD` ≈ +646/−17 в 17 файлах. Перестань сохранять файлы в CRLF из Windows-редактора/PowerShell (`Set-Content`/`-Encoding`): проверяй `git diff --numstat` до коммита — аномалия «N/N строк» = стоп.
2. **Перепиши `without_data_blocks`** (`conversation_context.py:37-44`) на линейный алгоритм, а допустимые метки ограничь списком. Добавь тесты: (а) 200 КБ с 8000 незакрытых `НАЧАЛО РАСШИФРОВКИ` обрабатывается < 0.2 с; (б) опасная просьба внутри блока не получает ни прав, ни пропуска в `is_dangerous`; (в) вложенные/перекрёстные блоки ничего не скрывают.
3. **Закрой `/key` до конца:** оберни `apply_provider_key` в общий `except Exception → "error:internal"`, добавь негативные тесты (сбой сети, неизвестный провайдер, нечисловой `id`) и тест «значение ключа не встречается в ответе, логах и событиях». Убедись, что сообщение с ключом удаляется из чата Telegram.
4. **Не называй PROVEN без квитанции.** Для `/api/funds`, `/key → vault`, data blocks, recall-ранжирования приложи `receipt.json` с SHA установленного билда и живым прогоном через реальный пульт; иначе в harness-логе статус `PARTIAL`/`NOT_RUN`. Перепиши строки 5, 6, 7, 13 лога.
5. **Лимит на `/funds?refresh=true`:** минимальный интервал между живыми пробами независимо от параметра + тест. Ключи по-прежнему не выходят из процесса.
6. **Pump.fun / 5-Wallet:** не развивай `FIVE_WALLET_ROLES` дальше, пока владелец письменно не решит судьбу сценария (удалить или оставить как анализ чужих запусков). Любой PR, добавляющий подпись/`sendTransaction`/RPC в `trading_learning/`, — отклонять. Добавь тест-ограждение: модули `trading_learning/pump_*` не импортируют signer/HTTP-клиенты, а `apps/token-launch-helper` по-прежнему отвергает `wallets`/массив кошельков и `devBuySol > 5`.
7. **Убери фальшивую «доходность»:** в `pump_project_launchpad.py` и `receipt.json` пометь `phases_spec` как `DEMO_CONSTANT`, удали слово «organic» и 6742.5 USD из отчётов, `blocked_wash_volume_usd`/`creator_sold` не зашивай — вычисляй или не показывай. Добавь тест: при `use_demo_fixtures=True` каждый денежный показатель имеет `evidence_class` и `data_source: DEMO_FIXTURE`.
8. **Валидация входа мобильного маршрута** `/m/api/trading-lab/pump-launchpad`: длина/алфавит `project_name`, `ticker`, `twitter_handle`, диапазон `total_sol_budget`; тест на `<script>` в имени (preview.html) и на отказ при ticker вне `[A-Za-z0-9$]{1,10}`.
9. **Квитанции тестировщиков игр (Jev/Kev):** перестань писать `status: completed` как проверку. В `tools/jev_kev_game_testers.py` проверяй, что процитированные `файл:строка` существуют и содержат упомянутый символ; ответы моделей маркируй `UNVERIFIED_MODEL_CLAIMS`. Не используй их как аргумент «игра протестирована» — только `tools/check.sh` + Studio F5.
10. **AGENTS.md (директива 01.11):** допиши, что код проекта для облачных агентов проходит Privacy Gateway/сканер секретов до отправки, запись — только как diff на ревью; добавь тест отказа при секрете в файле проекта.
11. **SwapmeGame — первое исправление:** `DataService:Update` сделать транзакционным (применять `fn` к копии, коммит при успехе) и добавить тест «grant бросил исключение — профиль не изменён»; затем `FlightStart/FlightFinish/ParkState` — либо реализовать с `TicketService.consume`, либо удалить из `Remotes.luau`; не включай Robux-товары билетов (`Park.Tickets[*].product`) пока потребитель не работает.
12. **SwapmeGame — торговля:** считать элементы `offer` циклом (лимит 9 вместо `#offer`), проверять `MaxInventory` получателя до обмена, добавить RateLimit на `ArchitectCityModePrompt`, убрать `purchases` из `StateChanged`. Для каждого — Luau-тест/скрипт-проверка в `tools/check.sh`.
13. **Сделай `tools/check.sh` переносимым:** брать пути из `$ROBLOX_TOOLS` (по умолчанию PATH), чтобы `luau-compile`/`selene`/`rojo` можно было запускать и в CI на Linux; пока этого нет — все отчёты по fox-city честно `NOT_RUN`.
14. **Геометрия:** прорежь кольцо `Streets` вокруг HQ (x 70..110, z −18..18) и платформы Архитектора (x −95..−73, z ±11), либо подвинь HQ/кольцо; добавь в `check.sh` проверку пересечения AABB районов по константам и подтверди одним F5-скриншотом в квитанции.
15. **Процесс:** перед любым слиянием в main запускай полный CI на точном SHA, а не подмножество; фиксируй в коммите список конфликтов (`git merge --no-commit --no-ff` на временной ветке) и результаты тестов; никогда не маркируй зелёным то, что запускалось только в облачном контейнере без ПК владельца.
