# Bossman как harness-агент — журнал «умеет / не хватает» (01–05.11.2026, доведён до Tier 4)

Правило записи: только то, что наблюдалось на живом runtime (`:8801`, коммит в строке), с id задач/коммитов и JSON-квитанциями в `docs/audits/harness-tier4-20261105/`.
Статусы: PROVEN (видели результат) · PARTIAL · DEFECT (найден и исправлен, есть коммит) · MISSING · BLOCKED.

| # | Возможность harness | Статус | Доказательство | Чего не хватает |
|---|---|---|---|---|
| 1 | Очередь задач + агенты с разными моделями (local / free NIM / платный OpenRouter / 7API) | PROVEN | задачи 1019–1038, агенты 25/44/46/70/71/80 | — |
| 2 | Чат-проекты: облачный агент пишет код только внутри папки проекта (cloud_sandbox) | PROVEN | Kev (71) записал `World/HQ.luau` в swapme-roblox, task 1035; добавлены G3–G6 ворота (`publish_gate.py`) | — |
| 3 | Tool-calls у локальных/7API моделей | PROVEN (Tier 4, 2026-11-05) | `parse_text_tool_calls` в `bcc/engine.py` разбирает `<tool_call>`, `<invoke>` и ```tool_call блоки только для разрешённых `tool_specs` (76/76 pytest PASS + live Agent 80) | закрыто (`docs/audits/harness-tier4-20261105/`) |
| 4 | Фолбэк облако→локаль | PROVEN (Tier 4, 2026-11-05) | Jev (70) при мёртвом ключе OpenRouter ответил через локальный sparse-coder-30b (task 1036); добавлен `provider_meta["model_switched"]` и поле в SSE `run.assistant_message` | закрыто (`bcc/engine.py:_mark_switched`) |
| 5 | Бюджеты/стоимость: учёт cost_usd по прогону, счётчик остатков | PROVEN | `/api/funds` + `funds.html` (533444b6, 14b7263b): OpenRouter 50/50 USD live | баланс 7API/Outrun/NIM недоступен по API (NOT_AVAILABLE) |
| 6 | Ключи: ввод через пульт `/key` | DEFECT → исправлен | ключ писался только в файл, vault держал просроченный → 401; 0ee8d234 применяет сразу | ротация/проверка ключа по расписанию |
| 7 | action_contract (защита от «сделал» без доказательств) | DEFECT → исправлен | анализ расшифровок падал no_verified_action: слова из видео считались просьбой (5/8 задач); de311164, 166 тестов | — |
| 8 | Локальный ASR NVIDIA Nemotron‑3.5‑Transcribe (Vulkan, AMD) | DEFECT → обход | длинный файл → галлюцинация (sim 0.02–0.13); нарезка по 30 с → sim 0.886–0.951, ~11× реального времени | нарезку встроить в штатный ASR-инструмент Bossman |
| 9 | Бесплатная облачная модель для анализа (NIM Nemotron Ultra 550B) | PROVEN (Tier 4, 2026-11-05) | `bcc/free_pool.py` автоматически переключает между `#55` (NIM), `#71` (Groq) и `#82` (Gemini Flash) при 429/503/timeout/401 пула (`ladder_receipt.json`, `swarm_cloud_local_receipt.json`) | закрыто |
| 10 | Управление ПК (Roblox Studio): запуск, F5, мультиплеер 2 клиента, скриншоты, импорт 3D | PROVEN (Tier 4, 2026-11-05) | `pc.studio` зарегистрирован в `bcc.tools.REGISTRY`, устранён баг минимизации окна в `Front($h)`, проверены `status`, `busy_guard`, `inspect`, `shot` (`studio_proof/proof_receipt.json`) | закрыто |
| 11 | Память/уроки (`/api/memory/lessons`) | PROVEN (Tier 4, 2026-11-05) | `tools/memory_recall_eval.py`: `3/3` эталонных урока (`#238`, `#592`, `#600`) возвращаются в `top-3` на живой `bcc.db` (`memory_recall_receipt.json`) | закрыто |
| 12 | Перезапуски из одного checkout (backend / pult / companion) | PROVEN (Tier 4, 2026-11-05) | `/health/live` подтверждает `source_identity: "PASS"`, `git_clean: true`, `build_sha == checkout_sha` (`live_runtime_and_agent80_receipt.json`) | закрыто |
| 13 | action_router: данные в промпте (URL/«go to website» из видео) = браузерное действие | DEFECT → исправлен | эскалации 1028/1042/1063/1067; df86e765, 175 тестов | — |
| 14 | Память: recall уроков на новой задаче | PROVEN (Tier 4, 2026-11-05) | В `facts.py` и `lifecycle_wiring.py` снято отсечение `200/500 -> 2500` + `layer_balanced_top_k` (`#238 rank=1`, `#592 rank=1`, `#600 rank=2` в `memory_recall_receipt.json`) | закрыто |
| 15 | Бесплатные облачные модели как пул | PROVEN (Tier 4, 2026-11-05) | `FreeModelPool` с cooldown и продолжением ротации при сбое отдельного провайдера (`ladder_receipt.json`, `swarm_cloud_local_receipt.json`) | закрыто |
| 16 | Privacy gateway на публичных данных | PARTIAL | ролик «earn Robux» помечен как «финансы» (1046) | разметка «public data» для источников с публичным URL — решение владельца (security) |
| 17 | Защитные контуры G1–G6 (`window_target`, `local_context`, `config_refs`, `asset_provenance`, `content_policy`, `publish_gate`) | PROVEN (Tier 4, 2026-11-05) | `76/76` тестов + живые прогоны G1–G6 на `swapme-v09` @ `b95c22c` (`g1..g6_pass_receipt.json`) | закрыто |
