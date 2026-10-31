# ТЗ и отчёт закрытия багов `main` без новых функций (31.10.2026)

> **Принцип**: 0 новых функций и 0 новых ручек API — только устранение реальных дефектов текущего `main`, подтверждённых скрытыми holdout-скриптами (`tools/tree_holdout/`), системным лаунчером `owner_one_bossman.py` и журналом ошибок живой базы `bcc.db`.

## 1. Техническое задание (ТЗ `B1`–`B4`)

| Код | Дефект на `main` (`8c3b479d`) | Причина | Критерий закрытия (`PASS`) |
|---|---|---|---|
| **B1** | Падение скрытого holdout `tools/tree_holdout/ungrounded_screen_claim.py` (`0/1 passed`, дефект `D-036`) | В `command-center/bcc/features/action_contract.py` отсутствовала функция `ungrounded_screen_claim(answer, tools_called)` и проверка в `_gate` (`gate_completion`), из-за чего агент мог написать *«На экране я вижу…»* без вызова `computer.observe` / `pc.screenshot` / `browser.screenshot` | `ungrounded_screen_claim.py` проходит `32/32 passed`; все 5 скриптов `tools/tree_holdout/*.py` проходят `130/130 passed`; `_gate` возвращает `FAIL` (`reasons: action_contract/ungrounded_screen_claim`) при отсутствии инструмента наблюдения |
| **B2** | `tools/owner_one_bossman.py find` не видит живой backend `:8801` (`uvicorn bcc.app:create_app --factory`) и мобильный пульт `:8840` (`-m bossmobile serve`) из checkout-дерева | `process_kind()` искал только `-m bcc(\.app)?`, а `find_processes()` при пустом `BCC_DATA_DIR` учитывал только `\runtime\python` | `owner_one_bossman.py find` на живом ПК находит все 3 процесса (`backend` на `:8801`, `mobile` на `:8840`, `companion`) |
| **B3** | `tools/owner_one_bossman.py status` возвращает `"backend": null, "backend_pid_alive": false` при запущенном через `uvicorn` сервере `:8801` (из-за чего `soak_sample` пишет `backend_ok: false`) | `status()` проверял только `backend.lock`, который не создаётся при прямом запуске `uvicorn bcc.app:create_app --factory` | `status()` при отсутствии `backend.lock` сверяет живой `/health/live` на `:8801` и процесс `backend` из `find_processes()`, возвращая реальный `pid`, `port: 8801`, `build_sha` и `backend_pid_alive: true` |
| **B4** | В `bcc.db` агенты `9` и `12` привязаны к удалённым моделям `7` (`ling-3.0-flash-fin:free`, `unavailable`) и `5` (`nex-n2.5-pro:free`, `unavailable`), модели `68`/`69` (`z-ai/glm-5.3*`) имеют `price_in=0, price_out=0` (блокируя `long_run.py`), а задача `815` висит в `waiting_approval` (`approval_id=334`) | Устаревшие записи в таблицах `agents`, `models` и `approvals` после ротации каталога провайдера | Агенты `9` и `12` переключены на `model_id=6` (`nemotron-ultra-free-canonical`), у моделей `68`/`69` выставлен тариф `$0.15 / $0.50` за 1M токенов, зависшее одобрение `334` закрыто (`pending_approvals: []`) |

## 2. Доказательства исполнения

- **Holdout-проверка (`tools/tree_holdout/`)**:
  - `atomic_json_replace.py`: `10/10 passed`
  - `cli_task_paging.py`: `7/7 passed`
  - `discovery_nonfinite.py`: `72/72 passed`
  - `goal_budget_nonfinite.py`: `9/9 passed`
  - `ungrounded_screen_claim.py`: `32/32 passed`
- **Pytest**: `15 passed` (`test_action_contract.py -k d036` + `test_owner_one_bossman.py`), полный прогон `test_action_contract.py` + `test_owner_one_bossman.py` — `133 passed`.
- **Живой `owner_one_bossman.py status`**: `backend_pid_alive: true`, `source_identity: "PASS"`, `mobile.ok: true`, `companion_poller_lock_held: true`.
