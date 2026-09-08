"""B4 — one parser, every frame shape a real provider actually sends.

Cloud QA reported `streaming FAIL (0 chunks)` for GLM 5.3 and for the free
model. The transport was fine; the reader accepted exactly one frame shape
(`choices[0].delta.content`) and silently dropped everything else. These
fixtures are the shapes that were being dropped.

The classification matters as much as the parsing: `stream_failed` ("this model
does not stream") and `provider_failed` ("this provider is unwell right now")
are different facts, and recording the second as the first is how a healthy
model gets permanently blacklisted by its own health record.
"""
from __future__ import annotations

import json

import pytest

from bcc.streaming import (DEGRADED, FAILED, PROVIDER_FAILED, SUPPORTED,
                           iter_sse_events, outcome_from_exception, parse_frames,
                           read_stream)


def sse(*frames: object) -> list[str]:
    """Render frames the way a provider writes them onto the wire."""
    lines: list[str] = []
    for frame in frames:
        body = frame if isinstance(frame, str) else json.dumps(frame, ensure_ascii=False)
        lines.append(f"data: {body}")
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return lines


def delta(**kw) -> dict:
    return {"choices": [{"index": 0, "delta": kw}]}


def parse(*frames: object):
    return parse_frames(list(iter_sse_events(sse(*frames))))


# ------------------------------------------------------------ the plain case

def test_ordinary_content_deltas_stream():
    out = parse(delta(content="Hel"), delta(content="lo"))
    assert out.status == SUPPORTED and out.text == "Hello"
    assert out.ok is True


def test_a_single_delta_is_degraded_not_supported():
    """The content arrived, but not incrementally. Calling that "streaming"
    would promise the caller a delivery it will not get."""
    out = parse(delta(content="Hello"))
    assert out.status == DEGRADED and out.text == "Hello" and out.ok is True


# --------------------------------------------------- the shapes that were lost

def test_a_reasoning_model_streams_from_the_reasoning_field():
    """The GLM 5.3 shape verbatim: `content` is null, the text lives in
    `reasoning`. This is the exact frame that produced "0 chunks"."""
    out = parse(delta(content=None, reasoning="1 "),
                delta(content=None, reasoning="2 "),
                delta(content=None, reasoning="3"))
    assert out.status == SUPPORTED and out.text == "1 2 3"


def test_reasoning_content_is_also_read():
    out = parse(delta(reasoning_content="a"), delta(reasoning_content="b"))
    assert out.status == SUPPORTED and out.text == "ab"


def test_content_wins_over_reasoning_so_text_is_never_doubled():
    """A frame that fills both must contribute once, or a streamed answer comes
    back with every token duplicated."""
    out = parse(delta(content="X", reasoning="X"), delta(content="Y", reasoning="Y"))
    assert out.text == "XY"


def test_content_as_a_list_of_typed_parts():
    out = parse(delta(content=[{"type": "text", "text": "par"}]),
                delta(content=[{"type": "text", "text": "ts"}]))
    assert out.status == SUPPORTED and out.text == "parts"


def test_a_tool_call_stream_is_supported_even_with_no_text():
    """A stream that works perfectly and produces no prose at all — the old
    reader called this failure."""
    out = parse(delta(tool_calls=[{"index": 0, "function": {"name": "f"}}]),
                delta(tool_calls=[{"index": 0, "function": {"arguments": "{}"}}]))
    assert out.status == SUPPORTED and out.tool_call_frames == 2 and out.text == ""


def test_a_non_delta_message_body_is_degraded_not_failed():
    out = parse({"choices": [{"index": 0, "message": {"content": "whole answer"}}]})
    assert out.status == DEGRADED and out.text == "whole answer"


# ------------------------------------------------------------- SSE mechanics

def test_comment_keepalives_do_not_end_the_stream():
    """`: OPENROUTER PROCESSING` arrives between real frames."""
    lines = [": OPENROUTER PROCESSING", ""] + sse(delta(content="a"), delta(content="b"))
    out = parse_frames(list(iter_sse_events(lines)))
    assert out.status == SUPPORTED and out.text == "ab"


def test_multiline_data_fields_are_joined_before_parsing():
    """SSE says successive `data:` lines of one event are joined with a newline.
    JSON treats that newline as whitespace between tokens, so a payload split at
    a token boundary reassembles — which the old reader could not do at all,
    because it parsed each line on its own and discarded both halves."""
    payload = json.dumps(delta(content="multi"))
    cut = payload.index('"choices"')                  # a real token boundary
    lines = [f"data: {payload[:cut]}", f"data: {payload[cut:]}", "", "data: [DONE]", ""]
    events = list(iter_sse_events(lines))
    assert len(events) == 1 and "\n" in events[0]
    assert parse_frames(events).text == "multi"


def test_done_terminates_and_later_frames_are_ignored():
    lines = ["data: [DONE]", "", "data: " + json.dumps(delta(content="late")), ""]
    assert list(iter_sse_events(lines)) == []


def test_a_stream_with_no_trailing_blank_line_still_yields_its_last_frame():
    lines = ["data: " + json.dumps(delta(content="tail"))]
    assert parse_frames(list(iter_sse_events(lines))).text == "tail"


def test_other_sse_fields_are_ignored_without_breaking_the_stream():
    lines = ["event: message", "id: 1", "retry: 500",
             "data: " + json.dumps(delta(content="ok")), "", "data: [DONE]", ""]
    assert parse_frames(list(iter_sse_events(lines))).text == "ok"


# ------------------------------------------------------------- classification

def test_an_error_frame_is_a_provider_failure_not_a_model_verdict():
    """The distinction the whole classification exists for: a 429 delivered
    inside the body must not become "this model cannot stream"."""
    out = parse(delta(content="partial"),
                {"error": {"code": 429, "message": "rate limited"}})
    assert out.status == PROVIDER_FAILED
    assert "rate limited" in out.error


def test_an_error_frame_stops_the_stream_immediately():
    out = parse({"error": {"message": "boom"}}, delta(content="never read"))
    assert out.status == PROVIDER_FAILED and out.text == ""


def test_a_transport_failure_is_a_provider_failure():
    out = outcome_from_exception(TimeoutError("read timed out"))
    assert out.status == PROVIDER_FAILED and out.ok is False
    assert "TimeoutError" in out.error


def test_frames_with_no_usable_payload_are_a_stream_failure():
    out = parse(delta(content=None), delta(content=""), {"choices": []})
    assert out.status == FAILED and out.ok is False
    assert "no text" in out.detail


def test_an_empty_stream_is_a_stream_failure_with_a_reason():
    out = parse_frames([])
    assert out.status == FAILED and "no frames" in out.detail


def test_malformed_json_does_not_abort_a_working_stream():
    """One corrupt frame must not lose the frames around it."""
    lines = ["data: {not json", "", "data: " + json.dumps(delta(content="a")), "",
             "data: " + json.dumps(delta(content="b")), "", "data: [DONE]", ""]
    out = parse_frames(list(iter_sse_events(lines)))
    assert out.status == SUPPORTED and out.text == "ab"


# -------------------------------------------------------------------- usage

def test_usage_is_recorded_once_not_accumulated():
    """A streamed run that folded usage from every frame would bill the same
    tokens repeatedly against the mission budget."""
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    out = parse(delta(content="a"), {"choices": [], "usage": usage},
                {"choices": [], "usage": usage})
    assert out.usage == usage                      # last one wins, not the sum


def test_finish_reason_is_captured():
    out = parse(delta(content="a"),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    assert out.finish_reason == "stop"


def test_max_chunks_bounds_the_read():
    out = parse_frames([json.dumps(delta(content=str(i))) for i in range(100)], max_chunks=5)
    assert len(out.deltas) == 5                    # no unbounded accumulation


# ---------------------------------------------------------------- async twin

async def test_read_stream_matches_the_synchronous_parser():
    frames = sse(delta(content="a"), delta(content="b"))

    async def lines():
        for line in frames:
            yield line

    out = await read_stream(lines())
    assert out.status == SUPPORTED and out.text == "ab"


# ------------------------------------------------- the client end to end

@pytest.mark.parametrize("body,expected,text", [
    (sse(delta(content="A"), delta(content="B")), SUPPORTED, "AB"),
    (sse(delta(content=None, reasoning="G"), delta(content=None, reasoning="LM")),
     SUPPORTED, "GLM"),
    (sse({"error": {"message": "upstream 502"}}), PROVIDER_FAILED, ""),
    (sse(delta(content=None)), FAILED, ""),
])
async def test_openrouter_client_classifies_recorded_streams(body, expected, text):
    """Same fixtures through the real client, so the transport wiring is covered
    and not just the parser."""
    import httpx
    from bcc.v2.openrouter_ext import OpenRouterClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join(body),
                              headers={"content-type": "text/event-stream"})

    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(handler))
    out = await client.stream_outcome("z-ai/glm-5.3", [{"role": "user", "content": "hi"}])
    assert out.status == expected and out.text == text


async def test_a_provider_http_error_is_not_a_streaming_verdict():
    import httpx
    from bcc.v2.openrouter_ext import OpenRouterClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(handler))
    out = await client.stream_outcome("vendor/model", [{"role": "user", "content": "hi"}])
    assert out.status == PROVIDER_FAILED and "429" in out.error


async def test_the_probe_does_not_blacklist_a_model_for_a_provider_outage():
    """`skipped=True` makes `verified` None — "we do not know" — instead of
    False, which would read as "we checked and it cannot"."""
    import httpx
    from bcc.v2.capability_probe import probe_streaming
    from bcc.v2.openrouter_ext import OpenRouterClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "upstream down"}})

    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(handler))
    res = await probe_streaming(client, "vendor/model")
    assert res.ok is False and res.skipped is True and res.verified is None


async def test_stream_raw_stays_backward_compatible():
    import httpx
    from bcc.v2.openrouter_ext import OpenRouterClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join(sse(delta(content="x"), delta(content="y"))),
                              headers={"content-type": "text/event-stream"})

    client = OpenRouterClient("sk-test", base_url="http://openrouter.test/api/v1",
                              transport=httpx.MockTransport(handler))
    assert await client.stream_raw("m", [{"role": "user", "content": "hi"}]) == ["x", "y"]
