import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from pokervision.api import create_app


def make_frames(d, n=6):
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = np.full((900, 520, 3), 20, np.uint8)
        cv2.imwrite(str(d / f"{i:04d}.png"), img)
    return d


@pytest.fixture()
def client(tmp_path):
    import shutil
    prof = tmp_path / "profiles"                                     # tests must never write into the repository's profile directory
    shutil.copytree(Path(__file__).resolve().parents[1] / "pokervision" / "adapters" / "profiles", prof)
    return TestClient(create_app(tmp_path / "data", prof))


def wait_done(c, secs=20):
    t = time.time()
    while time.time() - t < secs:
        s = c.get("/api/v1/status").json()
        if s["finished"]:
            return s
        time.sleep(0.1)
    raise AssertionError("session did not finish")


def test_replay_session_produces_status_state_overlay_history(client, tmp_path):
    frames = make_frames(tmp_path / "frames")
    r = client.post("/api/v1/session", json={"mode": "replay", "adapter": "poker_train", "path": str(frames)})
    assert r.status_code == 200, r.text
    s = wait_done(client)
    assert s["frames"] == 6 and s["error"] is None and s["latency_ms"]["p50"] is not None
    st = client.get("/api/v1/state").json()
    assert st["fields"]["hero_cards"][0]["status"] == "UNKNOWN"            # empty frame: UNKNOWN, nothing invented
    assert st["uncertainty"] and st["uncertainty"][0]["why"]
    assert client.get("/api/v1/overlay.png").headers["content-type"] == "image/png"
    h = client.get("/api/v1/history").json()
    assert isinstance(h["hands"], list)


def test_stop_halts_a_running_session(client, tmp_path):
    frames = make_frames(tmp_path / "frames", 400)
    assert client.post("/api/v1/session", json={"mode": "replay", "adapter": "poker_train", "path": str(frames), "interval_s": 0.05}).status_code == 200
    time.sleep(0.4)
    out = client.post("/api/v1/stop").json()
    assert out["stopped"] is True and out["running"] is False and out["frames"] < 400
    n = out["frames"]; time.sleep(0.3)
    assert client.get("/api/v1/status").json()["frames"] == n               # nothing is processed after STOP


def test_second_session_while_running_is_refused(client, tmp_path):
    frames = make_frames(tmp_path / "frames", 400)
    client.post("/api/v1/session", json={"mode": "replay", "adapter": "poker_train", "path": str(frames), "interval_s": 0.05})
    assert client.post("/api/v1/session", json={"mode": "replay", "adapter": "poker_train", "path": str(frames)}).status_code == 409
    client.post("/api/v1/stop")


@pytest.mark.parametrize("body", [
    {"mode": "trainer", "adapter": "poker_train", "url": "https://www.example.com/", "act": True},
    {"mode": "trainer", "adapter": "ton_poker", "url": "http://127.0.0.1:3000/"},
    {"mode": "window", "adapter": "ton_poker", "act": True, "window": {"rect": [0, 0, 500, 500]}},
    {"mode": "window", "adapter": "poker_train", "window": {"rect": [0, 0, 500, 900]}},      # its WSOP-like theme must not read a captured client
    {"mode": "replay", "adapter": "poker_train", "path": "/nonexistent-dir"},
])
def test_refusals(client, body):
    r = client.post("/api/v1/session", json=body)
    assert r.status_code in (400, 403), r.text


def test_replay_of_ton_without_profile_is_unknown_not_an_error(client, tmp_path):
    frames = make_frames(tmp_path / "frames", 3)
    assert client.post("/api/v1/session", json={"mode": "replay", "adapter": "ton_poker", "path": str(frames)}).status_code == 200
    wait_done(client)
    st = client.get("/api/v1/state").json()
    assert all(f["status"] == "UNKNOWN" for f in st["fields"]["hero_cards"])
    assert "calibr" in json.dumps(st["uncertainty"], ensure_ascii=False)


def test_token_required_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("POKERVISION_TOKEN", "t" * 40)
    c = TestClient(create_app(tmp_path / "d"))
    assert c.get("/api/v1/status").status_code == 401
    assert c.get("/api/v1/status", headers={"Authorization": "Bearer " + "t" * 40}).status_code == 200


# ---- calibration of a new layout through the backend (ROI adapter) ---------------------------------------------------
def _labelled_dir(d, values, swap=None):
    from .test_roi_gate import frame
    d.mkdir(parents=True, exist_ok=True)
    truth = {}
    for i, v in enumerate(values):
        cv2.imwrite(str(d / f"{i:03d}.png"), frame(v).bgr)
        truth[f"{i:03d}.png"] = {"pot": v.translate(swap) if swap else v}
    (d / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    return d


def test_calibrate_verify_save_then_replay_reads_the_new_layout(client, tmp_path):
    from .test_roi_gate import ROIS, values
    cal = _labelled_dir(tmp_path / "cal", ["1234567890", "9081726354", "5566778899", "1020304050"] + values(30, 1))
    held = _labelled_dir(tmp_path / "held", values(20, 99))
    r = client.post("/api/v1/calibrate", json={"adapter": "ton_poker", "labelled_dir": str(cal), "heldout_dir": str(held), "rois": ROIS}).json()
    assert "report" in r, r
    assert r["report"]["passed"] and r["saved"] is True
    r2 = client.post("/api/v1/calibrate", json={"adapter": "ton_poker", "labelled_dir": str(cal), "heldout_dir": str(cal), "rois": ROIS})
    assert r2.status_code == 400                                              # held-out must be different frames


def test_poisoned_calibration_is_not_saved(client, tmp_path):
    from .test_roi_gate import ROIS, values
    cal = _labelled_dir(tmp_path / "cal", ["1234567890", "9081726354", "5566778899", "1020304050"] + values(30, 1), swap=str.maketrans("17", "71"))
    held = _labelled_dir(tmp_path / "held", values(20, 99))
    r = client.post("/api/v1/calibrate", json={"adapter": "ton_poker", "labelled_dir": str(cal), "heldout_dir": str(held), "rois": ROIS}).json()
    assert r["report"]["passed"] is False and r["saved"] is False


def test_saved_profile_can_be_reverified_on_new_frames_and_a_failing_check_withdraws_trust(client, tmp_path):
    from .test_roi_gate import ROIS, values
    cal = _labelled_dir(tmp_path / "cal", ["1234567890", "9081726354", "5566778899", "1020304050"] + values(30, 1))
    held = _labelled_dir(tmp_path / "held", values(20, 99))
    assert client.post("/api/v1/calibrate", json={"adapter": "ton_poker", "labelled_dir": str(cal), "heldout_dir": str(held), "rois": ROIS}).json()["saved"]
    good = _labelled_dir(tmp_path / "again", values(20, 7))
    r = client.post("/api/v1/verify", json={"adapter": "ton_poker", "heldout_dir": str(good), "context": "same theme, new deals"}).json()
    assert r["passed"] is True and r["frames"] >= 8
    bad = _labelled_dir(tmp_path / "bad", values(20, 8), swap=str.maketrans("17", "71"))     # labels no longer match what is on screen (a changed look)
    r2 = client.post("/api/v1/verify", json={"adapter": "ton_poker", "heldout_dir": str(bad), "context": "other theme"}).json()
    assert r2["passed"] is False
    assert client.post("/api/v1/verify", json={"adapter": "ton_poker", "heldout_dir": str(good), "context": "after failure"}).status_code == 200
