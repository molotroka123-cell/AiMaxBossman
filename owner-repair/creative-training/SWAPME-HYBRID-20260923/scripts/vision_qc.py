"""Bossman vision quality gate for generated video (owner request 2026-09-23).

qc <clip> "<intended shot>" [--out report.json]
    1) deterministic: full decode, duration, black frames, frozen frames
    2) vision: local Qwen3.6-35B-A3B + its mmproj (Ollama `bossman-fast-qwen36-vision`) looks at a 3x2 frame grid
       against a fixed rubric AND the owner's own past rejections (OWNER_TASTE) → PASS / FAIL + issues
feedback <clip> "<owner verdict text>" [good|bad]
    stores the owner's judgement: OWNER_TASTE.jsonl + Bossman memory fact (subject "owner video taste")
    so the next qc run includes it in the rubric. WEIGHTS_UNCHANGED — this is retrieval, not fine-tuning.
Run with the Bossman bundle python.
"""
import base64, json, re, subprocess, sys, tempfile, urllib.request
from datetime import datetime, timezone
from pathlib import Path

MEDIA = Path(r"C:\Users\asd\Bossman Test 0923\sha4\BOSSMAN-Windows-x64-12612c8184a1\media")
FF, FP = str(MEDIA / "ffmpeg.exe"), str(MEDIA / "ffprobe.exe")
OLLAMA = "http://127.0.0.1:11435/api/chat"
VISION_MODEL = "bossman-fast-qwen36-vision"
TASTE = Path(r"C:\Users\asd\Bossman\creative-runs\OWNER_TASTE.jsonl")
BOSSMAN = "http://127.0.0.1:8810"
TOKEN_FILE = Path(r"C:\Users\asd\Bossman Test 0923\data\token")

RUBRIC = """You are the strict quality gate of an ad production studio. You see 6 frames (left→right, top→bottom = time order)
of ONE generated video shot. Intended shot: {intent}
Reject ("FAIL") if ANY of these is clearly visible: broken anatomy (extra/melted fingers or paws, deformed face, extra limbs),
character identity changes between frames (different fox/face/clothes), morphing or melting objects, garbled AI text or
fake letters, heavy flicker/noise/blur that ruins the shot, the shot not matching the intended action at all,
cigarettes/alcohol (brand-safety), watermarks.
The owner has personally rejected these things before — treat them as FAIL too:
{taste}
Answer ONLY with JSON: {{"verdict":"PASS"|"FAIL","score":0-10,"issues":["..."],"identity_consistent":true|false,
"anatomy_ok":true|false,"text_garbled":true|false,"matches_intent":true|false}}"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, errors="replace")


def deterministic(clip):
    d = json.loads(run([FP, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(clip)]).stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    dec = run([FF, "-v", "error", "-i", str(clip), "-f", "null", "-"])
    det = run([FF, "-v", "info", "-i", str(clip), "-vf", "blackdetect=d=0.2:pix_th=0.08,freezedetect=n=0.001:d=0.6",
               "-an", "-f", "null", "-"]).stderr
    return {"duration": round(float(d["format"]["duration"]), 3), "size": f"{v['width']}x{v['height']}",
            "decode_ok": dec.returncode == 0 and not dec.stderr.strip(),
            "black_segments": len(re.findall(r"black_start", det)), "frozen_segments": len(re.findall(r"freeze_start", det))}


def grid(clip):
    out = Path(tempfile.mkdtemp()) / "grid.jpg"
    dur = float(json.loads(run([FP, "-v", "error", "-show_format", "-of", "json", str(clip)]).stdout)["format"]["duration"])
    run([FF, "-y", "-v", "error", "-i", str(clip), "-vf", f"fps=6/{dur:.3f},scale=540:-1,tile=3x2:padding=4",
         "-frames:v", "1", "-q:v", "3", str(out)])
    return out


def taste_lines():
    if not TASTE.exists():
        return "- (no owner rejections recorded yet)"
    rows = [json.loads(l) for l in TASTE.read_text(encoding="utf-8").splitlines() if l.strip()]
    bad = [r["text"] for r in rows if r.get("label") == "bad"][-12:]
    return "\n".join(f"- {t}" for t in bad) or "- (no owner rejections recorded yet)"


def vision(img, intent):
    body = {"model": VISION_MODEL, "stream": False, "think": False, "format": "json",
            "options": {"temperature": 0, "num_ctx": 16384},
            "messages": [{"role": "user", "content": RUBRIC.format(intent=intent, taste=taste_lines()),
                          "images": [base64.b64encode(Path(img).read_bytes()).decode()]}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        text = json.loads(r.read())["message"]["content"]
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {"verdict": "UNKNOWN", "raw": text[:400]}


def qc(clip, intent, out=None):
    det = deterministic(clip)
    img = grid(clip)
    vis = vision(img, intent)
    ok = det["decode_ok"] and det["black_segments"] == 0 and vis.get("verdict") == "PASS"
    rep = {"t": now(), "clip": str(clip), "intent": intent, "deterministic": det, "vision_model": VISION_MODEL,
           "vision": vis, "grid": str(img), "gate": "PASS" if ok else "FAIL",
           "owner_taste_rules_used": taste_lines().count("\n- ") + (0 if "no owner" in taste_lines() else 1)}
    if out:
        Path(out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))
    return rep


def feedback(clip, text, label="bad"):
    rec = {"t": now(), "clip": str(clip), "label": label, "text": text}
    with TASTE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    body = {"subject": "owner video taste", "predicate": "rejects" if label == "bad" else "likes", "object": text[:1000],
            "source_kind": "human", "source_note": f"owner verdict on {Path(clip).name}"}
    req = urllib.request.Request(BOSSMAN + "/api/memory/facts", data=json.dumps(body).encode(),
                                 headers={"X-BCC-Token": tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("saved to Bossman memory:", json.loads(r.read()).get("id", "?"))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "qc":
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        qc(Path(sys.argv[2]), sys.argv[3], out)
    elif cmd == "feedback":
        feedback(Path(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "bad")
