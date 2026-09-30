# Intelligence Preservation Gate

Bossman must not gain autonomy by making the underlying model worse at reasoning, coding, structured output, tool choice, or adaptation.

The gate compares the **same model** on the **same benchmark items** in four lanes:

1. `raw` — user task -> model
2. `system` — user task -> Bossman system policy -> model
3. `context` — system + Bossman context compiler/retrieval, without the full tool surface
4. `full` — complete Bossman orchestration with relevant tools/skills/router enabled

Missing lanes, metrics, invalid scores, or too few samples are `INSUFFICIENT_EVIDENCE`, never PASS.

## What makes a payload evidence (gate version 2)

- `model`, `dataset_id` and a 40-hex `evaluated_sha` are required. A lane that
  declares its own `model`/`dataset_id`/`model_version`/`quantization`/`decoding`
  must agree with the payload: one model, one configuration, one item set.
- CI passes `--expect-sha` with the commit under test; a payload measured on
  another commit is `INSUFFICIENT_EVIDENCE` (`OLD_SHA_PASS != CURRENT_SHA_PASS`).
- Every metric must report the same `samples` in every lane (paired items).
- 98% retention is a relative ratio and a non-inferiority claim. The gate reports a
  conservative lower confidence bound (default 95%) on core retention. A point
  estimate above the bar with a bound below it is `INSUFFICIENT_EVIDENCE`, not
  PASS. A genuine paired runner can report per-metric discordance versus the
  baseline lane, `"paired": {"lost": b, "gained": c}`, which tightens the bound;
  without it the unpaired bound is used, and 1000 items per metric are not enough
  to prove a 2% margin. Twenty observations per metric is a smoke-test floor.
- Each core metric is checked on its own (`CORE_METRIC_REGRESSION`); a regression
  cannot hide inside the core mean.
- Secondary metrics (long context, memory retrieval, computer-use planning, task
  completion) need the sample floor and are reported with their full/raw
  retention; a drop is a `SECONDARY_REGRESSION` WARNING, never silent.
- Tool and hallucination rules are point-estimate rules ("must not regress");
  their intervals are informational.

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

A measured same-model run must remain outside Git on the owner's PC. A redacted
summary can be attached as an owner-authored commit comment on the **same** SHA;
CI fetches it and runs this unchanged gate. Without that comment, CI fails
closed. Committing the measurement into the tree cannot satisfy `--expect-sha`,
because the commit changes the SHA. See `INTELLIGENCE_MEASUREMENT.md` for the
private-evidence and comment procedure. Contract tests passing are not evidence
that intelligence was preserved.

### How the evidence is produced

Run `tools/intelligence_preservation_run.py` on the exact source SHA with the
owner's local model and an independently reviewed, sufficient corpus. Keep the
full report outside Git. `tools/intelligence_evidence_transport.py prepare`
checks the paired rows and gate, then prepares a redacted commit-comment
request. The owner reviews and posts it on the same SHA. CI fetches the comment
and runs the unchanged gate. See `INTELLIGENCE_MEASUREMENT.md` for the exact
procedure and its privacy limits.

### Why the gate is still red in CI

The measurement needs the SAME model the owner runs, over the same items, in
four lanes, with roughly a thousand paired items per metric to clear the 2%
non-inferiority margin. The build environment has no model credentials and no
local weights, so the run cannot happen there, and no runner output may be
synthesised. `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE` is the honest
state until the owner executes the runner against a real model and supplies an
exact-SHA redacted summary. The owner's local machine is the intended place for
measurement.

## Diagnosis

- `SYSTEM_CORE_REGRESSION` — system/policy instruction collision
- `CONTEXT_CORE_REGRESSION` — context pollution/retrieval/authority problem
- `FULL_CORE_REGRESSION` — complete orchestration harms baseline reasoning
- `TOOL_REGRESSION` — tool overload/capability-selection regression
- `HALLUCINATION_REGRESSION` — orchestration increases unsupported output

## Non-negotiable rule

`MORE_AUTONOMY != MORE_INTELLIGENCE`

A V4/V5 milestone is not release-ready if autonomy improves while Bossman causes the same underlying model to lose more than the allowed baseline capability.
