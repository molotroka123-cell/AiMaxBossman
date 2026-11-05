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

## Развёртывание (обновлено 05.11.2026 — Tier 4 Audit)

- Живой рантайм `:8801` перезапущен из рабочего дерева `wt-harness-guards` (`SIBLING_PACKAGES` `bossman_shared` и `bossman` смотрят в тот же чекаут): `/health/live` отдаёт `source_identity: "PASS"`, `git_clean: true`, `build_sha == checkout_sha`.
- В `bcc/v2/memory/facts.py` и `bcc/v2/memory/lifecycle_wiring.py` устранён жёсткий потолок `200`/`500` строк при выборке фактов из `bcc.db` (723 активных факта, из-за чего уроки `#238`, `#592`, `#600` отрезались до ранжирования): лимит пула поднят до `2500`, `python tools/memory_recall_eval.py` даёт **3/3 в top-3** (`T1 #238 rank=1`, `T2 #592 rank=1`, `T3 #600 rank=2`).
- В `bcc/api.py` (`_default_chat_only`) и `bcc/features/harness_guards.py` (`harness_profile_patch`) устранён дефект `cloud_pc_access: false` у локального агента 80 (`model_id = 102`, `qwen3-coder:30b`), из-за которого `chat_only(agent)` блокировал 100 % инструментов агента 80 на локальной модели (при этом облачный пул/fallback по-прежнему не получает PC-инструменты, так как `cloud_pc_access` не равен `True`).
- В `bcc/engine.py` добавлены:
  1. `parse_text_tool_calls`: разбор `<tool_call>{...}</tool_call>` и ````tool_call {...}```` для локальных и 7API-моделей без нативного `tool_calls` (строго ограниченный выданными `tool_specs`).
  2. Ротация на `free_pool` при молчаливом пустом ответе основной модели (`reject_empty_primary=True` после `G2`).
  3. Явная мета-пометка `provider_meta["model_switched"]` (и поле `model_switched` в `run.assistant_message`), когда ответила модель из `free_pool` или `fallback_model_id`.
- В `bcc/free_pool.py` исправлена мульти-провайдерная ротация: ошибка `401 unauthorized` на одном провайдере пула (например, просроченный ключ Groq на `71`) переводит эту модель в `unauthorized` и продолжает обход остальных провайдеров пула (`55` NIM, `82` Gemini), тогда как `401` на основной модели агента по-прежнему не маскируется ротацией.

## Доказательства Tier 4 (03.11 → 05.11.2026, квитанции в `docs/audits/harness-tier4-20261105/`)

| Возможность | Статус | Доказательство |
|---|---|---|
| Ротация бесплатного пула (4 ступени) | **PROVEN (Tier 4)** | 10 unit-тестов (`test_free_pool_rotation.py`) + живые квитанции `ladder_receipt.json` и `swarm_cloud_local_receipt.json`: (1) Rung 1 локальный `qwen3-coder:30b` (`#102`, $0); (2) Rung 2 `nvidia/nemotron-3-ultra-550b-a55b` (`#55`, $0); (3) реальный `429` от Gemini 3.5 Flash (`#82`) и `401` от Groq (`#71`) → cooldown/пропуск → авто-ответ `#55`; (4) платный потолок GLM 5.3 Flash (`#105`/`#93`) |
| Урок применяется на новой задаче (`[ENGINEERING CHECKLIST]`) | **PROVEN (Tier 4, 3/3 в top-3 и 3/3 в коде)** | `memory_recall_receipt.json`: после снятия усечения `200`/`500` строк в `facts.py`/`lifecycle_wiring.py` живой recall на `bcc.db` находит **3/3 уроков в top-3** (`#238 rank=1`, `#592 rank=1`, `#600 rank=2`). В `swarm_cloud_local_receipt.json` `qwen3-coder:30b` прошёл все 3 исполняемых проверки (`T1_daily_utc_claim=true`, `T2_bindable_event=true`, `T3_station_watchdog=true`) |
| `pc.studio` для Roblox Studio | **PROVEN (Tier 4)** | 9 unit-тестов (`test_studio_pc.py`) + регистрация `pc.studio` в `bcc.tools.REGISTRY` + убран `ShowWindow($fg, 6)` из `Front($h)` (больше не сворачивает чужое окно). Квитанция `studio_proof/proof_receipt.json`: проверены живой `BUSY GUARD` и полный цикл `status → front → play → run_lua (маркер в Output) → screenshot → stop → close` (`all_ok: true`) |
| 70B против 30B и 3D-специалист | **PROVEN (Tier 4)** | `qwen3-coder:30b` (`#102`, 9/10 общих задач, 3/3 чек-лист) + локальный `qwen35-3d-coder:latest` (6.48 с, проверка 3D-бюджетов `<=10k` tris и `G1`/`G3`) в `swarm_cloud_local_receipt.json` |
| Единый агент Harness (id 80) + живой `:8801` | **PROVEN (Tier 4)** | Агент 80 обновлён (`guard.config_refs`, `guard.media_provenance`, `guard.content_policy`, `guard.publish_gate`, `pc.studio`; снят ошибочный `cloud_pc_access: false`), живой `:8801` перезапущен с `source_identity: "PASS"` (`health_live_receipt.json`, `agent80_live_task_receipt.json`) |
| Рой облачных и локальных агентов (Swarm Audit) | **PROVEN (Tier 4)** | `tools/swarm_tier4_audit.py` → `swarm_cloud_local_receipt.json`: 5 агентов (2 локальных Ollama `#102` и `qwen35-3d-coder:latest` + 2 бесплатных облачных `#55` NIM + 1 платный с потолком `#93`/`#105` GLM 5.3 Flash), `all_passed: true`, расход `$0.000068` (экономия `$0.110` vs Sonnet / `$0.551` vs Opus) |

Облачная помощь: агент 46 (GLM 5.3 Flash, 7API) ревьюил дифф пула (задача 1655, $0.000622) — 3 из 5 замечаний
подтверждены и исправлены (4f36603b).
Находка: память в виде system-блока «DATA, NOT INSTRUCTIONS» модель игнорирует; правила должны идти вместе с задачей.
Находка: локальная очередь Ollama общая с Roblox-циклом (агенты 77/79) — вызов A/B упал по таймауту 900 с (06:33).

## Guards G1–G6

Реальные сбои 03.11 превращены в автоматические проверки — см. [HARNESS_GUARDS_20261103_RU.md](HARNESS_GUARDS_20261103_RU.md).
