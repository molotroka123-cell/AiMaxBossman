"""Character swap video via OpenRouter /api/v1/videos (owner-authorized live).

Generates a new video with the owner's character (from a photo) in a requested
setting, following an existing tool pattern (tools/seedance_shorts.py):
budget guard, JSON state, artifacts kept locally. Owner approved a paid run in
chat on 2026-09-28 with a hard per-job cost cap.

IMPORTANT: reference-guided generation, NOT motion transfer. OpenRouter video
models accept image references only, so the motion of the original clip is not
preserved (that requires Higgsfield Genjutsu, separate key).

Key: env OPENROUTER_API_KEY, else %LOCALAPPDATA%\\Bossman\\secrets\\openrouter-test.env
     (never logged, never committed).
Budget: refuses to submit when key remaining < --max-cost (default $1.20).
State: artifacts/video_factory/out/state_charswap.json

Usage:
  python tools/video_character_swap.py submit --photo PATH [--first-frame PATH] \
      [--model alibaba/wan-3.0] [--duration 16] [--resolution 480p] \
      [--aspect 9:16] [--max-cost 1.20] [--prompt TEXT | --prompt-file PATH]
  python tools/video_character_swap.py poll
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "video_factory" / "out"
STATE = OUT / "state_charswap.json"
API = "https://openrouter.ai/api/v1"


def utf8_console() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        secrets = (Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "secrets"
                   / "openrouter-test.env")
        if secrets.is_file():
            for line in secrets.read_text(encoding="utf-8-sig").splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("no OpenRouter key (env OPENROUTER_API_KEY or local secrets file)")
    return key


def headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def key_remaining(client: httpx.Client, key: str) -> float:
    r = client.get(f"{API}/auth/key", headers=headers(key), timeout=30)
    if r.status_code == 401:
        raise SystemExit("OPENROUTER_API_KEY invalid (401) - paste a fresh key and retry")
    r.raise_for_status()
    d = r.json()["data"]
    return float(d.get("limit_remaining") or 0.0)


def data_uri(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"file not found: {p}")
    mime = {".png": "image/png", ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
    b = p.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(b).decode()}"


def load_state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"jobs": []}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def submit() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--first-frame", default="")
    ap.add_argument("--model", default="alibaba/wan-3.0")
    ap.add_argument("--duration", type=int, default=16)
    ap.add_argument("--resolution", default="480p")
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--max-cost", type=float, default=1.20)
    ap.add_argument("--prompt", default="")
    ap.add_argument("--prompt-file", default="")
    a = ap.parse_args(sys.argv[2:])

    prompt = a.prompt
    if a.prompt_file:
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    if not prompt and not a.first_frame:
        raise SystemExit("prompt required when --first-frame is not set")

    key = load_key()
    with httpx.Client(timeout=120) as c:
        rem = key_remaining(c, key)
        print(f"budget: key remaining ${rem:.2f}, job cap ${a.max_cost:.2f}")
        if rem < a.max_cost:
            raise SystemExit(f"OWNER_REQUIRED: remaining ${rem:.2f} < cap ${a.max_cost:.2f}")

        payload: dict = {
            "model": a.model,
            "prompt": prompt,
            "duration": a.duration,
            "resolution": a.resolution,
            "aspect_ratio": a.aspect,
            "generate_audio": False,
            "input_references": [
                {"type": "image_url", "image_url": {"url": data_uri(a.photo)}}
            ],
        }
        if a.first_frame:
            payload["frame_images"] = [
                {"type": "image_url", "image_url": {"url": data_uri(a.first_frame)},
                 "frame_type": "first_frame"}
            ]

        r = c.post(f"{API}/videos", headers=headers(key), json=payload)
        if r.status_code in (200, 202):
            j = r.json()
            st = load_state()
            st["jobs"].append({
                "id": j["id"],
                "model": a.model,
                "duration": a.duration,
                "resolution": a.resolution,
                "aspect": a.aspect,
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": j.get("status", "pending"),
                "cost": 0.0,
            })
            save_state(st)
            print(f"submitted: job={j['id']} status={j.get('status')}")
        else:
            print(f"SUBMIT FAILED: HTTP {r.status_code} {r.text[:400]}")
            if r.status_code == 402:
                print("OWNER_REQUIRED: insufficient OpenRouter credits")


def poll() -> None:
    key = load_key()
    with httpx.Client(timeout=300) as c:
        while True:
            st = load_state()
            pending = [j for j in st["jobs"]
                       if j["status"] not in ("downloaded", "failed", "cancelled", "expired")]
            if not pending:
                total = sum(j.get("cost") or 0.0 for j in st["jobs"])
                print(f"nothing pending. total cost here: ${total:.2f}")
                break
            for job in pending:
                r = c.get(f"{API}/videos/{job['id']}", headers=headers(key))
                if r.status_code != 200:
                    print(f"poll {job['id']}: HTTP {r.status_code}")
                    continue
                d = r.json()
                job["status"] = d.get("status")
                cost = (d.get("usage") or {}).get("cost")
                if cost is not None:
                    job["cost"] = cost
                if d.get("status") == "completed":
                    urls = d.get("unsigned_urls") or []
                    if urls:
                        mp4 = OUT / f"charswap_{job['id']}.mp4"
                        dl = c.get(urls[0], headers=headers(key), timeout=300)
                        mp4.write_bytes(dl.content)
                        job["file"] = mp4.name
                        job["sha256"] = hashlib.sha256(dl.content).hexdigest()
                        job["bytes"] = len(dl.content)
                        job["status"] = "downloaded"
                        print(f"downloaded -> {mp4.name} ({len(dl.content)//1024} KB) "
                              f"cost=${cost} sha256={job['sha256'][:12]}…")
                    else:
                        print(f"completed but no urls: {d}")
                elif d.get("status") == "failed":
                    job["error"] = str(d.get("error"))[:400]
                    print(f"FAILED: {job['error']}")
                save_state(st)
            if any(j["status"] in ("pending", "in_progress") for j in load_state()["jobs"]):
                time.sleep(20)


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
