# Master Parser 2.0 audit

Scope: `command-center/bcc/pit/master_parser/*`, `resilient_chat.py`, `ollama_native.py`,
`passport_checkpoint.py`. Method: code reading plus tests with synthetic corpora and fake
adapters. No real participant data was read, and no real model was called.

## Bugs found and fixed

| # | Bug | Fix | Test |
|---|-----|-----|------|
| 1 | Degraded Ollama runner returns empty answers (`eval_count=1`); all calls failed as `ProviderError`, checkpoint 8/8 `MODEL_ERROR` in 1.85 s | `EmptyAnswer` (retryable), one unload+retry per participant and phase, shared-runner epoch so concurrent participants do not unload twice, honest status `LLM_FAILED`/`EMPTY_ANSWER`; checkpoint uses the same `ResilientChat` | `test_native_adapter_*`, `test_empty_answer_is_recovered_*`, `test_persistent_empty_answer_*`, `test_checkpoint_recovers_*` |
| 2 | Checkpoint summarised stored facts only | narrative phase; checkpoint rows carry the narrative and a participant with no facts but a narrative becomes `DRAFT_REVIEW` | `test_checkpoint_gets_the_narrative_*` |
| 3 | Failed batches: re-queue semantics unverified; one participant exception aborted the whole `gather`; `messages_pending` counted non-admitted participants; a failed batch left the row `OK` | verified failed batches are never in `analyzed` (re-sent next run); per-participant guard (`ERROR`); pending 0 when not admitted; `requeued` and `PARTIAL`/`LLM_FAILED` statuses | `test_failed_batch_is_requeued_*`, `test_one_participant_crash_*` |

## Consent gates

Analysis and narrative both require `sink.admission` (memory consent, owner blocklist). Non-admitted
participants get no model call and no narrative (`NO_MEMORY_CONSENT`, `BLOCKED`); tested with a
non-consenting participant whose text never appears in any prompt.

## Isolation between participants

`Corpus.timeline(person_key)` is the only read used for a narrative; every prompt and every
`ResilientChat` scope is one participant (`<key>`, `<key>|narrative`, `<key>|checkpoint`).
Narrative files are named by person key and are never merged. Residual: `latest.json` (the
owner checkpoint) lists all participants' rows side by side, as before 2.0; it is an owner-only file.

## Provenance

Narrative record: participant/total message counts, period, chunk count, merge levels, LLM calls,
run id, model. Facts keep their corpus-uid evidence as before. The narrative is not a fact and
does not enter the passport facts.

## No cloud path

The narrative calls `route.resilient().chat` (local adapter) directly; `ModelRoute.complete`
(the only cloud fallback) is not used. Tested with remote consent on, cloud key configured and
the local model failing: zero cloud calls. The `--profile uncensored` preset also sets `--no-cloud`.
Fact extraction still has its 1.x optional free-cloud fallback, unchanged.

## Secret filter and content rules

Every message is passed through `redact_secrets` before it reaches a narrative prompt (tested).
Output is checked in code: clinical/diagnostic stems and sensitive-category stems the participant
did not write are rejected after one bounded rewrite and nothing is saved (tested). Weakness: the
stem lists are short heuristics, not a classifier; the wording rule in the prompt is the main guard
and the model is not perfectly obedient. Paragraph 2 is always prefixed as an inference when the
model did not mark it.

## Failure modes

- Model unreachable/timeouts: not retried (only empty answers are); status `FAILED`/`LLM_FAILED`,
  messages remain queued, exit code 4/5.
- Runner stays empty after unload: the participant is marked degraded; further calls for that
  participant fail fast (no call storm); the next participant still gets its own recovery.
- Model returns invalid JSON: the batch stays queued (no rewrite in facts phase); narrative gets one rewrite.
- `unload` failing is counted (`unload_errors`) and the retry still happens.
- Very large corpora: many chunk calls; merge levels are capped at 4. Not measured.

## Measured vs not measured

Measured (fake adapters): logic, statuses, isolation, redaction, limits, report shape, exit codes,
speed-report sanitisation (full test suite subset: 754 passed, 1 skipped for pit/parse/jeff/companion).
Not measured: real Qwen3.6-35B-A3B latency, real narrative quality and paragraph length compliance,
real Ollama unload/reload behaviour and its timing (`settle` default 0.5 s is a guess), throughput
of a real corpus. All speed numbers in the tests come from fake models and mean nothing. The
integrator's first real run produces the first real numbers.
