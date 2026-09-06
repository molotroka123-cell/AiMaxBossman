# Daytime acceptance — unified Bossman

This is a runnable checklist, **not a scheduled background task** and not a PASS.
The owner requested full acceptance during daytime. Ordinary GitHub triggers may
run sooner; do not disable them or use skip-ci to manufacture a quiet green SHA.

## 1. Freeze a candidate

From a clean disposable checkout, without user credentials or production data:

```bash
git fetch origin
git switch --detach origin/integration/bossman-unified-20260906
git rev-parse HEAD
git status --porcelain
git merge-base --is-ancestor b85ab17c3ec46c7ba10ca33270cc6d2b95e83a33 HEAD
git merge-base --is-ancestor dc0d149b91bee6617f5d2d207e99cd744eb24905 HEAD
```

Record FINAL_SHA and the workflow run/attempt IDs. Confirm that no further Fable
milestone is waiting to be integrated before starting the full run. New commit
means a new candidate; a parent PASS is historical evidence only.

## 2. Environment

Use supported Python 3.11 and 3.12 in clean virtual environments; clear inherited
PYTHONPATH. Install the root shared distribution and both components from this
checkout. Follow current workflow setup for PostgreSQL/Redis and platform deps.

```bash
python -m pip install -e . -e "./bossman-core[dev]" -e "./command-center[dev]"
ffmpeg -version
ffprobe -version
python -m playwright install chromium
```

Do not silently skip render acceptance when FFmpeg is absent. Windows-specific
permissions/ACL and actual AI Max hardware require their own environment.

## 3. Narrow merge tripwires first

Use each component's pytest configuration; do not run all async tests under the
root configuration and call fixture errors a product regression.

```bash
node --test command-center/tests/js/desktop.test.mjs command-center/tests/js/unified_shell.test.mjs
python -m pytest tests/test_epoch4_mission_ir.py tests/test_epoch5_objective_spec.py -q
(cd bossman-core && python -m pytest tests/test_v3_authenticated_journal.py tests/test_epoch4_golden_foundation.py -q)
(cd command-center && python -m pytest tests/test_approval_resume_matrix.py tests/test_finalize_capability_match.py tests/test_finalize_unclassified_failure.py tests/test_video_native_finalize_contract.py tests/test_web_designer.py -q)
```

Then add `test_redteam_closure.py` and `test_golden_missions.py` from Command Center.
No mass change from completed to waiting_approval; trace actual effects and proof.

## 4. Full deterministic regression

Current workflows remain the source of truth for setup and exact test arguments.
The following Linux commands are reference invocations, not Windows signal tests:

```bash
python -m pytest tests -q --timeout=120 --timeout-method=signal
(cd bossman-core && python -m pytest tests -q --timeout=300 --timeout-method=signal --cov=bossman_v3 --cov-report=term:skip-covered --cov-fail-under=85)
(cd command-center && python -m pytest -q --timeout=180 --timeout-method=signal --cov=bcc --cov-report=term:skip-covered --cov-fail-under=72)
python scripts/update_readme_scorecard.py --check
python tools/skips_registry.py --check
python tools/ci_secret_scan.py
git diff --check
```

Run SAST/SCA and the portable Windows jobs using the existing CI setup. Keep all
passed/failed/skipped/xfailed counts and stderr; do not lower coverage or add skips.

## 5. Actual application checks

Cold start/login; mission/executor selection; approval then resume; interruption
and restart; stale evidence rejection; real file edit; browser task; PRIVATE local
model unavailable; unknown cost; queued media export and verified download.

Video: use actual generated FFmpeg fixtures, interrupted export, repeated thumbnail
requests, CFR/frame cuts, duration/NaN, undo/revision conflicts and negative evidence.
Web: source-preserving edits, preview sandbox, stale revision and malicious markup.
Desktop: narrow/wide screen, Russian long labels, 125/150/200% zoom, keyboard-only,
dark/light, reduced motion, forced colors and no duplicate video launcher.

## 6. Intelligence and release evidence

The intelligence-gate unit tests validate the evaluator, not the model. Hold model
version, quantization, generation settings, data, tool affordances and budgets
constant for appropriate paired RAW/SYSTEM/CONTEXT/FULL comparisons. Evaluate each
category; do not hide losses behind averages. Report uncertainty and insufficient
sample size. Do not enable paid calls, change model weights or fabricate metrics.

Verify required workflows on the actual candidate SHA: root, Core, Command Center,
V2 repair, ASTRA portable/Windows, Solana, Intelligence Preservation, plus this
candidate smoke. A green subset cannot overrule a failed required gate. If a
workflow did not trigger on this branch, dispatch it with supported tooling or
report NOT_RUN; do not reuse the parent or a different PR merge-tree run.

Final record: SHA, environment, commands, exit codes, counts, coverage, run IDs,
known blockers, owner-only checks, and verdict. Commit reporting before final
acceptance or publish the final evidence as workflow artifacts/PR comments so a
report-only commit does not silently change the certified SHA.
