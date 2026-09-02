"""Hailuo rescue short — OpenRouter /api/v1/videos, minimax/hailuo-3-max.

Story (user prompt, 2026-09-02): green-face guy + bandolier guy walk on Red Square;
a scammer (chain necklace) rides up selling bracelets; a bunny-suit superhero flies
in and punches the scammer away. 2 segments x 10s, 768p -> final 720p concat.

Usage: python tools/hailuo_rescue.py stylize|submit|poll|status
State: artifacts/video_factory/out/state_hailuo.json
API log: artifacts/video_factory/out/hailuo_api_log.jsonl (full req/resp log)
Key: env OPENROUTER_API_KEY (never logged, never committed).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "video_factory" / "out"
REFS = ROOT / "artifacts" / "video_factory" / "refs"
STATE = OUT / "state_hailuo.json"
APILOG = OUT / "hailuo_api_log.jsonl"
BASE = "https://openrouter.ai/api/v1"
MODEL = "minimax/hailuo-3-max"
IMAGE_MODEL = "google/gemini-2.5-flash-image"
# 4+ refs per image: only gemini-3.1-flash-image accepts them on the Image API
COMPOSE_MODEL = "google/gemini-3.1-flash-image"
RES = "768p"
AR = "16:9"
DUR = 10
BUDGET_FLOOR = 0.10

REF_FILES = {
    "green": "ae69bbeb-637c-42a9-b0d7-caba44f70727.jpg",
    "bandolier": "77d2cc2c-011f-4c72-b908-f47e75081f0f.jpg",
    "scammer": "a23c9cfa-0e07-4497-a214-145eb112f2bb.jpg",
    "hero_bunny": "8a6a88b9-885c-4e0f-ac9a-56bca1d04dbc.png",
}

GREEN = (
    "the first friend: a man with short dark spiky hair and a bright green painted face "
    "(green zelenka paint covers his whole face), wearing a light grey button-up shirt"
)
BAND = (
    "the second friend: a young man with messy platinum blond hair wearing a long black "
    "coat over an open white shirt with a bullet bandolier strap across his chest"
)
SCAM = (
    "the scammer: a man with short light brown hair, light stubble, wearing a black jacket "
    "over a white t-shirt and a chunky silver chain necklace"
)
HERO = (
    "the superhero: a muscular hero in a glossy white bunny-suit costume with big white "
    "rabbit ears on his head, a black bunny emblem on the chest, white rabbit half-mask "
    "over his eyes, tattoos on his neck and hands"
)

# i2v mode: hailuo-3-max supports first_frame images, NOT input_references.
# First frames are composed from character refs via gemini-2.5-flash-image.
SEGMENTS = [
    {
        "id": "hseg1_walk_scammer",
        "compose": {
            "refs": ["green", "bandolier", "scammer"],
            "instruction": (
                "WIDE 16:9 LANDSCAPE horizontal composition, like a cinematic movie still, "
                "1920x1080 format. Do NOT make it vertical or square. "
                "Combine these characters into ONE bold flat 2D comic book illustration "
                "(thick black outlines, flat cel colors, no photorealism). "
                "Setting: Moscow Red Square, sunny day, red brick Kremlin towers and St "
                "Basil's Cathedral with colorful domes behind, cobblestone pavement, a few "
                "blurred tourists. On the left, these two friends walk together laughing: "
                "the man with the bright green painted face in the light grey shirt, and "
                "the platinum blond man in the long black coat with white shirt and bullet "
                "bandolier. From the right, a scammer (the man in the black jacket, white "
                "t-shirt and chunky silver chain necklace) rides up to them on a small "
                "silver kick scooter, eagerly holding out colorful souvenir bracelets and "
                "trinkets toward their faces. The two friends look annoyed and suspicious. "
                "Bright warm afternoon light, comedic cartoon energy."
            ),
        },
        "prompt": (
            "The two friends walk together across Red Square: the man with the bright "
            "green painted face in a light grey shirt and the platinum blond man in a long "
            "black coat with a bullet bandolier. A pushy scammer in a black jacket with a "
            "silver chain necklace rides up on a kick scooter, eagerly waving colorful "
            "bracelets at them, pushing trinkets into their faces. The friends step back, "
            "annoyed and suspicious, waving him off. Handheld tracking camera follows the "
            "group, comedic timing, warm sunny light, consistent characters, continuous "
            "single shot, no cuts."
        ),
    },
    {
        "id": "hseg2_superhero_punch",
        "compose": {
            "refs": ["green", "bandolier", "scammer", "hero_bunny"],
            "instruction": (
                "WIDE 16:9 LANDSCAPE horizontal composition, like a cinematic movie still, "
                "1920x1080 format. Do NOT make it vertical or square. "
                "Combine these characters into ONE bold flat 2D comic book illustration "
                "(thick black outlines, flat cel colors, no photorealism). "
                "Setting: Moscow Red Square, sunny day, St Basil's Cathedral with colorful "
                "domes behind, cobblestone pavement, gasping tourists. The scammer (black "
                "jacket, white t-shirt, chunky silver chain necklace) pushes colorful "
                "bracelets at the two annoyed friends: the green-painted-face man in the "
                "grey shirt and the platinum blond man in the long black coat with bullet "
                "bandolier. From above, a superhero swoops down: a muscular hero in a "
                "glossy white bunny-suit costume with big white rabbit ears, white rabbit "
                "half-mask over his eyes, black bunny emblem on the chest, tattoos on his "
                "neck and hands, flying down with a raised fist, cape-less, dynamic "
                "superhero pose, comic action energy."
            ),
        },
        "prompt": (
            "The bunny-suit superhero in the white costume with rabbit ears swoops down "
            "from the sky and lands dramatically between the scammer and the two friends "
            "on Red Square. With one mighty punch he sends the scammer flying far away "
            "through the air over the square, comically, arms flailing. Tourists gasp and "
            "point. The superhero strikes a heroic pose, the green-painted-face friend and "
            "the blond friend in the black coat cheer and give him a thumbs up. Dynamic "
            "action camera, slow-motion on the punch then back to real time, comedic "
            "action movie energy, consistent characters, continuous single shot, no cuts."
        ),
    },
]


def log_api(op: str, method: str, url: str, status: int | None, req: dict | None, resp: str | None) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "op": op,
        "method": method,
        "url": url,
        "status": status,
        "req": _strip_b64(req),
        "resp": (resp or "")[:1200],
    }
    with APILOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _strip_b64(req: dict | None) -> dict | None:
    if not req:
        return req

    def clean(v):
        if isinstance(v, str) and len(v) > 120 and v.startswith("data:"):
            return v[:40] + f"...<b64 {len(v)} chars>"
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v

    return clean(req)


def headers() -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def client() -> httpx.Client:
    return httpx.Client(timeout=180, headers=headers())


def key_info(c: httpx.Client) -> tuple[float, str]:
    r = c.get(f"{BASE}/auth/key")
    log_api("auth_key", "GET", f"{BASE}/auth/key", r.status_code, None, r.text)
    d = r.json()["data"]
    return float(d.get("limit_remaining") or 0.0), str(d.get("expires_at"))


def ref_path(name: str) -> Path:
    comic = REFS / f"{name}_comic.png"
    if comic.exists():
        return comic
    p = REFS / REF_FILES[name]
    if not p.exists():
        raise SystemExit(f"missing ref: {p}")
    return p


def data_uri(p: Path) -> str:
    b = p.read_bytes()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(b).decode()}"


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"jobs": {}, "cost_total": 0.0}


def save_state(st: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def stylize() -> None:
    """Real-photo refs -> bold flat 2D comic style (privacy filter + bandolier bg fix)."""
    targets = ["green", "scammer", "bandolier"]
    with client() as c:
        rem, exp = key_info(c)
        print(f"budget remaining=${rem:.2f} expires={exp}")
        for name in targets:
            dst = REFS / f"{name}_comic.png"
            if dst.exists():
                print(f"stylize {name}: exists, skip")
                continue
            src = REFS / REF_FILES[name]
            body = {
                "model": COMPOSE_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": (
                                "Redraw this person as a BOLD FLAT 2D COMIC BOOK illustration: "
                                "thick black outlines, flat cel colors, poster style, absolutely "
                                "no photorealism, no photo texture. Keep the person's identity: "
                                "same face features, hairstyle, beard, clothing, chain necklace. "
                                "Replace the background with a plain flat warm gray background. "
                                "Full upper body, front view."
                            )},
                            {"type": "image_url", "image_url": {"url": data_uri(src)}},
                        ],
                    }
                ],
            }
            r = c.post(f"{BASE}/chat/completions", json=body)
            log_api(f"stylize_{name}", "POST", f"{BASE}/chat/completions", r.status_code, body, r.text)
            if r.status_code != 200:
                print(f"stylize {name}: HTTP {r.status_code} {r.text[:200]}")
                continue
            d = r.json()
            images = ((d.get("choices") or [{}])[0].get("message") or {}).get("images") or []
            if not images:
                print(f"stylize {name}: no image in response")
                continue
            url = images[0].get("image_url", {}).get("url", "")
            b64 = url.split(",", 1)[1] if "," in url else ""
            dst.write_bytes(base64.b64decode(b64))
            print(f"stylize {name}: -> {dst.name} ({dst.stat().st_size // 1024} KB)")


def compose() -> None:
    """Compose 16:9 scene first-frames via dedicated Image API (aspect_ratio + refs)."""
    with client() as c:
        rem, exp = key_info(c)
        print(f"budget remaining=${rem:.2f} expires={exp}")
        for seg in SEGMENTS:
            sid = seg["id"]
            dst = OUT / f"{sid}_first_frame.png"
            if dst.exists():
                print(f"compose {sid}: exists, skip")
                continue
            payload = {
                "model": COMPOSE_MODEL,
                "prompt": seg["compose"]["instruction"],
                "aspect_ratio": "16:9",
                "input_references": [
                    {"type": "image_url", "image_url": {"url": data_uri(ref_path(n))}}
                    for n in seg["compose"]["refs"]
                ],
            }
            r = c.post(f"{BASE}/images", json=payload)
            log_api(f"compose_{sid}", "POST", f"{BASE}/images", r.status_code, payload, r.text)
            if r.status_code not in (200, 202):
                print(f"compose {sid}: HTTP {r.status_code} {r.text[:200]}")
                continue
            d = r.json()
            items = d.get("data") or []
            if not items or not items[0].get("b64_json"):
                print(f"compose {sid}: no image in response")
                continue
            dst.write_bytes(base64.b64decode(items[0]["b64_json"]))
            cost = (d.get("usage") or {}).get("cost")
            print(f"compose {sid}: -> {dst.name} ({dst.stat().st_size // 1024} KB) cost=${cost}")


def submit() -> None:
    st = load_state()
    with client() as c:
        rem, exp = key_info(c)
        print(f"budget remaining=${rem:.2f} expires={exp}")
        for seg in SEGMENTS:
            sid = seg["id"]
            if sid in st["jobs"] and st["jobs"][sid].get("id"):
                print(f"skip {sid} (already submitted)")
                continue
            ff = OUT / f"{sid}_first_frame.png"
            if not ff.exists():
                print(f"compose {sid}: missing first frame {ff.name}, run compose first")
                continue
            payload = {
                "model": MODEL,
                "prompt": seg["prompt"],
                "duration": DUR,
                "resolution": RES,
                "aspect_ratio": AR,
                "frame_images": [
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri(ff)},
                        "frame_type": "first_frame",
                    }
                ],
            }
            r = c.post(f"{BASE}/videos", json=payload)
            log_api(f"submit_{sid}", "POST", f"{BASE}/videos", r.status_code, payload, r.text)
            if r.status_code in (200, 202):
                j = r.json()
                st["jobs"][sid] = {
                    "id": j.get("id"),
                    "status": j.get("status"),
                    "polling_url": j.get("polling_url"),
                }
                print(f"submitted {sid}: job={j.get('id')} status={j.get('status')}")
            else:
                st["jobs"][sid] = {"id": None, "status": "submit_failed", "error": r.text[:500]}
                print(f"SUBMIT FAILED {sid}: HTTP {r.status_code} {r.text[:300]}")
            save_state(st)
            rem, _ = key_info(c)
            if rem < BUDGET_FLOOR:
                print(f"budget guard: remaining ${rem:.2f} < ${BUDGET_FLOOR:.2f}, stop")
                break


def poll() -> None:
    st = load_state()
    with client() as c:
        while True:
            pending = [
                s for s, j in st["jobs"].items()
                if j.get("id") and j.get("status") not in ("completed", "failed", "downloaded")
            ]
            rem, exp = key_info(c)
            print(
                f"pending={pending} spent_here=${st['cost_total']:.2f} "
                f"key_remaining=${rem:.2f}"
            )
            if not pending:
                break
            for sid in pending:
                job = st["jobs"][sid]
                r = c.get(f"{BASE}/videos/{job['id']}")
                log_api(f"poll_{sid}", "GET", f"{BASE}/videos/{job['id']}", r.status_code, None, r.text)
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
                        mp4 = OUT / f"{sid}_take1.mp4"
                        dl = c.get(urls[0], timeout=600)
                        log_api(
                            f"download_{sid}", "GET", urls[0], dl.status_code, None,
                            f"<binary {len(dl.content)} bytes>",
                        )
                        mp4.write_bytes(dl.content)
                        job["file"] = mp4.name
                        job["status"] = "downloaded"
                        print(f"downloaded {sid} -> {mp4.name} ({len(dl.content) // 1024} KB) cost=${cost}")
                    st["cost_total"] = sum(j.get("cost") or 0 for j in st["jobs"].values())
                elif d.get("status") == "failed":
                    job["error"] = str(d.get("error"))[:500]
                    print(f"FAILED {sid}: {job['error']}")
                save_state(st)
            still_busy = any(
                j.get("status") in ("pending", "in_progress", "queued")
                for j in st["jobs"].values()
            )
            if still_busy:
                time.sleep(25)
    total = sum(j.get("cost") or 0 for j in st["jobs"].values())
    print(f"ALL DONE. total_cost=${total:.2f}")


def status() -> None:
    st = load_state()
    print(json.dumps(st, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "stylize":
        stylize()
    elif cmd == "compose":
        compose()
    elif cmd == "submit":
        submit()
    elif cmd == "poll":
        poll()
    elif cmd == "status":
        status()
    else:
        raise SystemExit("usage: stylize|submit|poll|status")
