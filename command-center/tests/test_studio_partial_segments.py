"""«Стоп обрывает на том, что уже есть»: a stopped chain gives back its finished segments.

Owner instruction (2026-09-22, after pid 1644 was stopped by hand and 40 minutes of 720p/81/50
work left nothing on disk): a cancel or a blown deadline must not throw the whole chain away.
The segments the engine actually finished are joined into one file and handed over honestly
marked — partial, how many of how many, the real duration — never as a complete result.

MOCK_ENGINE throughout (a Python child writing a tiny testsrc clip with ffmpeg); no GPU.
"""
from __future__ import annotations

import asyncio
import shutil
import time

import pytest

from bcc.studio.providers import sdcpp

from .test_studio_sdcpp_hostile import Harness, plane, wait_done  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")

WAN = "sdcpp:wan2.2-ti2v-5b"


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def _chain_plane(segments: int, **extra):
    """A `length` preset gives the chain its segment count; 15s = 3, 10s = 2."""
    length = {2: "10s", 3: "15s", 6: "30s"}[segments]
    return plane(WAN, length=length, width=640, height=352, frames=17, fps=16, steps=16,
                 seed=1, **extra)


async def _stop_after(provider, rid, done_segments, timeout=60):
    """Let the chain finish `done_segments` segments, then cancel it."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    job = provider.job_record(rid)
    while loop.time() < deadline:
        if len(job.get("segment_results") or []) >= done_segments:
            break
        if provider._jobs[rid]["task"].done():
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError(f"chain never reached {done_segments} finished segments")
    await provider.cancel(rid)


async def test_cancel_before_any_segment_finished_saves_nothing_and_says_so(harness):
    """Nothing finished -> nothing is handed over. The engine writes a segment's container only
    when that segment ends, so there are genuinely no bytes; an empty or broken file must never
    be passed off as a result."""
    harness.mode = "slow"                       # the first segment never finishes
    p = harness.provider(WAN)
    sub = await p.submit(_chain_plane(3))
    await asyncio.sleep(0.5)
    await p.cancel(sub.request_id)

    info = p.partial_result(sub.request_id)
    assert info["segments_done"] == 0 and info["segments_total"] == 3
    assert info["partial"] is True and info["complete"] is False
    assert info["duration_s"] is None and "no segment finished" in info["detail"]

    dest = harness.storage / "stump.mp4"
    with pytest.raises(sdcpp.ProviderFailure):
        await p.fetch_partial(sub.request_id, dest)
    assert not dest.exists(), "an empty result was written to disk"
    assert (await p.status(sub.request_id)).state == "canceled"


async def test_cancel_after_one_of_three_segments_returns_that_one_marked_partial(harness):
    p = harness.provider(WAN)
    sub = await p.submit(_chain_plane(3))
    await _stop_after(p, sub.request_id, 1)

    # The core regression: before this change cancel() deleted every raw in the chain, so
    # the finished segment was gone and 40 minutes of real work left nothing behind.
    done = p._done_segment_files(p.job_record(sub.request_id))
    assert len(done) == 1 and done[0].is_file() and done[0].stat().st_size > 0,         "the finished segment was deleted by the stop"

    info = p.partial_result(sub.request_id)
    assert info["segments_done"] == 1 and info["segments_total"] == 3
    assert info["reason"] == "canceled" and info["complete"] is False
    # the `15s` preset fixes the chain at 3 x 81 frames @ 16 fps; one segment is 81/16 s,
    # the whole chain (81 + 80 + 80)/16 s. The partial says the length it really has.
    assert info["duration_s"] == pytest.approx(81 / 16, abs=1e-3)
    assert info["duration_s_if_complete"] == pytest.approx((81 + 80 * 2) / 16, abs=1e-3)
    assert info["duration_s"] < info["duration_s_if_complete"]

    dest = harness.storage / "partial.mp4"
    fetched = await p.fetch_partial(sub.request_id, dest)
    assert dest.is_file() and fetched.bytes > 0 and fetched.mime == "video/mp4"
    assert len(fetched.sha256) == 64

    trace = p.traces[sub.request_id]
    assert trace["partial"] is True and trace["complete"] is False
    assert trace["segments_done"] == 1 and trace["segments_total"] == 3
    assert trace["stopped_by"] == "canceled"
    assert trace["generation"]["source"] == "engine_process_interrupted"
    assert trace["transcode"]["operation"] == "concat_finished_segments_only"
    assert len(trace["transcode"]["input_sha256"]) == 1
    # No frame repetition / interpolation may stand in for the missing generation.
    assert "no frame was repeated" in trace["generation"]["synthesis"]
    assert trace["generation"]["duration_s_requested"] > trace["generation"]["duration_s_declared"]
    # The status of the JOB is still what it was: stopped, not completed.
    assert (await p.status(sub.request_id)).state == "canceled"


async def test_the_absolute_deadline_keeps_what_was_already_generated(harness):
    """The job budget runs out mid-chain: the finished segments survive the kill."""
    p = harness.provider(WAN)
    sub = await p.submit(_chain_plane(3))
    job = p.job_record(sub.request_id)
    # Let the first segment land, then close the whole job's budget: the next segment is killed.
    while not (job.get("segment_results") or []):
        assert not p._jobs[sub.request_id]["task"].done(), "chain ended before a segment landed"
        await asyncio.sleep(0.02)
    harness.mode = "slow"
    job["budget_at"] = time.time() + 0.4

    st = await wait_done(p, sub.request_id)
    assert st.state == "failed" and st.reason == "timeout"
    assert "job budget" in p.failure_detail(sub.request_id)

    info = p.partial_result(sub.request_id)
    assert info["reason"] == "timeout" and info["segments_done"] >= 1
    assert info["segments_done"] < info["segments_total"]
    dest = harness.storage / "timed-out.mp4"
    fetched = await p.fetch_partial(sub.request_id, dest)
    assert dest.is_file() and fetched.bytes > 0
    assert p.traces[sub.request_id]["stopped_by"] == "timeout"


async def test_a_partial_result_is_never_reported_as_complete(harness):
    """The property, on every stopped chain: nothing in the delivered evidence says 'complete'."""
    p = harness.provider(WAN)
    sub = await p.submit(_chain_plane(3))
    await _stop_after(p, sub.request_id, 1)
    await p.fetch_partial(sub.request_id, harness.storage / "never-complete.mp4")

    trace = p.traces[sub.request_id]
    assert trace.get("complete") is False and trace.get("partial") is True
    assert (await p.status(sub.request_id)).state != "completed"
    # The ordinary full-result path must refuse this job outright: a partial chain has no
    # complete output to fetch, and fetching twice is not allowed either.
    with pytest.raises(ValueError):
        await p.fetch_partial(sub.request_id, harness.storage / "again.mp4")

    # Negative control: an untouched chain that really did finish reports complete, not partial.
    ok = await p.submit(_chain_plane(2))
    st = await wait_done(p, ok.request_id)
    assert st.state == "completed"
    assert p.partial_result(ok.request_id) is None, "a finished chain must not offer a partial"
    fetched = await p.fetch(st.outputs[0], harness.storage / "whole.mp4")
    assert fetched.bytes > 0
    assert p.traces[ok.request_id].get("partial") is None
