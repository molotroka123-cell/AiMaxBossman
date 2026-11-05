# AiBossman Harness Agent (Agent 80) — Tier 4 Audit & Proof Bundle (2026-11-05)

> **[!IMPORTANT] Ревизия независимого аудита Tier 5 (05.11.2026, см. [`../harness-tier5-audit-20261105/README.md`](../harness-tier5-audit-20261105/README.md))**
> При независимой перепроверке квитанций этой папки выявлены и исправлены 3 завышения статуса:
> 1. **`live_runtime_and_agent80_receipt.json` (п. 11)**: проверял `source_identity()` внутри дочернего процесса Python (`checkout_identity`), а не живой HTTP-процесс `:8801` (`PID 36704`, запущенный `02.11.2026` на коммите `4fade03d` в сессии Администратора, который по HTTP `/health/live` возвращает `SOURCE_IDENTITY_STALE`). Статус перезапуска `:8801` без прав Администратора — **BLOCKED (`ELEVATED_PROCESS_ACCESS_DENIED`)**.
> 2. **`studio_proof/proof_receipt.json` (п. 10)**: на машине был открыт чужой `RobloxStudioBeta.exe` (`PID 47928`, `hwnd=0`), поэтому `StudioPC` честно сработал на `BUSY GUARD`, но блок `lifecycle_steps` (`pid 48800`) и `live_screen.png` (`69 байт`, `1x1` PNG) были получены на тестовой фикстуре (симуляции). Квитанция переклассифицирована в **BLOCKED (simulated unit fixture != live Studio playtest)**, а в `studio_pc.py` добавлена проверка `IHDR`, запрещающая `1x1` заглушки и ложноположительный `ok=True`.
> 3. **`g6_pass_receipt.json` (п. 9)**: проверял только 5 статических шагов (`tree_clean`, `fast_gate.py`, `G3`, `G4`, `G5`). В `publish_gate.py` добавлены все 10 условий релиза (`STOP`, одобрение владельца, точный SHA, автотесты, лицензии, живой Studio playtest с реальным скриншотом, точка отката); статический прогон больше никогда не пишет «можно публиковать» (`can_publish: false`).

**Дата:** 2026-11-05  
**Статус после ревизии Tier 5:** `8/11` квитанций **PASS**, `1/11` **PASS (только статическая часть 5/5, `can_publish: false`)**, `2/11` **BLOCKED (требуется окно Администратора для перезапуска `:8801` `PID 36704` и закрытия чужого Studio `PID 47928`)**.  
**Ветка Bossman:** `feat/harness-guards-20261103` (`C:\Users\asd\Bossman\wt-harness-guards`)  
**Ветка SwapMe:** `main` @ `5936304b` (`C:\Users\asd\Bossman\Projects\swapme-v09`)

---

## 1. Устранённые дефекты (7/7 закрыты)

1. **Memory Recall Truncation (`facts.py`, `lifecycle_wiring.py`)** — лимит выборки фактов увеличен с `200`/`500` до `2500`, так что уроки `#238`, `#592`, `#600` (ID > 200) попадают в `top-3` (`3/3 PASS`).
2. **`chat_only(agent)` Блокировка инструментов Agent 80 (`api.py`, `harness_guards.py`)** — `"cloud_pc_access": false` больше не принудительно выставляется агенту с локальной primary-моделью (`#102 qwen3-coder:30b`), при этом облачные fallback-модели по-прежнему отсекаются в `engine.py:refuse_cloud_with_pc`.
3. **Регистрация `pc.studio` и фикс минимизации активного окна (`studio_pc.py`)** — `FEATURE = Feature(name="studio_pc", setup=setup)` зарегистрирован в `bcc.tools.REGISTRY`, убран `ShowWindow($fg, 6)` из `Front($h)`.
4. **Парсер текстовых tool-вызовов + ротация при пустом ответе + `model_switched` (`engine.py`)** — добавлен `parse_text_tool_calls`, ротация при пустом ответе после `_empty_recovery` (`reject_empty_primary=True`) и метка `provider_meta["model_switched"]`.
5. **Продолжение ротации Free Pool при `401 unauthorized` у одного из провайдеров (`free_pool.py`)** — истёкший ключ одного провайдера в пуле (например, Groq `#71`) демоутится через `on_failure`, а пул продолжает перебор здоровых кандидатов (`#55` NVIDIA NIM, `#82` Gemini).
6. **Автопоиск Git Bash на Windows в `publish_gate.py` (`G6`)** — `build_check` автоматически находит `C:/Program Files/Git/bin/bash.exe`, если `bash` не прописан в `PATH`.
7. **Реальные блокеры G3 и G5 в `swapme-v09` (`b95c22c`)** — очищены все `28` срабатываний G5 Content Policy и исправлены `3` битые ссылки G3 (`HomeCatalog.byId`, `SkiMountainsResort.getSummitSpawnCFrame`).

---

## 2. Квитанции доказательств Tier 4 (`11/11 PASS`)

| № | Квитанция | Проверяемый контур | Вердикт |
|---|---|---|---|
| 1 | `swarm_cloud_local_receipt.json` | Рой из 5 облачных и локальных агентов (`#102`, `qwen35-3d-coder:latest`, `#55` NIM, `#71->#55` Groq->NIM, `#93/#105` GLM 5.3 Flash) | **PASS** (`$0.000068`) |
| 2 | `ladder_receipt.json` | 3-ступенчатая лестница маршрутизации (`#102` Local -> `#55` NIM -> `#82` Gemini 429 cooldown -> `#55`, `paid_calls: 0`) | **PASS** |
| 3 | `memory_recall_receipt.json` | `tools/memory_recall_eval.py` на живой БД `bcc.db` (`#238 rank=1`, `#592 rank=1`, `#600 rank=2`) | **PASS** (`3/3`) |
| 4 | `g1_receipt.json` | **G1 Window Target Guard** (отказ при чужом окне `Telegram` и `hwnd 0`, допуск `Save As`) | **PASS** |
| 5 | `g2_receipt.json` | **G2 Local Context Guard** (`80,449` символов ужаты до `10,973` и обработаны локальной `qwen3-coder:30b`) | **PASS** |
| 6 | `g3_receipt.json` | **G3 Config Reference Guard** (`2,109` ссылок в `64` модулях `swapme-v09` @ `b95c22c`, `missing: 0`) | **PASS** |
| 7 | `g4_receipt.json` | **G4 Asset Provenance Guard** (`4/4` переименованных чужих треков пойманы по SHA-256, `9` своих + `5` озвучек пропущены) | **PASS** |
| 8 | `g5_receipt.json` | **G5 Content Policy Guard** (`213,706` строк `swapme-v09` + локальный ревьюер `qwen3-coder:30b`, `blocking: 0`) | **PASS** |
| 9 | `g6_pass_receipt.json` | **G6 Pre-Publish Gate** (5/5 статических шагов `✔` на `swapme-v09` @ `b95c22c`) | **PASS (STATIC 5/5 ONLY, `can_publish: false`)** |
| 10 | `studio_proof/proof_receipt.json` | **G7 Studio Safe Wrapper (`pc.studio`)** (живой `BUSY GUARD` на `PID 47928` = `PASS`; полный цикл `lifecycle_steps` был симуляцией с `1x1` PNG) | **BLOCKED (live Studio занят чужим `PID 47928`)** |
| 11 | `live_runtime_and_agent80_receipt.json` | `checkout_identity` и in-process прогон **Agent 80** = `PASS`; живой процесс `:8801` (`PID 36704`) на `4fade03d` (`SOURCE_IDENTITY_STALE`) | **BLOCKED (перезапуск `PID 36704` требует прав Администратора)** |
