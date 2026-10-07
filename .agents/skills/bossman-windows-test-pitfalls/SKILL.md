---
name: bossman-windows-test-pitfalls
description: Windows test-environment traps that wasted hours on the owner machine (stale editable install, cp1251 stdout, Git Bash path mangling, leaked secrets, PYTHONPATH, timeouts, env vars not reaching tests). Read before running or debugging Bossman tests on Windows.
compatibility: BOSSMAN (Windows owner machine), Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: testing
  learned_from: green-leaves run 2026-10-06
---

# Windows test pitfalls

Each item: sign, fix, check.

## Wrong code under test
- Sign: results do not match the diff; an editable install points at an old checkout.
- Fix: put the worktree first on `PYTHONPATH` (absolute `command-center` and `bossman-core` paths).
- Check: assert `module.__file__` starts with the worktree path before trusting any result.
- `bossman-core` tests need `command-center` OFF `PYTHONPATH`; run them in a separate process.

## Encoding and paths
- A cp1251 console corrupts or crashes on Cyrillic output: set `PYTHONIOENCODING=utf-8`.
- Git Bash rewrites `/api/...` args and eats backslashes: use `MSYS_NO_PATHCONV=1` or call Python/PowerShell.

## Isolated probes
- Run probes with temp `HOME`, `LOCALAPPDATA`, `APPDATA` and `BCC_DATA_DIR`. Never point them at the owner's live data dir or port.
- Strip every env var whose name contains `KEY`, `TOKEN` or `SECRET` before spawning a probe, then confirm none remain.

## Load and timeouts
- On a shared machine other agents consume CPU. Give each test its own timeout instead of one global limit; a timeout under load is a retry, not a verdict.
- Rerun a lone timeout once, alone, before calling it a regression.

## Env must reach the test process
- `BOSSMAN_VERIFY_PYTHON` must be in the environment of the process that actually runs pytest. The cloud-worker launcher once dropped it, so verification used the wrong interpreter.
- Check by printing it from inside a test or conftest, not from the parent shell.

## Known environment artifacts (not product bugs)
- Windows ignores POSIX modes: tests asserting `0600` fail. Mark or skip on `win32`; do not change product code for it.
- `psutil.is_running` can report zombie or reused pids oddly. Treat related failures as environment unless reproduced on Linux/CI.
- List these as known artifacts in the report; do not count them as passes or as failures of the change.
