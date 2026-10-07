# Очередь самоисправления 07.10.2026 (wt @6869f67a, только чтение)

Реестр: 831 узел, 815 листьев; листья: reported 556, recorded 180, branch 32, code 30, blocked 10, idea 6, prepared 1. Расписки: PASS 609, FAIL 69, RETIRE 2. Сайт = реестр (556, 0 расхождений).

## Очередь

| id | лист | файл | дефект | воспроизведение | приёмка | зависимости | риск | цена | тип | пара |
|---|---|---|---|---|---|---|---|---|---|---|
| Q1 | mod-autonomy | command-center/bcc/autonomy/workers.py (TreeRunner.run) | TreeRunner.run returns after tree.kill before the killed worker is reaped: test asserts process not is_running, got True (3/3 deterministic on Windows; receipt ops.json FAIL tests_failed) | `cd command-center && python -m pytest -q -p no:cacheprovider tests/test_autonomy_bounded_loop.py::test_tree_runner_kills_the_whole_worker_tree_when_stop_appears` | that test green 3 of 3 runs, other 16 tests in test_autonomy_*.py still pass, diff only workers.py | - | medium: process handling, Windows Job Object semantics | ~40k tokens, 10-15 steps | CODE+TEST | Q6 |
| Q2 | bcc/pit/discovery.py | command-center/bcc/pit/discovery.py | choose_discovery_question lets NaN/inf/None numeric fields win or crash (TypeError) | `python tools/tree_holdout/discovery_nonfinite.py --repo <wt>` | holdout exit 0 (12 FAIL -> 0), valid behaviour unchanged, existing pit tests green | BOSSMAN_VERIFY_PYTHON in worker sidecar (selfrepair blocker 1) | low | ~25k tokens, 8 steps (small file) | CODE+TEST | Q3 |
| Q3 | bcc/autonomy/goals.py | command-center/bcc/autonomy/goals.py | GoalStore.charge(cost_usd=nan/inf/negative/str-nan) leaves budget unbound; NaN usage on disk not treated as exceeded | `python tools/tree_holdout/goal_budget_nonfinite.py --repo <wt>` | holdout exit 0 (5 FAIL -> 0), valid charging unchanged, tests/test_autonomy_goals.py green | Q2 recipe (transfer test) | low-medium: money cap | ~25k tokens, 8 steps | CODE+TEST | Q2 |
| Q4 | skill-36 compact | bossman-core/skills/compact/SKILL.md | frontmatter has `purpose` but no `description`; parse_skill description len=0 | `python tools/tree_proof/skills_oss_probe.py skill-worker bossman-core/skills/compact/SKILL.md  (VERDICT FAIL)` | probe VERDICT PASS; one-file doc edit | - | very low | ~8k tokens, 3 steps | CODE | Q5 (missing-field/test class) |
| Q5 | plugin http / safe_get | command-center/bcc/plugin_security.py; command-center/tests/test_plugin_security.py | safe_get handles content-encoding after aiter_bytes decode, but no gzip regression test exists (grep gzip in plugin tests: 0) | `cd command-center && python -m pytest -q tests/test_plugin_security.py -k gzip  (expected 0 selected; write test with httpx.MockTransport gzip body)` | new test passes and fails if content-encoding handling is removed | - | low | ~20k tokens, 6 steps | TEST | Q4 |
| Q6 | self_improvement runner | bossman-core/bossman_v3/self_improvement/runner.py (atomic_json) | os.replace PermissionError flake on Windows (AV/indexer lock); no retry | `UNKNOWN: flake not reproduced; stress loop of atomic_json on one path from 2 threads` | bounded retry/backoff on PermissionError; 200-iteration stress green | - | low | ~20k tokens | CODE+TEST | Q1 (Windows timing class) |
| Q7 | app-ai-webcam-vision | apps/ai-webcam-vision/tests/test_storage.py; apps/ai-webcam-vision/tests/test_privacy_defaults.py | chmod 0o600 is a no-op on NTFS: st_mode 666 != 600; 2 tests fail on Windows. Not a product defect; fix = skipif win32 or ACL check (owner decision) | `cd apps/ai-webcam-vision && PYTHONPATH=src python -m pytest -q tests/test_storage.py tests/test_privacy_defaults.py  (2 failed, 16 passed)` | Windows: explicit skip with reason or ACL check; POSIX unchanged | OWNER decision: skip vs icacls | low, but weakening a test must be explicit | ~10k tokens | TEST+OWNER_HW | Q4 |
| Q8 | skill-22 solana-volume-suite | .agents/skills/solana-volume-suite/SKILL.md | scan_policy critical 'telemetry' (description and WS /ws/telemetry line) | `python tools/tree_proof/skills_oss_probe.py skill-worker .agents/skills/solana-volume-suite/SKILL.md` | no critical violation without hiding real telemetry; owner decides wording vs policy exception | OWNER | medium: policy-dodge risk | ~10k tokens | CODE+OWNER |  |

Проверено запуском (только pytest/holdout): Q1 красный 3/3; Q2 12 FAIL; Q3 5 FAIL; Q4 VERDICT FAIL; Q7 2 failed.

Пары для проверки переноса рецепта: Q2->Q3 (не-конечные числа), Q1<->Q6 (тайминг Windows), Q4<->Q5 (пропущенное поле/тест).

## Расхождения
- site 556 reported == tree.export.json 556 (831 nodes, 0 status diffs, ids identical); meta head 6869f67ad8ef: site NOT stale
- 11 reported leaves without PASS receipt: cap-0, cap-6, cap-8, cap-9, cap-16, cap-17, cap-44 and ci-37304275631, ci-37209801507, ci-37209801489, ci-37209801468 (seeded/CI evidence, not receipts)
- 0 non-reported leaves with PASS receipt; 22 nodes have only FAIL receipts and none is green (consistent)
- mod-autonomy has status branch with FAIL receipt (TreeRunner test), still red now
- article external links: github.com/tonhowtf/omniget 404 (real); higgsfield.ai/genjutsu and /blog 404 only due to trailing backtick in article text (markdown artefact)
- HANDOFF_GREEN_LEAVES says reported 209; registry now 556 (later checkpoint, handoff text outdated)

## UNKNOWN
- browser click/open of branches not exercised (data files only)
- atomic_json flake not reproduced
- skill-22 rerun not done
- exact CI status

## Кэш
checked_sha в queue.json = sha256 файла; не перепроверять при совпадении.