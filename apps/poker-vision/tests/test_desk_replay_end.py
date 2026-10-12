"""A replay desk must finish cleanly when the recording runs out of frames (12.10.2026: both owner recordings ended with
`AttributeError: 'NoneType' object has no attribute 't_ms'` after every frame was processed, and the hand history stayed empty)."""
import time

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from pokervision.service import VisionService


def _frames(d, n=3):
    d.mkdir(parents=True)
    for i in range(n):
        img = np.full((128, 72, 3), (30, 90, 50), np.uint8)
        cv2.imwrite(str(d / f"{i:06d}.png"), img)
    return d


def test_replay_desk_ends_without_error_when_frames_run_out(tmp_path):
    svc = VisionService(tmp_path / "data")
    svc.start_desk(source={"kind": "replay", "path": str(_frames(tmp_path / "frames"))}, adapter="ton_poker", desk_mode="observe")
    deadline = time.time() + 20
    while not svc.finished and time.time() < deadline:
        time.sleep(0.05)
    st = svc.status()
    assert svc.finished, "the replay desk never finished"
    assert svc.error is None, svc.error
    assert svc.frames == 3 and st.get("error") is None
