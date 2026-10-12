"""Private, bounded video recording of the existing observe-only window session.

Retains timestamps and prepares unreviewed frame datasets after each segment.
No audio, strategy labels, model promotion, cloud calls, or game inputs.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.request

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pokervision.recording_dataset import prepare, _private_root

BASE = "http://127.0.0.1:8931"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


def run(destination, seconds=1800):
    root = _private_root(Path(destination))
    root.mkdir(parents=True, exist_ok=False)
    report = {"status": "WAITING_FOR_AIRPLAY", "video_frames": 0,
              "segments": [], "training": "NOT_RUN", "labels": "UNREVIEWED",
              "audio": False, "cloud_calls": 0, "profile_activation": False,
              "limitation": "4 FPS screen crop; occlusion possible; capture timestamps retained"}
    def progress():
        (root / "progress.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    progress()
    deadline = time.monotonic() + seconds
    writer = None
    stamps = None
    segment_path = None
    segment_started = None
    segment_frames = 0
    session_id = None
    source_id = None
    source_rect = None
    last_seq = None
    last_check = 0
    stale_since = None

    def close_segment():
        nonlocal writer, stamps, segment_path, segment_frames
        if writer is None:
            return
        writer.release()
        writer = None
        stamps.close()
        sha = hashlib.sha256(segment_path.read_bytes()).hexdigest()
        entry = {"file": segment_path.name, "sha256": sha, "frames": segment_frames,
                 "fps": 4, "actual_timing": segment_path.with_suffix('.jsonl').name}
        report["segments"].append(entry)
        progress()
        # Every segment belongs to the SAME session group/split. No blind test
        # or accurate labels are inferred from the video or from OCR drafts.
        entry["dataset"] = prepare(segment_path, root / "datasets", game="unknown",
                                   split="train", session_group="live_owner_20261011",
                                   sample_fps=1, max_frames=100)
        progress()

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_check >= 1:
                status = get("/api/v1/status")
                windows = [w for w in get("/api/v1/sources")["sources"]
                           if w["title"] == "AirPlay Video Stream"]
                last_check = now
                if session_id is None:
                    if len(windows) != 1 or not status["running"]:
                        time.sleep(1)
                        continue
                    if status["session"]["mode"] != "window" or status["session"]["act"]:
                        raise ValueError("Observe-only window session required")
                    if status["session"].get("adapter") != "ton_poker":
                        raise ValueError("Unexpected backend adapter")
                    session_id = status["session"]["id"]
                    source_id, source_rect = windows[0]["id"], windows[0]["rect"]
                    report.update(status="RECORDING", session=session_id)
                    progress()
                elif (len(windows) != 1 or windows[0]["id"] != source_id or
                      windows[0]["rect"] != source_rect or not status["running"] or
                      status["error"] or status["session"]["id"] != session_id or
                      status["session"]["act"]):
                    report["stop_reason"] = "selected_source_or_observe_session_lost"
                    break
                if shutil.disk_usage(root).free < 1_000_000_000:
                    report["stop_reason"] = "disk_free_budget"
                    break
            with urllib.request.urlopen(BASE + "/api/v1/frame.jpg", timeout=10) as response:
                data = response.read()
                seq = response.headers.get("X-Frame-Seq")
            if seq == last_seq:
                stale_since = stale_since or now
                if now - stale_since > 5:
                    report["stop_reason"] = "stale_capture"
                    break
                time.sleep(.1)
                continue
            stale_since = None
            last_seq = seq
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("Invalid backend frame")
            if writer is None:
                segment_path = root / f"segment-{len(report['segments']):04}.mp4"
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(str(segment_path), cv2.VideoWriter_fourcc(*"mp4v"), 4, (w, h))
                if not writer.isOpened():
                    raise ValueError("Video encoder did not open")
                stamps = segment_path.with_suffix('.jsonl').open('x', encoding='utf-8')
                segment_started = now
                segment_frames = 0
            writer.write(frame)
            stamps.write(json.dumps({"frame": segment_frames, "sequence": seq,
                                    "captured_unix_s": time.time(),
                                    "elapsed_s": now-segment_started}) + '\n')
            stamps.flush()
            segment_frames += 1
            report["video_frames"] += 1
            if segment_frames % 20 == 0:
                progress()
            if segment_frames >= 240:
                close_segment()
                if sum(p.stat().st_size for p in root.glob('*.mp4')) >= 1_000_000_000:
                    report["stop_reason"] = "video_byte_budget"
                    break
            time.sleep(.25)
        report.setdefault("stop_reason", "duration_limit")
    except Exception as exc:
        report["stop_reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            close_segment()
        except Exception as exc:
            report["dataset_error"] = f"{type(exc).__name__}: {exc}"
        report["status"] = "STOPPED"
        progress()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    run(sys.argv[1])
