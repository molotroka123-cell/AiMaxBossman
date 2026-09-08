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

    @property
    def text(self) -> str:
        return "".join(self.deltas)

    @property
    def ok(self) -> bool:
        """Did the caller get a usable answer? `degraded` counts: the content
        arrived, just not incrementally."""
        return self.status in (SUPPORTED, DEGRADED)


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
    """
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n") if isinstance(raw, str) else ""
        if line.startswith(":"):
            continue                                  # comment / keepalive
        if line.strip() == "":
            if buffer:
                payload = "\n".join(buffer)
                buffer = []
                if payload.strip() == "[DONE]":
                    return
                yield payload
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
            continue
        # Any other SSE field (event:, id:, retry:) carries no chat payload.
    if buffer:
        payload = "\n".join(buffer)
        if payload.strip() != "[DONE]":
            yield payload


def parse_frames(payloads: Iterable[str], *, max_chunks: int = 0) -> StreamOutcome:
    """Fold SSE data payloads into one outcome. Pure and synchronous, so the
    provider variants can be tested from recorded fixtures without a network."""
    outcome = StreamOutcome(status=FAILED)
    message_text = ""
    for payload in payloads:
        body = payload.strip()
        if not body or body == "[DONE]":
            continue
        try:
            chunk = json.loads(body)
        except json.JSONDecodeError:
            outcome.detail = outcome.detail or f"non-JSON frame: {body[:80]}"
            continue
        if not isinstance(chunk, dict):
            continue
        outcome.frames += 1
        error = frame_error(chunk)
        if error:
            # A provider error ends the stream and is NOT evidence about the
            # model's streaming capability.
            outcome.status = PROVIDER_FAILED
            outcome.error = error
            return outcome
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            # Read once. A streamed run that folded usage from every frame
            # would bill the same tokens repeatedly against the budget.
            outcome.usage = dict(usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        if choice.get("finish_reason"):
            outcome.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if isinstance(delta, dict):
            text = _delta_text(delta)
            if text:
                outcome.deltas.append(text)
            if delta.get("tool_calls"):
                outcome.tool_call_frames += 1
        message = choice.get("message")
        if isinstance(message, dict):
            # Non-delta body: the provider answered in one piece.
            message_text = message_text or _content_text(message.get("content"))
            if message.get("tool_calls"):
                outcome.tool_call_frames += 1
        if max_chunks and len(outcome.deltas) >= max_chunks:
            break
    return _classify(outcome, message_text)


def _classify(outcome: StreamOutcome, message_text: str) -> StreamOutcome:
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
    """Async twin of `parse_frames` over a live line iterator."""
    collected: list[str] = []
    buffer: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r\n") if isinstance(raw, str) else ""
        if line.startswith(":"):
            continue
        if line.strip() == "":
            if buffer:
                payload = "\n".join(buffer)
                buffer = []
                if payload.strip() == "[DONE]":
                    break
                collected.append(payload)
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    if buffer:
        payload = "\n".join(buffer)
        if payload.strip() != "[DONE]":
            collected.append(payload)
    return parse_frames(collected, max_chunks=max_chunks)


def outcome_from_exception(exc: BaseException) -> StreamOutcome:
    """Transport-level failure. Always `provider_failed`: a refused connection,
    a 500 or a timeout says nothing about whether the model can stream, and
    recording it as a model property is how a healthy model gets blacklisted."""
    return StreamOutcome(status=PROVIDER_FAILED,
                         error=f"{type(exc).__name__}: {exc}"[:300],
                         detail="transport failure before or during the stream")
