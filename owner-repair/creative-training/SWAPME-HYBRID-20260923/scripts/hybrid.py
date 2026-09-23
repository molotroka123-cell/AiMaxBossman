"""SwapMe hybrid ad — production driver over Bossman Studio (test instance :8810, build = media fix branch).
DELEGATED_BY_OWNER_MEDIA_RUN. Keys/tokens never printed. Run with the Bossman bundle python (Pillow).
usage: python hybrid.py <cmd> [args]
"""
import base64, hashlib, json, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image

RUN = Path(r"C:\Users\asd\Bossman\creative-runs\SWAPME-HYBRID-20260923")
for d in ("final", "production", "learning", "evidence", "refs", "clips", "bench", "work"):
    (RUN / d).mkdir(parents=True, exist_ok=True)
BOARD = Path(r"C:\Users\asd\Desktop\SwapMe Fox_ Luxury Crypto Nights (2).png")
MEDIA = Path(r"C:\Users\asd\Bossman Test 0923\sha4\BOSSMAN-Windows-x64-12612c8184a1\media")
FFMPEG, FFPROBE = str(MEDIA / "ffmpeg.exe"), str(MEDIA / "ffprobe.exe")
BASE = "http://127.0.0.1:8810"
TOK = (Path(r"C:\Users\asd\Bossman Test 0923\data") / "token").read_text(encoding="utf-8").strip()
STATE = RUN / "evidence" / "state.json"
PANELS = {"01_fox_hero": (0, 0, 498, 548), "02_exchange": (511, 0, 1025, 548), "04_prague_street": (1038, 0, 1536, 548),
          "03_vault": (0, 560, 753, 1024), "05_skyline": (766, 560, 1536, 1024)}
SEEDANCE = "openrouter:bytedance/seedance-2.5"
WAN = "sdcpp:wan2.2-ti2v-5b"


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def api(path, body=None, method=None, timeout=120):
    req = urllib.request.Request(BASE + path, method=method or ("POST" if body is not None else "GET"),
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-BCC-Token": TOK, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {path}: {e.read()[:600].decode('utf-8', 'replace')}") from None


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"refs": {}, "jobs": {}}


def save(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def jl(name, rec):
    with (RUN / "evidence" / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": now(), **rec}, ensure_ascii=False) + "\n")


def import_ref(st, name, path):
    if name in st["refs"]:
        return st["refs"][name]["run_id"]
    r = api("/api/studio/references", {"filename": Path(path).name, "data_base64": base64.b64encode(Path(path).read_bytes()).decode()})
    st["refs"][name] = {"run_id": r["id"], "path": str(path), "sha256": sha(path)}
    save(st)
    jl("CLI_AND_API_CALLS.jsonl", {"call": "POST /api/studio/references", "name": name, "run_id": r["id"], "sha256": sha(path)})
    return r["id"]


def cmd_refs():
    st = load()
    board = Image.open(BOARD).convert("RGB")
    st["board"] = {"path": str(BOARD), "sha256": sha(BOARD), "size": board.size, "source": "OWNER_INPUT"}
    for name, box in PANELS.items():
        out = RUN / "refs" / f"{name}.png"
        board.crop(box).save(out)
        import_ref(st, name, out)
    save(st)
    print(json.dumps(st["refs"], indent=1))


def submit(shot, model, prompt, settings, media):
    st = load()
    if shot in st["jobs"]:
        print("already submitted", shot, st["jobs"][shot]["id"]); return
    body = {"model": model, "prompt": prompt, "settings": settings, "count": 1, "media": media}
    if model.startswith("openrouter:"):
        conf = api("/api/studio/egress/confirm", {"provider": "openrouter", "media": media})
        jl("CLI_AND_API_CALLS.jsonl", {"call": "POST /api/studio/egress/confirm", "shot": shot, "confirmation": conf.get("confirmation", "")[:24]})
    t0 = time.time()
    j = api("/api/studio/jobs", body)
    st = load()
    st["jobs"][shot] = {"id": j["id"], "model": model, "prompt": prompt, "settings": settings, "media": media, "t0": t0, "submitted": now()}
    save(st)
    jl("CLI_AND_API_CALLS.jsonl", {"call": "POST /api/studio/jobs", "shot": shot, "job": j["id"], "model": model, "settings": settings,
                                   "media": media, "status": j.get("status")})
    print(shot, "job", j["id"], j.get("status"))


def fetch(shot, timeout=7200):
    st = load()
    job = st["jobs"][shot]
    while True:
        j = api(f"/api/studio/jobs/{job['id']}")
        if j.get("status") in ("completed", "failed", "cancelled", "partial"):
            break
        if time.time() - job["t0"] > timeout:
            raise SystemExit(f"{shot}: still {j.get('status')} after {timeout}s")
        time.sleep(15)
    elapsed = round(time.time() - job["t0"], 1)
    job.update({"status": j.get("status"), "elapsed_s": elapsed, "error": j.get("error")})
    if j.get("status") == "completed":
        runs = api("/api/studio/runs")
        items = runs if isinstance(runs, list) else runs.get("items", [])
        r = next(x for x in items if x.get("job_id") == job["id"])
        data = urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/studio/runs/{r['id']}/file", headers={"X-BCC-Token": TOK}), timeout=600).read()
        out = RUN / ("clips" if not shot.startswith("bench") else "bench") / f"{shot}.mp4"
        out.write_bytes(data)
        prov = r.get("provenance") or {}
        job.update({"run_id": r["id"], "file": str(out), "sha256": sha(out), "cost_usd": prov.get("cost_usd", prov.get("cost")),
                    "provenance_keys": sorted(prov)[:40]})
        job["probe"] = probe(out)
    st = load()  # HARNESS FIX: re-read — another writer (rescue) may have updated other shots meanwhile
    st["jobs"][shot] = job
    save(st)
    jl("GENERATIONS.jsonl", {"shot": shot, **{k: v for k, v in job.items() if k not in ("prompt", "media")}})
    print(shot, job["status"], elapsed, "s", job.get("probe"), "cost", job.get("cost_usd"), job.get("error"))


def probe(p):
    d = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(p)],
                                  capture_output=True, text=True).stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    n, dd = (int(x) for x in v["r_frame_rate"].split("/"))
    dec = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-f", "null", "-"], capture_output=True, text=True)
    return {"duration": round(float(d["format"]["duration"]), 3), "w": v["width"], "h": v["height"], "fps": round(n / dd, 3),
            "frames": int(v.get("nb_frames") or 0), "audio": bool(a), "decode_ok": dec.returncode == 0 and not dec.stderr.strip()}


if __name__ == "__main__":
    globals()["cmd_" + sys.argv[1]](*sys.argv[2:])
