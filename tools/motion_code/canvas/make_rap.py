"""Bossman's own path: POST /api/music/generate on :8801 (ACE-Step, local) -> poll -> save + master."""
import json, os, pathlib, sys, time, urllib.request
OUT = pathlib.Path(__file__).parent
tok = pathlib.Path(os.environ["LOCALAPPDATA"], "Bossman/CommandCenter/token").read_text().strip()
B = "http://127.0.0.1:8801/api/music"
def call(method, path, body=None, timeout=600):
    req = urllib.request.Request(B + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", "X-BCC-Token": tok}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
LYRICS = """[verse]
Bossman on the desk, one brain, one stop
Local on the Max, from the bottom to the top
Pult in my pocket, Telegram on call
Every feature proven, receipts for it all

[chorus]
Bossman! Bossman! running on my PC
Two point six, harness locked, safe and free"""
body = {"prompt": "hard-hitting modern hip hop, confident male rap vocal, english, punchy boom bap drums, deep 808 bass, dark synth stabs, crisp hi-hats, studio mixed, energetic",
        "lyrics": LYRICS, "duration": 22, "bpm": 96, "thinking": False, "time_signature": "4"}
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
t0 = time.time()
r = call("POST", "/generate", body); print("queued", r, flush=True)
tid = r["task_id"]
while True:
    time.sleep(5)
    s = call("GET", f"/tasks/{tid}")
    if s["status"] != "running": break
print("state", s["status"], round(time.time() - t0), "s", flush=True)
sv = call("POST", f"/tasks/{tid}/save", {"master": "hype", "duration": 20})
print(json.dumps(sv, ensure_ascii=False), flush=True)
(OUT / f"rap_{tid[:8]}.json").write_text(json.dumps({"request": body, "save": sv, "seconds": round(time.time() - t0)}, ensure_ascii=False, indent=1))
