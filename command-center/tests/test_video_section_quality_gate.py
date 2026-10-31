"""Tests for zero-overhead video section quality improvements and the Studio non-regression Quality Gate.

Verifies:
  1. 0% Studio degradation guarantee: default `quality_guard="safe"` keeps existing Studio prompts/steps/timings
     100% compatible, with `diffusion_overhead_s == 0.0`.
  2. Automatic rollback to original Studio output whenever any post-processing or section filter causes >= 0.1%
     degradation in Laplacian sharpness, highlight/shadow Y clipping, oversharpening ringing, or FPS/duration.
  3. All 5 section improvements (keyframes/color-match <50ms, motion/boundary smoothing, smart transitions,
     single-pass FFmpeg unsharp+eq, and audio loudnorm/ducking + beat alignment).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import numpy as np
import pytest

from bcc.direct_gen import pult, video


def _midtone_textured_frame(height: int = 96, width: int = 128, base_y: int = 120) -> np.ndarray:
    """Create a safe mid-tone textured frame (no clipping, moderate edges) where gentle unsharp improves sharpness."""
    y_grid, x_grid = np.mgrid[0:height, 0:width]
    pattern = (
        base_y
        + 18.0 * np.sin(x_grid * 0.35) * np.cos(y_grid * 0.35)
        + 8.0 * ((x_grid // 4 + y_grid // 4) % 2)
    )
    r = np.clip(pattern + 6.0, 20, 220)
    g = np.clip(pattern, 20, 220)
    b = np.clip(pattern - 6.0, 20, 220)
    return np.stack([r, g, b], axis=-1).astype(np.uint8)


def test_section_catalog_and_zero_diffusion_overhead():
    assert len(video.SECTION_IMPROVEMENTS_CATALOG) == 5
    for idx, item in enumerate(video.SECTION_IMPROVEMENTS_CATALOG, start=1):
        assert item["section"] == idx
        assert item["diffusion_overhead_s"] == 0.0


def test_section1_prompt_enrichment_preserves_safe_verbatim_and_enriches_on_request():
    raw = "A detective under a warm streetlamp in the rain"
    safe = video.enrich_section_prompt(raw, section_role="intro", camera_move="Slow dolly in", quality_guard="safe")
    assert safe["raw_prompt"] == raw
    assert safe["effective_prompt"] == raw, "safe mode by default must keep Studio's verbatim prompt untouched"
    assert "35mm anamorphic" in safe["enriched_prompt"]
    assert safe["diffusion_overhead_s"] == 0.0 and safe["steps_overhead"] == 0 and safe["steps_added"] == 0

    enhanced = video.enrich_section_prompt(
        raw, section_role="intro", camera_move="Slow dolly in", quality_guard="enhanced"
    )
    assert enhanced["applied"] is True
    assert enhanced["effective_prompt"] == enhanced["enriched_prompt"]
    assert enhanced["diffusion_overhead_s"] == 0.0 and enhanced["steps_added"] == 0


def test_section1_color_match_runs_under_50ms_and_passes_quality_gate():
    ref = _midtone_textured_frame(120, 160, base_y=135)
    src = _midtone_textured_frame(120, 160, base_y=105)
    res = video.match_section_color_palette(src, ref, quality_guard="safe", strength=0.85)
    assert res["within_50ms_budget"] is True and res["elapsed_ms"] < 50.0
    assert res["diffusion_overhead_s"] == 0.0
    assert res["passed"] is True and res["rolled_back"] is False and res["action"] == "applied_verified"
    # Exposure of matched frame moved closer to reference without losing sharpness
    src_m = video.compute_frame_metrics(src)
    ref_m = video.compute_frame_metrics(ref)
    out_m = video.compute_frame_metrics(res["frame"])
    assert abs(out_m["mean_y"] - ref_m["mean_y"]) < abs(src_m["mean_y"] - ref_m["mean_y"])
    assert out_m["sharpness_laplacian"] >= src_m["sharpness_laplacian"] * 0.999

    # Frame with unclipped bright specular region (242) must not clip or lose sharpness when shifted toward brighter ref
    src_spec = _midtone_textured_frame(64, 64, base_y=110)
    src_spec[:8, :8, :] = 242
    res_spec = video.match_section_color_palette(src_spec, ref[:64, :64, :], quality_guard="safe", strength=0.85)
    assert res_spec["passed"] is True and res_spec["rolled_back"] is False
    assert res_spec["gate"]["candidate_metrics"]["y_clip_total_share"] == 0.0


def test_section2_motion_schedule_zero_steps_overhead_and_boundary_smoothing():
    settings = {"width": 832, "height": 480, "frames": 81, "fps": 16, "steps": 20, "cfg_scale": 5.0}
    s0 = video.optimize_section_motion_schedule(settings, section_index=0, total_sections=3, quality_guard="safe")
    s2 = video.optimize_section_motion_schedule(
        settings, section_index=2, total_sections=3, section_role="outro", quality_guard="safe"
    )
    assert s0["steps_overhead"] == 0 and s2["steps_overhead"] == 0
    assert s0["steps_added"] == 0 and s2["steps_added"] == 0
    assert s0["diffusion_overhead_s"] == 0.0 and s2["diffusion_overhead_s"] == 0.0
    assert s2["applied_settings"] == settings, "safe mode keeps exact Studio settings"
    assert s2["recommended_settings"]["steps"] == 20
    assert s2["recommended_settings"]["flow_shift"] > s0["recommended_settings"]["flow_shift"]
    assert s2["section_role"] == "outro"

    # Zero cfg_scale (e.g. turbo/distilled) must not be clobbered by falsy fallback
    s_zero = video.optimize_section_motion_schedule({"steps": 4, "cfg_scale": 0.0}, section_index=1, total_sections=2)
    assert s_zero["recommended_settings"]["cfg_scale"] == 0.0

    tail = [_midtone_textured_frame(64, 64, base_y=110), _midtone_textured_frame(64, 64, base_y=112)]
    head = [_midtone_textured_frame(64, 64, base_y=136), _midtone_textured_frame(64, 64, base_y=134)]
    sm = video.smooth_boundary_frames(tail, head, quality_guard="safe")
    assert sm["passed"] is True and sm["rolled_back"] is False and sm["action"] == "applied_verified"
    assert sm["edge_preserving_gradient_guard"] is True
    assert sm["diffusion_overhead_s"] == 0.0
    before_jump = abs(video.compute_frame_metrics(tail[-1])["mean_y"] - video.compute_frame_metrics(head[0])["mean_y"])
    after_jump = abs(
        video.compute_frame_metrics(sm["tail_frames"][-1])["mean_y"]
        - video.compute_frame_metrics(sm["head_frames"][0])["mean_y"]
    )
    assert after_jump < before_jump


def test_section3_smart_transitions_preserve_detail_and_build_valid_filtergraph():
    fg_safe = video.build_smart_transition_filter(3, mode="drop_repeat_safe")
    assert "trim=start_frame=1" in fg_safe["filter_complex"] and "concat=n=3:v=1:a=0[out]" in fg_safe["filter_complex"]
    fg_xfade = video.build_smart_transition_filter(3, section_duration_s=5.0, transition_duration_s=0.25, mode="smart_xfade_luma")
    assert "xfade=transition=fade:duration=0.250" in fg_xfade["filter_complex"]

    f_a = _midtone_textured_frame(64, 64, base_y=115)
    f_b = _midtone_textured_frame(64, 64, base_y=130)
    blended = video.blend_section_transition_frames(f_a, f_b, alpha=0.4, quality_guard="safe")
    assert blended["passed"] is True and blended["rolled_back"] is False and blended["action"] == "applied_verified"
    assert blended["gate"]["candidate_metrics"]["sharpness_laplacian"] >= blended["gate"]["original_metrics"]["sharpness_laplacian"]

    # Transition from a sharp frame A to a softer frame B at alpha=0.5 must retain sharp frame A's Laplacian detail
    y_g, x_g = np.mgrid[0:64, 0:64]
    f_sharp = np.stack([np.clip(120 + 25 * np.sin(x_g * 0.5) * np.cos(y_g * 0.5), 20, 220).astype(np.uint8)] * 3, axis=-1)
    f_soft = np.stack([np.clip(130 + 8 * np.sin(x_g * 0.2) * np.cos(y_g * 0.2), 20, 220).astype(np.uint8)] * 3, axis=-1)
    tr_mid = video.blend_section_transition_frames(f_sharp, f_soft, alpha=0.5, quality_guard="safe")
    assert tr_mid["passed"] is True and tr_mid["rolled_back"] is False
    assert video.compute_frame_metrics(tr_mid["frame"])["sharpness_laplacian"] >= video.compute_frame_metrics(f_sharp)["sharpness_laplacian"]


def test_section4_postprocess_improves_normal_frame_and_builds_single_pass_argv():
    argv = video.build_zero_overhead_encode_argv("ffmpeg", "in.webm", "out.mp4", crf=18, quality_guard="safe")
    assert "-vf" in argv and video.FFMPEG_ZERO_OVERHEAD_VF in argv
    assert "-preset" in argv and argv[argv.index("-preset") + 1] == "veryfast"
    assert "-crf" in argv and argv[argv.index("-crf") + 1] == "18"
    assert "-pix_fmt" in argv and "yuv420p" in argv
    assert "-movflags" in argv and "+faststart" in argv

    frame = _midtone_textured_frame(80, 80, base_y=125)
    res = video.apply_postprocess_with_quality_gate(frame, quality_guard="safe")
    assert res["passed"] is True
    assert res["rolled_back"] is False
    assert res["action"] == "applied_verified"
    assert res["selected"] == "candidate"
    assert res["candidate_metrics"]["sharpness_laplacian"] > res["original_metrics"]["sharpness_laplacian"]
    assert res["candidate_metrics"]["y_clip_total_share"] == 0.0


def test_quality_gate_automatically_rolls_back_on_highlight_clipping():
    """Critical non-regression proof: when a synthetic frame has bright highlights near 250 where
    unsharp + eq(contrast=1.04) causes highlight clipping >= 0.1%, Quality Gate automatically
    rolls back to the original Studio frame untouched."""
    near_highlight = _midtone_textured_frame(80, 80, base_y=238)
    # Push 25% of the frame to Y=249..252 (not clipped yet: < 253.5, but contrast=1.04 + unsharp clips them to 255)
    near_highlight[:40, :, :] = 251
    orig_copy = near_highlight.copy()

    res = video.apply_postprocess_with_quality_gate(near_highlight, quality_guard="safe")
    assert res["passed"] is False
    assert res["rolled_back"] is True
    assert res["action"] == "rollback_to_original"
    assert res["selected"] == "original"
    assert "luminance_clipping_detected" in res["reason"]
    assert np.array_equal(res["frame"], orig_copy), "Rollback must return the exact original Studio frame byte-for-byte"


def test_quality_gate_automatically_rolls_back_on_oversharpening_ringing():
    """Critical non-regression proof: when a frame already has extreme step edges where unsharp mask
    induces ringing halo spikes (`|Laplacian(Y)| >= 90`), Quality Gate rolls back to original."""
    y_grid, x_grid = np.mgrid[0:80, 0:80]
    # Checkerboard with ~22-level step jumps -> |Laplacian| ~ 88 (just below 90 ringing ceiling before unsharp, >90 after)
    checker = np.where((x_grid + y_grid) % 2 == 0, 117, 139).astype(np.uint8)
    rgb = np.stack([checker, checker, checker], axis=-1)
    orig_copy = rgb.copy()

    res = video.apply_postprocess_with_quality_gate(rgb, quality_guard="safe")
    assert res["passed"] is False
    assert res["rolled_back"] is True
    assert res["action"] == "rollback_to_original"
    assert res["selected"] == "original"
    assert "oversharp_ringing_detected" in res["reason"]
    assert np.array_equal(res["frame"], orig_copy)


def test_quality_gate_rolls_back_on_0_1_percent_sharpness_drop_or_metadata_drift():
    orig = _midtone_textured_frame(80, 80, base_y=120)
    # Attenuate local contrast so uint8 pixels lose ~3% Laplacian sharpness (> 0.1% limit)
    slightly_softer = np.clip(np.rint(120.0 + (orig.astype(np.float64) - 120.0) * 0.94), 0, 255).astype(np.uint8)
    gate = video.evaluate_quality_gate(orig, slightly_softer, quality_guard="safe")
    assert gate["passed"] is False
    assert gate["rolled_back"] is True
    assert gate["action"] == "rollback_to_original"
    assert "sharpness_degraded" in gate["reason"]
    assert np.array_equal(gate["frame"], orig)

    # Also test FPS / duration / diffusion_overhead_s guards
    good_cand = video._unsharp_and_eq_frame(orig)
    gate_overhead = video.evaluate_quality_gate(orig, good_cand, diffusion_overhead_s=0.05, quality_guard="safe")
    assert gate_overhead["passed"] is False and "diffusion_overhead_s" in gate_overhead["reason"]

    gate_fps = video.evaluate_quality_gate(
        orig, good_cand, original_meta={"fps": 16, "duration_s": 5.0}, candidate_meta={"fps": 15, "duration_s": 5.0}
    )
    assert gate_fps["passed"] is False and "fps_regression" in gate_fps["reason"]

    gate_off = video.apply_postprocess_with_quality_gate(orig, quality_guard="passthrough")
    assert gate_off["passed"] is False and gate_off["rolled_back"] is True and gate_off["selected"] == "original"
    assert np.array_equal(gate_off["frame"], orig)


def test_section5_audio_mastering_and_beat_alignment():
    aud = video.build_audio_mastering_filter(integrated_lufs=-16.0, true_peak_db=-1.5, lra=11.0, voice_ducking=True)
    assert aud["voice_ducking"] is True
    assert "sidechaincompress" in aud["filter_complex"]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in aud["filter_complex"]
    assert aud["diffusion_overhead_s"] == 0.0

    # At 120 BPM, beats occur every 0.5s (0.0, 0.5, 1.0, ..., 5.0, ..., 10.0)
    cuts = [0.0, 5.12, 9.88, 15.0]
    aligned = video.align_section_cuts_to_beats(cuts, bpm=120.0, max_snap_s=0.18, total_duration_s=15.0)
    assert aligned["aligned_cuts_s"] == [0.0, 5.0, 10.0, 15.0]
    assert aligned["monotonic"] is True
    assert aligned["duration_preserved"] is True
    assert aligned["diffusion_overhead_s"] == 0.0

    # Closely spaced cuts near the same beat must never collapse onto a duplicate timestamp (0s section)
    close_cuts = [0.0, 4.90, 5.08, 10.0]
    aligned_close = video.align_section_cuts_to_beats(
        close_cuts, bpm=120.0, max_snap_s=0.18, min_section_s=0.25, total_duration_s=10.0
    )
    assert aligned_close["monotonic"] is True
    assert len(set(aligned_close["aligned_cuts_s"])) == 4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_real_ffmpeg_guarded_postprocess_preserves_or_improves_video(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    # 1. Safe mid-tone studio clip -> MUST pass Quality Gate and apply candidate
    src_safe = tmp_path / "orig_safe.mp4"
    dst_safe = tmp_path / "out_safe.mp4"
    frames_safe = [_midtone_textured_frame(64, 64, base_y=120) for _ in range(24)]
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", "64x64", "-r", "16", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(src_safe),
        ],
        input=b"".join(f.tobytes() for f in frames_safe),
        check=True,
    )
    report_safe = video.postprocess_video_file_guarded(src_safe, dst_safe, quality_guard="safe", crf=18)
    assert dst_safe.is_file() and dst_safe.stat().st_size > 0
    assert report_safe["passed"] is True
    assert report_safe["rolled_back"] is False
    assert report_safe["selected"] == "candidate"
    assert report_safe["candidate_metrics"]["sharpness_laplacian"] > report_safe["original_metrics"]["sharpness_laplacian"]
    assert report_safe["diffusion_overhead_s"] == 0.0

    # 2. Clip with near-clipped highlights in the SECOND HALF (frames 16..31) -> MUST detect tail clipping and roll back
    src_clip = tmp_path / "orig_clip.mp4"
    dst_clip = tmp_path / "out_clip.mp4"
    frames_clip = []
    for i in range(32):
        f = _midtone_textured_frame(64, 64, base_y=120)
        if i >= 16:
            f[:32, :, :] = 251
        frames_clip.append(f)
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", "64x64", "-r", "16", "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", str(src_clip),
        ],
        input=b"".join(f.tobytes() for f in frames_clip),
        check=True,
    )
    report_clip = video.postprocess_video_file_guarded(src_clip, dst_clip, quality_guard="safe", crf=18)
    assert report_clip["passed"] is False
    assert report_clip["rolled_back"] is True
    assert report_clip["selected"] == "original"
    assert "luminance_clipping_detected" in report_clip["reason"]
    assert dst_clip.read_bytes() == src_clip.read_bytes(), "Rollback must preserve original MP4 byte-for-byte"

    # 3. Passthrough mode copies original byte-for-byte
    dst_pass = tmp_path / "out_pass.mp4"
    rep_pass = video.postprocess_video_file_guarded(src_safe, dst_pass, quality_guard="passthrough")
    assert rep_pass["selected"] == "original" and rep_pass["rolled_back"] is True
    assert dst_pass.read_bytes() == src_safe.read_bytes()


async def test_pult_send_message_delivers_quality_report():
    sent_payloads: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json
        sent_payloads.append(_json.loads(req.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 901}})

    text = video.build_pult_quality_report_ru()
    assert "ГАРАНТИЯ 0% УХУДШЕНИЯ СТУДИИ" in text
    assert "0.1%" in text
    msg_id = await pult.send_message(
        "999999:FAKE_token_for_unit_tests_only_0123456789",  # ci-secret-scan: allow
        424242,
        text,
        transport=httpx.MockTransport(handler),
    )
    assert msg_id == 901
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["chat_id"] == "424242"
    assert "unsharp=5:5:0.6:3:3:0.0" in sent_payloads[0]["text"]


def test_edge_cases_and_mobile_section_quality_view():
    import sys
    mobile_root = Path(__file__).resolve().parents[2] / "apps" / "bossman-mobile"
    if str(mobile_root) not in sys.path:
        sys.path.insert(0, str(mobile_root))
    from bossmobile import video as mvideo

    view = mvideo.section_quality_view()
    assert view["quality_guard"] == "safe"
    assert view["default_quality_guard"] == "safe"
    assert view["max_degradation_pct"] == 0.1
    assert view["diffusion_overhead_s"] == 0.0
    assert len(view["sections"]) == 5

    # Edge cases: empty inputs, mismatched shapes, invalid quality_guard, invalid bpm
    with pytest.raises(ValueError):
        video.compute_sequence_metrics([])
    with pytest.raises(ValueError):
        video.evaluate_quality_gate([], [])
    with pytest.raises(ValueError):
        video.evaluate_quality_gate(_midtone_textured_frame(16, 16), _midtone_textured_frame(16, 16), quality_guard="invalid")
    with pytest.raises(ValueError):
        video.enrich_section_prompt("   ")
    with pytest.raises(ValueError):
        video.smooth_boundary_frames([], [])
    with pytest.raises(ValueError):
        video.build_smart_transition_filter(0)
    with pytest.raises(ValueError):
        video.align_section_cuts_to_beats([1.0], bpm=0)

    # Frame dimension mismatch rolls back cleanly to original
    f1 = _midtone_textured_frame(32, 32)
    f2 = _midtone_textured_frame(16, 16)
    mismatch_gate = video.evaluate_quality_gate(f1, f2, quality_guard="safe")
    assert mismatch_gate["passed"] is False
    assert mismatch_gate["rolled_back"] is True
    assert mismatch_gate["action"] == "rollback_to_original"
    assert mismatch_gate["reason"] == "frame_dimensions_mismatch"
    assert np.array_equal(mismatch_gate["frames"][0], f1)
    assert np.array_equal(mismatch_gate["frame"], f1)
