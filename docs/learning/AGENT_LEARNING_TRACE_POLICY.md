# Agent Learning Trace Policy

Bossman accumulates engineering knowledge from every strong model that works on the
repository: Fable, Opus, GLM, OpenAI, Gemini, local Qwen and future Bossman agents.

Every meaningful coding, debugging, security, architecture or operational task MUST
produce a learning trace — a concise, explicit engineering decision record, not a diary
and not hidden chain-of-thought.

## What a trace records

| Field | Meaning |
|---|---|
| symptom / reproduction / evidence | what failed, how it was reproduced, what was observed (commands, outputs, hashes, test names) |
| root_cause_hypotheses / rejected_hypotheses | every plausible cause considered and the *evidence* that eliminated the wrong ones |
| root_cause / relevant_code_paths | the mechanism, with file:function anchors |
| fix_strategy / alternatives_considered / why_this_fix | the change, the tempting alternatives, why they lost |
| tests_added / original_repro_result / adversarial_variants / regression_result | how the fix was attacked and what the suites said |
| external_verification / verified_by | fresh, independent proof (never the same agent's own claim) |
| failure_recovery_lessons / generalizable_lessons / teach_local_model | what transfers to the next bug |
| confidence / limitations | calibrated, with explicit gaps (`NOT_TESTED_ON_THIS_HOST`, `BLOCKED_ENV`) |
| tags {domain, bug_class, component, severity, security_boundary} / outcome / finding_ids | retrieval filters and provenance |

Schema: `schemas/learning_fix_case.schema.json` (`additionalProperties: false`; the
fields `chain_of_thought`, `hidden_reasoning`, `thoughts`, `scratchpad`, `raw_reasoning`,
`private_reasoning` are rejected by validation).

## What is never stored

- hidden chain-of-thought or private reasoning transcripts;
- secrets, tokens, passwords, credentials (the store redacts Bearer/api_key/token-like
  values and the synthetic canary `BOSSMAN_TEST_SECRET_*`, and validation refuses residues);
- raw personal data;
- speculation presented as fact — hypotheses go into `root_cause_hypotheses`, facts into
  `evidence`.

## Statuses and corpora

`VERIFIED | FAILED_EXPERIMENT | PARTIAL | UNVERIFIED | REJECTED`

- `data/learning/fix_cases.jsonl` — **only VERIFIED**. This is the canonical retrieval and
  (future) training corpus.
- `data/learning/failed_experiments.jsonl` — everything else, status preserved. Negative
  knowledge is valuable, but retrieval returns it only on explicit request and always with a
  `retrieval_warning`; it must never be presented as preferred production behaviour.
- `docs/learning/fix_logs/<TASK_ID>.md` — the human-readable card rendered from the record.

## Promotion invariant

No case becomes authoritative because the same model says it succeeded.
`VERIFIED` requires: non-empty `evidence`, a non-empty `external_verification`, and at
least one verifier in `verified_by` that is not the record's own agent/model
(`pytest`, a named independent model, a human, a fresh-observation verifier).
Where an objective effect exists, the verification must be a fresh observation
(file reopened, row re-queried, page re-snapshotted, PoC re-run) — see
`command-center/bcc/v2/verification.py` for the runtime equivalent (F-012).

## Tooling

```
python -m learning.trace validate case.json
python -m learning.trace add case.json           # redacts, validates, routes by status, renders md
python -m learning.trace retrieve --finding_id F-001 --domain security
python -m learning.trace retrieve --include-failed --text "asyncpg"   # negative knowledge, warned
```

`learning.LearningStore.retrieve(...)` is the programmatic API; `compact(case)` returns the
context-injection form (lessons, evidence, provenance) for local models.

## Wiring into agents

- Deep Fix Mode (`bossman-core/bossman/deep_fix.py`, flag OFF) emits a record
  automatically at `LEARNING_RECORDED`; the coder agent cannot set `VERIFIED` — only the
  independent verifier stage can.
- Task templates for strong models (session packs / master prompts) must end with
  "write the learning trace, validate it, commit it with the fix".
- Sub-agents report the same fields in their final message; the lead validates and stores.

## Handoff packet (token economy, idea F7.9)

Before escalating a task to a stronger model or a new session, produce the packet with tools,
not prose:

```
python tools/context_slice.py map <app_root>                 # once per commit (cached by sha)
python tools/context_slice.py slice <app_root> <failing_test> # hashed manifest, depth 2
python -m learning.trace retrieve --finding_id <F-xxx> --limit 3   # verified cases + rejected fixes
```

The packet = failing test + slice manifest (file@sha256) + evidence ledger so far + surviving
hypotheses + retrieved VERIFIED cases (compact form). The receiving model starts at the frontier
and never re-discovers; a stale manifest hash means re-slice before editing.
