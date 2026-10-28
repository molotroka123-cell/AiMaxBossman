"""Synthetic, local video checks for the optional RVM compositing adapter."""
import json
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from tools.motion_studio.genjutsu_e_video import render_composite_e_video


pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg and ffprobe required")


def _video(path, color, size, fps, frames):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color={color}:s={size}:r={fps}", "-frames:v", str(frames),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


@pytest.fixture
def inputs(tmp_path):
    base, head, source, mask = (tmp_path / f"{n}.mp4" for n in ("base", "head", "source", "mask"))
    _video(base, "blue", "36x64", 4, 4)
    _video(head, "white", "16x16", 4, 4)
    _video(source, "red", "36x64", 8, 16)
    _video(mask, "black", "36x64", 4, 4)
    boxes = tmp_path / "boxes.json"
    boxes.write_text(json.dumps({"src_w": 36, "src_h": 64, "fps": 4, "frames": 4, "t0": 1,
                                 "boxes": [{"i": i, "x": 2, "y": 2, "s": 16} for i in range(4)]}))
    return dict(base=base, head=head, source=source, mask=mask, boxes=boxes, start=1,
                output=tmp_path / "out.mp4", matte=lambda frame: (frame.astype(np.float32), np.ones(frame.shape[:2], np.float32)))


def test_preserves_background_geometry_and_complete_frames(inputs):
    result = render_composite_e_video(**inputs)
    capture = cv2.VideoCapture(str(inputs["output"]))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    assert len(frames) == result["frames"] == 4
    assert frames[0].shape == (64, 36, 3)
    assert frames[0][40, 20, 2] > 200  # black person mask restores red source
    probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(inputs["output"])], text=True)
    assert not probe.strip()


def test_short_input_rejected_without_partial_publication(inputs):
    _video(inputs["head"], "white", "16x16", 4, 3)
    with pytest.raises(ValueError):
        render_composite_e_video(**inputs)
    assert not inputs["output"].exists()


def test_bad_coordinates_rejected_without_output(inputs):
    data = json.loads(inputs["boxes"].read_text())
    data["boxes"][2]["x"] = 99
    inputs["boxes"].write_text(json.dumps(data))
    with pytest.raises(ValueError):
        render_composite_e_video(**inputs)
    assert not inputs["output"].exists()


def test_non_portrait_source_rejected(inputs):
    _video(inputs["source"], "red", "64x36", 8, 16)
    with pytest.raises(ValueError):
        render_composite_e_video(**inputs)
    assert not inputs["output"].exists()


def test_existing_output_is_not_replaced(inputs):
    inputs["output"].write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        render_composite_e_video(**inputs)
    assert inputs["output"].read_bytes() == b"existing"


def test_matte_failure_after_frames_never_publishes_partial_file(inputs):
    calls = 0

    def failing_matte(frame):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError("synthetic matte failure")
        return frame.astype(np.float32), np.ones(frame.shape[:2], np.float32)

    inputs["matte"] = failing_matte
    with pytest.raises(ValueError, match="synthetic matte failure"):
        render_composite_e_video(**inputs)
    assert calls == 3
    assert not inputs["output"].exists()
    assert not list(inputs["output"].parent.glob(".genjutsu-e-*"))


def test_source_window_matches_start_and_sampling(inputs):
    frames = [np.full((64, 36, 3), (0, 0, i * 12), dtype=np.uint8) for i in range(16)]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                    "-s", "36x64", "-r", "8", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(inputs["source"])], input=b"".join(frame.tobytes() for frame in frames), check=True)
    render_composite_e_video(**inputs)
    cap = cv2.VideoCapture(str(inputs["output"]))
    source = cv2.VideoCapture(str(inputs["source"]))
    for expected in (8, 10, 12, 14):
        ok, frame = cap.read()
        assert ok
        source.set(cv2.CAP_PROP_POS_FRAMES, expected)
        ok, original = source.read()
        assert ok
        assert abs(int(frame[40, 20, 2]) - int(original[40, 20, 2])) < 8
    cap.release()
    source.release()


def test_window_mismatch_rejected(inputs):
    inputs["start"] = 0
    with pytest.raises(ValueError, match="boxes"):
        render_composite_e_video(**inputs)
    assert not inputs["output"].exists()
