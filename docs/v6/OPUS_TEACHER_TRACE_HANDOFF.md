# Opus Handoff — V6 Teacher Trace Learning

Repository: `molotroka123-cell/AiMaxBossman`

Branch: `v6/teacher-traces-local-model-learning`

## Mission

Turn the initial `TeacherTraceRecorder` into the single production path by which difficult cloud-model runs create privacy-safe training/evaluation material for future local models.

Do not touch the V4/V5 freeze line while implementing this V6 work.

## Existing seed

- `command-center/bcc/v2/teacher_trace.py`
- `command-center/tests/test_v6_teacher_trace.py`
- `docs/v6/TEACHER_TRACE_LEARNING.md`

## Implement next

1. Find the canonical provider/model completion boundary used by production Command Center.
2. Add one bounded hook that emits a trace only after a run has a stable task/session identity.
3. Capture provider/model, compact prompt summary, compact decision summary, tool events, outcome and verification references.
4. Never persist hidden chain-of-thought. If the provider exposes reasoning, store only a short generated decision summary suitable for replay/training.
5. Redact secrets before persistence, including nested tool arguments/results.
6. Default `reusable=false`.
7. Add a curation function that can mark a trace reusable only when independent verification exists and no private-data flag is set.
8. Add immutable linkage between trace, task/run ID and verification evidence.
9. Add local-model replay scaffolding that runs the same task contract without teacher answers and scores tool selection, schema accuracy, outcome verification and recovery.
10. Keep actual fine-tuning/training as a separate explicit owner action; logging must never auto-train or auto-promote a model.

## Required tests

- secret in prompt summary is redacted
- secret nested in tool args/result is redacted
- private trace cannot be reusable
- unverified successful-looking trace is not exported
- verified clean trace is exported
- failed trace excluded by default
- append-only log survives restart
- duplicate terminal hooks are idempotent by run/trace identity
- teacher answer is not leaked into local replay prompt
- local replay result cannot overwrite teacher evidence
- provider failure still produces a failure trace when safe to do so
- recorder failure must not make the product action falsely succeed or fail; it should be observable and bounded

## Evening owner-test mode

Add a clearly visible setting such as `Teacher learning logs` with three states:

- OFF
- LOG ONLY
- LOG + MARK FOR REVIEW

Default should be `LOG ONLY` for V6 development builds, with `reusable=false` until explicit curation.

During Windows testing, prioritize traces from coding, browser/computer-use, Video Studio, memory, tool-calling, agent orchestration and recovery tasks.

## Metrics

Report per teacher model:

- number of hard tasks
- success rate
- independently verified rate
- tool-call accuracy
- correction count
- average steps
- cost/tokens when available
- reusable trace count
- local replay pass rate by model

## Conflict rules

Do not merge unrelated PR38/PR51/PR52 work into this branch unless required by the V6 base plan.
Do not change V4/V5 canary, AT-01, AT-03, Video CFR, PR26 or release ledgers from this branch.
Do not weaken permissions or verification to collect better-looking traces.

## Definition of done

A real cloud-model run through Bossman produces a safe durable trace; an independently verified clean trace can be curated into a dataset; a local model can replay the task without teacher leakage; all privacy/permission negative controls stay green.
