# Two synthetic coding jobs from CMD

This is an owner-only rehearsal, not a customer order or an income measurement.
The `$10` and `$20` labels are hypothetical task values. No payments, outreach,
production changes, or external repositories are involved.

From this checkout in `cmd.exe`:

```cmd
python tools\coding_value_sim.py --installed-python C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-0d5d1d4d3f42\runtime\python.exe --model bossman-fast-qwen36-35b-a3b-q5:latest
```

That installed runtime is pinned to source SHA `0d5d1d4d3f42ea4b7949bc8f76fd357bd9082b0e`.
Pass the newly installed runtime path after a later build; the checkpoint records
its source SHA. Omitting `--installed-python` runs the checkout's backend, CLI,
and sidecar instead.

`--endpoint` defaults to loopback Ollama at `http://127.0.0.1:11434/v1`.
`--task-timeout` bounds each task (default 420 seconds). A private checkpoint is
created under `%LOCALAPPDATA%\Bossman\owner-run\coding-value-sim\` and its path
is printed at the end. The checkpoint is written before the first case and
after each result. Keep it private because it contains the synthetic diffs and
local paths. It never contains an owner token.

For each case, the runner creates a new synthetic Git repository with a failing
test, starts a private Bossman backend, and calls `bossman code` through the
terminal CLI with one allowed source file, a protected test file, and host-run
verification. Bossman creates and cleans its own isolated sandbox. A second,
independent verifier applies the returned diff to a fresh clone, checks that
only the allowed file changed, runs the tests three times, and exercises an
additional check outside the sidecar. The original synthetic repository must
remain unchanged.

`PASS` means both tasks passed with a real model. `FAIL` and `BLOCKED` retain
their evidence in the checkpoint. A deterministic scripted-model check may be
run with `--allow-mock`; its verdict is `PLUMBING_PASS`, which says nothing about
the coding ability of a real model. Even a real-model `PASS` on two synthetic
cases does not establish client readiness or earnings. The three separate
rehearsals in the first-work plan and owner review still apply.
