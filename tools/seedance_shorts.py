"""Seedance shorts batch — OpenRouter /api/v1/videos.

5 segments x 10s, 9:16, 720p, bytedance/seedance-2.0-mini.
Refs via input_references (data URI). Budget guard $3.40.
Usage: python tools/seedance_shorts.py submit <take> | poll <take>
State: artifacts/video_factory/out/state_take<N>.json
"""
from __future__ import annotations

import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "video_factory" / "out"
REFS = ROOT / "artifacts" / "video_factory" / "refs"
KEY = None  # from env
BUDGET_CAP = 3.40
MODEL = "bytedance/seedance-2.0-mini"

FACE = "the same young man: short dark hair, light brown beard, tattoos on neck and hands"

SEGMENTS = [
    {
        "id": "seg1_plane",
        "refs": ["2_la_stylized"],
        "prompt": (
            "Vertical 9:16 handheld selfie vlog, YouTube Shorts style. "
            f"{FACE}, wearing a beige-black camouflage bomber jacket over a tee and baggy grey jeans, "
            "films himself walking through a sunny Los Angeles airport terminal pulling a suitcase, "
            "airplanes visible through huge windows. He speaks to camera in English with a heavy Russian accent: "
            "\"LA was cool... but Moscow is calling me, bro.\" "
            "Excited energy, golden light, authentic vlog camera shake, continuous single shot."
        ),
    },
    {
        "id": "seg2_kremlin",
        "refs": ["3_stylized"],
        "prompt": (
            "Vertical 9:16 handheld selfie vlog, YouTube Shorts style. "
            f"{FACE}, now wearing a dark green pixelated field jacket with a small Russian flag patch on the sleeve, "
            "stands on Red Square in Moscow with St Basil's Cathedral and the Kremlin towers behind him under an overcast sky. "
            "He speaks to camera in English with a thick Russian accent: \"Moscow, baby! I am home!\" "
            "Wide amazed gesture at the cathedral, blurred tourists behind, continuous single shot."
        ),
    },
    {
        "id": "seg3_club",
        "refs": ["3_stylized"],
        "prompt": (
            "Vertical 9:16 handheld selfie vlog, YouTube Shorts style, night scene. "
            f"{FACE} in the same dark green pixelated field jacket with Russian flag patch, "
            "inside an upscale Moscow nightclub: neon purple and blue lights, crowd dancing behind him, expensive cocktail in hand. "
            "He shouts to camera in English with a Russian accent: \"Moscow nightlife — so expensive, bro!\" "
            "Pumping music vibe, dynamic neon glow, continuous single shot."
        ),
    },
    {
        "id": "seg4_prices",
        "refs": ["3_stylized"],
        "prompt": (
            "Vertical 9:16 handheld selfie vlog, YouTube Shorts style. "
            f"{FACE} in the same dark green pixelated field jacket, sits at a small apartment kitchen table at night, "
            "counting ruble banknotes with a shocked face, a restaurant receipt and phone on the table; "
            "then he stands and slams a train ticket on the table. "
            "He says in English with a Russian accent: \"Ten dollars for a salad?! I am going to Perm.\" "
            "Moody kitchen light, continuous single shot."
        ),
    },
    {
        "id": "seg5_perm",
        "refs": ["3_stylized"],
        "prompt": (
            "Vertical 9:16 handheld selfie vlog, YouTube Shorts style. "
            f"{FACE} in the same dark green pixelated field jacket, inside the grand Soviet-era main hall of Perm-2 railway station in Perm, Russia: "
            "high arched ceiling, departure boards, marble columns, travelers with bags. He walks to a small cheburek eatery stall, "
            "takes a huge bite of a golden fried cheburek, his face turns green, he clutches his stomach and runs for the exit. "
            "He mumbles in English with a Russian accent: \"Oh no... bad cheburek...\" "
            "Comedic tragic timing, station announcement echo, continuous single shot."
        ),
    },
]


def data_uri(name: str) -> str:
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = REFS / (name + ext)
        if p.exists():
            b = p.read_bytes()
            mime = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(b).decode()}"
    raise FileNotFoundError(f"ref image not found: {REFS / name}.(*)")


def state_path(take: int) -> Path:
    return OUT / f"state_take{take}.json"


def load_state(take: int) -> dict:
    p = state_path(take)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"jobs": {}, "cost_total": 0.0, "done": {}}


def save_state(take: int, st: dict) -> None:
    state_path(take).write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def headers() -> dict:
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def remaining_budget(client: httpx.Client) -> float:
    r = client.get("https://openrouter.ai/api/v1/auth/key", headers=headers(), timeout=30)
    d = r.json()["data"]
    return (d.get("limit_remaining") or 0.0), d.get("expires_at")


def submit(take: int) -> None:
    st = load_state(take)
    with httpx.Client() as client:
        rem, exp = remaining_budget(client)
        print(f"budget remaining=${rem:.2f} key_expires={exp}")
        if rem < 0.35:
            raise SystemExit("budget too low to start")
        for seg in SEGMENTS:
            sid = seg["id"]
            if sid in st["jobs"]:
                print(f"skip {sid} (already submitted)")
                continue
            payload = {
                "model": MODEL,
                "prompt": seg["prompt"],
                "duration": 10,
                "resolution": "720p",
                "aspect_ratio": "9:16",
                "generate_audio": True,
                "input_references": [
                    {"type": "image_url", "image_url": {"url": data_uri(ref)}} for ref in seg["refs"]
                ],
            }
            r = client.post("https://openrouter.ai/api/v1/videos", headers=headers(), json=payload, timeout=120)
            if r.status_code in (200, 202):
                j = r.json()
                st["jobs"][sid] = {"id": j["id"], "polling_url": j.get("polling_url"), "status": j.get("status")}
                print(f"submitted {sid}: job={j['id']}")
                rem, exp = remaining_budget(client)
                if rem < 0.40:
                    print(f"budget guard: remaining ${rem:.2f} < 0.40, stop submitting further segments")
                    save_state(take, st)
                    break
            else:
                st["jobs"][sid] = {"id": None, "status": "submit_failed", "error": r.text[:400]}
                print(f"SUBMIT FAILED {sid}: HTTP {r.status_code} {r.text[:200]}")
            save_state(take, st)


def poll(take: int) -> None:
    st = load_state(take)
    with httpx.Client(timeout=60) as client:
        while True:
            pending = [s for s, j in st["jobs"].items() if j.get("status") not in ("completed", "failed", "submit_failed", "downloaded")]
            rem, exp = remaining_budget(client)
            print(f"pending={pending} spent_here=${st['cost_total']:.2f} key_remaining=${rem:.2f}")
            if not pending:
                break
            for sid in pending:
                job = st["jobs"][sid]
                if not job.get("id"):
                    continue
                r = client.get(f"https://openrouter.ai/api/v1/videos/{job['id']}", headers=headers())
                if r.status_code != 200:
                    print(f"poll {sid}: HTTP {r.status_code}")
                    continue
                d = r.json()
                job["status"] = d.get("status")
                cost = (d.get("usage") or {}).get("cost")
                if cost is not None:
                    job["cost"] = cost
                if d.get("status") == "completed":
                    urls = d.get("unsigned_urls") or []
                    if urls:
                        mp4 = OUT / f"{sid}_take{take}.mp4"
                        dl = client.get(urls[0], headers=headers(), timeout=300)
                        mp4.write_bytes(dl.content)
                        job["file"] = mp4.name
                        job["status"] = "downloaded"
                        print(f"downloaded {sid} -> {mp4.name} ({len(dl.content)//1024} KB) cost=${cost}")
                    st["cost_total"] = sum(j.get("cost") or 0 for j in st["jobs"].values())
                elif d.get("status") == "failed":
                    job["error"] = str(d.get("error"))[:300]
                    print(f"FAILED {sid}: {job['error']}")
                save_state(take, st)
            if any(j.get("status") in ("pending", "in_progress") for j in st["jobs"].values()):
                time.sleep(20)
    total = sum(j.get("cost") or 0 for j in st["jobs"].values())
    print(f"TAKE {take} DONE. total_cost=${total:.2f}")


if __name__ == "__main__":
    cmd, take = sys.argv[1], int(sys.argv[2])
    OUT.mkdir(parents=True, exist_ok=True)
    if cmd == "submit":
        submit(take)
    elif cmd == "poll":
        poll(take)
    else:
        raise SystemExit("usage: submit|poll <take>")
