"""Astra/Codex F2 + F3 (2026-09-08, reproduced on 45027d3): a stream must be
honest about how much it read and about whether the answer arrived whole.

F2: `read_stream(max_chunks=2)` buffered all 100 upstream frames and only then
    applied the cap — the cap bounded what was RETURNED, not what was READ.
F3: HTTP 200, one content delta, then `{broken-json`, EOF, no finish_reason,
    no [DONE] → `stream_degraded`, `ok=True`. A truncated corrupted answer
    advertised as usable.

`status` keeps answering the router's question (does this model stream?);
`completion` answers the caller's (did THIS answer arrive whole?), and `ok`
now requires both.
"""
from __future__ import annotations

import json

import httpx
import pytest

from bcc.streaming import (CAPPED, COMPLETE, DEGRADED, FAILED, MALFORMED, PARTIAL,
                           PROVIDER_ERROR, PROVIDER_FAILED, RATE_LIMITED, SILENT, SUPPORTED,
                           TIMEOUT, iter_sse_events, outcome_from_exception, parse_frames,
                           read_stream)
from bcc.v2.openrouter_ext import OpenRouterClient


def delta(**kw) -> dict:
    return {"choices": [{"index": 0, "delta": kw}]}


def finish(reason="stop") -> dict:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def wire(*frames, done=True) -> list[str]:
    lines: list[str] = []
    for f in frames:
        body = f if isinstance(f, str) else json.dumps(f, ensure_ascii=False)
        lines += [f"data: {body}", ""]
    if done:
        lines += ["data: [DONE]", ""]
    return lines


async def alines(lines, counter=None):
    for line in lines:
        if counter is not None:
            counter["n"] += 1
        yield line


# ------------------------------------------------------------------- F2: cap

async def test_max_chunks_bounds_upstream_consumption_not_just_the_return_value():
    consumed = {"n": 0}

    async def lines():
        for i in range(100):
            consumed["n"] += 1
            yield "data: " + json.dumps(delta(content=str(i)))
            yield ""

    out = await read_stream(lines(), max_chunks=2)
    assert len(out.deltas) == 2
    # two content events = four wire lines; nothing beyond them was pulled
    assert consumed["n"] <= 4, consumed
    assert out.capped is True and out.completion == CAPPED and out.ok is True


def test_the_synchronous_parser_stops_pulling_at_the_cap_too():
    pulled = {"n": 0}

    def payloads():
        for i in range(100):
            pulled["n"] += 1
            yield json.dumps(delta(content=str(i)))

    out = parse_frames(payloads(), max_chunks=3)
    assert len(out.deltas) == 3 and pulled["n"] == 3


async def test_reading_stops_at_done_and_does_not_drain_what_follows():
    consumed = {"n": 0}
    lines = wire(delta(content="a"), finish()) + ["data: " + json.dumps(delta(content="late")), ""] * 50
    out = await read_stream(alines(lines, consumed))
    assert out.text == "a" and out.terminated is True and out.completion == COMPLETE
    assert consumed["n"] <= len(wire(delta(content="a"), finish()))


async def test_a_provider_error_frame_stops_reading_immediately():
    consumed = {"n": 0}
    lines = wire({"error": {"message": "upstream 502"}}, done=False) + wire(delta(content="x")) * 20
    out = await read_stream(alines(lines, consumed))
    assert out.status == PROVIDER_FAILED and out.completion == PROVIDER_ERROR
    assert consumed["n"] <= 2


# ------------------------------------------------------ F3: partial/malformed

async def test_partial_then_malformed_then_eof_is_not_a_usable_answer():
    body = 'data: {"choices":[{"delta":{"content":"partial"}}]}\n\ndata: {broken-json\n\n'
    client = OpenRouterClient("fixture", base_url="http://fixture.invalid",
                              transport=httpx.MockTransport(lambda r: httpx.Response(200, text=body)))
    out = await client.stream_outcome("fixture", [{"role": "user", "content": "hi"}])
    assert out.ok is False
    assert out.completion == MALFORMED and out.malformed_frames == 1
    assert out.text == "partial"                       # the fragment is kept, labelled, not trusted
    assert out.status == DEGRADED                      # the router's fact is unchanged


def test_content_then_eof_without_a_terminal_marker_is_partial():
    out = parse_frames(list(iter_sse_events(wire(delta(content="a"), delta(content="b"), done=False))))
    assert out.status == SUPPORTED and out.text == "ab"
    assert out.completion == PARTIAL and out.ok is False and out.terminated is False


def test_finish_reason_alone_is_a_terminal_state():
    out = parse_frames(list(iter_sse_events(wire(delta(content="a"), finish(), done=False))))
    assert out.completion == COMPLETE and out.ok is True


def test_done_alone_is_a_terminal_state():
    out = parse_frames(list(iter_sse_events(wire(delta(content="a"), delta(content="b")))))
    assert out.completion == COMPLETE and out.ok is True and out.terminated is True


def test_a_corrupt_frame_inside_a_terminated_stream_is_still_malformed():
    """The protocol ended properly, but a frame was lost: the text cannot be
    trusted to be the whole answer. `status` still records that streaming works."""
    lines = ["data: {not json", "", "data: " + json.dumps(delta(content="a")), "",
             "data: " + json.dumps(delta(content="b")), "", "data: [DONE]", ""]
    out = parse_frames(list(iter_sse_events(lines)))
    assert out.status == SUPPORTED and out.text == "ab"
    assert out.completion == MALFORMED and out.ok is False


def test_an_empty_stream_is_silent():
    out = parse_frames([])
    assert out.status == FAILED and out.completion == SILENT and out.ok is False


def test_frames_without_content_are_silent_even_when_terminated():
    out = parse_frames(list(iter_sse_events(wire(delta(content=None), finish()))))
    assert out.completion == SILENT and out.ok is False


def test_a_rate_limit_error_frame_is_classified_as_rate_limited():
    out = parse_frames(list(iter_sse_events(wire({"error": {"message": "429: rate limit exceeded", "code": 429}}))))
    assert out.status == PROVIDER_FAILED and out.completion == RATE_LIMITED


def test_a_transport_timeout_is_classified_as_timeout():
    out = outcome_from_exception(httpx.ReadTimeout("read timed out"))
    assert out.status == PROVIDER_FAILED and out.completion == TIMEOUT and out.ok is False
    other = outcome_from_exception(ConnectionResetError("reset"))
    assert other.completion == PROVIDER_ERROR


def test_a_non_delta_message_body_is_complete_by_shape():
    out = parse_frames([json.dumps({"choices": [{"message": {"content": "whole"}}]})])
    assert out.status == DEGRADED and out.completion == COMPLETE and out.ok is True


# --------------------------------------------------- GLM / OpenRouter shapes

def test_glm_reasoning_stream_with_usage_and_done_is_complete():
    frames = [delta(content=None, reasoning="Th"), delta(content=None, reasoning="ink"),
              {"choices": [{"index": 0, "delta": {"content": "42"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 5, "completion_tokens": 3}}]
    out = parse_frames(list(iter_sse_events(wire(*frames))))
    assert out.status == SUPPORTED and out.text == "Think42"
    assert out.completion == COMPLETE and out.usage == {"prompt_tokens": 5, "completion_tokens": 3}


def test_glm_stream_cut_mid_reasoning_is_partial_not_complete():
    frames = [delta(content=None, reasoning="Th"), delta(content=None, reasoning="ink")]
    out = parse_frames(list(iter_sse_events(wire(*frames, done=False))))
    assert out.status == SUPPORTED and out.completion == PARTIAL and out.ok is False


def test_openrouter_processing_comments_do_not_affect_completion():
    lines = [": OPENROUTER PROCESSING", ": OPENROUTER PROCESSING"] + wire(delta(content="a"), finish())
    out = parse_frames(list(iter_sse_events(lines)))
    assert out.completion == COMPLETE and out.text == "a"


@pytest.mark.parametrize("body,status,completion,ok", [
    ("\n".join(wire(delta(content="A"), delta(content="B"))), SUPPORTED, COMPLETE, True),
    ("\n".join(wire(delta(content="A"), delta(content="B"), done=False)), SUPPORTED, PARTIAL, False),
    ('data: {"choices":[{"delta":{"content":"A"}}]}\n\ndata: garbage\n\n', DEGRADED, MALFORMED, False),
    ("\n".join(wire({"error": {"message": "quota exceeded"}}, done=False)), PROVIDER_FAILED, RATE_LIMITED, False),
    ("\n".join(wire(delta(content=None))), FAILED, SILENT, False),
])
async def test_the_client_end_to_end_reports_completion(body, status, completion, ok):
    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(lambda r: httpx.Response(
                                  200, text=body, headers={"content-type": "text/event-stream"})))
    out = await client.stream_outcome("z-ai/glm-5.3", [{"role": "user", "content": "hi"}])
    assert (out.status, out.completion, out.ok) == (status, completion, ok)


async def test_the_capability_probe_still_reads_status_not_completion():
    """The probe answers "can this model stream?" — a partial answer is still
    evidence of streaming; it is the caller of the ANSWER that must not use it."""
    from bcc.v2.capability_probe import probe_streaming
    body = "\n".join(wire(delta(content="A"), delta(content="B"), done=False))
    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(lambda r: httpx.Response(
                                  200, text=body, headers={"content-type": "text/event-stream"})))
    res = await probe_streaming(client, "vendor/model")
    assert res.ok is True
