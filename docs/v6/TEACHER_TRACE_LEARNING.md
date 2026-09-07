# V6 Teacher Trace Learning Loop

## Goal

Every difficult task solved by a cloud teacher model can leave behind a compact, privacy-safe trace that Bossman can later use to evaluate, distill, fine-tune, or train local models.

This layer is post-freeze/V6 only. It must not modify V4/V5 authority, permissions, canary, rollback, execution-truth, or release evidence.

## What is recorded

For each eligible run:

- teacher provider and model
- session/task identity
- timestamp
- compact prompt summary
- compact decision summary
- observable tool calls/results
- final outcome
- verification evidence
- difficulty tags
- exact source-prompt SHA-256
- explicit `reusable` flag
- privacy flag

The recorder does **not** persist hidden chain-of-thought. Training material is based on concise decision summaries plus observable actions and verified outcomes.

## Privacy and safety

- redact common API keys, bearer tokens, passwords and secrets before disk write
- raw prompt content is not stored by the V1 recorder; only its hash and summary are stored
- traces marked `contains_private_data=true` can never become reusable training candidates
- export is explicit and fail-closed: only `reusable=true && contains_private_data=false`
- no automatic fine-tune, model replacement or promotion occurs from logging alone
- no secret-bearing trace may enter a training dataset
- append-only run logs are distinct from curated training datasets

## Runtime flow

```text
Cloud model solves a hard task
        |
        v
TeacherTraceRecorder
        |
        +--> append-only teacher_traces.jsonl
        |
        v
verification / outcome arrives
        |
        v
curation gate
        |
        +--> reject private / failed / low-quality / unverifiable traces
        |
        v
training_candidates.jsonl
        |
        +--> evaluation/replay
        +--> distillation dataset
        +--> optional LoRA/SFT/DPO later
        +--> skill/router training
```

## Recommended evening Windows acceptance logging

During owner testing, enable trace collection for cloud-model runs that perform substantial work. Prefer difficult tasks such as:

1. multi-step repository debugging
2. browser research + action
3. local computer control
4. code generation + test + repair loops
5. Video Studio workflows
6. tool selection with multiple candidate tools
7. long-context memory retrieval
8. tasks where the first plan fails and the model recovers
9. agent orchestration / delegation
10. real tasks later intended for local-model autonomy

Mark a trace reusable only after its result has been independently verified.

## Quality labels for future curation

Each trace should eventually receive:

- success / partial / failure
- verification strength
- human correction count
- tool-call accuracy
- number of steps
- recovery-from-error flag
- latency and token cost
- task family
- required capability set
- local-model replay result

High-value teacher examples are not simply the longest traces. Prefer verified, efficient solutions to difficult tasks.

## Local-model training stages

### Stage 1 — Replay benchmark

Run the local model against the same task contracts without showing the teacher answer. Compare outcome/evidence, tool selection, argument correctness and recovery behavior.

### Stage 2 — Distillation

Use clean teacher traces to teach compact decision patterns and correct tool trajectories. Keep final verification as the authority; do not train the local model to imitate unverified mistakes.

### Stage 3 — Skill specialization

Build task-family datasets for coding, browser control, computer use, Video Studio, research, memory and orchestration.

### Stage 4 — Promotion gate

A locally trained candidate must beat its own pre-training baseline on held-out tasks and preserve safety/tool-boundary tests before it is promoted.

## Acceptance criteria

V6 teacher-learning is considered implemented only when:

- cloud provider calls emit traces through a single production hook
- secret/private negative controls pass
- a verified run can be exported as a training candidate
- a private run cannot be exported
- failed/unverified traces are excluded by default
- replay can compare a local model with the teacher task without leaking the teacher answer
- training/promotion remains a separate explicit operation
- the original raw run/evidence remains immutable
