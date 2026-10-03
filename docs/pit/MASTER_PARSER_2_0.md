# Master Parser 2.0

Status: candidate in the owner bug-test worktree, branch
`claude/bossman-1.9-owner-bugtest-20260930` at base SHA `4b9049a097daf49e9fa6c3e8dedeb5f6a3591e33`.
Current changes are uncommitted and not merged. Everything below is covered by tests
with fake adapters only; Telegram export ingestion, Docling conversion, and real-data
behavior remain unverified.

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

## Local Telegram Desktop document attachments

The Master Parser can now ingest supported attachments from Telegram Desktop JSON
exports in its existing `master-parser/inbox` source. It follows each message's
relative `file` path, accepts `.txt`, `.md`, `.pdf`, `.docx`, `.pptx` and `.xlsx`,
and appends extracted text to that participant's message with an explicit
`untrusted data` marker. The source is never modified. Only already-known Jeff
participants are attributed; unsafe paths, symlinks, unsupported types and files
over 32 MiB are rejected and counted in the source stats. Extracted text is capped
at 20,000 characters. OOXML ZIP packages are checked before conversion for entry
count and expanded-size limits, encrypted entries, and traversal names.

PDF and Office conversion uses the optional open-source Docling adapter. Docling
is selected because one local converter provides structured document conversion
for PDF, DOCX, PPTX and XLSX and exports a common Markdown representation. Its
upstream project is MIT-licensed; review the pinned transitive dependency licenses
before redistributing a packaged runtime. The adapter is lazy and local-path-only.

The existing owner runtime already installs Docling through its `documents`
extra. Minimal installations can use `bossman-command-center[documents]` for
conversion, while the `master-parser-documents` extra adds local-model and CLI
support for PDF setup. DOCX/PPTX/XLSX conversion uses no PDF model weights and
does not need a model directory. PDF conversion requires pre-fetched model artifacts: run
`docling-tools models download` during setup and set `DOCLING_ARTIFACTS_PATH` to
the local models directory. PDF inference runs with Hugging Face offline flags
temporarily enabled and restores the process environment after conversion. The
adapter does not accept URLs or download weights during parsing. Without the
optional dependency, or without local PDF artifacts for a PDF, captions and
ordinary text messages remain ingestible while attachment extraction is reported
as unavailable. PDF model weights remain outside the standard Bossman runtime.
Plain `.txt` and `.md` files need no Docling.

This follows the existing source-reader seam (`document_parser: Path -> str`):
the Telegram export reader owns participant attribution, safe relative-path
validation, size/type limits and the untrusted-evidence marker; the adapter only
extracts text. A later parser can implement the same seam without coupling the
corpus engine to Docling. MarkItDown was reviewed as another MIT option, but the
current path needs layout-aware PDF and Office conversion from one local document
representation, which is Docling's documented focus. This is a parser choice,
not a claim that extraction is lossless or safe to treat as instructions.

The integration is covered by synthetic tests for extraction, path traversal,
unsupported formats, preservation of captions on extraction failure, bounded
Office archive handling, Office conversion without PDF models, and fail-closed/
offline PDF setup. A real Telegram Desktop export and owner-machine Docling
conversion have not yet been verified.

Upstream references: [Docling supported formats](https://github.com/docling-project/docling/blob/main/docs/usage/supported_formats.md),
[Docling offline usage](https://github.com/docling-project/docling/blob/main/docs/faq/index.md),
[Docling slim package and MIT license](https://github.com/docling-project/docling/blob/main/packages/docling-slim/README.md),
[MarkItDown](https://github.com/microsoft/markitdown).

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
