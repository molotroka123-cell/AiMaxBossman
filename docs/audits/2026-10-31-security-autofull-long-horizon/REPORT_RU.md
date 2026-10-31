# Полный аудит и закрытие приоритетных задач Bossman (31.10.2026)

**Аудитор:** Аргон  
**Вердикт:** `PASS`  
**Рабочее дерево `main`:** `C:\Users\asd\Bossman\wt-bossman-20-argon`

> Выполнены и доказаны на живом ПК владельца все приоритетные пункты (пункты **5**, **1**, **2**, **3**, **6a**, **6b**, **6c** — всё кроме Openjutsu, которым параллельно занимается Claude).

---

## 1. Автостарт и спутник на `main` + удаление `prototypes/eth-drainer` (Пункты 5 и 6c)

- **Удалён** каталог `prototypes/eth-drainer` из рабочего дерева `main`.
- **Переведены все 5 якорей автостарта** со старого `wt-final` / `wt-trio-argon` на `C:\Users\asd\Bossman\wt-bossman-20-argon` (`main`):
  1. `C:\Users\asd\Bossman\one-bossman\current.json` + защита `_resolve_canonical_src` в [`tools/owner_one_bossman.py`](../../../tools/owner_one_bossman.py) (перехватывает старые аргументы планировщика с `wt-final*` и ведёт в `wt-bossman-20-argon`).
  2. `C:\Users\asd\Bossman\Start-BossmanApps.ps1`.
  3. `C:\Users\asd\AppData\Local\Bossman\CommandCenter\governor.json`.
  4. Все 5 editable-пакетов Python (`bossman-command-center`, `bossman-core`, `bossman-mobile`, `bossman-shared`, `war-terminal`) → `C:\Users\asd\Bossman\wt-bossman-20-argon`.
  5. Разрешённые корни терминала (`terminal.roots` на `:8801`) включают `C:\Users\asd\Bossman\wt-bossman-20-argon`.
- Перезапущены и проверены `:8801` (`ALIVE`, `source_identity: PASS`), `:8840` (`ok: true`), Telegram-спутник (`companion`) и `governor`.

---

## 2. Закрытие 4 дыр безопасности аудита (`1a`–`1d`)

| Подпункт | Проблема | Решение | Доказательство |
|---|---|---|---|
| **1a** | `pytest` и `git grep -O` считались «чтением», хотя исполняют код | В [`finalize.py`](../../../command-center/bcc/finalize.py) и [`action_contract.py`](../../../command-center/bcc/features/action_contract.py) `pytest`/`py.test`/`tox`/`nox`, `git grep -O`/`--open-files-in-pager`, `git -c`, `--ext-diff`, `--textconv`, `--exec-path` переведены в `WRITE` | `pytest test_action_contract.py` (`48 passed`) |
| **1b** | Временная аренда на запись (`write`) покрывала `git push` и `pip install` | В [`approval_scope.py`](../../../command-center/bcc/approval_scope.py) и [`tools_terminal.py`](../../../command-center/bcc/features/tools_terminal.py) выделен класс `PRIVILEGED` (`NON_LEASABLE_EFFECT_CLASSES`): `git push`, `pip/npm/pnpm/yarn/poetry/uv install|add`, `python -m pip install`, `docker compose`, `sudo`, `curl|sh`, `network=True` всегда требуют отдельного подтверждения | `pytest test_approval_scope.py` (`27 passed`) |
| **1c** | `pult.report` пропускал часть секретов и ломал HTML-разметку при обрезке длинного отчёта | В [`pult_report.py`](../../../command-center/bcc/features/pult_report.py) добавлены `contains_secret()` / `redact()` (OpenAI/Anthropic/GitHub/Telegram/AWS/JWT/PEM/EVM hex/BIP-39) и обрезка по границам строк/блоков **до** оборачивания в `<b>`, `<code>`, `<blockquote>` и подписи `— Аргон` | `pytest test_pult_report.py test_pult_report_tool.py` (`14 passed`) |
| **1d** | Rate-limiter входа в мобильный пульт `:8840` был общим и позволял чужому шуму заблокировать владельца | В [`auth.py`](../../../apps/bossman-mobile/bossmobile/auth.py) и [`api.py`](../../../apps/bossman-mobile/bossmobile/api.py) лимитер изолирован по клиентскому бакету (`RateLimiter(client_id)`) | `pytest apps/bossman-mobile/tests/test_mobile.py` (`135 passed`) |

---

## 3. Режим «Авто полный» с карточкой владельца (Пункт 2)

- **Backend (`:8801`)**:
  - `GET /api/approvals/auto-full`, `POST /api/approvals/auto-full`, `POST /api/approvals/auto-full/revoke` в [`api.py`](../../../command-center/bcc/api.py) и [`approval_scope.py`](../../../command-center/bcc/approval_scope.py).
  - Автоматически исполняет команды чтения и записи **только внутри выбранной папки владельца** (при этом подставляет выбранную папку первой в `_roots(svc)`, если агент не передал `cwd`), ведёт живой журнал `action_log` в `approval_scope_log` и блокирует любые выходы за пределы папки, жёсткие запреты и `PRIVILEGED`-команды (`git push`, `pip install`, сеть).
- **Мобильный пульт (`:8840`, `bossman-shell-v34`)**:
  - Карточка **«Авто полный (папка и журнал)»** в [`index.html`](../../../apps/bossman-mobile/bossmobile/ui/index.html) и [`app.js`](../../../apps/bossman-mobile/bossmobile/ui/app.js): выбор папки, срок (`15 мин` – `8 ч`), живой счётчик и список последних команд, кнопка мгновенного отключения **«Выключить Авто полный»**.

---

## 4. Long-Horizon прогон `lh-20261031-autofull-v2` + память и отчёт 3/3 (Пункт 3)

- **Найден и исправлен корневой баг ранжирования памяти в [`learning/retrieval.py`](../../../learning/retrieval.py)**:
  - В `_from_notes` заметки с точным совпадением сигнала (`exact_match`, например `orders.csv`, `out/verify.json`, `P-07`, `C-0042`) получали фиксированный `score=5.0` и пропускались при добавлении BM25-веса (`if ref in seen: continue`), из-за чего старые общие заметки без точных совпадений (`BM25 ~ 12.0`) вытесняли релевантную заметку прогона. Теперь `by_ref[ref].score += bm25` суммирует точное совпадение и BM25, а в `bm25_scores` введён нижний порог корпуса `eff_n = max(n_docs, 20)`.
- **Результаты живого 5-шагового прогона `lh-20261031-autofull-v2` ([`tools/long_horizon_run.py`](../../../tools/long_horizon_run.py))**:
  - **Вердикт:** `PASS`
  - **Шаги 1–5:** `profile` (task `821`), `metrics` (task `822`), `findings` (task `823`), `verify` (task `825`), `report` (task `829`) — все `verified: true`.
  - **Убийство и перезапуск `:8801` посреди шага 3 (`findings`):** `interrupted: true`, `duplicated_completed_calls: 0`.
  - **Проверка `STOP` посреди шага 4 (`verify`):** `stop_new_calls_after_stop: 0`.
  - **Подъём фактов из памяти на шаге 5 (`report`):** `recalled_events: 1`, `recalled_relevant_facts: true`, `missing_facts: []` (первым источником поднята каноническая запись `BOSSMAN Memory/2026-10-31-1300-LongHorizon-lh-20261031-autofull-v2-orderscsv-verified-findings.md` + темпоральный факт `fact#46`).
  - **Полнота отчёта `REPORT.md` (`9e24f35cbac40412`):** присутствуют все **3 из 3** проверенных находок (`P-07` с маржой `-207530.4`, `C-0042` с долей `15.9%`, регион `Север` с падением `32.1%`).

---

## 5. Сверка цены многоходового диалога Outrun и живой бенчмарк исследований недели (Пункты 6a и 6b)

- **Пункт 6a — Многоходовый диалог DeepSeek через Outrun ([`tools/outrun_cost_check.py`](../../../tools/outrun_cost_check.py))**:
  - 4 хода в одной сессии (`session_id=71`, `agent_id=51`), `tokens_in=8413`, `tokens_out=1099`.
  - Счётчик Bossman: `$0.007061`, списание баланса Outrun: `$0.007052` (`$10.772544 → $10.765492`), расхождение `-$0.000009` (`PASS`). Записано в [`docs/audits/2026-10-29-outrun-deepseek/receipt.json`](../2026-10-29-outrun-deepseek/receipt.json).
- **Пункт 6b — Живой агентный прогон по исследованиям недели ([`tools/run_research_weekly_20261030.py`](../../../tools/run_research_weekly_20261030.py))**:
  - Запущен с `--live --agent 51`: `unit_guards_status: PASS_UNIT`, `live_agent_status: PASS_LIVE` (`task_id=816 completed`, `true_claim_verifier_status=VERIFIED`, `false_claim_caught=True`, `fault_state_halted_before_irreversible=True`, `out_of_scope_work_hits=0`, `hits_after_revoke=0`).
  - Квитанция: [`docs/audits/2026-10-30-weekly-research-benchmarks/receipt.json`](../2026-10-30-weekly-research-benchmarks/receipt.json) (`sha256: a6bea1beec2bb7d81ad0ce5c4f622eecfeff347f17d7a9144a5b3339a0610a2b`).

— **Аргон**
