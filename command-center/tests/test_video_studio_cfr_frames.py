"""Real CFR endpoint and content oracles; no container frame-count trust."""
import hashlib
import json
from fractions import Fraction
import shutil

import pytest

from bcc.video_studio.commands import apply_command
from bcc.video_studio.media import MediaLibrary, binary, process
from bcc.video_studio.model import TICKS, frame_ticks, new_project
from bcc.video_studio.render import cfr_frame_count, render_project, verify_output


@pytest.mark.parametrize("fps", [{"num": 25, "den": 1}, {"num": 30, "den": 1},
    {"num": 30000, "den": 1001}, {"num": 24000, "den": 1001}])
def test_cfr_count_respects_tick_rounded_frame_boundaries(fps):
    for frame in (1, 2, 7, 25, 30, 300, 10000):
        assert cfr_frame_count(frame_ticks(frame, fps), fps) == frame
    # A genuinely non-aligned duration includes the final partial frame.
    span = Fraction(1, 10) * Fraction(fps["num"], fps["den"])
    assert cfr_frame_count(100000, fps) == -(-span.numerator // span.denominator)
    for first, last in ((1, 2), (2, 4), (3, 7)):
        start, end = frame_ticks(first, fps), frame_ticks(last, fps)
        assert cfr_frame_count(end - start, fps, start_ticks=start) == last - first


@pytest.mark.parametrize("value", [0, -1, True, 1.5, 86400*TICKS+1])
def test_cfr_count_rejects_unbounded_or_untyped_time(value):
    with pytest.raises(ValueError):
        cfr_frame_count(value, {"num": 25, "den": 1})


async def fixture(tmp_path, fps, frames, *, red_at=0):
    # Distinct endpoint markers: a cloned penultimate green frame cannot pass
    # the last-frame BLUE assertion. First frame RED detects leading shifts.
    source = tmp_path / "source.mp4"
    rate = f"{fps['num']}/{fps['den']}"
    await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        f"color=c=green:s=160x90:r={rate},drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='eq(n,{red_at})',drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='eq(n,{frames-1})'",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
        "-frames:v", str(frames), "-t", str(float(Fraction(frames * fps['den'], fps['num']))),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)])
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    media = await MediaLibrary(tmp_path).import_file(source)
    p = new_project("cfr-project", "CFR endpoints")
    p["media"] = {media["id"]: media}
    seq = p["sequences"][0]
    seq.update(width=160, height=90, fps=fps)
    p = apply_command(p, {"type": "clip.add", "track_id": seq["tracks"][0]["id"], "clip": {
        "id": "clip", "media_id": media["id"], "source_out": frame_ticks(frames, fps)}})[0]
    return p, source, source_digest


async def decoded_frames(path):
    raw, _ = await process([binary("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)], max_output=65536)
    return json.loads(raw)["frames"]


async def rgb_frame(path, frame):
    raw, _ = await process([binary("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path),
        "-vf", f"select=eq(n\\,{frame}),scale=16:16", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    assert len(raw) == 16 * 16 * 3
    return [sum(raw[i::3]) / 256 for i in range(3)]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="real local FFmpeg/ffprobe required")
@pytest.mark.parametrize("fps,count", [({"num": 25, "den": 1}, 7), ({"num": 25, "den": 1}, 25),
    ({"num": 30, "den": 1}, 15), ({"num": 30000, "den": 1001}, 30),
    ({"num": 30000, "den": 1001}, 7), ({"num": 30000, "den": 1001}, 1)])
async def test_real_cfr_keeps_actual_endpoint_content_audio_count_and_pts(tmp_path, fps, count):
    p, source, source_digest = await fixture(tmp_path, fps, count)
    output = tmp_path / "output.mp4"
    result = await render_project(p, tmp_path, output)
    proof = result["verification"]
    assert proof["passed"] and proof["decoded"] and proof["has_audio"]
    assert proof["decoded_video_frames"] == count
    frames = await decoded_frames(output)
    assert len(frames) == count
    assert [float(f["best_effort_timestamp_time"]) for f in frames] == pytest.approx(
        [float(Fraction(i * fps['den'], fps['num'])) for i in range(count)], abs=1e-6)
    first, last = await rgb_frame(output, 0), await rgb_frame(output, count - 1)
    if count > 1:
        assert first[0] > 150 and first[1] < 60 and first[2] < 60, first
    assert last[2] > 150 and last[0] < 60 and last[1] < 60, last
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
    wrong = await verify_output(output, {"fps": fps, "frame_count": count + 1})
    assert not wrong["passed"] and "decoded frame count mismatch" in wrong["failures"]


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="real local FFmpeg/ffprobe required")
@pytest.mark.parametrize("first,last", [(3, 7), (2, 4), (1, 3), (1, 7)])
async def test_rational_range_keeps_selected_final_source_frame(tmp_path, first, last):
    fps = {"num": 30000, "den": 1001}
    p, source, source_digest = await fixture(tmp_path, fps, last, red_at=first)
    output = tmp_path / "range.mp4"
    result = await render_project(p, tmp_path, output,
        {"range": {"start": frame_ticks(first, fps), "end": frame_ticks(last, fps)}})
    count = last - first
    assert result["verification"]["decoded_video_frames"] == count
    frames = await decoded_frames(output)
    assert len(frames) == count
    start_color = await rgb_frame(output, 0)
    assert start_color[0] > 150 and start_color[1] < 60 and start_color[2] < 60, start_color
    end_color = await rgb_frame(output, count - 1)
    assert end_color[2] > 150 and end_color[0] < 60 and end_color[1] < 60, end_color
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
