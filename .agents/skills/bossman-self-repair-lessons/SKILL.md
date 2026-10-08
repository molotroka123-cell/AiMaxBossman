---
name: bossman-self-repair-lessons
description: Measured lessons about Bossman fixing its own defects with free workers: how workers fail, what feedback they need, what counts as repair, provider rotation, and the staged proof ladder. Read before running or judging a self-repair cycle.
compatibility: BOSSMAN, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: self-improvement
  learned_from: self-repair cycles 2026-10-05..06
---

# Self-repair lessons

## Owner rules
- Free models only. No paid worker unless the owner says so in that run.
- A patch written by Claude is NOT a Bossman repair. Never count it toward the ladder; label it as outside help.

## How free workers failed (measured)
- `max_steps` loop: re-reads one file again and again, never edits.
- `no_tool_call`: answers in prose when a tool call is required.
- Edits only tests, so the defect stays and the tests bend to fit.
- Edits files outside the allowed scope.
- Lost-edit suspicion: a reported edit is not on disk. Always diff the worktree; never trust the transcript.
- Mitigation: give a narrow file list, require a diff after each step, stop a worker after N steps without a write.

## Feedback the worker needs
- Red/green output from the worker's own test runner, inside the loop, after each edit. Without it workers guess.
- Lessons injected into the prompt did not change outcomes (recall hit 100%, results unchanged). Make lessons executable (a check or a gate) and route hard tasks to a stronger free model.

## Providers
- Rotate free providers: OpenRouter `:free` models and NVIDIA NIM. On HTTP 429 back off (exponential, then switch provider) instead of hammering.
- Keep a ledger of which route failed and why; do not retry a dead route every cycle.

## Staged ladder (each stage needs its own evidence)
1. `DEFECT_REPRODUCED`: a test fails on current code.
2. `MODEL_PATCH_CREATED`: a Bossman worker produced the diff.
3. `INDEPENDENT_VERIFICATION_PASS`: a different process re-runs the test and the holdout and passes.
4. `EXPERIENCE_AUTO_SAVED`: the lesson is stored in memory automatically.

Stop at the last stage with evidence and say so. A leaf is green only at stage 3 or later.

## Holdout numbers seen
- `discovery_nonfinite`: baseline 35-37 failing of 72 holdout checks; best partial patch 44/72 passing. Partial progress is reported as partial, never as a repair.

## Report
- State stage reached, worker and provider, steps used, failure class, and the holdout count before and after.
