# SELF_REPAIR_3_CYCLE_PASS attempt with Claude Haiku 5.5 as worker (07.10.2026): NOT reached

Goal: three consecutive independent self-repair cycles (discovery -> goal-budget -> atomic-json), each passing the full ladder
of `tools/tree_self_repair_cycle.py`: DEFECT_REPRODUCED, MODEL_PATCH_CREATED, BOSSMAN_ZONE_CHECK_PASS, HOLDOUT_PASS_ON_PATCH,
SCOPE_RESPECTED, INDEPENDENT_VERIFICATION_PASS, EXPERIENCE_AUTO_SAVED.

Setup: installed backend :8801 build 6de18f8d9a08 (`/health/live` ALIVE, source_identity PASS, contains worker `haiku-5.5`).
Tool run from `wt-green-0610/tools` with `app\BOSSMAN-Windows-x64-6de18f8d9a08\runtime\python.exe`, worker `haiku-5.5`
(OpenRouter `anthropic/claude-haiku-5.5`, $0.10/$0.50 per M tokens, owner-approved via `--allow-paid-worker haiku-5.5`),
`--source-repo C:\Users\asd\Bossman\evo-tree-src` for every case, `--transfer` for goal-budget. Base commit of every task:
916f7c5e (evo-tree-src `bossman-2.1-candidate`; refs untouched). No other coding task was active before any launch
(GET /api/coding-tasks: 0 active each time). Raw evidence: `Bossman\bugtest-20261001\tree-1005\selfrepair-haiku\cycle01..cycle08`
(+ `.log`), task records and audits in `...\selfrepair-haiku\audits\`.

## Attempts (8 of 9 used)

| # | case | worker | task | steps / stop / sidecar s | stages reached | holdout base -> patched (failed/total) | independent re-check | cost |
|---|------|--------|------|--------------------------|----------------|-----------------------------------------|----------------------|------|
| 01 | discovery | haiku-5.5 | 5b2fe9c606ec | 17 / finished / 57 | DEFECT, ZONE_CHECK, SCOPE | 35/72 -> no diff | n/a | est. |
| 02 | discovery | haiku-5.5 | 48306dce403a | 15 / finished / 56 | **all 7** (recipe tree-selfrepair-48306dce403a VERIFIED, lesson coach-lesson:c779ff314ecb2b3d) | 35/72 -> **0/72** | git archive 916f7c5e: base 35/72 failed; + patch.diff (applies cleanly) 0/72 failed | est. |
| 03 | goal-budget (--transfer) | haiku-5.5 | be73e4c3949c | 40 / max_steps / 79 | DEFECT, SCOPE, RECIPE_RECALLED | 5/9 -> no diff | n/a | est. |
| 04 | goal-budget (--transfer) | haiku-5.5 | 4590fb2777a6 | 40 / max_steps / 74 | DEFECT, SCOPE, RECIPE_RECALLED | 5/9 -> no diff | n/a | est. |
| 05 | discovery (chain restart) | haiku-5.5 | 9dec2414ce69 | 13 / finished / 46 | DEFECT, ZONE_CHECK, SCOPE | 35/72 -> no diff | n/a | est. |
| 06 | discovery (restart) | haiku-5.5 | 0a4ce5c87017 | 21 / finished / 115 | DEFECT, ZONE_CHECK, SCOPE | 35/72 -> no diff | n/a | est. |
| 07 | discovery (restart) | haiku-5.5 | b3a251063618 | 8 / finished / 34 | **all 7** (recipe tree-selfrepair-b3a251063618 VERIFIED, lesson coach-lesson:72445981d0ceb957) | 35/72 -> **0/72** | git archive 916f7c5e: base 35/72 failed; + patch.diff 0/72 failed | est. |
| 08 | goal-budget (--transfer) | haiku-5.5 | 8e496975b5cc | 40 / max_steps / 86 | DEFECT, SCOPE, RECIPE_RECALLED | 5/9 -> no diff | n/a | est. |

Chain 1: discovery PASS (02) -> goal-budget failed twice (03, 04) -> broken. Chain 2: discovery failed (05, 06), PASS (07) ->
goal-budget failed (08) -> broken. With one attempt left a three-case chain was impossible; the 9th attempt was not spent
(atomic-json was never reached). **Consecutive passes: 1. SELF_REPAIR_3_CYCLE_PASS NOT reached.**

## Haiku 5.5 as worker

- Fast: every run took 34-115 s of sidecar time, against 5-20 min for GLM Flash and Nemotron. When it edits, it edits well:
  both discovery patches went from 35/72 to 0/72 holdout failures in 8 and 15 steps, with a failing-first test.
- Discovery is unreliable: 2 of 5 runs passed. 3 of 5 (01, 05, 06) ended `finished` with **no edit**. Haiku ran the zone tests,
  saw them green, and wrote that the defect was "not confirmed by tests", then asked for confirmation or suggested the fix was
  in the installed build. That is overcaution, not a tool failure.
- goal-budget: 0 of 3. It hit the same read loop as GLM Flash: 03 made 38 read_file calls, 04 made 36, and 08 made 28 reads plus
  11 searches and one write_file that left no final diff. All three stopped at max_steps 40. goals.py is 431 lines and is read
  window by window, so the budget runs out before any edit. `--transfer` recalled only discovery recipes (no goal-budget recipe
  exists), so RECIPE_RECALLED was true but unhelpful.
- Scope: 02 left an empty new file `command-center/tests/test_discovery_probe_tmp.py`. The wish says "no new files", but the
  tool's tree-mode scope rule accepts any `command-center/tests/test_*`, so SCOPE_RESPECTED stayed true. The file is empty, so
  holdout and re-check are unaffected. The tool's scope rule is weaker than the wish text here.
- Independence caveat (as in ns3): both passes recalled earlier VERIFIED discovery recipes (02: 040332fbc248, 4af43d85d619,
  d7f61c18f309; 07 also had 48306dce403a from 02). They are recipe-assisted repeats of a solved case, not new repairs.

## Haiku 5.5 as auditor (owner request)

For every attempt that produced a patch (02, 07), Haiku 5.5 received only the task wish text and the unified diff, through
OpenRouter chat completions (temperature 0, json_object). It was asked for `{"verdict", "defects", "missed_cases", "scope_ok"}`.
The audit ran after the task finished and before the cycle tool's holdout result was read. The auditor did not affect the cycle.
Records are in `audits/audit-cycleNN.json` (prompt sha256, diff sha256, raw response, usage, cost).

| attempt | auditor verdict | scope_ok | hidden holdout truth | classification | named the real missed cases? |
|---------|-----------------|----------|----------------------|----------------|------------------------------|
| 02 | REJECT | false | PASS (72/72) | **false reject** (but the scope objection was correct: an empty new test file, which the wish forbids) | none existed. It flagged 1e308 overflow, NaN from the score formula itself, and an order test; none are in the holdout |
| 07 | REJECT | true | PASS (72/72) | **false reject** | none existed. Same speculative overflow and NaN-transitivity concerns, plus test-coverage remarks |

Auditor accuracy against the holdout: 0/2 correct, 0 false accepts, 2 false rejects. Cost: $0.00068 + $0.00105 = $0.0017
(OpenRouter-reported), 9-10 s each. Only two samples, both on the same case, and the system prompt told it to be skeptical. Even
so, Haiku as auditor leans strongly toward REJECT on correct patches: it invents edge cases beyond the contract. It did catch
one real wish-scope breach that the tool missed. In this sample it is useful as a scope and lint second opinion, not as an
accept/reject gate. No failing patch was produced, so there is no evidence on false accepts or on whether it spots real misses
(for example the goal-budget residuals "negative charge must bind" and "NaN already on disk").

## Cost (cap $1.50)

- Worker: 194 agent steps (17+15+40+40+13+21+8+40), one chat request each. The sidecar does not record token counts.
- Estimate at Haiku prices: with the same generous 25k prompt + 1.5k completion tokens per step used for earlier GLM runs,
  that is $0.0033 per step, so at most about $0.63. Realistically $0.2-0.4, since the runs were short.
- Auditor: $0.0017, exact.
- Hard upper bound: the OpenRouter key's `usage_daily` after the run was $1.18, which includes all sessions today (the ns3
  GLM runs too). Under the $1.50 cap.

## Rules kept

Claude wrote no fix, edited no candidate, did not change holdouts, cases, wish text or owner roots, and did not move evo-tree-src
refs. It applied no patch to any branch. It did not push, Switch, install or publish. The only patches here are the worker's
(tasks 48306dce403a and b3a251063618), candidates only.

Files: `cycle02/` and `cycle07/` (patch.diff, task/cycle/holdout/recipe json, recheck-base/patched.json); `audits/` (both audits).
JSON files are LF-normalised copies of the raw evidence, so SHA256 values refer to these copies. SHA256.txt covers every file.
