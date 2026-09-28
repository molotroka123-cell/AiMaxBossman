"""Generate a set of objects and write a summary table (JSON + Markdown).

    python tools/voxel3d/run_batch.py tools/voxel3d/examples/bossblocks_set.json --out <dir> [--vision] [--reuse-specs]

--reuse-specs rebuilds from <out>/<id>/spec.json when present (no model call): deterministic re-run.
Runs at below-normal priority on Windows (the machine is shared).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxel3d.make_object import make  # noqa: E402


def _low_priority() -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("items", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--vision", action="store_true")
    ap.add_argument("--reuse-specs", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    _low_priority()
    items = json.loads(args.items.read_text(encoding="utf-8"))
    labels = [it["label"] for it in items] + ["car", "chair", "sword", "boat", "flower", "tower"]
    rows = []
    for it in items:
        if args.only and it["id"] not in args.only:
            continue
        out = args.out / it["id"]
        spec_path = out / "spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8")) if args.reuse_specs and spec_path.is_file() else None
        t0 = time.monotonic()
        rep = make(it["prompt"], out, spec=spec, vision=args.vision, target_label=it["label"], labels=labels,
                   name=it["id"])
        g = rep.get("glb_check", {})
        rows.append({
            "id": it["id"], "prompt": it["prompt"], "ok": rep.get("ok"), "tries": rep.get("tries"),
            "size": rep.get("size"), "voxels": (rep.get("grid") or {}).get("voxels"),
            "components": (rep.get("grid") or {}).get("components"),
            "glb_vertices": g.get("vertices"), "glb_faces": g.get("faces"),
            "checks": rep.get("checks"), "repairs": (rep.get("spec_repairs") or []) + (rep.get("build_repairs") or []),
            "errors": rep.get("spec_errors"), "lint": rep.get("lint"), "fits_world": (rep.get("structure") or {}).get("fits_world_at_origin"),
            "preview": (rep.get("paths") or {}).get("preview"), "vision": rep.get("vision"),
            "seconds": round(time.monotonic() - t0, 1),
        })
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["| id | prompt | voxels | size | GLB verts/faces | checks | vision label (description) | match |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        checks = "PASS" if r["ok"] else "FAIL " + ",".join(k for k, v in (r["checks"] or {}).items() if not v)
        v = r["vision"] or {}
        lines.append(f"| {r['id']} | {r['prompt']} | {r['voxels']} | {r['size']} | {r['glb_vertices']}/{r['glb_faces']} | "
                     f"{checks} | {v.get('label', '-')} ({v.get('description', v.get('error', '-'))}) | "
                     f"{'Y' if v.get('match') else 'N' if v else '-'} |")
    (args.out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
