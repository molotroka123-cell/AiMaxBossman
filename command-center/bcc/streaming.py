"""B4 — one canonical parser for OpenAI-compatible streaming responses.

Cloud QA reported streaming probes failing on both a paid model (GLM 5.3) and a
free one, with the same symptom: `0 chunks`. The transport was fine. The reader
was too narrow — it accepted exactly one shape of frame:

    line.startswith("data:") -> json -> choices[0].delta.content

Everything else in a real SSE stream fell through that filter silently and was
reported as "streaming does not work":

  * a reasoning model puts its text in `delta.reasoning` /
    `delta.reasoning_content` and leaves `delta.content` empty or null. GLM 5.3
    streams exactly like this, which is why a model that streams perfectly well
    probed as 0 chunks;
  * `delta.content` may be a LIST of typed parts, not a string;
  * tool-call streams carry `delta.tool_calls` and no text at all — a stream
    that works and produces nothing the old reader could see;
  * some providers send the whole turn as `choices[0].message.content` in a
    final frame instead of deltas;
  * SSE permits several `data:` lines per event, which must be joined with a
    newline before parsing, and comment lines (`: OPENROUTER PROCESSING`
    keepalives) which must be skipped without ending the stream;
  * an in-stream `{"error": {...}}` frame is a PROVIDER failure — a distinct
    thing from "this model cannot stream", and reporting them as one number
    sent routing the wrong signal;
  * the terminal `usage` frame must be read exactly once, or a streamed run
    double-counts its tokens against the budget.

`parse_sse_stream` handles all of the above and returns a `StreamOutcome`
carrying a classification the router can act on:

    stream_supported  text (or tool calls) arrived incrementally
    stream_degraded   the response completed, but not as a stream: a single
                      frame, or only a non-delta message body. Usable, but the
                      caller must not promise incremental delivery.
    stream_failed     the stream opened and produced nothing usable
    provider_failed   the provider refused or errored — NOT a statement about
                      the model's streaming capability

The distinction between the last two is the point of the classification: one
means "do not stream from this model", the other means "this provider is
unwell right now" and must not be recorded as a permanent model property.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable

#: A stream that has produced nothing after this long is not slow, it is stuck.
#: Separate from the total budget: a provider that accepts the connection and
#: then goes silent is the failure mode a total timeout catches far too late.
DEFAULT_FIRST_BYTE_TIMEOUT = 20.0
DEFAULT_TOTAL_TIMEOUT = 120.0

SUPPORTED = "stream_supported"
DEGRADED = "stream_degraded"
FAILED = "stream_failed"
PROVIDER_FAILED = "provider_failed"

# --- completion: did THIS response arrive whole? -------------------------------
#
# `status` says what the stream tells the router about the MODEL (does it
# stream?). `completion` says what it tells the caller about THIS ANSWER, and
# the two were conflated: Astra/Codex F3 (2026-09-08) — HTTP 200, one content
# delta, then `{broken-json`, EOF, no finish_reason, no [DONE] — came back as
# `stream_degraded`, `ok=True`. A truncated, corrupted answer advertised as
# usable. The protocol defines a successful terminal state (a finish_reason
# and/or the `[DONE]` sentinel); anything short of it is not "complete".
COMPLETE = "complete"            # terminal state observed, no corrupt frames
CAPPED = "capped"                # the CALLER stopped reading at max_chunks
PARTIAL = "partial"              # content, then EOF before any terminal marker
MALFORMED = "malformed"          # a data frame was not JSON: content may be missing
TIMEOUT = "timeout"              # transport timed out (first byte or total)
SILENT = "silent"                # 2xx, frames, nothing usable in them
RATE_LIMITED = "rate_limited"    # 429 / quota / credit — a provider condition
PROVIDER_ERROR = "provider_error"  # every other provider/transport failure

#: Completions after which `text` may be handed on as the model's answer.
USABLE_COMPLETIONS = frozenset({COMPLETE, CAPPED})

_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "quota", "too many requests",
                       "insufficient", "credit", "402")


@dataclass(slots=True)
class StreamOutcome:
    status: str
    deltas: list[str] = field(default_factory=list)
    tool_call_frames: int = 0
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    error: str = ""
    frames: int = 0                 # data frames seen, including empty ones
    detail: str = ""
    completion: str = ""            # one of the completion constants above
    terminated: bool = False        # finish_reason or [DONE] observed
    malformed_frames: int = 0       # data payloads that were not JSON
    capped: bool = False            # stopped by the caller's max_chunks

    @property
    def text(self) -> str:
        return "".join(self.deltas)

    @property
    def ok(self) -> bool:
        """Did the caller get a usable, WHOLE answer? `degraded` counts (the
        content arrived, just not incrementally); a partial or corrupted
        response does not, whatever its `status` says about the model."""
        return self.status in (SUPPORTED, DEGRADED) and self.completion in USABLE_COMPLETIONS


def _content_text(value: Any) -> str:
    """Text out of a content field that may be a string, null, or a list of
    typed parts (`[{"type": "text", "text": "..."}]`)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for part in value:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif isinstance(part.get("content"), str):
                    out.append(part["content"])
        return "".join(out)
    return ""


#: Where providers put streamed text. `content` first so a model that fills
#: both is not double-counted; reasoning keys only carry text when `content`
#: is empty for that frame, which is exactly the GLM 5.3 shape.
_TEXT_KEYS = ("content", "reasoning_content", "reasoning")


def _delta_text(delta: dict[str, Any]) -> str:
    for key in _TEXT_KEYS:
        text = _content_text(delta.get(key))
        if text:
            return text
    return ""


def frame_error(chunk: dict[str, Any]) -> str:
    """A provider error delivered inside the stream body rather than as a
    status code. Returns "" when the frame is not an error."""
    err = chunk.get("error")
    if isinstance(err, dict):
        message = err.get("message") or err.get("code") or "provider error"
        return str(message)[:300]
    if isinstance(err, str) and err:
        return err[:300]
    return ""


def iter_sse_events(lines: Iterable[str]) -> Iterable[str]:
    """SSE line stream -> event data payloads.

    Implements the parts of the SSE grammar providers actually use: comment
    lines are skipped (keepalives, `: OPENROUTER PROCESSING`), several `data:`
    lines in one event are joined with a newline, and a blank line ends the
    event. A `[DONE]` payload terminates the stream.

    The `[DONE]` sentinel IS yielded (as the literal payload "[DONE]") and then
    the iterator stops: the folder needs to see it to know the stream ended the
    way the protocol says a stream ends, rather than at an EOF.
    """
    for payload in _SseLines().feed_all(lines):
        yield payload
        if payload.strip() == DONE:
            return


DONE = "[DONE]"


class _SseLines:
    """Line -> event-payload state machine, shared by the sync and async readers."""

    def __init__(self) -> None:
        self.buffer: list[str] = []

    def feed(self, raw: Any) -> str | None:
        """One wire line in; a complete event payload out, or None."""
        line = raw.rstrip("\r\n") if isinstance(raw, str) else ""
        if line.startswith(":"):
            return None                               # comment / keepalive
        if line.strip() == "":
            if self.buffer:
                payload = "\n".join(self.buffer)
                self.buffer = []
                return payload
            return None
        if line.startswith("data:"):
            self.buffer.append(line[5:].lstrip())
        # Any other SSE field (event:, id:, retry:) carries no chat payload.
        return None

    def flush(self) -> str | None:
        if not self.buffer:
            return None
        payload = "\n".join(self.buffer)
        self.buffer = []
        return payload

    def feed_all(self, lines: Iterable[str]) -> Iterable[str]:
        for raw in lines:
            payload = self.feed(raw)
            if payload is not None:
                yield payload
        tail = self.flush()
        if tail is not None:
            yield tail


class _Folder:
    """Fold event payloads into a StreamOutcome ONE AT A TIME.

    `feed` returns True when the caller must stop reading: the cap is reached
    (Astra/Codex F2 — `max_chunks` used to be applied after the whole upstream
    stream had been buffered, so a cap of 2 still consumed 100 frames), the
    protocol terminated, or the provider reported an error."""

    def __init__(self, max_chunks: int = 0) -> None:
        self.outcome = StreamOutcome(status=FAILED)
        self.message_text = ""
        self.max_chunks = max(0, int(max_chunks))

    def feed(self, payload: str) -> bool:
        out = self.outcome
        body = payload.strip()
        if not body:
            return False
        if body == DONE:
            out.terminated = True
            return True
        try:
            chunk = json.loads(body)
        except json.JSONDecodeError:
            out.malformed_frames += 1
            out.detail = out.detail or f"non-JSON frame: {body[:80]}"
            return False
        if not isinstance(chunk, dict):
            out.malformed_frames += 1
            out.detail = out.detail or f"non-object frame: {body[:80]}"
            return False
        out.frames += 1
        error = frame_error(chunk)
        if error:
            # A provider error ends the stream and is NOT evidence about the
            # model's streaming capability.
            out.status = PROVIDER_FAILED
            out.error = error
            return True
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            # Read once. A streamed run that folded usage from every frame
            # would bill the same tokens repeatedly against the budget.
            out.usage = dict(usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return False
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason"):
            out.finish_reason = str(choice["finish_reason"])
            out.terminated = True
        delta = choice.get("delta")
        if isinstance(delta, dict):
            text = _delta_text(delta)
            if text:
                out.deltas.append(text)
            if delta.get("tool_calls"):
                out.tool_call_frames += 1
        message = choice.get("message")
        if isinstance(message, dict):
            # Non-delta body: the provider answered in one piece.
            self.message_text = self.message_text or _content_text(message.get("content"))
            if message.get("tool_calls"):
                out.tool_call_frames += 1
        if self.max_chunks and len(out.deltas) >= self.max_chunks:
            out.capped = True
            return True
        return False

    def finish(self) -> StreamOutcome:
        return _classify(self.outcome, self.message_text)


def parse_frames(payloads: Iterable[str], *, max_chunks: int = 0) -> StreamOutcome:
    """Fold SSE data payloads into one outcome. Pure and synchronous, so the
    provider variants can be tested from recorded fixtures without a network.
    Stops consuming `payloads` at the cap or at the terminal marker."""
    folder = _Folder(max_chunks)
    for payload in payloads:
        if folder.feed(payload):
            break
    return folder.finish()


def _completion(outcome: StreamOutcome, message_text: str) -> str:
    if outcome.status == PROVIDER_FAILED:
        text = (outcome.error or "").lower()
        return RATE_LIMITED if any(m in text for m in _RATE_LIMIT_MARKERS) else PROVIDER_ERROR
    if not (outcome.deltas or outcome.tool_call_frames or message_text):
        return SILENT
    if outcome.capped:
        return CAPPED
    if outcome.malformed_frames:
        return MALFORMED
    if outcome.terminated:
        return COMPLETE
    if message_text and not outcome.deltas:
        # A non-delta message body is one whole JSON object: complete by shape,
        # even when the provider sent no finish_reason around it.
        return COMPLETE
    return PARTIAL


def _classify(outcome: StreamOutcome, message_text: str) -> StreamOutcome:
    outcome.completion = _completion(outcome, message_text)
    if outcome.status == PROVIDER_FAILED:
        return outcome
    incremental = len(outcome.deltas) > 1 or outcome.tool_call_frames > 1
    if outcome.deltas or outcome.tool_call_frames:
        # One delta frame is a complete answer delivered in a single piece: the
        # content is usable, but calling it "streaming" would promise the caller
        # an incremental delivery it will not get.
        outcome.status = SUPPORTED if incremental else DEGRADED
        outcome.detail = outcome.detail or (
            f"{len(outcome.deltas)} delta(s), {outcome.tool_call_frames} tool frame(s)")
        if outcome.completion not in USABLE_COMPLETIONS:
            outcome.detail = f"{outcome.completion}: {outcome.detail}"
        return outcome
    if message_text:
        outcome.status = DEGRADED
        outcome.deltas = [message_text]
        outcome.detail = "provider answered with a non-delta message body"
        return outcome
    outcome.status = FAILED
    outcome.detail = outcome.detail or (
        f"{outcome.frames} frame(s) carried no text and no tool calls"
        if outcome.frames else "stream produced no frames")
    return outcome


async def read_stream(lines: AsyncIterator[str], *, max_chunks: int = 0) -> StreamOutcome:
    """Async twin of `parse_frames` over a LIVE line iterator.

    Folds each event as it arrives and stops pulling from `lines` the moment
    the cap, the terminal marker or a provider error is reached — so
    `max_chunks` bounds what is read from the provider, not merely what is
    returned (F2)."""
    sse = _SseLines()
    folder = _Folder(max_chunks)
    stopped = False
    async for raw in lines:
        payload = sse.feed(raw)
        if payload is not None and folder.feed(payload):
            stopped = True
            break
    if not stopped:
        tail = sse.flush()
        if tail is not None:
            folder.feed(tail)
    return folder.finish()


def outcome_from_exception(exc: BaseException) -> StreamOutcome:
    """Transport-level failure. Always `provider_failed`: a refused connection,
    a 500 or a timeout says nothing about whether the model can stream, and
    recording it as a model property is how a healthy model gets blacklisted.
    The completion distinguishes a TIMEOUT (nothing arrived in time) from the
    other transport errors so the caller can act on the difference."""
    name = type(exc).__name__
    is_timeout = "timeout" in name.lower() or "timed out" in str(exc).lower()
    return StreamOutcome(status=PROVIDER_FAILED,
                         error=f"{name}: {exc}"[:300],
                         detail="transport failure before or during the stream",
                         completion=TIMEOUT if is_timeout else PROVIDER_ERROR)
