# Квитанции и аудиты (docs/audits)

Каждая папка — улики одной проверки. Вердикт прочитан из `receipt.json`, иначе из `summary.json`/`verdict.json`
или самой поздней `receipt-*.json` (поле `verdict`, затем `status`). Где поля нет, вердикт смотрите в отчёте папки.
Эти файлы — доказательства: их не переносят и не правят задним числом. Индекс собран 24.10.2026 по файлам,
отслеживаемым git; папки, которые ещё пишутся другими агентами и не закоммичены, сюда не входят.

| Дата | Папка | Что проверялось | Вердикт |
|---|---|---|---|
| 2026-10-08 | [2026-10-08-tree-audit-13-zones](2026-10-08-tree-audit-13-zones/) | Аудит дерева развития Bossman по 13 веткам — 08.10.2026 (ЧЕКПОИНТ, НЕ ЗАВЕРШЁН) | в JSON нет поля verdict — см. отчёт |
| 2026-10-11 | [2026-10-11-local-bug-audit](2026-10-11-local-bug-audit/) | Быстрый аудит багов локального Bossman, 11.10.2026 | в JSON нет поля verdict — см. отчёт |
| 2026-10-18 | [2026-10-18-freellmapi](2026-10-18-freellmapi/) | Аудит FreeLLMAPI (tashfeenahmed/freellmapi), 18.10.2026 | в JSON нет поля verdict — см. отчёт |
| 2026-10-18 | [2026-10-18-ollama-wakeups](2026-10-18-ollama-wakeups/) | Фантомные пробуждения Ollama каждые ~11 минут (18.10.2026) | в JSON нет поля verdict — см. отчёт |
| 2026-10-18 | [2026-10-18-production-readiness](2026-10-18-production-readiness/) | Инвентаризация готовности 39 требований v2.2 (обновлено 19.10.2026, финальная сборка `main`) | в JSON нет поля verdict — см. отчёт |
| 2026-10-18 | [2026-10-18-run-audit](2026-10-18-run-audit/) | Аудит прогона 17–18.10.2026 | в JSON нет поля verdict — см. отчёт |
| 2026-10-18 | [2026-10-18-swarm-self-improve](2026-10-18-swarm-self-improve/) | Карта ботов Bossman (версия f189904fce9e) | в JSON нет поля verdict — см. отчёт |
| 2026-10-19 | [2026-10-19-final](2026-10-19-final/) | Финальный аудиторский отчёт Bossman — Закрытие Red Team 20.10.2026, разделение контуров доказательств и обн… | `NOT_PROVEN` |
| 2026-10-19 | [2026-10-19-morning-report](2026-10-19-morning-report/) | Утренний отчёт Argon 4 — ночной прогон втроём: Argon + Claude + Codex (18→19.10.2026) | в JSON нет поля verdict — см. отчёт |
| 2026-10-19 | [2026-10-19-redteam](2026-10-19-redteam/) | Red Team Audit — 2026-10-19 (SHA `4fdc825c` → `1f1774b6`) | в JSON нет поля verdict — см. отчёт |
| 2026-10-19 | [2026-10-19-tier1-audit](2026-10-19-tier1-audit/) | Аудит доведения Bossman до уровня Tier-1 (`2026-10-19`) | `PASS` |
| 2026-10-20 | [2026-10-20-independent-redteam](2026-10-20-independent-redteam/) | Корректировка задания Argon после независимой проверки | в JSON нет поля verdict — см. отчёт |
| 2026-10-20 | [2026-10-20-opencode-self-coding](2026-10-20-opencode-self-coding/) | OpenCode: самостоятельное кодирование | `PASS` |
| 2026-10-20 | [2026-10-20-v26-release](2026-10-20-v26-release/) | Bossman v2.6.0 — Полная интеграция (Wave 1 + Wave 2 + Wave 3), доказательство заработка и полного управлени… | в JSON нет поля verdict — см. отчёт |
| 2026-10-21 | [2026-10-21-stage6-benchmark](2026-10-21-stage6-benchmark/) | Бенчмарк этапа 6 | `PASS` |
| 2026-10-21 | [2026-10-21-stage7-acceptance](2026-10-21-stage7-acceptance/) | Приёмка этапа 7 | `PASS` |
| 2026-10-22 | [2026-10-22-pc-defense](2026-10-22-pc-defense/) | Независимый аудит киберзащиты ПК владельца и доработка 2 линий обороны Bossman (22.10.2026) | в JSON нет поля verdict — см. отчёт |
| 2026-10-22 | [2026-10-22-tools-skills-proof](2026-10-22-tools-skills-proof/) | Матрица доказательств: инструменты и навыки Bossman (WIP, 22.10.2026) | частично — см. PROOF_MATRIX_RU.md |
| 2026-10-23 | [2026-10-23-chat-attachments](2026-10-23-chat-attachments/) | Chat attachments reach the model — live proof (owner 23.10, class 1) | `PASS` |
| 2026-10-23 | [2026-10-23-fable-candidate](2026-10-23-fable-candidate/) | Идентичность исходников кандидата Fable | `SOURCE_IDENTITY_STALE` |
| 2026-10-23 | [2026-10-23-lora-dataset](2026-10-23-lora-dataset/) | LoRA-датасет | `PASS` |
| 2026-10-23 | [2026-10-23-stack](2026-10-23-stack/) | Stack on the owner PC — what is really installed (23.10.2026) | `PASS` |
| 2026-10-24 | [2026-10-24-7api-glm](2026-10-24-7api-glm/) | Провайдер 7API: GLM 5.3 Flash Uncensored, цена и лимит $5 | `PASS` |
| 2026-10-24 | [2026-10-24-cloud-no-pc](2026-10-24-cloud-no-pc/) | owner 24.10: cloud models get no PC access, chat only | `PASS` |
| 2026-10-24 | [2026-10-24-direct-gen-freeze](2026-10-24-direct-gen-freeze/) | after a backend restart under heavy disk load (254 GB hash verification) every request incl. /api/health ti… | `PASS` |
| 2026-10-24 | [2026-10-24-earn-paid-docs](2026-10-24-earn-paid-docs/) | Заработок A: платный помощник по документам, счёт на банковский перевод | `PASS` (способность; выручка NOT_RUN) |
| 2026-10-24 | [2026-10-24-earn-site-audit](2026-10-24-earn-site-audit/) | Заработок D: аудит сайта (стоматология) с уликами на каждую находку, эмулятор, без рассылки | `PASS` (способность; выручка NOT_RUN) |
| 2026-10-24 | [2026-10-24-earn-studio-orders](2026-10-24-earn-studio-orders/) | Заработок B: заказы локальной фото/видеостудии — цена, себестоимость, маржа | `PASS` (способность; выручка NOT_RUN) |
| 2026-10-24 | [2026-10-24-earn-tender-dossier](2026-10-24-earn-tender-dossier/) | Заработок C: досье по открытым тендерам, офлайн, синтетический профиль | `PASS` (способность; выручка NOT_RUN) |
| 2026-10-24 | [2026-10-24-functions](2026-10-24-functions/) | Живые доказательства функций: чат, генерация, память, сжатие сессии | `PASS` |
| 2026-10-24 | [2026-10-24-glm-security](2026-10-24-glm-security/) | Ревью прав доступа моделью GLM 5.3 Flash: 27 находок, 3 подтверждены и исправлены | в JSON нет поля verdict — см. отчёт |
| 2026-10-24 | [2026-10-24-local-model-tournament](2026-10-24-local-model-tournament/) | Турнир локальных моделей (инвентарь и профили) | в JSON нет поля verdict — см. отчёт |
| 2026-10-24 | [2026-10-24-memory](2026-10-24-memory/) | Перенос моделей и кэшей на D: (инвентаризация и миграции) | `PASS` |
| 2026-10-24 | [2026-10-24-niche-research](2026-10-24-niche-research/) | niche_research 28-day run infrastructure | `PASS` |
| 2026-10-24 | [2026-10-24-ssd](2026-10-24-ssd/) | do media models from D: (new SSD) beat C: on the same sd-cli model and params? | `PROVEN FASTER from D: - 1.28x (flux, order biased for D) and 2.47x …` |
| 2026-10-24 | [2026-10-24-three-model-audit](2026-10-24-three-model-audit/) | Если в задании или брифе клиента упомянут чужой бренд (Starbucks, Nike и т.п.), что делать при сдаче заказа? | `DECIDED` |
| 2026-10-24 | [2026-10-24-tiktok-trend](2026-10-24-tiktok-trend/) | Тренды TikTok (данные) | в JSON нет поля verdict — см. отчёт |
| 2026-10-24 | [2026-10-24-voice](2026-10-24-voice/) | owner 24.10: can we bring a Dessa-like (Block) natural voice to Bossman calls? | `MEASURED: median reply latency 8.7-11.2 s -> 4.8 s with a call-only…` |
| 2026-10-25 | [2026-10-25-pult-agent-self-edit](2026-10-25-pult-agent-self-edit/) | Живое доказательство самоправки Bossman через режим AGENT в пульте (:8840 → :8801 → агент 48) + 6 дефектов | `PASS_WITH_DEFECTS` |
| 2026-10-26 | [2026-10-26-argon-pc-agent-master](2026-10-26-argon-pc-agent-master/) | Мастер-промпт Аргона (P0–P5): закрытие 6 дефектов пульта/агента, уровни автономии L0–L3, инструменты ОС 1–7, `/browser`, контур самоизменения с откатом и 8 правок ядра по турниру | `PASS` |
| 2026-10-26 | [2026-10-26-integrator-stop-unification](2026-10-26-integrator-stop-unification/) | Интеграторский цикл (Приоритет A): унификация глобального STOP и Resume между Command Center, Mobile Pult, Telegram и PC-Agent | `PASS` |
| 2026-10-28 | [2026-10-28-live-proofs](2026-10-28-live-proofs/) | Живые доказательства 28.10 (Topbar health split, ACE-Step on-demand, Push-to-Talk Chrome, `browser.active_tab_info`, Freeze Audit) | `PASS` |
| 2026-10-28 | [2026-10-28-swarm2-synthesis](2026-10-28-swarm2-synthesis/) | Сведение Роя №2 и надстройка Bossman 2.0 (`/api/v20/*`, Obsidian Memory 2.0) | `PASS` |
| 2026-10-29 | [2026-10-29-ai-gateway](2026-10-29-ai-gateway/) | Локальный AI Privacy Gateway (`PUBLIC / INTERNAL / CONFIDENTIAL`), локальная очистка PII/секретов и защита терминала от EIP-7702 | `PASS` |
| 2026-10-29 | [2026-10-29-studio-t1-t2](2026-10-29-studio-t1-t2/) | T1/T2 Studio: сходимость контуров фото/видеостудии и STOP | `PASS` |
| 2026-10-29 | [2026-10-29-studio-t2-live-acceptance](2026-10-29-studio-t2-live-acceptance/) | T2: Живая приёмка локальной студии | `PASS` |
| 2026-10-29 | [2026-10-29-t3-governor](2026-10-29-t3-governor/) | T3 Governor: замер и управление ресурсами | `PASS` |
| 2026-10-29 | [2026-10-29-full-emulator-buttons-proof](2026-10-29-full-emulator-buttons-proof/) | T4: Полная проверка всех кнопок и 8 вкладок мобильного пульта (`:8840`) и связки с `:8801` в эмуляторе | `PASS` |
| 2026-10-29 | [2026-10-29-t5-swarm](2026-10-29-t5-swarm/) | T5: Песочница роя и самоулучшения (автономный локальный патч D-036 — `NOT_PROVEN`) | `PASS` |
| 2026-10-29 | [2026-10-29-north-star-long-horizon-claude](2026-10-29-north-star-long-horizon-claude/) | Long-Horizon прогон 15 веток дерева Bossman в бюджете ≤$1.00 с записью уроков в память | `PASS` |
| 2026-10-30 | [2026-10-30-long-horizon-run-g](2026-10-30-long-horizon-run-g/) | Long-Horizon прогон `lh-20261030-g` | `PASS` |
| 2026-10-30 | [2026-10-30-weekly-research-benchmarks](2026-10-30-weekly-research-benchmarks/) | Защитные гейты по 4 исследованиям недели (AgentProofBench, CUA-Recovery, TimlMemBench, Data Minimization): юнит-гейты `PASS_UNIT` + живой агентный прогон `PASS_LIVE` (`task_id=816`) | `PASS_LIVE` |
| 2026-10-30 | [2026-10-30-full-project-audit-and-ux](2026-10-30-full-project-audit-and-ux/) | Полный аудит проекта на 30.10.2026, интеграция Freefield Studio Apps (`app.json`), живые UX-скриншоты и отправка в пульт | `PASS` |
| 2026-10-31 | [2026-10-31-security-autofull-long-horizon](2026-10-31-security-autofull-long-horizon/) | Закрытие 4 дыр безопасности (`1a–1d`), режим «Авто полный» с карточкой владельца, автостарт с `main`, сверка 4-ходового диалога Outrun, Long-Horizon `lh-20261031-autofull-v2` с памятью и отчётом 3/3 | `PASS` |
| 2026-10-31 | [2026-10-31-main-bugfix-pass](2026-10-31-main-bugfix-pass/) | Закрытие багов `main` по ТЗ `B1–B4` без новых функций (`D-036` `ungrounded_screen_claim` 32/32 holdout, `owner_one_bossman.py find/status` для `uvicorn`/`:8801`/`:8840`, очистка устаревших моделей и `waiting_approval` в `bcc.db`) | `PASS` |
| 2026-10-31 | [2026-10-31-bossblock-dnipro-gamedev](2026-10-31-bossblock-dnipro-gamedev/) | Интеграция BossBlock (`apps/bossblock`, Aleph Game-Dev 5-Layer World Capture) в `main` и мобильный пульт `:8840` + реальный захват центра г. Днепр, Украина (`mesh.glb`, `satellite.tif`, `osm_buildings_roads.geojson`, `terrain.tif`, `52` фото Street View) | `PASS` |
| — | [astra-7b1377a](astra-7b1377a/) | Astra forensic audit | в JSON нет поля verdict — см. отчёт |

## Отдельные файлы

- [2026-08-29__ai-gateway__implementation-audit__v1.md](2026-08-29__ai-gateway__implementation-audit__v1.md)
- [2026-08-29__computer-use__implementation-audit__v1.md](2026-08-29__computer-use__implementation-audit__v1.md)
- [2026-08-29__gateway-context-browser__red-team-findings__v1.md](2026-08-29__gateway-context-browser__red-team-findings__v1.md)
- [2026-09-03__bcc-desktop-launch__audit__v1.md](2026-09-03__bcc-desktop-launch__audit__v1.md)
- [2026-09-03__bcc-desktop-user-perspective-full-audit__v1.md](2026-09-03__bcc-desktop-user-perspective-full-audit__v1.md)
- [2026-09-03__bcc-desktop-user-perspective-full-audit__v1__RESOLUTION.md](2026-09-03__bcc-desktop-user-perspective-full-audit__v1__RESOLUTION.md)
- [2026-09-03__v2-stability-pass__interim.md](2026-09-03__v2-stability-pass__interim.md)
- [2026-09-07__astra-runtime-responsiveness__audit__v1.md](2026-09-07__astra-runtime-responsiveness__audit__v1.md)
- [2026-09-07__perplexity-total-performance__audit__v1.md](2026-09-07__perplexity-total-performance__audit__v1.md)
- [2026-10-05-one-jeff.md](2026-10-05-one-jeff.md)
- [2026-10-06-blue-leaf-audit.md](2026-10-06-blue-leaf-audit.md)
- [2026-10-06-leaf-usefulness-queue.md](2026-10-06-leaf-usefulness-queue.md)
- [2026-10-06-night-build.md](2026-10-06-night-build.md)
- [2026-10-06-owner-pc-run.md](2026-10-06-owner-pc-run.md)
- [2026-10-06-windows-artifact-90dd13fb.md](2026-10-06-windows-artifact-90dd13fb.md)
- [2026-10-06-zone-tests-memory-agents.md](2026-10-06-zone-tests-memory-agents.md)
- [2026-10-07__bossman-tree__claude-auditor-view__v1.md](2026-10-07__bossman-tree__claude-auditor-view__v1.md)
- [AUDIT_PROMPT_v2.md](AUDIT_PROMPT_v2.md)
- [EDITORS_USER_BASELINE_20260906.md](EDITORS_USER_BASELINE_20260906.md)
- [EVENING_TAKEOVER_20260906.md](EVENING_TAKEOVER_20260906.md)
- [GLM_CHECKPOINT_30fce6c_RESPONSE_20260906.md](GLM_CHECKPOINT_30fce6c_RESPONSE_20260906.md)
- [MASTER_PARSER_2_0_AUDIT.md](MASTER_PARSER_2_0_AUDIT.md)
- [MASTER_PARSER_2_0_SPEED_RUN_20260929.json](MASTER_PARSER_2_0_SPEED_RUN_20260929.json)
- [PERF_20260929.md](PERF_20260929.md)
- [PERF_20260930.md](PERF_20260930.md)
- [PR_MERGE_TRIAGE_20260907.md](PR_MERGE_TRIAGE_20260907.md)
- [SANDBOX_USER_RUN_20260907.md](SANDBOX_USER_RUN_20260907.md)
- [TAKEOVER_ADMISSION_CI_20260906.md](TAKEOVER_ADMISSION_CI_20260906.md)
- [TOTAL_PERFORMANCE_AUDIT_PERPLEXITY_2026_09_07.md](TOTAL_PERFORMANCE_AUDIT_PERPLEXITY_2026_09_07.md)
- [blue-leaf-audit-20261006.json](blue-leaf-audit-20261006.json)
- [earning-emulator-demo-20261006.jsonl](earning-emulator-demo-20261006.jsonl)
- [leaf-usefulness-queue-20261006.json](leaf-usefulness-queue-20261006.json)
