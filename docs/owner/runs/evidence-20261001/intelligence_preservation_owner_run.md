# Intelligence Preservation: замер на железе владельца (RU, 2026-10-01)

Источник: код ветки `claude/bossman-1.9-owner-bugtest-20260930` @ `6afbddaa` (read-only разбор).
Файлы: `.github/workflows/intelligence-preservation.yml`, `tools/intelligence_preservation_gate.py`,
`tools/intelligence_evidence_transport.py`, `tools/intelligence_preservation_run.py`,
`docs/benchmark/INTELLIGENCE_MEASUREMENT.md`, `docs/benchmark/INTELLIGENCE_PRESERVATION.md`,
`docs/owner/RC19_RELEASE_PROCEDURE.md` (раздел 2).

## 1. Почему красный (без обхода)

Workflow `Intelligence Preservation` = 2 job:

* `gate-contract` ("anti-dumbness gate contract") - `pytest tests/test_intelligence_preservation_gate.py`. Зелёный, это только контракт.
* `measured-gate` (**"measured intelligence retention"**) - красный. Шаги:
  1. `python tools/intelligence_evidence_transport.py fetch --repo $GITHUB_REPOSITORY --expect-sha $BOSSMAN_ACCEPTANCE_SHA` (SHA = PR head, не merge-коммит).
     Берёт commit-comments на этом SHA, оставляет только комментарии, где автор = владелец репозитория (`molotroka123-cell`),
     `commit_id` = SHA и тело начинается с `BOSSMAN_INTELLIGENCE_EVIDENCE_V1\n`. Нет такого -> exit 2:
     `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE: no owner-authored evidence comment on this exact commit`.
     Это ровно та ошибка, что в логе (job 108755930095, RC19_RELEASE_PROCEDURE.md §2).
  2. Если комментарий есть: `intelligence_preservation_gate.py ... --core-retention-min 0.98 --tool-retention-min 1.0 --min-samples 20 --expect-sha <SHA>`
     (доверие 95%, нижняя граница Уилсона; точечные 98% недостаточно).

Вторая, более глубокая причина: корпус `docs/benchmark/intelligence_tasks.json` (`bossman-retention-v2`) = 20 задач на каждую из 11 метрик.
При 20 на метрику лучшая достижимая нижняя граница core-retention = 0.8389 < 0.98, т.е. даже безупречный прогон = `INSUFFICIENT_EVIDENCE`.
Нужно **>= 189 независимых парных задач на каждую из 4 core-метрик** (reasoning_accuracy, coding_correctness,
structured_output_accuracy, unknown_task_adaptation); при 0.90 raw - 210; при 0.5% потерь - 280-401; реалистично 300. Остальные 7 метрик - >= 20.
Раннер сам откажется ДО обращений к модели (`capacity_check`). Готового корпуса >= 189 в репозитории нет.

Порог/гейт/`--min-samples` менять нельзя. `DEFAULT_REQUIRED` (exact_sha_certify) этот workflow не включает - отдельная ось, но красный на PR.

## 2. Команды владельца (PowerShell, на ПК Ryzen AI MAX+ 395)

Предусловия:
* Ollama (подписанный, 0.34.3) на **:11435**, модель импортирована; другие модели не гонять, GPU ничем не занимать (13-33 ч).
* Раннер говорит на **нативном Ollama API** (`/api/tags`, `/api/chat`). Целься в `http://127.0.0.1:11435` напрямую.
  Прокси :11500 (`nothink_proxy.py`) подставляет `reasoning_effort=none` ТОЛЬКО в `/v1/chat/completions`, на `/api/chat` он не влияет.
  Поэтому no-think задаётся флагом раннера `--think off` (нативный `think:false` во ВСЕ 4 полосы; пишется в результат).
* Python 3.12, `pip install -e bossman-core` (иначе полоса FULL не существует, раннер откажет, а не подменит промптом).
* `gh` залогинен **аккаунтом-владельцем репо** `molotroka123-cell` (`gh auth status`). Иначе CI комментарий не примет.
* Корпус `retention-v3.json` вне репо, независимо проверен (формат как у `docs/benchmark/intelligence_tasks.json`: `dataset_id`, `tasks[{task_id,metric,prompt,expect,context}]`;
  без дублей prompt+context, `dataset_id` != `bossman-retention-v2`). RAW не видит `context`, вопросы, зависящие от контекста, дают нулевой baseline - не использовать их в core-метриках.

```powershell
# --- 0. фиксируем ФИНАЛЬНЫЙ SHA (после него НИ ОДНОГО коммита в ветку PR) ---
$REPO = 'molotroka123-cell/AiMaxBossman'
$SHA  = '<40-hex head PR #89, проверить: gh pr view 89 --repo $REPO --json headRefOid -q .headRefOid>'
$EV   = "$env:USERPROFILE\Bossman\bossman-owner-evidence"      # ВНЕ чекаута
$CORP = "$env:USERPROFILE\Bossman\owner-corpus\retention-v3.json"
New-Item -ItemType Directory -Force $EV | Out-Null

# чистый чекаут ровно этого SHA (раннер сверяет дерево HEAD с файлами на диске)
git clone --no-checkout https://github.com/$REPO ip-src; Set-Location ip-src
git -c core.autocrlf=false checkout --detach $SHA
git rev-parse HEAD            # == $SHA
pip install -e bossman-core

# --- 1. Ollama жива, модель на месте ---
curl.exe -s http://127.0.0.1:11435/api/tags | Select-String 'bossman-fast-qwen36-35b-a3b-q5'

# --- 2. preflight: без генерации; обязан НЕ отказать (иначе корпус мал/модель не найдена) ---
python tools/intelligence_preservation_run.py `
  --model bossman-fast-qwen36-35b-a3b-q5:latest --endpoint http://127.0.0.1:11435 `
  --tasks $CORP --sha $SHA --think off --preflight-only `
  --out "$EV\ip-preflight.json" --gate-report "$EV\ip-preflight-gate.json"
# ожидание: "PREFLIGHT_ONLY: ..." и exit 0

# --- 3. настоящее измерение (13-33 ч) ---
python tools/intelligence_preservation_run.py `
  --model bossman-fast-qwen36-35b-a3b-q5:latest --endpoint http://127.0.0.1:11435 `
  --tasks $CORP --sha $SHA --think off --request-timeout 300 `
  --quantization Q5 --hardware "Ryzen AI Max+ 395 / Radeon 8060S / 128 GB / Ollama 0.34.3 :11435 (RUNTIME=OLLAMA_PROXY)" `
  --out "$EV\intelligence-current.json" --gate-report "$EV\intelligence-report.json"
# exit 0 = PASS, 1 = NO_GO, 2 = INSUFFICIENT_EVIDENCE. НЕ добавлять --allow-insufficient-samples (diagnostic_only=true, транспорт отвергнет).

# --- 4. редактированная сводка (откажет, если gate не PASS на приватных данных) ---
python tools/intelligence_evidence_transport.py prepare "$EV\intelligence-current.json" `
  --expect-sha $SHA --out "$EV\intelligence-comment-request.json"

# --- 5. ВЛАДЕЛЕЦ просматривает JSON, потом публикует (публично!), потом перезапускает job ---
gh api --method POST repos/$REPO/commits/$SHA/comments --input "$EV\intelligence-comment-request.json"
gh run list --repo $REPO --workflow intelligence-preservation.yml --limit 5
gh run rerun <run-id> --repo $REPO --failed
```

Замечания: `--model` = ТОЧНО та модель, которой Bossman реально пользуется (в RC19-процедуре `bossman-fast-qwen36-35b-a3b-q5:latest`;
основная - `bossman-main-qwen38-27b-q5:latest`). Модель обязана поддерживать `tools` в `/api/chat`, иначе `lanes.full.observed.executed=0`
и гейт откажет (`executed >= 1`). Прогон оставляет повторяемость на повторный прогон (seed=7, temperature=0 фиксируют сэмплер, не среду).

## 3. Что доказывает PASS и где это лежит

**Канал, который реально открывает CI: commit comment владельца на точном SHA** (`BOSSMAN_INTELLIGENCE_EVIDENCE_V1` + каноничный JSON-summary).
Коммит измерения в дерево не поможет: он меняет SHA, а `evaluated_sha` обязан совпасть с head PR (`OLD_SHA_PASS != CURRENT_SHA_PASS`).

Приватно (остаётся у владельца, вне Git/публики): `intelligence-current.json` + `intelligence-report.json`.
Поля, которые должны быть:

* `intelligence-report.json`: `gate=INTELLIGENCE_PRESERVATION`, `version=2`, **`status=PASS`**, `evaluated_sha==$SHA`, `findings` без BLOCKER/INSUFFICIENT,
  `scores.core_retention_lower_bound.{system,context,full} >= 0.98`, `tool_retention.* >= 1.0`, `hallucination_ratio <= 1.05`.
* `intelligence-current.json`: `diagnostic_only=false`; `source.evaluated_sha==$SHA`, `source.tracked_files_verified>=1`;
  `model`, `dataset_id`, `model_identity.model_version=sha256:...` (`revision_status=OBSERVED`), `quantization`, `hardware`, `configuration.think="off"`;
  `modes.{raw,system,context,full}.<11 метрик>.{score,samples}` с ОДИНАКОВЫМИ `samples` во всех полосах (core >= 189, остальные >= 20),
  `paired.{lost,gained}` для system/context/full; `lanes.full.kind="production_execution_loop"`, `executes_tools=true`,
  `observed.{model_turns,executed>=1,declined,items_with_executed_tool_call}`; `corpus.{file_sha256,tasks_sha256,tasks}`; `items[]` (булевы исходы по 4 полосам); `traces.full` (суммы сходятся с `observed`).
* `intelligence-comment-request.json`: `{"body": "BOSSMAN_INTELLIGENCE_EVIDENCE_V1\n{...}"}`, summary = ровно `model/dataset_id` (sha256-хэши), `evaluated_sha`, `modes`, `lanes.full`, `provenance`
  (`private_measurement_sha256, corpus_sha256, paired_items_sha256, model_identity_sha256, tracked_files_verified`). Тексты задач, ответы, трассы, пути в публичную сводку не входят.
* CI: job `measured intelligence retention` зелёный на том же SHA, артефакт `intelligence-preservation-report` со `status=PASS`.

**Evidence-ветка (по правилу владельца, не `release/*`)**: допустимо запушить в `evidence/owner-run-<дата>` только санитизированный аудит-пакет:
`intelligence-comment-request.json`, `intelligence-report.json`, SHA-256 приватного отчёта, версии/команды прогона, `ip-preflight*.json`.
Полный `intelligence-current.json` (корпус+ответы+трассы) в публичный репозиторий не класть. Это архив для аудитора и НЕ заменяет commit comment:
гейт evidence-ветку не читает. Пуш в ветку PR/`release/*` после замера обнуляет доказательство (новый SHA).

## 4. Чего нельзя делать

* Не создавать/коммитить `docs/benchmark/intelligence-preservation-current.json` с придуманными числами (docs/v8/OWNER_LOCAL_RUN_RU.md).
* Не подменять облаком (OpenRouter/GLM/Claude), моком, `cloud_stub.py`, другой моделью или квантизацией: модель = та же, что в проде, `provider=ollama`, `RUNTIME=OLLAMA_PROXY` помечать честно.
* Не снижать пороги (0.98/1.0/20/95%), не менять гейт, `GateConfig`, workflow, CI-аргументы; не править `evaluated_sha` в старом отчёте; не переиспользовать комментарий старого SHA.
* Не использовать `--allow-insufficient-samples` как доказательство; не размножать/переименовывать задачи корпуса (дубликаты отвергаются, независимость не появляется); не выбрасывать упавшие задачи ради парности.
* Не использовать `scripts/intelligence_retention_run.py` для CI: полоса `full` там prompt-smoke (AF-04), гейт отклонит по `executes_tools`.
* Не заменять FULL-полосу промптом; не запускать с грязным деревом/без `bossman-core`; не менять корпус/модель/HEAD во время прогона (раннер откажется).
* Не писать комментарий от агента/другого аккаунта (CI принимает только владельца репо) и не пушить измерение в `release/*`, `main`; не мержить, не тегать.
* NO_GO / INSUFFICIENT - это ответ, а не повод "подвинуть" планку.

## 5. Нерешённое по коду
См. итоговое сообщение: (a) канал "evidence branch" vs commit comment; (b) какой именно model id; (c) источник и ревьюер корпуса >= 189; (d) 11434 vs 11435 в старых доках; (e) какой SHA считается финальным.
