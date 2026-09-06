# Desktop-operator step latency — measured, and what it does not prove

Branch `claude/v4-v5-local-models-optimization-ya3utu`. This complements
[HUMAN_SPEED_AUDIT_20260906.md](HUMAN_SPEED_AUDIT_20260906.md), which measures
CAS, the objective store's crash recovery and the reaction controller. Nothing
there measured the loop that actually drives the computer, so the changes to
that loop had no number attached to them.

## Scope, stated before any number

`tools/operator_step_profile.py` profiles the **framework**: the real
`ComputerOperatorManager` loop, its store writes, policy, verifier and loop
guard, and how many observations it asks for. Observation, planning and action
are driven by stubs whose cost the caller **declares**, because the true cost of
a UIA descendant walk, a full-screen PNG and a model turn is a property of the
owner's host and model, not of this repository.

The report carries `human_comparison=NOT_RUN` and
`scope=framework_only`. A PASS here:

- is **not** human-level computer use;
- is **not** an end-to-end task measurement;
- authorizes **no** N0 activation and no autonomy.

## What changed in the loop

The loop took **two full observations per step** — one after the action, and one
immediately afterwards as the next step's `before`. A post-action observation
that the verifier confirmed against the action's declared expectation *is* the
current state, so it is now reused as the next `before` inside a bounded
freshness window. It is never reused across a generation change (any owner
intervention), after a verification failure, after an approval wait, after a
loop-guard trip, after an action error, or once the window has expired.

Three further defects in the same path, all on the owner's Windows host:

- `Observer.observe` awaited foreground, then the UI tree, then the screenshot
  strictly in sequence, so one observation cost the sum of three operations and
  its fragments described three different moments.
- `WindowsDesktop` resolved the foreground window twice per observation, once in
  `foreground` and once in `ui_tree`; the UIA `Desktop` wrapper is the expensive
  part of the call. A single `snapshot()` now resolves it once, and structured
  and screenshot capture run concurrently — which also *reduces* the temporal
  skew between fragments, as the visual-observation contract requires.
- `ui_tree` called `descendants()`, which materialises the entire UIA subtree of
  the window, and only then sliced it to 500 elements. It is now a bounded
  breadth-first walk that stops at the cap. On a browser window this is the
  difference between thousands of cross-process COM round trips and a few
  hundred.

## Measured on this container

Linux, Python 3.11, `--steps 40`, declared costs `observe=120 ms`,
`plan=250 ms`, `act=30 ms`. Every sample retained, no outlier trimming.

| | with reuse | without reuse |
|---|---|---|
| observations | 41 | 81 |
| observations per verified action | 1.03 | 2.03 |
| declared cost total | 16 370 ms | 21 170 ms |
| wall total | 16 831 ms | 21 760 ms |
| framework overhead / step | 11.5 ms | 14.7 ms |
| p50 step | 411 ms | 534 ms |
| p95 step | 418 ms | 541 ms |

40 observations saved over 40 steps; at the declared 120 ms that is 4 800 ms of
the 40-step run. The `+1` observation in each column is the final turn where the
planner reports COMPLETE.

The declared costs above are **illustrative, not measured on the owner's host**.
Substitute real ones to get a prediction for the real machine:

```bash
python tools/operator_step_profile.py --steps 40 \
  --observe-ms <measured> --plan-ms <measured> --act-ms <measured> \
  --json-out artifacts/operator_step_profile.json
```

The concurrent capture and the bounded UIA walk reduce the cost of one
observation itself. That cost is a declared input here, so this table does not
credit them — they need a measurement on the Windows host to be quantified.

## Bookkeeping cost, and the part of it that remains

`framework_overhead_per_step_ms` grew with the number of steps in the task,
because `JsonTaskStore` re-read and re-parsed the whole journal — including the
task's complete step history — on every save, several times per step. Reads now
come from a cache validated against the file's own `st_mtime_ns`/`st_size`, so
any write from another holder still invalidates it.

| steps in the task | before | after |
|---|---|---|
| 10 | 3.8 ms/step | 2.0 ms/step |
| 30 | 6.7 ms/step | 3.9 ms/step |
| 60 | 11.0 ms/step | 6.0 ms/step |

The remaining growth is real and **not** fixed: every save still serialises the
task's full history, so total bookkeeping is still quadratic in step count. At
the default `max_steps=80` the last steps pay roughly 8 ms each. Removing it
means encoding only the history entries that changed, which would make the store
assume that older step records are never mutated — an assumption the class does
not currently make, and one that would silently drop a write if it were ever
false. It is recorded here rather than traded for that risk.

## Owner control, measured separately

The same loop change closed a correctness defect, not a latency one: the loop
held a decoded task snapshot for the whole step and its next write erased any
`pause` / `stop` / `take_control` that landed in between. Reproduced against the
pre-fix loop: after Pause the second click still executes and the task reaches
COMPLETED. See `bossman-core/tests/test_computer_operator_owner_control.py`;
`JsonTaskStore.save` is now compare-and-set on `ComputerTask.revision`.
