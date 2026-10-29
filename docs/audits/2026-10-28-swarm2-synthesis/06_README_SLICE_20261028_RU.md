# README-срез состояния Bossman OS 2.0 (28.10.2026)

**Дата среза:** 28.10.2026  
**Ветка:** `main` (Harness Approved Bossman OS)  
**Статус релиза:** «Bossman движется к production; production/freeze/Tier 5 пока не доказан»

---

## Краткая сводка возможностей на текущем `main`

1. **Безопасность и периметр управления ПК (`computer`, `security`):**
   - **Разделение ролей:** все мутирующие маршруты `/api/pc-agent/*` (`approve`, `resume`, `trust`, `self-modify`) требуют токена `owner`; токен `agent` может вызвать только экстренный `/api/pc-agent/stop`.
   - **Верифицированные одобрения:** `POST /api/pc-agent/self-modify` проверяет наличие реальной одобренной записи `approval_id` в `svc.approvals` и остаётся fail-closed (`verification_blocked_unisolated`), пока не запущена контейнерная OS-песочница.
   - **Контроль процессов на Windows 11:** каждый дочерний процесс `pc.run_command` стартует приостановленным (`CREATE_SUSPENDED`), привязывается к Windows Job Object до `resume()` и гарантированно уничтожается вместе со всеми потомками при `STOP` или таймауте.
   - **Защита от обфускации PowerShell:** `_classify_run_command` блокирует `-EncodedCommand`, backtick-обфускацию, `iex` и `DownloadString` на уровне `T3` (`deny`).

2. **Финансовая защита и облачный роутер (`cloud`, `models`):**
   - **`SpendFirewall` (`$0.00` по умолчанию):** строгий разбор URL через `urlsplit`, блокировка любых платных или нераспознанных тарифов без одноразового токена владельца, привязанного к конкретным `allowed_provider`, `allowed_model` и `max_usd`.
   - **Честный учёт `cloud.ask`:** дневной бюджет учитывает все статусы вызовов (`ok`, `empty`, `error`), читает весь журнал `audit.jsonl` без усечения на 2000 строк и блокирует дальнейшие платные вызовы при `billing_unknown` или повреждении журнала.
   - **Честность бенчмарков:** синтетический отчёт турнира `CYBER-QLORA-20261026` отозван (`revocation.json`); до живого прогона весов на замороженном holdout статус удерживается как `NOT_MEASURED`.

3. **Надстройка Bossman 2.0, Память Obsidian и Приложения (`memory`, `apps`, `mobile`, `media`):**
   - **Bossman 2.0 Superstructure (`/api/v20/*`):** живой мониторинг статуса 15 веток, 5 самотестов безопасности (`/api/v20/security/self-test`) и синхронизация памяти Obsidian (`/api/v20/memory/sync`, 68 проверенных заметок, 0 мусорных черновиков).
   - **Мобильный пульт (`:8840`):** потоковый экран с приватным фильтром (`screen-stream`), предпросмотр тапа (`touch-preview`), голосовой дуплекс (`voice/turn`), `1-Tap Rollback` и оценка радиуса поражения (`blast-radius`).
   - **Контур Искусства (`bcc/art_v2`) и Инструменты (`tools/site_app.py`):** компилятор графов `Flux.1/SDXL`, мост `VoxelToRenderBridge`, 7/7 гейтов `Video Studio 2.0`, анализатор ритма `AudioBeatAnalyzer` и офлайн-сборщик сайтов/приложений с верификацией `verify_site`.
