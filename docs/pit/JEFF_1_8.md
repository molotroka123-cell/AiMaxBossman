# Jeff 1.8: models, voice and speed

Status: candidate on `feat/jeff-1.8` (from the 1.9 freeze line plus `feat/master-parser-2.0`).
Not merged, not measured on the owner host. Everything below is covered by tests with fakes
only (no Ollama, Telegram, network or voice binaries, no participant data). Source: sections 3.4,
3.5 and 5 of `docs/owner/JEFF_2_0_VISION.md`.

## 1. Free route by LIVE price, honest refusal

`JEFF_MODEL_ROUTE_SCHEMA = "bossman.pit.model-route/1"` (`bcc/pit/model_route.py`).

A remote chat model is a route only while the live OpenRouter catalog lists it with a known
price of exactly 0 for prompt AND completion, and its id is a `:free` id. The suffix alone is
never proof and an unknown price is not free (`route_verdict`: `not_listed`, `price_unknown`,
`price_positive`, `not_free_id`, `payment_required`).

* Prices are re-read at most every `PRICE_RECHECK_SECONDS` (120 s) on the next chat turn, not only
  every `catalog_refresh_seconds`. A flip to paid removes the route on that turn.
* Fail closed: if the live catalog cannot be read, remote routes are dropped (local routes stay).
* A provider answer "402 payment required" blocks that model for `PAYMENT_BLOCK_SECONDS` (1 h) even
  if the catalog still says 0/0.
* With no free route Jeff answers the honest `NO_MODEL_RU` text. There is no path to a paid route,
  Claude or OpenAI: `allow_paid=False` everywhere, the model list is the owner allowlist.
* `ParticipantRuntime.model_route_status()` and the heartbeat/`/api/jeff/health` field `route`
  show `free_remote`, `local`, `rejected` (model -> reason code), `refusal`
  (`no_free_route` / `catalog_unreachable`), `price_checked_age_s`, `paid_routes_allowed: false`.

Tests: `tests/test_jeff_1_8_route.py` (incl. price flip both ways, outage, 402, no fallthrough).

## 2. Local Ollama chat: no thinking, no empty answers, no leaked reasoning

* Native Ollama `/api/chat` always sends `think: false` (a caller cannot turn it on).
  Other local OpenAI-compatible runners use `LocalOpenAICompatChat`, which adds
  `reasoning_effort: "none"` and `chat_template_kwargs.enable_thinking=false` to every request.
* The `thinking` / `reasoning_content` fields are never read. Inline `<think>...</think>` in
  content is stripped (also chunk by chunk when streaming). A reply that is empty after
  stripping is an `EmptyAnswer`.
* The runtime routes every local attempt through `bcc/pit/resilient_chat.py` (reused from Master
  Parser 2.0): an empty answer means one unload (`keep_alive=0`) + one retry, shared across
  concurrent turns; still empty -> honest failure text, never an empty reply. The verdict is per
  turn (`ResilientChat.end_scope`), so a later turn is not stuck.

Tests: `tests/test_jeff_1_8_local.py`.

## 3. Speed: measured latency and live replies

* `ReplyMetrics` (`bcc/pit/latency.py`): time to first visible text (TTFT) and total, p50/p95 per
  route kind (local/remote). A non-streaming adapter records TTFT = total. Exposed as
  `reply_latency` in the heartbeat and `/api/jeff/health`.
* Streaming at adapter level: Ollama native NDJSON (`OllamaNativeChatAdapter._chat_streamed`) and the
  existing OpenAI-compatible SSE reader. Contract: `on_delta(text)` for visible text, `on_delta(None)`
  to discard what was shown (attempt failed, refused or is being retried).
* Jeff window: `POST /api/jeff/chat/stream` (SSE events `delta`, `reset`, `final`); `ui/jeff.js` renders
  the growing reply and falls back to `/api/jeff/chat` when the stream cannot be opened. The `final`
  event carries the authoritative reply that is saved once. STOP (`/api/jeff/stop` and the Stop
  button) cancels the turn; the stream ends with `final {stopped: true}` and no assistant message
  is saved.
* Telegram: progressive edit-in-place (`bcc/pit/reply_stream.py`, `Telegram.edit_message`). One draft
  message after the first ~40 characters, at most one live edit per 1.5 s (and >= 24 new chars),
  then ONE final edit with the rendered reply. The worker still delivers, logs and records the
  turn exactly once, under the draft's message id. Any failure degrades to the ordinary path: a
  failing preview stops previews; a failing final edit deletes the draft and sends once; replies
  over 3900 characters delete the draft and use the normal split send. Switch off with
  `BOSSMAN_JEFF_TELEGRAM_STREAM=0`. Voice-reply mode, commands, photos and documents never stream.
  The edit method reuses the send guards (identity, token scrub, egress guard, per-chat pacing).

Measured with fakes (test parameters: first token after 60 ms, 3 chunks 30 ms apart): TTFT 72 ms,
total 173 ms on the local route. These are harness numbers, not host latency; measure the real
route on the owner host with the heartbeat fields above.

Tests: `tests/test_jeff_1_8_stream.py`, `tests/test_jeff_1_8_web_stream.py`,
`tests/test_jeff_1_8_telegram_stream.py`.

## 4. Voice: pluggable TTS, CosyVoice 3 candidate slot

* `bcc/pit/tts_engines.py`: `TTSEngine` interface; `PiperEngine` is the default and the fallback;
  `CosyVoiceCandidate` is a slot. Nothing is downloaded or installed by Jeff.
* The candidate runs only when ALL hold: `BOSSMAN_JEFF_TTS_COSYVOICE=1` (feature flag),
  `BOSSMAN_JEFF_COSYVOICE_CMD` points to an owner-verified local command, and
  `BOSSMAN_JEFF_COSYVOICE_CONSENT` points to the checklist JSON with every item true. It is used
  first only if also selected with `BOSSMAN_JEFF_TTS_ENGINE=cosyvoice`; a failing candidate falls
  back to Piper (counted in `tts.latency.fallbacks`). It never becomes the default by itself.
* Command contract (no shell): `<cmd> --text-file <utf-8 txt> --out <mono PCM16 wav>`.
* Checklist and consent: `docs/pit/JEFF_1_8_VOICE_CHECKLIST.md`.
* Comparison harness: `python -m bcc.pit.voice_bench [--out report.json]` runs one phrase set
  (stress, numbers, questions, pauses) through every INSTALLED engine and reports latency p50/p95,
  RTF and, when local STT is available, WER. Engines that are not installed are listed and not
  measured. The report never declares a winner or an adoption (`adopted: false`): CosyVoice counts
  as adopted only after it spoke inside Jeff and the owner listened to it.

Tests: `tests/test_jeff_1_8_voice.py`.

## 5. STT/TTS latency in status

`speech.latency_snapshot("stt"|"tts")` (count, failures, last, p50, p95; TTS also `last_engine`,
`fallbacks`) is merged into `stt.latency` / `tts.latency` of the heartbeat snapshot and
`/api/jeff/health`. STOP and silence are not counted as slow or failed calls.

## Remaining gaps

* Remote (cloud) replies stream only as far as the provider streams; the OpenAI-compatible SSE
  reader exists but free models were not measured live.
* A Telegram draft left by an owner STOP or a crash keeps its cursor mark and partial text; it is
  not edited after STOP because STOP forbids further sends.
* The CosyVoice command contract is an adapter contract, not a verified integration: no binary or
  weights were installed or run. Adoption needs the checklist, an in-Jeff synthesis and the
  owner's listening.
* The gateway/registry free-only policy (`bcc/provider_governance.py`) still judges by the `:free`
  id and a positive price; the live zero-price proof lives in the PIT runtime.
* No real-host latency, WER or 24/48 h soak numbers yet.
