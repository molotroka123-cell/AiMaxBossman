# Intelligence Preservation Gate

Bossman must not gain autonomy by making the underlying model worse at reasoning, coding, structured output, tool choice, or adaptation.

This gate compares the **same model** on the **same benchmark items** in four lanes:

1. `raw` — user task -> model
2. `system` — user task -> Bossman system policy -> model
3. `context` — system + Bossman context compiler/retrieval, without the full tool surface
4. `full` — complete Bossman orchestration, relevant tools/skills/router enabled

The gate is deliberately fail-closed: missing lanes, missing metrics, invalid scores, or too few samples are `INSUFFICIENT_EVIDENCE`, never PASS.

## Required metrics

Each lane must report these metrics with `score` in `[0,1]` and `samples`:

- `reasoning_accuracy`
- `coding_correctness`
- `structured_output_accuracy`
- `unknown_task_adaptation`
- `tool_selection_accuracy`
- `schema_argument_accuracy`
- `long_context_accuracy`
- `memory_retrieval_accuracy`
- `computer_use_planning_accuracy`
- `task_completion_rate`
- `hallucination_rate` (lower is better)

Recommended measurement set is >= 200 tasks total and >= 20 observations per required metric. The CI contract only enforces the per-metric floor; benchmark generation owns stratification and paired-task identity.

## Release rules

Default hard gates:

- system core-intelligence retention vs RAW >= **98%**
- context core-intelligence retention vs RAW >= **98%**
- full Bossman core-intelligence retention vs RAW >= **98%**
- full tool-selection accuracy vs CONTEXT must not regress
- full schema-argument accuracy vs CONTEXT must not regress
- hallucination rate may not increase by more than 5% relative (and may not rise above zero if RAW is zero)

Core intelligence is the mean of reasoning, coding, structured-output, and unknown-task-adaptation accuracy. A future statistical runner should add paired confidence intervals; until then this gate intentionally uses simple transparent thresholds rather than fake statistical precision.

## Current evidence file

A measured run must be written to:

`docs/benchmark/intelligence-preservation-current.json`

Example schema:

```json
{
  "model": "same-model-id-and-revision-for-all-lanes",
  "dataset_id": "anti-dumbness-v1",
  "modes": {
    "raw": {
      "reasoning_accuracy": {"score": 0.84, "samples": 50},
      "coding_correctness": {"score": 0.80, "samples": 30}
    },
    "system": {},
    "context": {},
    "full": {}
  }
}
```

All required metrics must exist in every mode; the abbreviated example is not sufficient evidence.

## Interpretation

A failure isolates likely orchestration regressions:

- `SYSTEM_CORE_REGRESSION` — policy/system instruction collision
- `CONTEXT_CORE_REGRESSION` — context pollution/retrieval/authority problem
- `FULL_CORE_REGRESSION` — complete orchestration harms baseline reasoning
- `TOOL_REGRESSION` — tool overload or capability-selection regression
- `HALLUCINATION_REGRESSION` — orchestration increases unsupported output

## Non-negotiable rule

`MORE_AUTONOMY != MORE_INTELLIGENCE`

A V4/V5 milestone is not release-ready if autonomy improves while the same underlying model loses more than the allowed baseline capability through Bossman orchestration.
