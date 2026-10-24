"""End-to-end A/B of the real sd-cli process on the SAME model and parameters, models from C: vs D:.

    python tools/ssd_e2e_ab.py --model flux1-schnell --receipt <file.json>

Per root: run 1 = cold (the model files were never read through the OS cache), run 2 + 3 = warm / back-to-back queue of two.
Order alternates C, D so neither root always goes first. Neutral synthetic prompt, fixed seed, 512x512. Records wall seconds,
peak working set of the sd-cli process, output bytes + sha256 (same seed + same bytes of weights should give the same image),
exit code, runtime SHA. Needs an idle machine (bossman_idle) and refuses otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "command-center"))
sys.path.insert(0, str(HERE))
ROOTS = {"C": Path(r"C:\Users\asd\Bossman\models\media"), "D": Path(r"D:\Bossman\models\media")}
BIN = Path(r"C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan\sd-cli.exe")
PROMPT = "a red apple on a wooden table, studio light"


def run_once(argv: list[str]) -> dict:
    t0 = time.perf_counter()
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ps = psutil.Process(p.pid)
    peak = 0
    while p.poll() is None:
        try:
            peak = max(peak, ps.memory_info().rss)
        except psutil.Error:
            pass
        time.sleep(0.25)
    return {"seconds": round(time.perf_counter() - t0, 1), "exit": p.returncode, "peak_rss_mb": round(peak / 2**20)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="flux1-schnell")
    ap.add_argument("--receipt")
    ap.add_argument("--out", default=str(Path(r"D:\Bossman\work\e2e-ab")))
    a = ap.parse_args(argv)
    import model_tournament as mt
    idle, why = mt.bossman_idle()
    if not idle:
        print(json.dumps({"verdict": "NOT_RUN", "reason": why}))
        return 2
    from bcc.direct_gen import sdcli
    spec = next(s for s in sdcli.SPECS if s.id == a.model) if hasattr(sdcli, "SPECS") else sdcli.spec_for(a.model)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    params = {"width": 512, "height": 512, "steps": spec.default_steps if hasattr(spec, "default_steps") else 4, "seed": 12345}
    runs = []
    for phase in ("cold", "warm", "queue2"):
        for k in ("C", "D"):
            out = out_dir / f"{a.model}-{k}-{phase}.png"
            out.unlink(missing_ok=True)
            argv_ = sdcli.build_argv(spec, BIN, ROOTS[k], params, PROMPT, "", out, None)
            r = run_once(argv_)
            r.update(root=k, phase=phase, out_bytes=out.stat().st_size if out.is_file() else 0,
                     out_sha256=hashlib.sha256(out.read_bytes()).hexdigest() if out.is_file() else None,
                     cpu_pct_now=psutil.cpu_percent(interval=None))
            runs.append(r)
    res = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": a.model, "params": params, "prompt": PROMPT, "runs": runs,
           "runtime_sha": subprocess.run(["git", "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()}
    ok = all(r["exit"] == 0 and r["out_bytes"] > 0 for r in runs)
    by = lambda root, ph: next(r["seconds"] for r in runs if r["root"] == root and r["phase"] == ph)
    res["D_over_C"] = {ph: round(by("C", ph) / by("D", ph), 2) for ph in ("cold", "warm", "queue2")}
    res["same_image_C_vs_D"] = all(
        next(r["out_sha256"] for r in runs if r["root"] == "C" and r["phase"] == ph) == next(r["out_sha256"] for r in runs if r["root"] == "D" and r["phase"] == ph)
        for ph in ("cold", "warm", "queue2"))
    res["verdict"] = "MEASURED" if ok else "FAIL (a run failed)"
    if a.receipt:
        Path(a.receipt).parent.mkdir(parents=True, exist_ok=True)
        Path(a.receipt).write_text(json.dumps(res, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(res))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
