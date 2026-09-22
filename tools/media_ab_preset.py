#!/usr/bin/env python3
"""A/B matrix for isolating the Wan2.2 (stable-diffusion.cpp) "noise" cause — PLAN ONLY by default.

Owner handoff 2026-09-21: 832x480 / 17 frames / 20 steps / no extra flags gave a correct clip;
480x288 / 10 steps (with and without --diffusion-fa) gave colour noise. The cause is NOT isolated,
so no preset is declared working here. This tool prepares pairs that change EXACTLY ONE variable
against the same baseline with the same seed:

  resolution   832x480  -> 480x288
  steps        20       -> 10
  vae_tiling   off      -> --vae-tiling
  diffusion_fa off      -> --diffusion-fa

`plan` writes a JSON plan with the exact sd-cli argv per variant (built by the product's own
sdcpp._argv so the flags cannot drift from what Studio runs). `--run` executes the plan only when
BOSSMAN_SDCPP_BIN (or the media config file) points at an existing binary and the manifest
validates; it records exit code, elapsed time, output sha256 and ffprobe stats — never a quality
verdict. Whether a clip is "noise" is decided by the owner looking at it.

Usage:
  python tools/media_ab_preset.py plan --out ab-plan.json [--seed 7] [--prompt "..."]
  python tools/media_ab_preset.py plan --out ab-plan.json --run --results ab-results.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND_CENTER = ROOT / "command-center"
if COMMAND_CENTER.is_dir() and str(COMMAND_CENTER) not in sys.path:
    sys.path.insert(0, str(COMMAND_CENTER))

from bcc.studio import catalog  # noqa: E402
from bcc.studio.provider import GenerationPlane  # noqa: E402
from bcc.studio.providers import sdcpp  # noqa: E402

MODEL_ID = "sdcpp:wan2.2-ti2v-5b"
# Variant A from the owner run: the only configuration that produced a prompt-matched clip so far.
BASELINE = {"width": 832, "height": 480, "frames": 17, "fps": 16, "steps": 20, "cfg_scale": 5.0}
VARIABLES = {
    "resolution": {"settings": {"width": 480, "height": 288}, "flags": []},
    "steps": {"settings": {"steps": 10}, "flags": []},
    "vae_tiling": {"settings": {}, "flags": ["--vae-tiling"]},
    "diffusion_fa": {"settings": {}, "flags": ["--diffusion-fa"]},
}
DEFAULT_PROMPT = "a red fox walking through fresh snow, slow camera pan, daylight"


def catalog_valid(model: dict, settings: dict) -> bool:
    try:
        catalog.validate_settings(model, settings)
        return True
    except ValueError:
        return False


def build_plan(*, seed: int, prompt: str, out_dir: Path, cfg: dict | None, variables=None) -> dict:
    model = next(m for m in catalog.load()["models"] if m["id"] == MODEL_ID)
    entry_name = sdcpp.ENGINES[MODEL_ID]
    if cfg is not None:
        files = {role: Path(cfg["root"]) / spec["path"]
                 for role, spec in cfg["manifest"]["engines"][entry_name]["files"].items()}
        bin_path = Path(cfg["bin"])
        source = "configuration"
    else:
        files = {role: Path("<BOSSMAN_MEDIA_MODELS>") / f"{entry_name}" / f"<{role}>" for role in ("diffusion", "vae", "text_encoder")}
        bin_path = Path("<BOSSMAN_SDCPP_BIN>")
        source = "placeholders (engine not configured on this machine)"
    variants = []

    def variant(vid: str, changed: str | None, settings: dict, flags: list[str]) -> dict:
        full = {**BASELINE, **settings, "seed": seed}
        out = out_dir / f"{vid}.webm"
        argv = sdcpp._argv({"bin": bin_path}, MODEL_ID, GenerationPlane(MODEL_ID, prompt, full), full, files, out, None)
        argv += flags
        return {"id": vid, "changed_variable": changed, "settings": full, "extra_flags": flags,
                "catalog_valid": catalog_valid(model, {k: v for k, v in full.items() if k in model["settings"]}),
                "duration_s_declared": full["frames"] / full["fps"], "output": str(out), "argv": argv,
                "expected_verdict": "UNKNOWN: decided by the owner viewing the clip, never by this tool"}

    variants.append(variant("baseline", None, {}, []))
    pairs = []
    for name in variables or VARIABLES:
        spec = VARIABLES[name]
        v = variant(f"only-{name}", name, spec["settings"], spec["flags"])
        variants.append(v)
        pairs.append({"a": "baseline", "b": v["id"], "single_variable": name})
    return {"schema_version": 1, "purpose": "isolate the noise cause; one variable per pair; same seed",
            "model": MODEL_ID, "seed": seed, "prompt": prompt, "paths_source": source,
            "baseline_note": "Variant A of the 2026-09-21 owner run (correct clip once); not a declared working preset",
            "known_from_owner_run": {"832x480/17f/20 steps/no flags": "correct clip once (sha256 33a95853…)",
                                     "480x288/10 steps (+/- diffusion-fa)": "noise; cause NOT isolated"},
            "variants": variants, "pairs": pairs, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ffprobe_stats(path: Path) -> dict | None:
    exe = shutil.which("ffprobe")
    if not exe or not path.is_file():
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        return {"error": type(exc).__name__}
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"width": video.get("width"), "height": video.get("height"), "codec": video.get("codec_name"),
            "nb_frames": video.get("nb_frames"), "avg_frame_rate": video.get("avg_frame_rate"),
            "duration_s": data.get("format", {}).get("duration"), "format": data.get("format", {}).get("format_name")}


def run_plan(plan: dict, *, timeout_s: int, log=print, runner=subprocess.run) -> dict:
    results = []
    for v in plan["variants"]:
        out = Path(v["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        log(f"[{v['id']}] running ({v['changed_variable'] or 'baseline'}) ...")
        t0 = time.monotonic()
        record = {"id": v["id"], "changed_variable": v["changed_variable"], "argv": v["argv"], "returncode": None,
                  "elapsed_s": None, "output_sha256": None, "output_bytes": None, "ffprobe": None, "log_tail": [],
                  "quality": "NOT_ASSESSED_BY_TOOL: owner views the clip"}
        try:
            proc = runner(v["argv"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout_s, cwd=str(out.parent))
            record["returncode"] = proc.returncode
            text = (proc.stdout or b"").decode("utf-8", "replace")
            record["log_tail"] = [line[:300] for line in text.splitlines()[-20:]]
            record["backend"] = sdcpp.observe_backend(text.splitlines())
        except subprocess.TimeoutExpired:
            record["returncode"], record["error"] = None, f"timeout after {timeout_s}s"
        except OSError as exc:
            record["returncode"], record["error"] = None, f"{type(exc).__name__}: {exc}"
        record["elapsed_s"] = round(time.monotonic() - t0, 1)
        record["output_sha256"] = sha256_file(out)
        record["output_bytes"] = out.stat().st_size if out.is_file() else None
        record["ffprobe"] = ffprobe_stats(out)
        results.append(record)
    return {"schema_version": 1, "plan_seed": plan["seed"], "results": results,
            "verdict": "MEASURED_ONLY: exit codes, hashes and ffprobe stats; no quality claim",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def utf8_console() -> None:
    """Печать не имеет права падать на кириллице: под `python -I` PYTHONUTF8 игнорируется,
    поток получает кодировку локали Windows; errors='replace' держит печать живой везде."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None) -> int:
    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", required=True, help="plan JSON path")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--work-dir", default=None, help="where variant outputs go (default: next to the plan)")
    p.add_argument("--variable", action="append", choices=sorted(VARIABLES), default=None,
                   help="limit to these variables (repeatable)")
    p.add_argument("--run", action="store_true", help="execute the plan (only with a configured engine)")
    p.add_argument("--results", default=None, help="results JSON path (with --run)")
    p.add_argument("--timeout", type=int, default=3600, help="seconds per variant (with --run)")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    work = Path(args.work_dir) if args.work_dir else out_path.parent / "ab-out"
    report = sdcpp.describe_configuration()
    cfg = report["cfg"]
    plan = build_plan(seed=args.seed, prompt=args.prompt, out_dir=work, cfg=cfg, variables=args.variable)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"plan written: {out_path} ({len(plan['variants'])} variants, {len(plan['pairs'])} pairs)")
    if not args.run:
        return 0
    if cfg is None:
        print(f"refusing --run: engine not configured ({report['error']})", file=sys.stderr)
        return 2
    try:
        sdcpp.verify_engine_files(cfg, MODEL_ID, mode="force")
    except (ValueError, OSError, KeyError) as exc:
        print(f"refusing --run: model files failed verification: {exc}", file=sys.stderr)
        return 2
    results = run_plan(plan, timeout_s=args.timeout)
    results_path = Path(args.results) if args.results else out_path.with_name(out_path.stem + "-results.json")
    results_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"results written: {results_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
