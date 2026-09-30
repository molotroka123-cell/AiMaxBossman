# Мастер-промпт для следующего чата — исправленная версия

Черновик, ходивший по рукам 2026-09-07 05:48, отправлять нельзя: он опирается на
устаревшие числа и требует ровно того, что владелец сам запретил. Ниже сначала
расхождения (каждое проверяется командой), потом текст, который можно отправлять.

## Расхождения черновика с фактическим состоянием

| Черновик утверждает | Факт на `6cffbc2` | Чем проверено |
|---|---|---|
| Core 2612 passed | **2768 passed, 30 skipped, 0 failed** | `cd bossman-core && pytest tests/ --timeout=300 --ignore=tests/test_sandbox_safe_runtime.py --ignore=tests/test_stage9_sandbox_e2e.py` — команда CI дословно |
| Root 858 passed | **987 passed, 2 skipped, 0 failed** | `pytest tests -q` |
| «AT-01: ложное завершение по скриншоту без мутации» — сломано | Закрыто по **именным** обязательствам, 5 враждебных случаев + 3 положительных контроля | `pytest bossman-core/tests/test_at01_completion_obligations.py` → 8 passed; без слоя падают ровно 5 негативов |
| «AT-03: устаревание аппрувов между observe→act» — сломано | Закрыто, свежесть проверяется на границе эффекта и после подтверждения | `pytest bossman-core/tests/test_operator_at01_at03_regression.py` |
| PR #42: IV5-CAN-001/003, IV5-PROM-001/002/003 открыты | **Исправлены в `f1ece12`** — владелец сам это писал в списке DO NOT REDO | `git show --stat f1ece12` |
| Video core 311 passed | Video Studio: **263 passed, 11 skipped, 0 failed** | `pytest command-center/tests -k video_studio` |

Числа черновика не «примерно те же»: они описывают ветку до `dcdb223`.

## Что в черновике противоречит правилам самого владельца

1. **«Merge PR #37 + PR #40 в `claude/bossman-control-v03-43igbk`».** Владелец
   писал дословно: `no main merge`, `FEATURE_FREEZE=ON`, и «не сливай
   непроверенное только ради передачи». На PR #37 сейчас красный обязательный
   чек — `measured intelligence retention`, — и он красный ПО ЗАМЫСЛУ: файла
   `docs/benchmark/intelligence-preservation-current.json` нет, подделывать его
   запрещено. Слить PR #37 сейчас — значит внести в основную ветку кандидата,
   про которого владелец сам сказал: без реального измерения не сертифицируем.

2. **«Clean 50+ stale branches».** Прямой запрет: «Не удаляй ветки и не делай
   force-push», и отдельно «DO NOT perform during V5 closure: mass branch
   deletion».

3. **«Deadline: before Claude limits drop».** Спешка здесь уже один раз
   стоила: попытка мерить набор, привязанный к HEAD, во время коммита дала
   8 ложных падений (`ShaMismatch`). Дедлайн не отменяет порядок
   «воспроизвести → узкая правка → отрицательный контроль».

Пункты про ZIP в корне и `.bossman-state` — законны, но это PR #39 (repo
cleanup), и владелец просил не смешивать его с закрытием рантайма до RC.

---

# Текст для отправки

```markdown
# AiMaxBossman — V4/V5 closure, continuation

You are the single integrator. CONTINUE the current closure. Do NOT restart the
project, do NOT re-fix what is already closed, do NOT merge anything into the
default branch.

## FIRST — establish the truth yourself

Fetch and prove your checkout. The live integration line is PR #37,
branch `claude/v5-closure-at-reconcile-xdh12f`. Report the SHA you actually
have; do not trust any SHA quoted to you, including in this prompt.

Run these three and report the real counts before changing anything:
  pytest tests -q
  cd bossman-core && pytest tests/ -q --timeout=300 \
    --ignore=tests/test_sandbox_safe_runtime.py --ignore=tests/test_stage9_sandbox_e2e.py
  pytest command-center/tests -q

Note: bossman-core MUST be run from inside bossman-core/ — running it from the
repo root picks a different rootdir and different config. And never commit while
a suite is running: the benchmark engine binds reports to HEAD and will refuse
with ShaMismatch, which looks like 8 failures and is not one.

## ALREADY CLOSED — do not redo, do not "verify by rewriting"

Each of these has a negative control recorded in its commit message. Re-run the
tests if you doubt them; do not reimplement.

- AT-01 named FILE obligations: real filesystem re-read, pre-attempt snapshot,
  five hostile cases (missing / wrong contents / stale pre-existing / partial /
  unrelated verified mutation).
- AT-03 effect-boundary freshness and post-approval re-check.
- A2-03/A2-04 authorization at the effect boundary + the TypeError
  compatibility bypass that silently promoted a remote source to local.
- A6-03 owner identity required to set or clear an owner stop.
- A3-04 bounded desktop step: reads abandon on owner command, dispatched input
  parks for reconciliation instead of retrying.
- IV5-CAN-001, IV5-CAN-003, IV5-PROM-001, IV5-PROM-002, IV5-PROM-003 — fixed in
  f1ece12.
- Video CFR container slop; Chromium codec/preview diagnosis.
- Windows fenced-worker cancellation; Windows VerifiedRead descriptor identity.

## OPEN — this is the actual work

P0  AT-01 is PARTIAL_FILE_OBLIGATION_CLOSED, not CLOSED. The obligation layer
    understands file paths only. For UI-state changes, application settings,
    browser effects, multi-effect missions, and — most importantly — goals
    where the parser extracts nothing, the old weak rule still applies:
    "some verified mutation happened" closes the goal. Add at least one hostile
    non-file case and one parser-failure case. Do not report AT-01 as globally
    closed until these pass.

P0  IV5-CAN-002. bossman_shared/objective_canary.py still accepts
    CanaryOutcome.evidence_ref as an unattested string. Do NOT "fix" it with a
    non-empty check. Bind evidence at the real broad-activation caller to
    objective_id, revision_digest, cohort/run and process identity; reject
    stale, wrong-objective, wrong-revision, out-of-cohort and unattested refs.
    One invalid member must never authorize broad activation.

P1  AF-02 video fallback must live in the PRODUCT. A passing service-level test
    that bypasses the real button does not close the user path.
P1  AF-03 OpenRouter provider identity: with only a non-OpenRouter provider in
    the database, the key must never land in a foreign row.
P1  AF-04 the FULL retention lane appends a list of tool names and calls Ollama
    directly. Either label it a prompt-smoke or wire the real production loop.
    A test must FAIL if FULL is ever reduced to a tool-name string again.
P1  N4/N5/N6/N8 through PRODUCTION callers, with a real process restart. A
    helper test and "prepare_rollback returned a plan" are not evidence.
P1  Intelligence retention: same model, same config, same held-out set, paired
    per-item results, evaluated_sha == the exact frozen candidate.
P1  Owner Windows + local-model acceptance on the real host.

## HARD RULES

No merge into the default branch. No force-push. No branch deletion. No
skip/xfail to hide a failure. No threshold reduction. No fabricated evidence,
no reusing evidence from another SHA. No standing autonomy. Do not touch the
owner's running Windows session.

Freeze is FEATURE_FREEZE=ON: only a reproduced defect fix, a test/evidence fix,
or a CI fix needed to test the candidate.

## METHOD, non-negotiable

Reproduce first. Then the narrow change. Then a NEGATIVE CONTROL: revert the
production change, show which tests fail, restore, show them pass. A test that
passes both before and after proves nothing — say so out loud when it happens.

## REPORT

FINAL_SHA, then for each item: CLOSED_WITH_NEGATIVE_CONTROL / PARTIAL /
STILL_OPEN / NOT_RUN — with the command that produced the verdict. NOT_RUN is a
legitimate answer. INSUFFICIENT_EVIDENCE is a legitimate answer. "V4/V5
complete" is not, unless every live gate actually passed.
```
