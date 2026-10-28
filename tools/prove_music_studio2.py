"""Live proof of Bossman Music 2.0 through the real Command Center API (prompt -> local ACE-Step -> save -> measured master).

Thresholds are fixed BEFORE the run (owner rule) and checked on the delivered file, re-measured here independently:
duration = requested ±1.5 s; integrated loudness = preset target ±1.0 LU; true peak <= -0.9 dBTP; no silence > 2 s
(-45 dB); the file decodes. BPM is NOT measured (no reliable detector installed) and is reported as such.

    python tools/prove_music_studio2.py --base http://127.0.0.1:8811 --data-dir D:\\Bossman\\work\\music2-sandbox\\data
Receipt: docs/audits/2026-10-27-music-studio-2/receipt.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "command-center"))
from bcc.features.music_studio import MASTER_TARGET_LUFS, _find_ffmpeg, measure_loudness  # noqa: E402

DURATION = 60
TRACKS = [
    ("phonk_130", "dark aggressive phonk, distorted cowbell melody, punchy 808 bass, Memphis-inspired drums, instrumental", 130, "F# minor", "hype"),
    ("brazilian_phonk_140", "brazilian drift phonk, fast funk carioca tamborzao beat, massive distorted 808, metallic cowbell hook, "
                            "chopped vocal shouts as texture, dark hype energy, instrumental", 140, "D minor", "hype"),
    ("lofi_85", "warm lo-fi hip hop, dusty vinyl drums, mellow rhodes chords, soft bass, relaxed study mood, instrumental", 85, "A minor", "studio_veo"),
]


class Api:
    def __init__(self, base: str, token: str):
        self.base, self.token = base.rstrip("/"), token

    def req(self, method: str, path: str, body=None, timeout: float = 600):
        r = urllib.request.Request(self.base + "/api/music" + path, method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"X-BCC-Token": self.token, "X-Participant": "owner", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read()[:300].decode("utf-8", "replace")}


def max_silence_s(path: Path, ffmpeg: str) -> float:
    log = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "silencedetect=n=-45dB:d=0.5",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    durs = [float(x) for x in re.findall(r"silence_duration: (\d+(?:\.\d+)?)", log)]
    return max(durs, default=0.0)


def ensure_service(api: Api) -> str:
    code, h = api.req("GET", "/health")
    if h.get("status") != "READY":
        api.req("POST", "/service/start", {})
        for _ in range(60):
            time.sleep(10)
            code, h = api.req("GET", "/health")
            if h.get("status") == "READY":
                break
    return str(h.get("status"))


def run_track(api: Api, tid: str, prompt: str, bpm: int, key: str, preset: str, ffmpeg: str) -> dict:
    code, t = api.req("POST", "/generate", {"prompt": prompt, "lyrics": "[inst]", "duration": DURATION + 10, "bpm": bpm, "key_scale": key})
    if code >= 300 or "task_id" not in t:
        return {"id": tid, "verdict": "FAIL", "error": f"generate {code}: {t}"}
    task_id, t0, status = t["task_id"], time.time(), None
    while time.time() - t0 < 1500:
        code, st = api.req("GET", f"/tasks/{task_id}")
        status = st.get("status")
        if status in ("completed", "failed", "error"):
            break
        time.sleep(15)
    if status != "completed":
        return {"id": tid, "verdict": "FAIL", "error": f"generation status {status}", "gen_seconds": round(time.time() - t0)}
    code, saved = api.req("POST", f"/tasks/{task_id}/save", {"master": preset, "duration": DURATION})
    m = saved.get("master") or {}
    if code >= 300 or m.get("status") != "mastered":
        return {"id": tid, "verdict": "FAIL", "error": f"save {code}: {json.dumps(saved, ensure_ascii=False)[:300]}"}
    out = Path(m["output_path"])
    meas = measure_loudness(out, ffmpeg)                     # independent re-measure of the delivered file
    sil = max_silence_s(out, ffmpeg)
    target = MASTER_TARGET_LUFS[preset]
    checks = [
        (f"duration {DURATION}±1.5 s", abs(meas["duration_s"] - DURATION) <= 1.5),
        (f"loudness {target}±1.0 LUFS", abs(meas["lufs"] - target) <= 1.0),
        ("true peak <= -0.9 dBTP", meas["true_peak_dbtp"] <= -0.9),
        ("no silence > 2 s", sil <= 2.0),
        ("server-reported loudness matches the file (±0.3)", abs(m["measured_lufs"] - meas["lufs"]) <= 0.3),
    ]
    return {"id": tid, "preset": preset, "bpm_requested": bpm, "bpm_measured": "NOT_MEASURED",
            "verdict": "PASS" if all(ok for _, ok in checks) else "FAIL",
            "checks": [{"check": n, "ok": ok} for n, ok in checks],
            "measured_lufs": meas["lufs"], "true_peak_dbtp": meas["true_peak_dbtp"], "duration_s": meas["duration_s"],
            "max_silence_s": sil, "server_passes": m.get("passes"), "raw_path": saved.get("path"),
            "master_path": str(out), "master_sha256": m.get("sha256"), "gen_seconds": round(time.time() - t0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8811")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", default=str(ROOT / "docs" / "audits" / "2026-10-27-music-studio-2"))
    a = ap.parse_args()
    token = (Path(a.data_dir) / "token").read_text(encoding="utf-8").strip()
    api, ffmpeg = Api(a.base, token), _find_ffmpeg()
    service = ensure_service(api)
    results = []
    for tid, prompt, bpm, key, preset in TRACKS:
        results.append(run_track(api, tid, prompt, bpm, key, preset, ffmpeg))
        print(f"{tid}: {results[-1]['verdict']}", flush=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    receipt = {"proof": "music-studio-2.0", "sha": sha, "base": a.base, "service_status": service,
               "date": time.strftime("%Y-%m-%dT%H:%M:%S"), "thresholds_fixed_before_run": True,
               "verdict_counts": {v: sum(1 for r in results if r["verdict"] == v) for v in ("PASS", "FAIL")}, "tracks": results}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(receipt["verdict_counts"]))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
