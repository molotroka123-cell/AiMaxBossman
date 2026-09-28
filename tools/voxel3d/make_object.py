"""Make one voxel object: prompt -> spec (local model) -> .vox + .glb + BossBlocks structure + preview + checks.

    python tools/voxel3d/make_object.py "oak tree" --out out/oak_tree
    python tools/voxel3d/make_object.py "oak tree" --spec my_spec.json --out out/oak_tree   # no model call
    python tools/voxel3d/make_object.py "oak tree" --out out/oak --game C:/path/bossblocks --apply

Writes only into --out unless --game and --apply are both given (a file mutation in the game
project: in Bossman this is the approval step). --vision asks the local vision model to describe
the preview (a sanity check, not proof). Exit code 0 = all deterministic checks passed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "voxel3d"

from voxel3d import bossblocks, mesh, vox  # noqa: E402
from voxel3d.generate import (DEFAULT_ENDPOINT, DEFAULT_MODEL, FEW_SHOT_REPLIES, chat_ollama, generate,  # noqa: E402
                               wait_if_paused)
from voxel3d.spec import build, check_grid, lint, repair_spec, validate  # noqa: E402

VISION_MODEL = "bossman-fast-qwen36-vision:latest"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "object"


def make(prompt: str, out: Path, spec: dict | None = None, model: str = DEFAULT_MODEL,
         endpoint: str = DEFAULT_ENDPOINT, tries: int = 3, vision: bool = False, target_label: str | None = None,
         labels: list[str] | None = None, name: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"prompt": prompt, "model": None if spec else model, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    t0 = time.monotonic()
    if spec is None:
        spec, errors, fixes, transcript = generate(
            prompt, lambda m: chat_ollama(m, model=model, endpoint=endpoint), tries=tries)
        (out / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
        report["tries"] = sum(1 for m in transcript if m["role"] == "assistant") - FEW_SHOT_REPLIES
    else:
        spec, fixes = repair_spec(spec)
        errors = validate(spec)
        report["tries"] = 0
    report["spec_seconds"] = round(time.monotonic() - t0, 1)
    report["spec_repairs"] = fixes
    report["spec_errors"] = errors
    if spec is not None:
        (out / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    if spec is None or errors:
        report["ok"] = False
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return report
    name = slug(name or spec.get("name") or prompt)
    grid, build_fixes, build_errors = build(spec)
    report["name"] = name
    report["build_repairs"] = build_fixes
    report["size"] = list(grid.size)
    report["grid"] = check_grid(grid, bool(spec.get("allow_floating")))
    report["lint"] = lint(grid)
    paths = {"vox": out / f"{name}.vox", "glb": out / f"{name}.glb", "structure": out / f"{name}.bbstruct.json",
             "preview": out / f"{name}.preview.png"}
    vox.write(grid, paths["vox"])
    report["glb_stats"] = mesh.write_glb(grid, paths["glb"], name)
    st = bossblocks.write_structure(grid, paths["structure"], name, prompt)
    report["structure"] = {"blocks": len(st["blocks"]), "fits_world_at_origin": st["fits_world_at_origin"],
                           "kinds": sorted({b["kind"] for b in st["blocks"]})}
    try:
        from voxel3d.render import render_preview

        render_preview(grid, paths["preview"])
    except ImportError:
        paths.pop("preview")
    report["paths"] = {k: str(v) for k, v in paths.items()}
    checks = {k: report["grid"][k] for k in ("non_empty", "within_bounds", "grounded", "connected")}
    try:
        from voxel3d.verify import check_glb, check_vox

        report["vox_check"] = check_vox(grid, paths["vox"])
        report["glb_check"] = check_glb(grid, paths["glb"])
        checks["vox_roundtrip"] = report["vox_check"]["ok"]
        checks["glb_valid"] = report["glb_check"]["ok"]
    except ImportError as exc:
        report["verify_skipped"] = f"verification readers missing: {exc}"
    report["checks"] = checks
    report["ok"] = all(checks.values()) and not build_errors
    if vision and "preview" in paths:
        from voxel3d.verify import LABELS_DEFAULT, vision_check

        wait_if_paused()
        tv = time.monotonic()
        try:
            report["vision"] = vision_check(paths["preview"], target_label or prompt, labels or LABELS_DEFAULT,
                                            lambda m, fmt=None: chat_ollama(m, model=VISION_MODEL, endpoint=endpoint,
                                                                           fmt=fmt, max_tokens=200, temperature=0.0))
        except Exception as exc:  # vision is advisory; never fails the object
            report["vision"] = {"error": f"{exc.__class__.__name__}: {str(exc)[:160]}"}
        report["vision"]["seconds"] = round(time.monotonic() - tv, 1)
    report["seconds"] = round(time.monotonic() - t0, 1)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("prompt")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--spec", type=Path, help="use this spec instead of calling the model")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--vision", action="store_true", help="ask the local vision model what the preview shows")
    ap.add_argument("--label", help="expected label for the vision forced-choice check")
    ap.add_argument("--game", type=Path, help="BossBlocks project directory")
    ap.add_argument("--apply", action="store_true", help="actually copy the object into --game (file mutation)")
    args = ap.parse_args(argv)
    spec = json.loads(args.spec.read_text(encoding="utf-8")) if args.spec else None
    rep = make(args.prompt, args.out, spec, args.model, args.endpoint, args.tries, args.vision, args.label)
    if args.game and rep.get("ok"):
        if args.apply:
            written = bossblocks.install(args.game, rep["name"], Path(rep["paths"]["glb"]), Path(rep["paths"]["structure"]))
            rep["installed"] = [str(p) for p in written]
        else:
            rep["install_plan"] = [f"{args.game}/assets/voxel/{rep['name']}.glb", f"{args.game}/structures/{rep['name']}.json"]
            print("DRY RUN: pass --apply to write into the game project (needs owner approval)", file=sys.stderr)
        (args.out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {k: rep.get(k) for k in ("name", "ok", "size", "checks", "glb_stats", "structure", "vision", "seconds")}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
