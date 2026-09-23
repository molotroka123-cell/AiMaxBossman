# Сведение 1.0 + 1.1 + 1.2 в одну ветку — семантическое сравнение

Дата: 2026-09-23. Решение владельца (пересмотрено): всё, что сделал Aster в
`codex/bossman-v1.1-evolution`, и работа Claude сводятся **в одну** линию и затем в
`release/bossman-owner` к завтрашнему owner-run. Новых final-веток нет, force-push нет,
чужие коммиты сохраняются со своими SHA.

Линия: `claude/bossman-cloud-closure-owner-a6s1ki` (PR #74 → `integrate/owner-final-20260922`,
PR #73 → `release/bossman-owner`).

## Состояние remote на момент сведения (`git fetch --all --prune`)

| Ветка | SHA | Впереди нашей линии |
|---|---|---|
| integrate/owner-final-20260922 | d6e25fb4 | 0 (наша линия содержит её целиком) |
| release/bossman-owner | 7590cc79 | 5 → **влиты** (f5258d60) |
| codex/bossman-v1.1-evolution | 3bc81aee | 3 → **влиты** (b8dacfda): df30cf26 + 9a325d56 + 3bc81aee |
| audit/aster6-full-surface-20260922 | 528f0de3 | 7 — содержание уже в линии, кроме Telegram Codex bridge |
| audit/codex-full-surface-20260922 | 833a6a2a | 4 — только документы, уже в линии |

Ветка v1.1 — это ровно 2 feature-коммита поверх df30cf26; всё ниже уже было в линии,
поэтому merge принёс только их (со своими SHA), без «старой ветки поверх новой».

## Классификация изменений

| Изменение | Откуда | Класс | Решение |
|---|---|---|---|
| `bossman_v3/self_improvement/runner.py` — проваленный сценарий → точечный JSON-патч → отдельный worktree → двойной прогон набора → `evo/candidate-*` | v1.1 | UNIQUE_AND_USEFUL | влито; становится фазами SELECT/ATTEMPT/VERIFY единого цикла |
| `campaign.py` — турнир 1–5 кандидатов, повторные baseline, резерв бюджета proposer+reviewer, checkpoint | v1.1 | UNIQUE_AND_USEFUL | влито; основа бюджета и checkpoint цикла |
| `protocol.py` — ранжирование, ревью, привязанное к nonce / base SHA / hash патча, receipts | v1.1 | UNIQUE_AND_USEFUL | влито |
| `validation.py` — целостность evidence, однократный holdout | v1.1 | UNIQUE_AND_USEFUL | влито |
| `lab.py` — NaN/Infinity/отрицательные метрики не принимаются | v1.1 | UNIQUE_AND_USEFUL | влито (исправление гейта) |
| `tools/bossman_evolve.py` (assess/run/report/verify/validate/export) | v1.1 | UNIQUE_AND_USEFUL | влито; **единственный** CLI цикла — `loop/status/pause/resume/stop/gate/soak` добавляются в него же, не в новый CLI |
| `config/evolution/owner-v1.1.json` — неизменяемые сценарии train/regression | v1.1 | UNIQUE_AND_USEFUL | влито |
| `config/evolution/Dockerfile` — изолированный исполнитель JSON-патчей | v1.1 | UNSAFE_OR_UNPROVEN (не запускался: нет Docker в облаке; у владельца может не быть) | влито как необязательный исполнитель; правило Aster «модельные JSON-правки только в Docker» сохраняется |
| `config/evolution/local-champions.json` — 3 профиля с HF revision + SHA256 | v1.1 | CONFLICTING с `tools/model_profiles.json` (второй манифест моделей) | сводится в ОДИН источник истины `model_profiles.json`; закрепления переносятся, расхождение ловит тест |
| `config/evolution/sources.lock.json` — Cloudflare audit / Alibaba code review / Hermes | v1.1 | UNSAFE_OR_UNPROVEN как ревьюеры (адаптеры не установлены, ревью PENDING) | влито как ссылки; ни одно ревью не считается пройденным |
| `docs/evolution/*`, `BOSSMAN_V1_1_CLAUDE_IMPLEMENTATION.md`, `docs/WEEKLY_UPSTREAM_P0_2026-09-23.md`, North Star (`docs/evo/BOSSMAN_1_1_NORTH_STAR.md`, CLAUDE/AGENTS/README) | v1.1 / release | DOC_ONLY | влито; конфликты README и CLAUDE_NEXT_ACTION решены сохранением обеих сторон |
| `tests/test_evolution_{runner,metrics,protocol}.py` | v1.1 | TEST_ONLY | влито; 120 passed на сведённом дереве |
| `tools/self_improve_lab.py` + `features/lab_agents.py` + `features/coding_recipes.py` (агенты RAW…USER_UX через продуктовый coding path, рецепты, transfer) | облачная линия | UNIQUE_AND_USEFUL, пересекается с campaign по смыслу «самоулучшение» | **не второй движок**: лаборатория = сравнение оркестраций и transfer на ОДНОМ coding path; цикл 1.1 = планировщик, который вызывает этот же coding path как бэкенд ATTEMPT (`bossman_coding`) рядом с JSON-предлагателями Aster |
| `features/coding_tasks.py` + local sidecar + proc_tree (handshake, tools, cancel, UNKNOWN_INTERRUPTED) | облачная линия | UNIQUE_AND_USEFUL | единственный coding engine |
| Telegram Codex bridge (`tools/telegram_codex_bridge.py`, 528f0de3) | audit/aster6 | CONFLICTING (второй Telegram poller; владелец отключил) | **не влит**; команды `/evolution_*` — в существующем companion |
| Находки Aster6: ложный PASS по длительности видео, потеря hard_timeout | audit/aster6 | ALREADY_PRESENT (исправлено eee89e6f) | — |
| Документы протокола совместной работы и чекпоинты аудиторов | audit/* | ALREADY_PRESENT | — |
| Jev Ultrafast / JevDecisionProvider | release docs → облачная линия | UNIQUE_AND_USEFUL | влито shadow-only, выключено по умолчанию (647d9a47…fbd5444d) |

## Что строится поверх (эта ночь)

* **Единый цикл 1.1** (`bossman_v3/self_improvement/loop.py` + `verifier.py`, CLI `bossman_evolve.py`,
  `/api/evolution/*`, `/evolution_*` в Telegram): OBSERVE → SELECT → ATTEMPT → VERIFY → ACCEPT/REJECT →
  LEARN → CHECKPOINT → NEXT; STOP/PAUSE/RESUME, бюджеты, watchdog, lease, восстановление без слепого
  повтора; обязательные гейты «плохой патч» и «три цикла» (в облаке — MOCK_MODEL, только plumbing).
* **Лаборатория**: промпты RAW…USER_UX дословно по заданию владельца, CLAUDE_AUDITOR / RESULT_VERIFIER /
  UX_OBSERVER, вмешательства учителя LEVEL 0–5, метрики UX, честное сравнение «одна модель — разный
  Bossman», турнир моделей по «проверенной полезной работе в час».
* **1.2 терминал**: `bossman` в терминале поверх существующего backend (интерактивно и headless для Claude Code).

## Чего облако не доказывает

AUTONOMOUS_SELF_IMPROVEMENT_READY, TRANSFER_MEASURED_GAIN, 24H_SOAK_PASS и OWNER_HARDWARE_CERTIFIED
возможны только по результатам REAL_MODEL на железе владельца. Облачные прогоны с детерминированной
моделью доказывают связность и защитные гейты, не интеллект.
