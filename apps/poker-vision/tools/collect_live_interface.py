"""Bounded private collection from the existing observe-only Poker Vision API.

Numeric OCR drafts are never approved labels. Non-table screens are discarded.
No strategy, input control, network model, or installed profile modification.
"""
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

import cv2
import numpy as np


BASE = "http://127.0.0.1:8931"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=10))


def run(root, seconds=1800):
    root = Path(root).resolve()
    if any((p / ".git").exists() for p in (root, *root.parents)):
        raise ValueError("Private data must stay outside Git")
    root.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + seconds
    while True:
        windows = [w for w in get("/api/v1/sources")["sources"]
                   if w["title"] == "AirPlay Video Stream"]
        if len(windows) == 1:
            break
        (root / "progress.json").write_text(json.dumps({"status": "WAITING_FOR_AIRPLAY",
            "saved": 0, "training": "NOT_RUN", "profile_activation": False}), encoding="utf-8")
        if time.monotonic() >= deadline:
            (root / "progress.json").write_text(json.dumps({"status": "STOPPED",
                "stop_reason": "duration_limit", "saved": 0, "training": "NOT_RUN",
                "profile_activation": False}), encoding="utf-8")
            return
        time.sleep(5)
    initial = get("/api/v1/status")
    if not initial["running"]:
        body = json.dumps({"mode": "window", "adapter": "ton_poker", "act": False,
                           "window": {"title": windows[0]["title"], "rect": windows[0]["rect"]},
                           "max_frames": 14400, "interval_s": .25}).encode()
        request = urllib.request.Request(BASE + "/api/v1/session", data=body,
                                         headers={"Content-Type": "application/json"})
        initial = json.load(urllib.request.urlopen(request, timeout=10))
    session_id = initial["session"]["id"]
    if initial["session"]["act"] or initial["session"]["mode"] != "window":
        raise ValueError("An observe-only window session is required")
    windows = [w for w in get("/api/v1/sources")["sources"]
               if w["title"] == "AirPlay Video Stream"]
    if len(windows) != 1:
        raise ValueError("Expected one selected AirPlay surface")
    identity = (windows[0]["id"], windows[0]["rect"])
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    start = time.monotonic()
    saved = skipped = size = 0
    last_seq = None
    reason = "duration_limit"
    report = {"status": "COLLECTING", "session": session_id,
              "saved": 0, "skipped": 0, "training": "NOT_RUN",
              "labels": "UNVERIFIED_OCR_DRAFTS", "profile_activation": False,
              "cloud_calls": 0, "capture_limitation": "Screen crop can be occluded",
              "table_filter": "Heuristic OCR pot/blinds marker; manual review required"}
    try:
        with (root / "annotations.jsonl").open("x", encoding="utf-8") as out:
            while time.monotonic() < deadline:
                status = get("/api/v1/status")
                if (status["error"] or not status["running"] or
                        status["session"]["id"] != session_id or status["session"]["act"]):
                    reason = "session_stopped_changed_or_failed"
                    break
                now = [w for w in get("/api/v1/sources")["sources"]
                       if w["title"] == "AirPlay Video Stream"]
                if len(now) != 1 or (now[0]["id"], now[0]["rect"]) != identity:
                    reason = "selected_surface_lost_or_moved"
                    break
                with urllib.request.urlopen(BASE + "/api/v1/frame.jpg", timeout=10) as response:
                    data = response.read()
                    seq = response.headers.get("X-Frame-Seq")
                if seq == last_seq:
                    reason = "stale_capture"
                    break
                last_seq = seq
                im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if im is None:
                    raise ValueError("Invalid backend JPEG")
                result, _ = ocr(im)
                texts = [t.strip().lower() for _, t, confidence in result or []
                         if confidence >= .8]
                table = any(re.search(r"\b(total\s*pot|pot|blinds)\b|блайнд|общий банк", t)
                            for t in texts)
                if table:
                    name = f"frame-{int(seq):08d}.jpg"
                    (root / name).write_bytes(data)
                    h, w = im.shape[:2]
                    numbers = [{"text": text, "confidence": float(score),
                                "polygon_normalized": [[float(x)/w, float(y)/h] for x, y in box]}
                               for box, text, score in result or []
                               if re.fullmatch(r'[$€£]?\s*\d[\d,. ]*[kKmM]?', text.strip())]
                    out.write(json.dumps({"frame": name, "sha256": hashlib.sha256(data).hexdigest(),
                                          "t_ms": int((time.monotonic()-start)*1000),
                                          "numeric_tokens": numbers, "reviewed": False,
                                          "usable_for_training": False, "truth": None}) + "\n")
                    out.flush()
                    saved += 1
                    size += len(data)
                else:
                    skipped += 1
                report.update(saved=saved, skipped=skipped, bytes=size, sequence=seq)
                (root / "progress.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                if size >= 250_000_000:
                    reason = "byte_budget"
                    break
                time.sleep(2)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
    report.update(status="STOPPED", stop_reason=reason, elapsed_s=time.monotonic()-start)
    # Do not leave a screen crop running after collection or loss of the source.
    try:
        current = get("/api/v1/status")
        if current["session"]["id"] == session_id:
            urllib.request.urlopen(urllib.request.Request(BASE + "/api/v1/stop", data=b""), timeout=10).close()
    except Exception as exc:
        report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
    (root / "progress.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    import sys
    run(sys.argv[1])
