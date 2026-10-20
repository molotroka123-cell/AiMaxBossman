#!/usr/bin/env python3
"""Live parity + speed benchmark of the resident sd-server path (warm queue) against the proven per-job sd-cli path.

Run ONLY when the GPU is idle (no generation queued, owner not generating). It refuses to start when another sd-cli /
sd-server process exists or Bossman reports a live direct-gen job. Nothing is sent anywhere; the pictures stay in
``<data dir>/direct-gen/bench/<stamp>/`` and are deleted unless ``--keep``.

What it does for ONE model (same prompt, seed, size, steps, the exact argv Bossman builds):
  1. sd-cli once (cold)                       -> load time (to the first step), total, picture A
  2. sd-server: job 1 (cold start), job 2 (must be WARM: no second load) -> ready time, total, pictures B1, B2
  3. verdict with thresholds FIXED HERE, before any run (not tuned after a result):
       - the server started, became ready, both jobs completed with a PNG of the requested size;
       - job 2 was warm (one server start) and its load <= 25 % of the sd-cli load;
       - picture parity: mean absolute pixel difference A vs B1 <= 0.03 (of 255) and B1 vs B2 <= 0.03
         (same seed -> same picture; needs Pillow, otherwise byte-identical PNGs are required).
  4. ``--write-proof`` and PASS: records the model in ``direct-gen/resident-proof.json`` for THIS sd-server binary
     (size + mtime). The backend's default mode then keeps that model loaded inside a same-model queue run; a new
     binary voids the proof. FAIL never writes a proof.

Usage (from the repo root, owner PC, GPU idle):
  python tools/direct_gen_bench/warm_queue_bench.py --model z-image-turbo --write-proof
  python tools/direct_gen_bench/warm_queue_bench.py --model qwen-image-2.1-uc-edit --ref C:\\path\\own_photo.png --write-proof
  python tools/direct_gen_bench/warm_queue_bench.py --model z-image-turbo --dry-run      # argv + checks only, no engine
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center"))

from bcc.direct_gen import resident, sdcli  # noqa: E402

PROMPT = "a lighthouse on a rocky coast at dusk, film photo, soft light"
MAX_WARM_LOAD_SHARE = 0.25
MAX_MEAN_ABS_DIFF = 0.03                     # fraction of the 0..255 range, i.e. ~7.65 levels


def data_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def live_backend_jobs(root: Path, url: str) -> list[dict] | None:
    try:
        import httpx
        token = (root / "token").read_text(encoding="utf-8").strip()
        r = httpx.get(url.rstrip("/") + "/api/direct-gen/jobs", headers={"X-BCC-Token": token}, timeout=5.0)
        r.raise_for_status()
        return [j for j in r.json().get("jobs") or [] if j.get("status") in ("queued", "loading", "generating", "postprocessing")]
    except Exception:  # noqa: BLE001
        return None


def mean_abs_diff(a: Path, b: Path) -> float | None:
    """Mean absolute difference per channel as a fraction of 255; None when Pillow is missing or sizes differ."""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return None
    ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
    if ia.size != ib.size:
        return None
    return sum(ImageStat.Stat(ImageChops.difference(ia, ib)).mean) / 3.0 / 255.0


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


async def run_cli(argv: list[str], cwd: Path) -> dict:
    first: list[float] = []
    t0 = time.monotonic()

    async def on_progress(v, m):
        if not first:
            first.append(time.monotonic() - t0)
    res = await sdcli.SdCliRunner().run(argv, cwd=cwd, on_progress=on_progress)
    return {"returncode": res.returncode, "total_s": round(time.monotonic() - t0, 2),
            "load_s": round(first[0], 2) if first else None, "log_tail": res.log[-4:]}


async def run_server(engine: resident.ResidentEngine, argv: list[str], cwd: Path, tag: str) -> dict:
    first: list[float] = []
    t0 = time.monotonic()

    async def on_progress(v, m):
        if not first:
            first.append(time.monotonic() - t0)
    runner = resident.ResidentRunner(engine, tag=tag, cli_factory=lambda: _NoCli())
    res = await runner.run(argv, cwd=cwd, on_progress=on_progress)
    return {"returncode": res.returncode, "total_s": round(time.monotonic() - t0, 2), "warm": runner.warm,
            "ready_s": runner.ready_s, "first_step_s": round(first[0], 2) if first else None,
            "progress_lines": bool(first), "engine": runner.engine_name, "fallback": runner.fallback_reason,
            "startup_s": runner.startup_s, "log_tail": res.log[-4:]}


class _NoCli:
    """The benchmark measures the SERVER: a fallback to sd-cli is a FAIL, not a silent rerun."""
    proc = None

    async def run(self, argv, *, cwd, on_progress=None):
        return sdcli.RunResult(97, ["benchmark: sd-cli fallback is not allowed here"], 0, 0.0)

    def kill(self):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, choices=sorted(s.id for s in sdcli.SPECS if s.kind == "photo"))
    ap.add_argument("--ref", help="reference photo for an edit model (own or fictional person only)")
    ap.add_argument("--size", default="")
    ap.add_argument("--steps", type=int, default=0)
    ap.add_argument("--seed", type=int, default=424242)
    ap.add_argument("--data-dir")
    ap.add_argument("--backend", default="http://127.0.0.1:8801")
    ap.add_argument("--write-proof", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    spec = sdcli.BY_ID[a.model]
    media, cli_bin = sdcli.default_media_root(), sdcli.default_binary()
    server_bin = resident.server_binary_for(cli_bin)
    root = data_dir(a.data_dir)
    info = sdcli.describe(spec, media, cli_bin)
    if not info["available"]:
        print(json.dumps({"verdict": "BLOCKED", "reason": info["reason"]}, ensure_ascii=False))
        return 2
    if spec.requires_image and not a.ref:
        print(json.dumps({"verdict": "BLOCKED", "reason": f"{spec.id} needs --ref"}, ensure_ascii=False))
        return 2
    w, h = (int(x) for x in (a.size or spec.default_resolution).split("x"))
    steps = a.steps or spec.default_steps
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = root / "direct-gen" / "bench" / f"{stamp}-{spec.id}"
    params = {"width": w, "height": h, "steps": steps, "seed": a.seed, "frames": 1, "fps": 0}
    refs = None
    if a.ref:
        ext = sdcli.image_extension(Path(a.ref).read_bytes())
        if ext is None:
            print(json.dumps({"verdict": "BLOCKED", "reason": "--ref must be PNG or JPEG"}))
            return 2
        refs = [work / f"input{ext}"]
    outs = {k: work / f"{k}.png" for k in ("cli", "server1", "server2")}
    argvs = {k: sdcli.build_argv(spec, cli_bin, media, params, PROMPT, "", p, refs) for k, p in outs.items()}
    plan = resident.plan_argv(argvs["server1"])
    report = {"schema": "bossman.direct-gen.warm-queue-bench/1", "at": stamp, "model": spec.id, "size": f"{w}x{h}",
              "steps": steps, "seed": a.seed, "cli_bin": str(cli_bin), "server_bin": str(server_bin),
              "thresholds": {"max_warm_load_share": MAX_WARM_LOAD_SHARE, "max_mean_abs_diff": MAX_MEAN_ABS_DIFF},
              "server_plan_fallback": plan.fallback or None}
    if plan.fallback:
        report.update(verdict="BLOCKED", reason=f"this model's argv cannot run on sd-server: {plan.fallback}")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 2
    if not server_bin.is_file():
        report.update(verdict="BLOCKED", reason="sd-server binary missing next to sd-cli")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 2
    busy = sdcli.foreign_engines()
    if a.dry_run:                                                     # no backend call, no engine, nothing written
        report.update(verdict="DRY_RUN", gpu_check={"foreign_engines": busy},
                      argv_cli=[Path(x).name if os.path.isabs(x) else x for x in argvs["cli"]])
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    live = live_backend_jobs(root, a.backend)
    report["gpu_check"] = {"foreign_engines": busy, "live_backend_jobs": None if live is None else len(live)}
    if live is None:
        report.update(verdict="BLOCKED", reason="Bossman job list unreadable: cannot prove the GPU is idle")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 3
    if busy or live:
        report.update(verdict="BLOCKED", reason="GPU not idle: an engine process or a live direct-gen job exists")
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 3
    work.mkdir(parents=True, exist_ok=True)
    if a.ref:
        shutil.copyfile(a.ref, refs[0])

    async def go() -> dict:
        out = {"cli": await run_cli(argvs["cli"], work)}
        engine = resident.ResidentEngine(server_bin, idle_s=600, cwd=server_bin.parent)
        try:
            out["server1"] = await run_server(engine, argvs["server1"], work, spec.id)
            out["server2"] = await run_server(engine, argvs["server2"], work, spec.id)
            out["server_starts"] = engine.starts
        finally:
            await engine.stop()
        return out

    runs = asyncio.run(go())
    report["runs"] = runs
    reasons = []
    for k in ("cli", "server1", "server2"):
        if runs[k]["returncode"] != 0 or not outs[k].is_file() or sdcli.png_size(outs[k]) != (w, h):
            reasons.append(f"{k}: no {w}x{h} PNG (code {runs[k]['returncode']})")
    if runs.get("server_starts") != 1 or not runs["server2"].get("warm"):
        reasons.append("job 2 was not warm (the server was started more than once)")
    cli_load = runs["cli"].get("load_s")
    warm_load = runs["server2"].get("first_step_s") or runs["server2"].get("ready_s")
    if cli_load and warm_load is not None and warm_load > MAX_WARM_LOAD_SHARE * cli_load:
        reasons.append(f"warm load {warm_load} s > {MAX_WARM_LOAD_SHARE:.0%} of the sd-cli load {cli_load} s")
    if not reasons:
        diffs = {"cli_vs_server1": mean_abs_diff(outs["cli"], outs["server1"]),
                 "server1_vs_server2": mean_abs_diff(outs["server1"], outs["server2"])}
        report["parity"] = {k: (round(v, 5) if v is not None else None) for k, v in diffs.items()}
        for k, v in diffs.items():
            pair = {"cli_vs_server1": ("cli", "server1"), "server1_vs_server2": ("server1", "server2")}[k]
            if v is None:
                if sha(outs[pair[0]]) != sha(outs[pair[1]]):
                    reasons.append(f"{k}: Pillow missing and the PNGs differ (cannot measure parity)")
            elif v > MAX_MEAN_ABS_DIFF:
                reasons.append(f"{k}: mean abs diff {v:.4f} > {MAX_MEAN_ABS_DIFF:.4f}")
    report["verdict"] = "PASS" if not reasons else "FAIL"
    report["reasons"] = reasons
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.write_proof and report["verdict"] == "PASS":
        proof_path = root / "direct-gen" / resident.PROOF_FILE
        st = server_bin.stat()
        try:
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proof = {}
        if proof.get("server_size") != st.st_size or proof.get("server_mtime") != int(st.st_mtime):
            proof = {"models": {}}                                    # another binary: earlier PASSes do not carry over
        proof.update(schema=resident.PROOF_SCHEMA, server_bin=str(server_bin), server_size=st.st_size,
                     server_mtime=int(st.st_mtime))
        proof.setdefault("models", {})[spec.id] = {
            "verdict": "PASS", "at": stamp, "size": f"{w}x{h}", "steps": steps, "cli_load_s": cli_load,
            "warm_load_s": warm_load, "cli_total_s": runs["cli"]["total_s"], "warm_total_s": runs["server2"]["total_s"],
            "parity": report.get("parity"), "progress_lines": runs["server2"].get("progress_lines")}
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = proof_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(proof, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, proof_path)
        report["proof_written"] = str(proof_path)
    if not a.keep:
        for p in outs.values():
            p.unlink(missing_ok=True)
        for p in refs or []:
            p.unlink(missing_ok=True)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
