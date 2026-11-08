# Bossman Harness — единый агент (03.11.2026)

Ветка `feat/harness-unified-20261103` (в `main` не слита — только по «да» владельца).
Агент в реестре: **id 80 «Bossman Harness»**.

## Лестница исполнителей

```
задача ─► 1. локальная модель   qwen3-coder 30B (model 102, Ollama)          $0
          │  отказ 429/5xx/410/timeout/пустой ответ
          ├► 2. бесплатный облачный пул   permissions.free_pool = [55, 18, 71, 75, 82]   $0
          │     NIM nemotron-ultra · NIM deepseek-v4.1-flash · Groq gpt-oss-120b · Gemini 2.5/3.5 flash
          │     порядок = живое здоровье моделей; остывающие пропускаются; каждый отказ → cooldown
          │  пул исчерпан
          ├► 3. платный с потолком   fallback_model_id = 105 (GLM 5.3 Flash, 7API)   $0.028/Mtok, потолок $2/день
          │     (permissions.free_pool_first = true: платный только ПОСЛЕ бесплатного пула)
          └► 4. Claude — только решением владельца (вне роутера; агент сообщает «нужен Claude», сам не зовёт)
```

Код: `command-center/bcc/free_pool.py` (чистые функции), `Engine._free_pool_rotate` и ветка
`free_pool_first` в `Engine._call_model_scoped` (`command-center/bcc/engine.py`).

Правила ротации:
- ротация только на классах, которые чинит другая модель: throttled (429/402), provider_down (5xx/410),
  timeout, silent (пустой ответ), malformed. На 401/403 (ключ) — стоп: ключ не чинится другой моделью.
- `LookupError/TypeError/ValueError/PermissionError` (не вина провайдера) — модель пропускается без записи
  здоровья (иначе здоровая модель получала бы cooldown).
- не более 4 моделей за вызов; приватные задачи (`meta.privacy = private/local_only`) в облако не уходят;
  отказ облаку при PC-инструментах и проверки консента/цены — те же, что у настроенного fallback.
- кураторский список (`free_pool: [ids]`) обходит сверку возможностей, но не проверку tools.
- `free_pool: true` — любой бесплатный облачный с теми же возможностями (coding/tools/vision), что у модели агента.

## Память: уроки как чек-лист

- `FactsSnapshot.search` (`bcc/v2/memory/lifecycle_wiring.py`): 5-символьные основы слов (русские падежи),
  IDF-вес, слова заголовка урока ×2.
- `UnifiedRetriever._from_facts` (`learning/retrieval.py`): порядок порта фактов — основной сигнал,
  BM25 (теперь тоже по основам) — только для равенства. Раньше BM25 выталкивал нужный урок из 3 резервных слотов.
- `_render` добавляет блок `[ENGINEERING CHECKLIST]`: пункт = фразы «Правило:/Исправление:/Решение:» урока,
  а не начало истории инцидента. Блок — рекомендация: не даёт инструментов, прав или бюджета.

Офлайн-проверка: `python tools/memory_recall_eval.py` (живые факты только на чтение).

## pc.studio — Roblox Studio

`command-center/bcc/features/studio_pc.py`, CLI `tools/studio_pc.py`.
Действия: `status`, `open <place.rbxlx>`, `front`, `play` (F5), `stop` (Shift+F5), `run_lua` (командная строка,
доказательство — маркер в Output), `copy_output`, `screenshot`, `close` (сначала мягко, принудительно — только свой pid).
Политика PC-агента: `status/screenshot` T0, остальное T1, неизвестное действие T3.
**Busy guard**: любое действие с вводом отказывает, пока открыт Studio, запущенный не этим модулем
(чужой плей-тест не получает наших клавиш). Полный прогон: `python tools/studio_pc.py proof <place> <out_dir>`.

## Как измеряется экономия лимитов

Каждый прогон `tools/harness_proof.py` пишет квитанцию: какие ступени вызваны, токены, `spend_usd`
и `claude_equivalent_usd` — сколько стоили бы те же токены у Claude Sonnet/Opus по прайсу ($3/$15 и $15/$75 за Mtok).
Экономия = claude_equivalent − spend. Платная ступень считается в `paid_calls` (должна быть 0, пока отвечает бесплатная).

## Развёртывание и ревизия Tier 4 / Tier 5 (обновлено 05.11.2026)

- **Статус живого рантайма `:8801` vs `checkout_identity` (`BLOCKED` на перезапуске)**:
  - Внутренняя проверка кода рабочего дерева `source_identity()` (`checkout_identity`) возвращает `PASS`, но живой сервер `:8801` (`PID 36704`, запущен `02.11.2026 05:58:16` в привилегированной сессии Администратора) продолжает работать на старом коммите `4fade03df51140b66bbcafab575c7ee7156051df` и сообщает `source_identity: "SOURCE_IDENTITY_STALE"`, `checkout_sha: "18a39b457a067dadddbc59acb204a6f5b98e27ae"`.
  - Из непривилегированной сессии остановка `PID 36704` отклоняется ОС (`Access Denied`). Подготовлен безопасный скрипт переключения [`tools/safe_backend_cutover.py`](../../tools/safe_backend_cutover.py) (проверка активных задач, онлайн-бэкап `bcc.db`, проверка `/health/live`, Pult companion и откат), который честно фиксирует `BLOCKED` (`ELEVATED_PROCESS_ACCESS_DENIED`) до перезапуска из окна Администратора (`powershell -ExecutionPolicy Bypass -File tools\restart_backend.ps1`).
- **Память и автономное применение уроков (`PASS` в коде и живом recall, веса `NOT_RUN`)**:
  - В `bcc/v2/memory/facts.py` и `bcc/v2/memory/lifecycle_wiring.py` устранён жёсткий потолок `200`/`500` строк при выборке фактов из `bcc.db` (лимит пула поднят до `2500`), а в `learning/retrieval.py` исправлено формирование `[ENGINEERING CHECKLIST]` с тегами `[fact:ID]` и правилами из новых уроков.
  - Строго разделены 4 уровня проверки: (a) нахождение урока в top-3 (`memory_recall_eval.py`: `3/3`, `bossman_native_auto_recall_eval.py`: `4/4` без захардкоженных ID); (b) передача `[MEMORY CONTEXT]` + `[ENGINEERING CHECKLIST]` в модель; (c) изменение ответа модели на held-out задачах (`baseline 3/4` -> `after_native_recall 4/4`); (d) изменение весов моделей (`NOT_RUN`, веса `qwen3-coder:30b` и `qwen35-3d-coder:latest` неизменны).
- **Устранение ложноположительного `ok=True` в `pc.studio` (`studio_pc.py`)**:
  - Исправлен дефект в `StudioPC._input`, где проверка `"front=False" not in out` возвращала `ok=True` при пустом выводе PowerShell, исключении `PS_ERROR:` или частичном выполнении. Теперь требуется явный токен завершения `STUDIO_ACTION_OK:<action>`, отсутствие `PS_ERROR:`/`front=False`, а `screenshot()` проверяет заголовок `IHDR` и отвергает подставные `1x1` PNG.
  - Историческая квитанция `docs/audits/harness-tier4-20261105/studio_proof/proof_receipt.json` переклассифицирована из `PASS` в `BLOCKED` (`simulated: true`, `live_screen.png` 69 байт `1x1` был фикстурой юнит-теста при занятом чужом `RobloxStudioBeta.exe` `PID 47928`).
- **Агент 80, ротация и приватность**:
  - В `bcc/api.py` (`_default_chat_only`) и `bcc/features/harness_guards.py` (`harness_profile_patch`) устранён дефект `cloud_pc_access: false` у локального агента 80 (`model_id = 102`, `qwen3-coder:30b`), при этом облачные модели по-прежнему блокируются `refuse_cloud_with_pc`, а `withhold_local_memory` вырезает и `[MEMORY CONTEXT]`, и `[ENGINEERING CHECKLIST]` перед любым облачным вызовом.
  - Модели с неизвестной ценой (`pricing_known=False` или `None`) никогда не считаются бесплатными (`refuses_unknown_price` / `is_free_cloud`).

## Статус доказательств (ревизия 05.11.2026, квитанции в `docs/audits/harness-tier5-audit-20261105/`)

| Возможность | Статус | Доказательство |
|---|---|---|
| Ротация бесплатного пула + бюджет + приватность | **PASS** | Юнит-тесты (`test_free_pool_rotation.py`) + живые квитанции `ladder_receipt.json` и `swarm_cloud_local_receipt.json`: Rung 1 `#102` ($0), Rung 2 `#55` NIM ($0), реальный `429` (`#82`) и `401` (`#71`) → cooldown/пропуск, `unknown price != free`, `withhold_local_memory` и `refuse_cloud_with_pc` |
| Нативное вспоминание и применение уроков (`[ENGINEERING CHECKLIST]`) | **PASS (веса: NOT_RUN)** | `memory_recall_receipt.json` (`3/3` в top-3) + `p1_memory_auto_recall_receipt.json`: штатный `MemoryLifecycle.task_start` без ID уроков в скрипте находит **4/4 новых уроков (`#853..#862`) в top-3** и меняет поведение `qwen3-coder:30b` на held-out задачах с `3/4` до `4/4` (`$0.00`). Веса не менялись (`weights_modified: false`) |
| `pc.studio` для Roblox Studio | **PASS (код/тесты) / BLOCKED (live Studio)** | 16 юнит-тестов (`test_studio_pc.py`, включая негативные тесты на пустой вывод, потерю фокуса, ошибку PS и `1x1` PNG). Живой запуск Studio на машине честно имеет статус **BLOCKED**, так как в другой сессии открыт чужой `RobloxStudioBeta.exe` (`PID 47928`, `hwnd=0`), который по правилам безопасности нельзя завершать |
| Единый агент Harness (id 80) + живой `:8801` | **PASS (в `bcc.db` и ветке) / BLOCKED (перезапуск `:8801`)** | Профиль агента 80 в `bcc.db` содержит все 8 инструментов (`code.*`, `guard.*`, `pc.studio`), но живой процесс `:8801` (`PID 36704`, коммит `4fade03d`) запущен с правами Администратора (`SOURCE_IDENTITY_STALE`) и требует перезапуска из-под Администратора |
| Ворота публикации G6 (`assert_publishable`) | **PASS (механизм отказа) / BLOCKED (релиз игры)** | `publish_gate.py` проверяет все 10 условий (STOP, одобрение владельца, точный SHA, чистое дерево, сборка, автотесты, G3, G4+лицензии, G5+Paid Random Items, живой Studio playtest с реальным скриншотом, точка отката). Статический прогон (5/5) **не** выдаёт «можно публиковать» (`can_publish: false`) |

Облачная помощь: агент 46 (GLM 5.3 Flash, 7API) ревьюил дифф пула (задача 1655, $0.000622) — 3 из 5 замечаний
подтверждены и исправлены (4f36603b).
Находка: память в виде system-блока «DATA, NOT INSTRUCTIONS» модель игнорирует; правила должны идти вместе с задачей.
Находка: локальная очередь Ollama общая с Roblox-циклом (агенты 77/79) — вызов A/B упал по таймауту 900 с (06:33).

## Guards G1–G6

Реальные сбои 03.11 превращены в автоматические проверки — см. [HARNESS_GUARDS_20261103_RU.md](HARNESS_GUARDS_20261103_RU.md).
