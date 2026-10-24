# ARGON — ПОСЛЕДНИЙ ПРОГОН: СОБРАТЬ ОДНОГО BOSSMAN И ДОКАЗАТЬ СТУПЕНЬ NORTH STAR (22–23.10.2026)

Автор: Claude (Anthropic), 22.10.2026, по просьбе владельца: «мастер-промт для Argon сделать последний прогон».
Репозиторий `molotroka123-cell/AiMaxBossman`. Этот документ **дополняет**, а не заменяет твой собственный
`docs/owner/ARGON_NORTH_STAR_MASTER_PROMPT_20261023_RU.md`: там — правила по каждому листу; здесь — порядок последнего
прогона, точные SHA, что уже доказано, что только заявлено, и чем прогон обязан закончиться.

Все команды — с актуального `origin/main`; SHA ниже верны на 22.10 утро, перед стартом перепроверь `git fetch`.

## 0. Читать перед первой командой (в этом порядке)

1. `AGENTS.md` — ограничения владельца; North Star ladder; «спецификация ≠ доказательство».
2. `docs/owner/ARGON_NORTH_STAR_MASTER_PROMPT_20261023_RU.md` + `docs/owner/NORTH_STAR_LEAF_INDEX_20261022.json` — твой манифест листьев.
3. `docs/owner/BOSSMAN_2X_PERFORMANCE_NORTH_STAR_20261023_RU.md` — 2x: **сначала трассировка, потом оптимизация**; 2x — цель, не результат.
4. `docs/owner/runs/north-star-20261023/FINAL_ACCEPTANCE_RU.md` — твой receipt создания навыка; в этом прогоне его надо **повторить на холодном рантайме** (§3, G5).
5. `docs/owner/CLAIMS_AUDIT_20261022_RU.md` (ветка `chore/cleanup-north-star-docs-20261022`) — 27 утверждений: 10 VERIFIED_IN_TREE, 4 UNVERIFIABLE, 6 CONTRADICTED, 5 OVERSTATED. Не переносить CONTRADICTED/OVERSTATED в новые отчёты.
6. `docs/audits/2026-10-22-tools-skills-proof/PROOF_MATRIX_RU.md` (ветка `test/tools-skills-proof-20261021`) — независимая матрица: инструменты 112 → PROVEN_EXECUTED 19, PROVEN_GATED 5, FAILED 1, NOT_RUN 87; навыки 67 → PROVEN_EXECUTED 12, STRUCTURAL_ONLY 50; **меняет ли навык результат — OUTCOME_UNPROVEN**.
7. `docs/north-star/NORTH_STAR_PREP_20261022_RU.md` (задание владельца дословно) и `docs/north-star/CYBER_DEFENSE_SPEC_20261022_RU.md`.

Правило владельца 21.10, действует для тебя и всех субагентов: **«Запрещено читать локальные переписки, данные и фото».**
Отладка чатов — только id/статусы/счётчики; тесты — только синтетика. Генерации владельца имеют приоритет над любой твоей нагрузкой.

## 1. Состояние на старте (факты, не оценки)

| Ветка | SHA | Что внутри | Проверено | merge-tree с main |
|---|---|---|---|---|
| `origin/main` | `e8ebcd9b` | твои checkpoint 1–7, 927-листовой registry (`tests` 485 / `code` 358 / `none` 84; `ci`, `owner_pc` = NOT_RUN) | — | — |
| `integrate/claude-north-star-20261022` | `33488c07` | main + пульт HTML (`5f8337b2`, 20 тестов) + `tools/{gen_watch,oss_check,git_velocity,llm_task_bench,pc_defense_audit}.py` (только синтаксис, **тестов нет**) + doc-intake (`8e279098`, 17/17 + 6/6 + 469 companion) + README: шаблон «Argon Engineering Report», абзац Claude + MiniMax evidence | секрет-скан PASS, `git diff --check` 0 | **чисто** |
| `feat/phone-core-ctx-cloud-20261021` | `d13dac8a` | `agent_extras.py` (palette, context-шкала, `/compact`, `/memory`), `tools_cloud.py` (`cloud.ask`, всегда ASK) | **0 тестов, импорт не проверен — может уронить старт backend** | чисто |
| `feat/phone-slash-ui-20261021` | `7efb69ad` | меню по `/` в телефоне, шкала контекста, карточка `cloud.ask` | 34 + 11 + 74 + 60 тестов на синтетике; на iPhone не проверено | чисто |
| `fix/chat-history-after-stop-20261021` | `ad4cf2e9` | STOP не трогает файл сессии (доказано тестами); исправлены 3 реальных потери ходов (task_id=0, «нечитаемо = удалено», `turns` ≠ list), бэкап `.json.bak` | 12/12 новых + 88 соседних | **конфликт** в `agent_opencode.py` |
| `test/tools-skills-proof-20261021` | `c4b600da` | `tools/prove_tools_skills.py`, матрица + evidence | воспроизводимо скриптом | чисто |
| `chore/cleanup-north-star-docs-20261022` | `2ddfcc7f` | 17 docs в архив (`git mv`, 0 удалений), аудит утверждений, runbook на завтра, черновик закрытия обучений | тесты не запускались | **конфликт** в `README.md` (две строки сверху) |
| `audit/north-star-tree-20261022` | `22774a9b` | твоя ветка, влита (PR #124) | — | — |

Замер моделей (`tools/llm_task_bench.py`, 4 синтетические задачи с настоящими unit-тестами): `minimax/minimax-m3` (OpenRouter, платный,
$0.3/$1.2 за 1M) — **4/4**, 16–73 с на задачу, `max_tokens 24000`, reasoning 6000. `minimax-m3.1-flash:free` — 0/4, все
ответы обрезаны по `max_tokens 8000` → замер невалиден, не вывод о модели. 4 задачи — не «рекорд»; для роутера нужен
прогон ≥20 задач против текущих Tier 3–4 (`docs/north-star/evidence/`).

Живой рантайм `:8801`: SHA не сверялся с 21.10; Jeff-поллер на `hold-jeff`; `gh` на ПК не авторизован (draft PR — только владелец или после `gh auth login`).

## 2. Порядок интеграции (один коммит на шаг, после каждого — checkpoint в `docs/north-star/CHECKPOINTS.md`)

Общие гейты перед каждым push: `python tools/ci_secret_scan.py` → PASS; `git diff --check` → пусто;
`git merge-tree --write-tree origin/main HEAD` → код 0; целевые тесты ветки; **никаких** `git push --force`, `git add -A`.

```bash
git fetch origin
git checkout -b integrate/north-star-final-20261023 origin/main
# шаг 1 — Claude integration (чисто)
git merge --no-ff origin/integrate/claude-north-star-20261022
python -m pytest tests/test_pult_note.py tests/doc_intake -q
# шаг 2 — proof harness (чисто); запусти его, числа в CHECKPOINTS.md
git merge --no-ff origin/test/tools-skills-proof-20261021
python tools/prove_tools_skills.py
# шаг 3 — телефон: UI (чисто) + core (чисто, НО не тестирован)
git merge --no-ff origin/feat/phone-slash-ui-20261021
git merge --no-ff origin/feat/phone-core-ctx-cloud-20261021
python -c "import bcc.features.agent_extras, bcc.features.tools_cloud"   # из command-center; если падает — чинить ДО всего остального
python -m pytest apps/bossman-mobile/tests/test_mobile_slash_context.py command-center/tests/test_mobile_opencode_agent.py -q
# шаг 4 — история после STOP: НЕ мержить целиком, перенести смысл (конфликт в agent_opencode.py)
git merge --no-ff origin/fix/chat-history-after-stop-20261021   # конфликт решать «новее + строже побеждает», решение описать в коммите
python -m pytest command-center/tests/test_chat_history_after_stop.py command-center/tests/test_chat_threads.py -q
# шаг 5 — чистка и документация (конфликт только README: 2 строки «Старт 23.10» сверху)
git merge --no-ff origin/chore/cleanup-north-star-docs-20261022
```

Для `feat/phone-core-ctx-cloud-20261021` минимум до слияния в main: написать `command-center/tests/test_agent_extras.py` и
`test_tools_cloud_ask.py` по контракту из `docs/north-star/NORTH_STAR_PREP_20261022_RU.md` §«Что в работе»; `cloud.ask`
в режиме AUTO **не должен** автоодобряться (`_bus_bridge` — одна строка, добавить в `_NEVER_AUTO_APPROVE_KINDS`).

## 3. Гейты последнего прогона (каждый — PASS / FAIL / INSUFFICIENT_EVIDENCE, с путём к evidence)

| Гейт | Что доказывает | Как |
|---|---|---|
| G0 | один рантайм: live `:8801` SHA == candidate SHA, `source_identity: PASS` | `GET /api/system`, `/health/live`; перезапуск — **только когда очередь генераций пуста** (`/api/direct-gen/jobs` без queued/loading/generating) |
| G1 | тесты зелёные на candidate SHA: `command-center/tests`, `apps/bossman-mobile/tests`, `apps/war-terminal`, `tests/` | полный прогон один раз, ночью, с `LOCALAPPDATA`/`BCC_DATA_DIR` во временный каталог; числа, не «зелёно» |
| G2 | инструменты и навыки работают, а не зарегистрированы | `python tools/prove_tools_skills.py`; цель прогона — NOT_RUN 87 → ≤ 40 за счёт **реальных** запусков в песочнице; FAILED 1 — разобрать |
| G3 | навык **меняет результат** | A/B на 10 неизвестных задачах: с навыком / без, автоматический проверяющий, сырые счётчики; `OUTCOME_UNPROVEN` остаётся, если разницы нет — это тоже результат |
| G4 | Tier 5 e2e + owner acceptance | `python tools/verify_tier5_argon_e2e.py`, `python tools/prove_stage7_owner_acceptance.py`; receipt в `docs/audits/2026-10-23-final-run/` |
| G5 | самосозданный навык на холодном рантайме | повторить `north-star-tree-hygiene` сценарий: restart → реестр видит навык → применить к **новому** diff → сравнить с baseline; плюс одна из задач владельца A/B/C (анализ test failures / диагностика процессов Windows / отчёт из GitHub Actions) — spec, код, схема, unit + негативные тесты, review, SHA |
| G6 | безопасность | тесты `PROMPT_INJECTION_ESCALATION_BLOCKED`, `AUTO`-граница, `cloud.ask` без автоодобрения, RunPod 403, бюджет 402; `python tools/pc_defense_audit.py --json` — первый живой отчёт киберзащиты (ожидаемо найдёт замечания — это нормально) |
| G7 | 2x — только трассировка | P0 из 2x-плана: спаны/тайминги на 10 задачах матрицы, baseline зафиксирован в `docs/perf/`; **никаких заявлений об ускорении** без парных замеров |
| G8 | North Star ladder | явно назвать ступень с доказательством; по overlay сейчас `NORTH_STAR_FULL_RELEASE = NOT_VERIFIED`; если выше ступени 1 не поднялись — так и написать |

## 4. Запрещено в этом прогоне

- Слияние в `main` без зелёных G1 и без явного «да» владельца в пульте; авто-merge из `/improve`.
- Расширять свои права, трогать `permissions.cloud_allowed`, отключать гейты, ослаблять тесты (к любому ослаблению — пара тестов: хороший проходит, плохой отвергается).
- RunPod, платные модели сверх дневных капов (`long_run.json`), любые личные медиа владельца в облако.
- Переключать/трогать `wt-checkpoint-20261015` во время генераций; грузить модели параллельно с генерацией.
- Писать проценты готовности, «READY», «доказано», если нет файла evidence на точном SHA.

## 5. Чем прогон обязан закончиться

1. `docs/north-star/CHECKPOINTS.md`, `CAPABILITY_TREE.md`, `REMAINING_LEAVES.md`, `INTEGRATION_EVIDENCE.md` — по заданию владельца (§2 `NORTH_STAR_PREP`).
2. `docs/north-star/NORTH_STAR_HANDOFF_20261023.md`: SHA, 15 направлений, счётчики VERIFIED / PARTIAL / BLOCKED / NOT_RUN, что в едином рантайме, OSS проверено (`tools/oss_check.py` — живые реестры, не память), статус skill-тестов A/B/C, бюджет, точная команда продолжения.
3. README → раздел «Argon Engineering Report» заполнен **только** значениями, у которых есть evidence; пустое поле = `NOT_RUN`.
4. Отчёт владельцу в пульт через `python tools/pult_note.py --html --file report.html`: жирные заголовки, `<code>` для SHA/веток, `<pre>` для команд; в конце — экономия vs облако и что НЕ доказано.
5. Draft PR `integrate/north-star-final-20261023 → main` (если `gh` авторизован; иначе — ссылка `pull/new/...` владельцу), без merge.

## 6. Добавлено владельцем 22.10 (после первой версии промта)

- **Пайплайн «Модель → видео» в пульте** — `docs/owner/PULT_MODEL_VIDEO_PIPELINE_20261022_RU.md`: лист зоны `media`
  (`media.model_video_pipeline`). Порядок: снять `unsupported` у `wan2.2-animate-14b` (вход driving-video), затем первый
  кадр через `qwen-image-edit-2509` + FaceLock, затем команда `/model_video` в пульте с одобрением между этапами.
  Облако (Luma/Kling) выключено по умолчанию; личные медиа в облако не уходят; согласие персоны — до первого запуска.
- **Замер моделей на 20 задачах** (`tools/llm_task_bench.py --suite all`, evidence в `docs/north-star/evidence/bench20/`):
  результаты и выводы для роутера — в README «Claude — заметки» и в JSON; не переносить в роутер без парных прогонов.
- **Киберзащита ПК** — первый живой аудит `docs/north-star/evidence/pc_defense_audit_20261022.json` (YELLOW: 27 задач
  планировщика с PowerShell из `C:\temp`); действия (отключить задачи) — только по «да» владельца, через ASK-инструменты §4 спецификации.

Честный вердикт разрешён и ожидается: `INSUFFICIENT_EVIDENCE` лучше, чем PASS без файла.
