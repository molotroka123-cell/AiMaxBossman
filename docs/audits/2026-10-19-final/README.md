# Финальный аудиторский отчёт Bossman — Закрытие Red Team 20.10.2026, разделение контуров доказательств и обновление Telegram Calls / Mobile Pult

**Дата обновления:** 2026-10-20
**Ветка:** `main` (интеграция `feat/argon-tier5-best-of-20261019`)
**Рабочий чекаут:** `C:\Users\asd\Bossman\wt-rc-20261019`
**Runtime-чекаут `:8801`:** `C:\Users\asd\Bossman\wt-checkpoint-20261015`
**Главный вердикт по уровню:** **Tier 5 пока не доказан.**

**Опорные документы и артефакты:**
- Независимый отчёт Red Team: `docs/audits/2026-10-20-independent-redteam/README_RU.md` и `docs/audits/2026-10-20-independent-redteam/repro_synthetic.py`
- Машинная квитанция трёхуровневого верификатора (`SYNTHETIC` / `HISTORICAL_LIVE` / `CURRENT_LIVE`): `docs/audits/2026-10-19-final/e2e_stop_holdout_receipt.json`
- Замеры ускорения горячих контуров: `docs/audits/2026-10-19-final/BENCHMARK_SPEEDUP_RU.md` (`benchmark_speedup.json`)
- Замеры эффекта памяти локальной модели: `docs/audits/2026-10-19-final/g4-transfer/summary_ace-qwen3-17b_latest.json`

---

## 1. Закрытие 6 подтверждённых дефектов независимого Red Team (`RT20-01` – `RT20-06`)

Воспроизведение скриптом `python docs/audits/2026-10-20-independent-redteam/repro_synthetic.py`:
- **До исправления (`6342a18d`):**
  - `STOP_WRITE False quotes_before 0 quotes_after 1 answered_quote True`
  - `NO_MARGIN_AUTO_3_PERCENT True`
  - `STALE_RATE_SELECTED 20`
- **После исправления (текущий `HEAD`):**
  - `STOP_WRITE True quotes_before 0 quotes_after 0 answered_quote False`
  - `NO_MARGIN_AUTO_3_PERCENT False`
  - `STALE_RATE_SELECTED 25`

| ID | Статус | Модули и суть исправления | Первичное доказательство |
| :--- | :---: | :--- | :--- |
| **RT20-01** (Запрет записи котировки после `STOP`) | **PASS** | В `bossman_shared/exchange_assistant/agent.py` шаблоны `RE_GIVE` и `RE_GET` включены в `WRITE_PATTERNS`. В `bossman_shared/exchange_assistant/store.py::Store.save_quote` и `command-center/bcc/telegram_companion/exchange_bridge.py` добавлена принудительная проверка активного маркера `STOP` непосредственно перед `INSERT INTO quotes` (при активном `STOP` возбуждается `StopRefusalError("STOPPED_BY_OWNER")`, ни одной строки в `quotes`/`events` не создаётся). | `repro_synthetic.py` (`STOP_WRITE True quotes_before 0 quotes_after 0`); `pytest tests/test_exchange_assistant.py apps/bossman-mobile/tests/test_exchange.py command-center/tests/telegram_contracts/test_companion_exchange_bridge.py` (`39 passed`). |
| **RT20-02** (Обязательная ставка менеджера для каждой котировки) | **PASS** | В `bossman_shared/exchange_assistant/quote.py` установлено `DEFAULT_MARGIN = None` и `require_explicit_margin: bool = True` по умолчанию. В `agent.py` для обеих форм (`RE_GIVE` и `RE_GET`) при отсутствии `при X%` котировка не рассчитывается и не сохраняется, менеджеру возвращается запрос ставки (`Уточните ставку (процент комиссии) менеджера...`). | `repro_synthetic.py` (`NO_MARGIN_AUTO_3_PERCENT False`); `tests/test_exchange_assistant.py::test_rt20_02_quote_requires_explicit_manager_margin_on_both_patterns` (`PASS`). |
| **RT20-03** (Выбор наиболее свежего ручного курса) | **PASS** | В `bossman_shared/exchange_assistant/rates.py::RateBook.put` среди ручных курсов выбирается запись с максимальным `as_of`. В `bossman_shared/exchange_assistant/store.py::Store.manual_rates` SQLite-запрос `GROUP BY pair` заменён на детерминированный выбор самой свежей записи по кортежу `(as_of, id)` без перезаписи более старым курсом. | `repro_synthetic.py` (`STALE_RATE_SELECTED 25`); `tests/test_exchange_assistant.py::test_rt20_03_store_manual_rates_selects_newest_as_of` (`PASS`). |
| **RT20-04** (Запрос фактических сумм обеих сторон перед проводкой сделки в CRM) | **PASS** | В `bossman_shared/exchange_assistant/store.py::Store.confirm` убрана автоподстановка расчётных сумм котировки: требуются явные положительные `leg_in_actual` и `leg_out_actual`. В `agent.py` команда `обмен выполнен Q-...` без сумм не переводит сделку в `settled`, оставляет статус `unknown` и запрашивает у менеджера фактические суммы обеих сторон (`приняли <сумма> выдали <сумма>`). | `tests/test_exchange_assistant.py::test_rt20_04_crm_confirm_requires_explicit_actual_amounts_both_sides` (`PASS`). |
| **RT20-05** (Честная проверка постусловия `postcondition_verified` и маркировка `SYNTHETIC`) | **PASS** | В `command-center/bcc/features/coding_tasks.py::mission_receipt` добавлено поле `evidence_kind` (`"SYNTHETIC"` или `"LIVE"`), а `postcondition_verified = True` выставляется **только** при непустом `verify_cmd`, `verify_exit_code == 0`, непустом `git_diff` и `head_before != head_after`. Пустой `verify_cmd=[]` всегда даёт `postcondition_verified=False`. | `command-center/tests/test_coding_tasks.py` (`14 passed`). |
| **RT20-06** (Разделение `SYNTHETIC`, `HISTORICAL_LIVE` и `CURRENT_LIVE` в верификаторе и отчёте) | **PASS** | `tools/verify_tier5_argon_e2e.py` полностью переписан: формирует три раздельных блока доказательств (`SYNTHETIC`, `HISTORICAL_LIVE`, `CURRENT_LIVE`), никогда не смешивает синтетические тесты с живыми прогонами, сверяет `companion.sqlite3` и хэш-цепочку `pc-agent/audit.jsonl` для исторического события и выполняет свежую живую безопасную миссию через `:8801` с общим `task_id`, наблюдаемым действием, `STOP` и независимой проверкой результата. | `tools/verify_tier5_argon_e2e.py` -> `docs/audits/2026-10-19-final/e2e_stop_holdout_receipt.json` (`tier5_proven: false`, `verdict_ru: "Tier 5 пока не доказан"`). |

---

## 2. Статус ворот и проверок (`SYNTHETIC` / `HISTORICAL_LIVE` / `CURRENT_LIVE`)

| Пункт / Ворота | Статус | Контур доказательства | Первичное доказательство и метрики |
| :--- | :---: | :---: | :--- |
| **1. Исправление 6 красных проверок CI на точном коммите без ослабления проверок** | **PASS** | `CI / GitHub Checks API` + локальный прогон | На `f6467b7d` падали 6 проверок: `root pytest + hygiene (py3.11/py3.12)` (×2 из-за CRLF в `git diff --check`), `pytest (py3.11)` (`test_web_research_flag` из-за безусловной регистрации `web.search` в `web_access.py`) и `pytest (py3.12)` (`test_jeff_master_parser_surfaces` из-за блокировки event loop в `master_parser`). Все 3 первопричины устранены без ослабления тестов: на `c46cde9832d49aad8f32dfcafece208a9fb4b2ca` GitHub Checks API фиксирует **`0 failure`** (`40 success`, `1 skipped` hardware sandbox, `1 cancelled` по времени раннера). |
| **2. Сверка 4 идентичностей (`source`, `build`, `tree`, `archive`)** | **PASS** | `CURRENT_LIVE` | `tools/build_source_identity.py --reconcile --bundle-manifest C:\Users\asd\AppData\Local\Bossman\CommandCenter\app\BUILD_INFO.json` и `GET http://127.0.0.1:8801/health/live`: `source_identity: "PASS"`, `all_four_match: true`, `matched_count: 4`, `source_dirty: false`. |
| **3. Синтетический стенд (`SwapMe` / `LangGraph` / `OpenHands` / `Memory` / `Holdout`)** | **PASS** | `SYNTHETIC` | `tools/verify_tier5_argon_e2e.py` (`receipt["SYNTHETIC"]`): `rt20_01_stop_blocks_quote_write=true`, `rt20_02_missing_margin_rejected=true`, `rt20_03_newest_manual_rate_selected=true`, `rt20_04_confirm_without_actuals_rejected=true`, `unverified_postcondition_rejected=true` (`postcondition_verified=false` при `verify_cmd=[]`), `verified_postcondition_accepted=true` (`verify_exit_code=0`), `secret_holdout_leak_blocked=true`. |
| **4. Историческое живое событие `/stop` из Telegram (`19.10.2026`)** | **PASS** | `HISTORICAL_LIVE` | Первичная сверка с диском (`companion.sqlite3` `inbox.id=974334929`, `message_id=2310`, `created=1792471328.6959` + `pc-agent/audit.jsonl` `seq=13, 14` с проверкой `prev_hash`/`entry_hash`): `hash_chain_valid=true`, `actions_after_stop=0`. |
| **5. Новая безопасная живая миссия (`CURRENT_LIVE`) с общим `task_id`, наблюдаемым действием, `STOP` и независимой проверкой** | **PASS** | `CURRENT_LIVE` | Выполнена в `tools/verify_tier5_argon_e2e.py` против живого `:8801` (`receipt["CURRENT_LIVE"]`): единый `task_id`, шаг 1 (`POST /api/computer/resume`), шаг 2 (`POST /api/computer/act` `wait` с записью в живой `step-log`), шаг 3 (`POST /api/computer/stop` с `task_id`), шаг 4 (попытка действия после `STOP` отклонена `409 STOPPED_BY_OWNER`), шаг 5 (независимая проверка `audit.jsonl`, `/api/computer/status`, `/api/computer/step-log` во внешнем подпроцессе `verify_exit_code=0`), шаг 6 (штатный возврат `/api/computer/resume`). |
| **6. Доработка Telegram-звонков и обновление Мобильного пульта (`:8840`)** | **PASS (код, UI, doctor, selftest) / BLOCKED (живой MTProto-дозвон ждёт SMS-код владельца)** | `CURRENT_LIVE` + `SYNTHETIC` | Исправлен сброс локальной модели Jeff при наличии `generation-cloud.json` (`ignore_generation_cloud=True` в `bcc/pit/config.py`, `doctor_rows.py`, `jeff_engines.py`, `call_surface.py`): `POST /api/telegram/calls/doctor` на `:8801` теперь возвращает `PASS` по строке `Локальная модель Jeff` (`gemma4-31b-uncensored:latest`). В `apps/bossman-mobile` (`:8840`) добавлена вкладка **«Звонки»** (`#view-calls`) и 20 прокси-маршрутов `/m/api/calls/*` (настройка `api_id`/`api_hash`, вход по коду/2FA, выбор контакта, звонок, сброс, `STOP`/`Продолжить`, автоответчик, `selftest`, `doctor`). Прогнан живой `POST /api/telegram/calls/selftest` (`scenario="all"` -> `ok: true`, все 6 аудио-сценариев `PASS`). Живой вызов `POST /api/telegram/calls/call` возвращает честный `409 NOT_LOGGED_IN`, так как `account.state = "logged_out"` (`has_session: false`) и требует ввода одноразового кода владельцем из Telegram. Автотесты: `748 passed` (`tests/telegram_calls`), `26 passed` (`telegram_calls.test.mjs`), `171 passed` (`apps/bossman-mobile/tests`), `33 passed` (`apps/bossman-mobile/tests/ui`). |
| **7. Заранее зафиксированные скрытые задачи по 15 направлениям (3 повтора)** | **NOT_RUN** | `Tier 5 Gate` | Внешний слепой holdout-пакет с 3 независимыми прогонами по всем 15 веткам ещё не проводился. |
| **8. Измеренный перенос навыка локальной модели без подсказок из памяти** | **NOT_RUN** (`MEMORY_RECALL_EFFECT: PASS`) | `Tier 5 Gate` | Доказан перенос за счёт памяти уроков (`2/6 -> 5/6`, `13/30 -> 21/30` при `recalled_lessons=6`). Перенос навыка весов локальной модели без инъекции уроков в контекст: **`NOT_RUN`**. |
| **9. 24/48 часов устойчивой работы на едином SHA** | **NOT_RUN** | `Tier 5 Gate` | Непрерывный 24/48-часовой soak-прогон на финальном коммите ещё не выдержан (`NOT_RUN`). |
| **10. Подтверждённый практический бизнес-результат** | **BLOCKED / NOT_RUN** | `Tier 5 Gate` | Контур SwapMe и PDF/HTML-документов технически готов и проверен тестами, но коммерческий ключ `XE_API_KEY` не подключён (`fallback_mode="MANUAL_ENTRY"`), а подтверждённые реальные клиентские проводки требуют участия владельца. |

---

## 3. Итоговое заключение

Все 6 дефектов независимого Red Team (`RT20-01`..`RT20-06`) устранены в коде и верификаторе, 6 красных проверок CI исправлены без ослабления тестов, звонки Telegram и мобильный пульт (`:8840`) обновлены и проверены (`doctor` + `selftest`), проведена живая безопасная миссия (`CURRENT_LIVE`) с общим `task_id`, наблюдаемым действием, `STOP` и независимой проверкой.

Поскольку закрытый трёхкратный benchmark по 15 направлениям, перенос навыка локальной модели без памяти, 24/48-часовой soak-прогон и подтверждённый бизнес-результат ещё не выполнены (`NOT_RUN` / `BLOCKED`):

**Tier 5 пока не доказан.**
