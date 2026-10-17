"""Render validation with real ffmpeg on tiny lavfi videos: 9:16, no stretch, no upscale, <=60 s, measured by ffprobe."""
import pytest

from shortsfactory import media
from conftest import fake_transcript


def test_plan_frame_modes_never_upscale():
    land = media.plan_frame(1920, 1080, 0.5)
    assert land["mode"] == "inset" and land["scale"] == 1.0 and land["content_px"] == "1080x1080"
    uhd = media.plan_frame(3840, 2160, 0.3)
    assert uhd["mode"] == "cover" and uhd["scale"] <= 1.0 and (uhd["out_w"], uhd["out_h"]) == (1080, 1920)
    vert = media.plan_frame(1080, 1920)
    assert vert["mode"] == "cover" and vert["scale"] == 1.0
    small = media.plan_frame(640, 360)
    assert small["mode"] == "native" and small["out_w"] <= 640 and small["out_h"] == 360
    assert abs(small["out_w"] / small["out_h"] - 9 / 16) < 0.02 and "апскейла нет" in small["note"]


def _render(tmp_path, src, start, dur, words=True):
    info = media.probe(src)
    plan = media.plan_frame(info["width"], info["height"], 0.5)
    tr = fake_transcript(info["duration"])
    from shortsfactory.transcribe import words_in
    chunks = media.chunk_words(words_in(tr, start, start + dur)) if words else []
    media.write_ass(tmp_path / "subs.ass", chunks, plan["out_w"], plan["out_h"])
    out = tmp_path / "o.mp4"
    media.render_clip(src, out, start=start, dur=dur, src_w=info["width"], src_h=info["height"], face_x=0.5, ass_name="subs.ass", work=tmp_path)
    return out, plan, info, chunks


def test_landscape_render_is_1080x1920_with_audio_and_exact_duration(tmp_path, videos):
    out, plan, info, chunks = _render(tmp_path, videos["land"], 4.0, 16.0)
    v = media.validate_output(out, expected_w=1080, expected_h=1920, expected_s=16.0, src_w=info["width"])
    assert v["ok"], v
    p = v["ffprobe"]
    assert (p["width"], p["height"]) == (1080, 1920) and p["has_audio"] and p["video_codec"] == "h264"
    assert abs(p["duration"] - 16.0) <= 0.35 and chunks


def test_small_source_keeps_native_and_validator_rejects_claiming_1080(tmp_path, videos):
    out, plan, info, _ = _render(tmp_path, videos["small"], 2.0, 15.0)
    native = media.validate_output(out, expected_w=plan["out_w"], expected_h=plan["out_h"], expected_s=15.0, src_w=info["width"])
    assert native["ok"] and native["native_below_1080"] and native["ffprobe"]["width"] < 1080
    fake = media.validate_output(out, expected_w=1080, expected_h=1920, expected_s=15.0, src_w=info["width"])
    assert not fake["ok"] and any("разрешение" in x for x in fake["problems"])


def test_validator_rejects_landscape_source_and_wrong_duration(videos):
    v = media.validate_output(videos["land"], expected_w=1080, expected_h=1920, expected_s=10.0, src_w=1920)
    assert not v["ok"]
    assert any("вертикальное" in x for x in v["problems"]) and any("длительность" in x for x in v["problems"])


def test_render_refuses_over_60_seconds(tmp_path, videos):
    with pytest.raises(media.RenderError):
        media.render_clip(videos["land"], tmp_path / "x.mp4", start=0, dur=61, src_w=1920, src_h=1080, face_x=0.5, ass_name=None, work=tmp_path)


def test_signals_energy_and_scenes(videos):
    e = media.audio_energy(videos["land"])
    assert e["t"] and len(e["t"]) == len(e["z"])
    loud = [z for t, z in zip(e["t"], e["z"]) if 15 <= t < 19]
    quiet = [z for t, z in zip(e["t"], e["z"]) if 30 <= t < 35]
    assert sum(loud) / len(loud) > sum(quiet) / len(quiet)          # the louder middle part is measured as louder
    s = media.scene_changes(videos["land"])
    assert s["method"] and isinstance(s["cuts"], list)


def test_caption_chunks_short_and_ordered():
    words = [{"word": f" w{i}", "start": i * 0.3, "end": i * 0.3 + 0.25} for i in range(20)]
    ch = media.chunk_words(words, max_words=4)
    assert all(len(c["text"].split()) <= 4 for c in ch)
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(ch, ch[1:]))
