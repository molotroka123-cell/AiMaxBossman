# Master Parser 2.0

Status: candidate on `feat/master-parser-2.0` (from the 1.9 freeze line). Not merged, not
measured on real data. Everything below is covered by tests with fake adapters only.

## What it does

`bossman pit master-parse` collects every Jeff conversation into one append-only corpus and
updates passports (unchanged from 1.x). 2.0 adds, per consenting participant:

1. **Empty-answer recovery.** A long-lived Ollama runner can start answering every
   `/api/chat` with an empty text (`eval_count<=1`). That is now a retryable `EmptyAnswer`,
   never a success. Recovery is bounded: one unload (`POST /api/generate {"keep_alive":0}`)
   and one retry per participant and phase; if the retry is empty too the participant is
   reported honestly (`LLM_FAILED` / `PARTIAL`) and the messages stay queued. The passport
   checkpoint goes through the same route (`bcc/pit/resilient_chat.py`).
2. **Narrative.** Map-reduce over the participant's whole corpus in time order: chunks by the
   char budget -> chunk notes -> (merge levels if needed) -> final JSON with exactly two
   Russian paragraphs, each at most 450 characters: «Контекст общения» and «Личность и манера»
   (marked as an inference; no clinical wording; no sensitive-category guess the participant
   did not write themselves). Provenance: message counts, period, chunk count. Saved to
   `<pit-v1.7>/passport-checkpoints/narratives/<person_key>.json` (one file per participant).
   The checkpoint (`latest.json`) now also carries each participant's narrative.
3. **Speed report.** Per participant and overall: messages, chars, LLM calls, empty answers and
   recoveries, seconds per phase (collect, map, reduce, write), messages/s, chars/s, p50/p95
   LLM latency, time to first paragraph, delivery (write) time. Also a sanitized
   `speed_report_public.json` (participants P1..Pn, no ids, labels, text or dates).
4. **Requeue semantics.** A failed batch is never marked analysed; its messages are counted as
   `requeued` and are picked up by the next run. A crash inside one participant no longer
   aborts the others (`status: ERROR`).

## Privacy

- Consent-gated: only participants with memory consent are analysed and narrated.
- One participant per prompt; a narrative never sees another participant's text.
- Local model only for the narrative (no cloud route exists in that code path, even when a
  participant allowed remote processing); tests assert the cloud adapter is never called.
- Secrets are redacted (`redact_secrets`) before text reaches the model.
- Narrative text is stored only in the participant's own owner-only file and in the owner's
  checkpoint; never in the run report, the speed report or logs.
- Tests use synthetic corpora and fake models. Real data was not read.

## How to run

Owner profile (local uncensored Qwen, no cloud, concurrency 1):

```
bossman pit master-parse --profile uncensored --speed-report <PATH-OUTSIDE-THE-REPO>\speed.json
```

Flags: `--narrative/--no-narrative` (default on), `--profile uncensored` (model
`bossman-community-qwen-uncensored:latest`, `--no-cloud`, concurrency 1), `--speed-report PATH`
(full report at PATH, sanitized copy `speed_report_public.json` beside it), `--participant`,
`--dry-run` (no model narrative, nothing written), `--no-llm`, `--no-checkpoint`, `--status`
(phase shows `narrative` while paragraphs are written).

Exit codes: 0 all work done; 2 PIT not configured / bad argument; 3 already running; 4 partial
(some participant failed, was re-queued or got no paragraphs); 5 nothing succeeded. The
`--speed-report` full file names participants by hash label: keep it outside the repository;
only `speed_report_public.json` is meant to be committed.

## Revert

- Facts of one run: `bossman pit master-parse --revert <run_id>` (unchanged).
- Narratives are derived files: delete `passport-checkpoints/narratives/<person_key>.json`
  (or the whole folder). They are regenerated on the next run.
- Roll back the feature: do not merge the branch; the `--no-narrative` flag disables the phase.
