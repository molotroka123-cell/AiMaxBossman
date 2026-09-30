# Jeff 2.0 - Media Understanding (module 7, order 70)

File: `command-center/bcc/pit/j2/media.py`, tests: `command-center/tests/test_jeff_2_media.py`.

## What it does

Photos and documents are understood by the LOCAL vision model, reached only through an injected async callable
`vision(data, mime, prompt) -> str` (default: `runtime.photo_services.vision.analyze_fast`). No cloud model is used.
An attachment arrives as `ctx.extra["attachment"] = {"name", "mime", "data", "caption"}` (bytes stay in memory).

* Validation first: the real type comes from magic bytes (JPEG, PNG, WebP, PDF, plain text); the sender's mime and
  the file extension are ignored; GIF, executables, archives and binary blobs are refused. Limits: 10 MiB images,
  2 MiB text, 20 MiB PDF, at most 3 PDF pages, vision call 20 s, whole job 45 s.
* Safe names: the original name is a display label only (`sanitize_filename`: no directories, control or bidi
  characters, reserved Windows names, bounded length). Stored files are named from the content hash.
* Captioning and OCR-style extraction: the participant's own words choose the prompt («опиши» / «распознай текст»,
  both if both are asked). Text files are read directly, PDFs need an injected `render_pdf(data, pages) -> [png]`
  and otherwise say so honestly.
* Model output is untrusted data: control characters and instruction-like sentences are stripped (and reported),
  secrets are redacted, and the same text is never fed back to the model as instructions (`augment` marks it as data).
* Degradation: no backend, an error, a timeout or an open breaker (3 failures, 60 s cool-down) give a plain reply
  and store nothing. Exceptions from the callable are dropped without their message, so a payload cannot reach logs;
  `Attachment.data` is excluded from `repr`; `status()` holds counters only.
* Hooks are short, so analysis is a background task: `pre_route` waits 0.3 s and otherwise replies "смотрю файл";
  «что на фото» returns the result later.

## Consent gate (nothing is stored silently)

After an analysis Jeff replies with the description and asks «Запомнить это описание? (да/нет)». Only «да»
writes, and only the derived text note (`media/j2/notes.jsonl`, at most 200 notes, audited without content). The
original file is stored (`media/j2/files/<hash>.<ext>`) only if the same message asked to save it («сохрани файл»)
and the participant then answers «да». Notes with sensitive visual inference (health, religion, politics, address,
ethnicity, ...) or secrets are never stored. Offers live 5 minutes. Memory off, `/pause_memory` and
`/revoke_consent` drop pending offers and recent results at once and hide stored notes; «удали мои файлы» deletes the
whole `media/j2` folder.

## Isolation

Every structure is keyed by `person_key`; the result cache is `(person_key, sha256, mode)`, so one participant's
image never yields a hit for another. Recent results are kept in RAM for 10 minutes.

## Known gaps

The chat route does not yet put the incoming photo/document into `ctx.extra["attachment"]`; the existing
`photo_pipeline` still handles Telegram photos, so this module is exercised through the pipeline contract and its own
API until that wiring is added. A real PDF renderer is not bundled (injected). Scanned-document layout (tables) is
plain text only.
