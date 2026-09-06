# Intelligence Preservation Gate

Bossman must not gain autonomy by making the underlying model worse at reasoning, coding, structured output, tool choice, or adaptation.

The gate compares the **same model** on the **same benchmark items** in four lanes:

1. `raw` — user task -> model
2. `system` — user task -> Bossman system policy -> model
3. `context` — system + Bossman context compiler/retrieval, without the full tool surface
4. `full` — complete Bossman orchestration with relevant tools/skills/router enabled

Missing lanes, metrics, invalid scores, or too few samples are `INSUFFICIENT_EVIDENCE`, never PASS.

## Required metrics

Each lane reports `score` in `[0,1]` and `samples` for:

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

Recommended measurement set is >= 200 tasks total and >= 20 observations per metric.

## Default release rules

- SYSTEM core retention vs RAW >= **98%**
- CONTEXT core retention vs RAW >= **98%**
- FULL core retention vs RAW >= **98%**
- FULL tool-selection accuracy vs CONTEXT must not regress
- FULL schema-argument accuracy vs CONTEXT must not regress
- hallucination rate may not increase by more than 5% relative

Core intelligence is the mean of reasoning, coding, structured-output, and unknown-task-adaptation accuracy.

## Current evidence

A measured same-model run must be written to:

`docs/benchmark/intelligence-preservation-current.json`

The CI gate intentionally fails if this file is missing. Contract tests passing are not evidence that intelligence was preserved.

## Diagnosis

- `SYSTEM_CORE_REGRESSION` — system/policy instruction collision
- `CONTEXT_CORE_REGRESSION` — context pollution/retrieval/authority problem
- `FULL_CORE_REGRESSION` — complete orchestration harms baseline reasoning
- `TOOL_REGRESSION` — tool overload/capability-selection regression
- `HALLUCINATION_REGRESSION` — orchestration increases unsupported output

## Non-negotiable rule

`MORE_AUTONOMY != MORE_INTELLIGENCE`

A V4/V5 milestone is not release-ready if autonomy improves while Bossman causes the same underlying model to lose more than the allowed baseline capability.
