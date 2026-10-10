# North Star ladder run, 10.10.2026

Branch `feat/north-star-ladder-20261010` (from `feat/bossman-genjutsu-jeff-unified-20261010` = `44caf013`).
Lab: clone of that SHA at `C:\Users\asd\Bossman\ns-lab-20261010` (own port :8835 and data dir, never the owner's
:8801 / CommandCenter). Evidence folder: `docs/evidence/north-star-20261010/`. Harness: `tools/north_star/`.

Same-product contract: the lab runs the same `bcc` backend, coding-task path, RESULT_VERIFIER and recipe store as the
owner's Bossman (source checkout of the same SHA); nothing was applied to stable, nothing pushed.

## Verdict per stage

| # | Stage | Status | One line |
|---|-------|--------|----------|
| 1 | SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT | already PASS | unchanged (site ladder.override.json) |
| 2 | SELF_REPAIR_SINGLE_CYCLE_PASS | **PASS under the site definition; North Star doc gate PARTIAL** | one real defect, found by Bossman's own soak, fixed by Mistral Large 4, product verifier PASS, recipe saved and survived a restart; the "analogous task" step of the doc gate was attempted and failed |
| 3 | SELF_REPAIR_3_CYCLE_PASS | **FAIL (1 of 3)** | second defect not fixed in 3 Mistral + 10 sidecar attempts; no third defect exists |
| 4 | TRANSFER_MEASURED_GAIN | **NOT RUN** | needs >= 2 solved cycles and held-out tasks; only one recipe exists |
| 5 | 24H_SOAK_PASS | **IN_PROGRESS** (6.9 h of 24 at the last read), likely FAIL on its own rule | see below |

Ladder file `ladder.override.json` was NOT edited (no tool applies ladder evidence; statuses are never hand-edited).
Recommendation: do not set `SELF_REPAIR_SINGLE_CYCLE_PASS` on the site until the owner decides which definition
governs and has seen the caveats below.

## Stage 2: what was proven, exactly

Defect (found by the repo's own UX soak, `tools/ux_soak/soak.py cli_check`, not chosen by me): once more than 500 tasks
exist, `bossman list tasks --json --limit 1000` silently returns 500 while the API holds 837. 42 `high` findings
"CLI vs API tasks differ" in `C:\Users\asd\Bossman\soak-20261010\out\events.jsonl`. Cause: `GET /api/tasks` clamps `limit`
to 500 and offers `before_id`; `bcc/terminal_cli/cli.py::list_items` passes `limit` through without paging.

| Step | Evidence | Who |
|------|----------|-----|
| FIND | soak events (above); `holdouts-on-base/cli_task_paging.json`: base 4/7 pass, 3 defect cases fail | Bossman's UX soak; holdout wrapper written by the harness author (disclosed, same practice as the 07.10 atomic-json case) |
| FIX | `mf5/task-mistral91665054.json`, `mf5/model-reply.txt`; cli.py now pages via `before_id` + one new test file | `mistralai/mistral-large-4-0` via OpenRouter (3rd attempt; attempts 1-2 in `mf3/`, `mf4/` were rejected by the verifier) |
| ISOLATION | edits applied to base text, scoped to cli.py + one NEW test file, run in clean clones | harness (mechanical apply only, no fix text written by me) |
| VERIFY | `mf5/strict.json`, `mf5/verify-strict/verdict.json`: product `RESULT_VERIFIER` PASS, `counts_as_student_success: true`; hidden holdout 7/7 (base 3 failing); negative control: new test fails without the fix | `bossman_v3.self_improvement.verifier` |
| REVIEW | `mf5/reviews-strict.json`, `mf5/strict.run*.json`: Gemma 4 31B (Google) accept, no findings, 3 separate runs, 0 rejections | `google/gemma-4-31b-it` on NVIDIA NIM |
| LEARN | `mf5/recipe-strict.json`: recipe `tree-selfrepair-mistral91665054`, VERIFIED, lesson `coach-lesson:b7e81cd677a2ecbb` | `POST /api/coding-recipes` |
| RESTART | `restarts.jsonl`: backend process killed and started; recipe present before and after | harness |
| ANALOGOUS TASK | `goal-budget-recipe-match-after-restart.json`: recall = memory hit only; `gm1..gm3`: not solved | see stage 3 |

### Caveats (read before relying on stage 2)

1. The 8 attempts (ct1..ct8) through the product coding-task sidecar (free NVIDIA NIM / OpenRouter :free workers) produced one
   holdout-passing patch (`ct2-nim`, Nemotron 3 Super) which the product verifier rejected `INVALID_TEST` because it
   rewrote lines of an existing test helper. The accepted fix came from a **single direct call** to Mistral Large 4
   (`tools/north_star/mistral_fix.py`) that bypasses the sidecar's tool loop; isolation and verification are the same.
2. Mistral needed 3 tries; retries received only the product verifier's reason text (no solution). Attempt 3 is the
   one scored; the earlier two are kept as failures.
3. Reviewers: only Gemma returned valid verdicts. Kimi K3, gpt-oss-20b, GLM 5.3 (NIM and OpenRouter) and Phi-3.5 gave
   empty reasoning-only replies, HTTP 404/504/529: recorded as `no_verdict`, not counted. Gemma is lenient: it also
   accepted a patch whose own tests fail (`gm1`). The product verifier is the real gate; the review is a weak second one.
4. Memory was not a factor for the fix: the recipe store was empty (fresh lab data dir) when the fix was written.
5. The defect is small and its holdout was written by the harness author; this shows the mechanism works end to end
   once, not that Bossman repairs hard defects unaided.
6. North Star doc gate "find/fix/test/verify/learn/restart/analogous-task": the last element has no pass, so under that
   stricter wording stage 2 is PARTIAL.

## Stage 3: FAIL

- Only one real defect exists to find on this base. Bossman's own suite scan (`suite-scan.log`, 9103 tests, 44 failed
  under xdist) is clean: all 44 pass when re-run serially (`rerun-serial.json`), so they were run-order/port artifacts.
  The repo's three registered holdouts: discovery and atomic-json already pass on base (fixed earlier), goal-budget is
  still 5/9 failing (`holdouts-on-base/summary.json`).
- goal-budget: `gb1..gb10` (GLM Flash, Gemini x3, Nemotron Ultra, NIM x3, OpenRouter free x2): no patch passed the
  holdout (NIM/free workers only added a passing test; Gemini hit 503/429 quota; GLM and Ultra hit the 40-step cap).
  `gm1..gm3` (Mistral Large 4 with verifier feedback): best 8/9 on the holdout, own tests failing, verifier FAIL.
- Re-fixing the already-merged discovery/atomic-json defects would be "repeated known tasks"; not counted, not run.
- Earlier module-audit attempts `c01..c08`: 0 confirmed defects (5 hit the step cap with no test, 3 tests rejected by
  the independent judge as speculative). `infra-fail-oldsidecar/` = first 3, caused by the sidecar importing an old checkout.

## Stage 4: NOT RUN

No before/after measurement on held-out tasks was possible: a single solved cycle, and the one analogous task
(goal-budget) was not solved with or without the recalled recipe. A memory hit is recorded, not counted as learning.

## Stage 5: soak, IN_PROGRESS

- Tool: repo's `tools/ux_soak/soak.py`, server mode + headless Chromium + CLI chat, fake model, restart every 25
  interactions. Frozen snapshot of `44caf013` in `C:\Users\asd\Bossman\soak-20261010\src`; port 8871; fresh data dir.
- Started 2026-10-10T14:28:41Z, ends about 2026-10-11T14:28:41Z. PIDs: soak 26628, monitor (pythonw) 25012.
- Evidence: `C:\Users\asd\Bossman\soak-20261010\heartbeat.jsonl` (5-minute beats; 83 at 6.86 h, 5 with a refused
  health probe, all during deliberate restarts), `out\events.jsonl`, final `out\metrics.json`, `monitor-final.json`.
  Copy of the plan and rules: `docs/evidence/north-star-20261010/soak/README.md`.
- **Predicted FAIL on its own rule** (`by_severity.high == 0`): the soaked snapshot contains the CLI 500-cap defect
  above, so the soak keeps logging that one `high` finding class (42 so far, no other class, 0 Tracebacks). The
  candidate fix is not applied to the snapshot (owner promotion gate). Not a PASS before 24 h, and not a PASS with
  open high findings. Limits: fake model only, one task class, no STOP/retry-storm/orphan-model/git-corruption probes.

## Spend

| Provider | Use | Cost |
|---|---|---|
| OpenRouter Mistral Large 4 | 8 calls (2 empty reasoning-only replies $0.044, then reasoning disabled) | $0.079 |
| OpenRouter GLM 5.3 Flash | 2 reviewer calls (empty) | $0.016 |
| OpenRouter GLM Flash / Haiku 5.5 via sidecar | c06, gb1 (GLM, 40 steps each), c07 (Haiku, 40 steps, started before the "no Haiku" message and not repeated) | not metered per task; see below |
| Gemini free tier | judge calls; worker runs hit 503/429 | $0 |
| NVIDIA NIM (free) | Nemotron Super worker x10, Gemma/Kimi/gpt-oss/GLM reviewers | $0 |
| OpenRouter `:free` | Nemotron Super/Ultra, Cohere workers | $0 |
| Mistral vault key, local Ollama | not used | $0 |

OpenRouter key counter: 2.3547 at my start, 2.9166 at the end = $0.562 growth, shared with any other agent using the
key; the harness-metered part is $0.094, so the unmetered sidecar runs are at most about $0.47. That is not
attributable to me alone and may exceed the $0.40 budget if nothing else spent on that key.

## Reproduce

```
powershell tools\north_star\start_lab.ps1                      # lab backend :8835
python tools/north_star/mistral_fix.py --evidence <dir> --case cli-tasks [--feedback "<verifier reason>"]
python tools/north_star/verify_candidate.py --evidence <dir> --case cli-tasks --reviewers nvidia:google/gemma-4-31b-it
python tools/tree_holdout/cli_task_paging.py --repo <checkout>   # hidden holdout, new in this branch
```
Lab-only edits (not in this branch): two extra worker routes in the lab clone's `coding_tasks.WORKERS`
(`lab-workers.diff`, `lab-workers-2.diff`), and a venv whose `.pth` points `-I` sidecars at the lab clone.

## Blockers / next

1. A second and third independent defect source: the soak found one class; the suite scan found none. More
   self-repair evidence needs new real findings (a longer soak on a fixed snapshot, or the CI failures of PR #107).
2. Sidecar tool loops (GLM, Nemotron Ultra, free NIM) still stall at the 40-step cap or only add passing tests; the
   one-shot edit-JSON path works better for small defects and is cheap ($0.003 per call).
3. Reviewer reliability: reasoning models return empty content within ordinary token budgets; set `reasoning.enabled=false`
   on OpenRouter, give NIM reasoning models >= 14000 tokens, or use a non-reasoning reviewer.
4. Owner decision: which definition of SINGLE_CYCLE governs (site text vs doc gate with analogous task).
