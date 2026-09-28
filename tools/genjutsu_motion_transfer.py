"""Genjutsu motion transfer via Higgsfield REST API (owner-authorized live).

Motion transfer: takes a reference video + a character image, returns a new
video where the person from the image performs the original motion/camera.
Owner approved a paid test run in chat on 2026-09-28 (10 s @ 480p ~ $3.18).

Model endpoint: POST /higgsfield/genjutsu/motion-transfer/v1.0
Uploads: presigned URLs via POST /files/generate-upload-url (docs/concepts/file-uploads).
Key: env HIGGSFIELD_API_KEY ("KEY_ID:KEY_SECRET"), else
     %LOCALAPPDATA%\\Bossman\\secrets\\openrouter-test.env (never logged).
State: artifacts/video_factory/out/state_genjutsu.json

Usage:
  python tools/genjutsu_motion_transfer.py submit --video PATH --image PATH \
      [--resolution 480p] [--prompt ""]
  python tools/genjutsu_motion_transfer.py poll
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "video_factory" / "out"
STATE = OUT / "state_genjutsu.json"
API = "https://api.higgsfield.ai"
MODEL_PATH = "higgsfield/genjutsu/motion-transfer/v1.0"
PRICE_PER_SEC = {"480p": 0.318, "720p": 0.681, "1080p": 1.632}


def utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_hf_key() -> str:
    key = os.environ.get("HIGGSFIELD_API_KEY", "")
    if not key:
        secrets = (Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets"
                   / "openrouter-test.env")
        if secrets.is_file():
            for line in secrets.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("HIGGSFIELD_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no Higgsfield key (env HIGGSFIELD_API_KEY or local secrets file)")
    return key


def auth(key: str) -> dict:
    return {"Authorization": f"Key {key}"}


def video_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def upload(client: httpx.Client, key: str, path: Path, content_type: str) -> str:
    r = client.post(f"{API}/files/generate-upload-url", headers={**auth(key),
                    "Content-Type": "application/json"},
                    json={"content_type": content_type}, timeout=60)
    r.raise_for_status()
    d = r.json()
    up = client.put(d["upload_url"], headers=d.get("upload_headers") or
                    {"Content-Type": content_type}, content=path.read_bytes(), timeout=600)
    up.raise_for_status()
    return d["public_url"]


def load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"jobs": []}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def submit() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--resolution", default="480p", choices=list(PRICE_PER_SEC))
    ap.add_argument("--prompt", default="")
    a = ap.parse_args(sys.argv[2:])

    video, image = Path(a.video), Path(a.image)
    if not video.is_file():
        raise SystemExit(f"video not found: {video}")
    if not image.is_file():
        raise SystemExit(f"image not found: {image}")

    dur = video_duration(video)
    est = math.ceil(dur) * PRICE_PER_SEC[a.resolution] if dur else -1.0
    print(f"input video: {dur:.2f}s -> estimated cost ${est:.2f} ({a.resolution})")

    key = load_hf_key()
    with httpx.Client(timeout=600) as c:
        print("uploading video…")
        vurl = upload(c, key, video, "video/mp4")
        print("uploading image…")
        iurl = upload(c, key, image, "image/jpeg")
        payload = {"prompt": a.prompt, "video_url": vurl,
                   "image_urls": [iurl], "resolution": a.resolution}
        r = c.post(f"{API}/{MODEL_PATH}", headers={**auth(key),
                   "Content-Type": "application/json"}, json=payload, timeout=120)
        if r.status_code in (200, 202):
            j = r.json()
            st = load_state()
            st["jobs"].append({
                "request_id": j.get("request_id"),
                "status_url": j.get("status_url"),
                "model": MODEL_PATH,
                "resolution": a.resolution,
                "input_video": video.name,
                "input_image": image.name,
                "estimated_cost": round(est, 2),
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": j.get("status", "queued"),
                "cost": 0.0,
            })
            save_state(st)
            print(f"submitted: request_id={j.get('request_id')} status={j.get('status')}")
        else:
            print(f"SUBMIT FAILED: HTTP {r.status_code} {r.text[:500]}")


def poll() -> None:
    key = load_hf_key()
    with httpx.Client(timeout=120) as c:
        while True:
            st = load_state()
            pending = [j for j in st["jobs"]
                       if j["status"] not in ("completed", "failed", "canceled", "nsfw")]
            if not pending:
                print("nothing pending on higgsfield.")
                break
            for job in pending:
                url = job.get("status_url") or f"{API}/requests/{job['request_id']}/status"
                r = c.get(url, headers=auth(key))
                if r.status_code != 200:
                    print(f"poll {job['request_id']}: HTTP {r.status_code}")
                    continue
                d = r.json()
                job["status"] = d.get("status")
                if d.get("status") == "completed":
                    vurl = (d.get("video") or {}).get("url")
                    if vurl:
                        mp4 = OUT / f"genjutsu_{job['request_id']}.mp4"
                        dl = c.get(vurl, timeout=600)
                        mp4.write_bytes(dl.content)
                        job["file"] = mp4.name
                        job["sha256"] = hashlib.sha256(dl.content).hexdigest()
                        job["bytes"] = len(dl.content)
                        job["cost"] = job.get("estimated_cost")
                        print(f"downloaded -> {mp4.name} ({len(dl.content)//1024} KB)")
                    else:
                        print(f"completed, no video url: {json.dumps(d)[:300]}")
                elif d.get("status") in ("failed", "nsfw", "canceled"):
                    job["error"] = str(d.get("error"))[:400]
                    print(f"{job['status'].upper()}: {job['error']}")
                else:
                    print(f"poll {job['request_id']}: {job['status']}")
                save_state(st)
            alive = [j for j in load_state()["jobs"]
                     if j["status"] in ("queued", "in_progress")]
            if alive:
                time.sleep(15)


if __name__ == "__main__":
    utf8_console()
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "submit":
        submit()
    elif cmd == "poll":
        poll()
    else:
        raise SystemExit("usage: submit|poll (see module docstring)")
