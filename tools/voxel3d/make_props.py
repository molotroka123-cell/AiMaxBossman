"""High-poly (5k-10k triangle) game props -> GLB + validation + preview, optional BossBlocks install.

    python tools/voxel3d/make_props.py --out <dir> [--only hp_tree,hp_lantern] [--spec my.json] [--target 8000]
    python tools/voxel3d/make_props.py --out <dir> --from-mesh mesh.glb --name chair --height 1.0 --up z
    python tools/voxel3d/make_props.py --out <dir> --game <BossBlocks dir>            # dry run: install plan
    python tools/voxel3d/make_props.py --out <dir> --game <BossBlocks dir> --apply    # copy into assets/props

Needs numpy, trimesh, fast-simplification, pygltflib, scipy (image->3D colour transfer), Pillow
(preview). Install layout in the game: assets/props/<name>.glb + assets/props/props.json manifest.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voxel3d import hipoly, hpspec, mesh_check, meshgen  # noqa: E402


def make_one(spec: dict, out: Path) -> dict:
    t0 = time.perf_counter()
    spec, fixes = hpspec.repair(spec)
    glb, stats, errors = hpspec.build(spec)
    if errors:
        raise ValueError(f"{spec.get('name')}: " + "; ".join(errors))
    path = out / f"{spec['name']}.glb"
    path.write_bytes(glb)
    stats.update(method="PROCEDURAL: part spec -> dense CSG-style parts + fBm noise -> quadric decimation",
                 seconds=round(time.perf_counter() - t0, 2), repairs=fixes, glb=str(path),
                 check=mesh_check.check_prop_glb(path, expect_height=float(spec["height"])))
    try:
        hipoly.render_preview(path, out / f"{spec['name']}.preview.png")
        stats["preview"] = str(out / f"{spec['name']}.preview.png")
    except ImportError:
        pass
    return stats


def install(game_dir: Path, reports: list[dict], apply: bool) -> list[str]:
    game_dir = Path(game_dir)
    if not (game_dir / "project.godot").is_file():
        raise FileNotFoundError(f"{game_dir} is not a Godot project (project.godot missing)")
    dst_dir = game_dir / "assets" / "props"
    plan = []
    manifest_path = dst_dir / "props.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"format": "bossblocks.props", "version": 1, "props": {}}
    for r in reports:
        name = Path(r["glb"]).stem
        plan.append(str(dst_dir / f"{name}.glb"))
        c = r["check"]
        manifest["props"][name] = {"label": r["label"], "file": f"res://assets/props/{name}.glb",
                                   "triangles": c["triangles"], "height_m": c["height_m"],
                                   "footprint_m": c["footprint_m"], "method": r["method"]}
    plan.append(str(manifest_path))
    if apply:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for r in reports:
            shutil.copyfile(r["glb"], dst_dir / Path(r["glb"]).name)
        manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--spec", nargs="*", default=None, help="part-spec JSON files (default: the 5 examples)")
    ap.add_argument("--target", type=int, default=None)
    ap.add_argument("--from-mesh", type=Path, default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--height", type=float, default=1.0)
    ap.add_argument("--up", choices=["y", "z"], default="y")
    ap.add_argument("--game", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    if args.from_mesh:
        name = args.name or args.from_mesh.stem
        t0 = time.perf_counter()
        glb, stats = hipoly.from_mesh_file(args.from_mesh, name, args.target or 8000, args.height, args.up)
        path = args.out / f"{name}.glb"
        path.write_bytes(glb)
        stats.update(seconds=round(time.perf_counter() - t0, 2), glb=str(path),
                     check=mesh_check.check_prop_glb(path, expect_height=args.height))
        hipoly.render_preview(path, args.out / f"{name}.preview.png")
        reports.append(stats)
    else:
        specs = meshgen.example_specs()
        if args.spec:
            specs = {Path(f).stem: json.loads(Path(f).read_text(encoding="utf-8")) for f in args.spec}
        names = [n for n in args.only.split(",") if n] or list(specs)
        for n in names:
            spec = dict(specs[n])
            if args.target:
                spec["target"] = args.target
            reports.append(make_one(spec, args.out))
    for r in reports:
        c = r["check"]
        print(json.dumps({"name": r["name"], "ok": c["ok"], "triangles": c["triangles"], "dense": r["dense_triangles"],
                          "height_m": c["height_m"], "footprint_m": c["footprint_m"],
                          "watertight_face_fraction": c["watertight_face_fraction"], "sat": c["mean_saturation"],
                          "seconds": r["seconds"]}))
    (args.out / "report.json").write_text(json.dumps(reports, indent=1), encoding="utf-8")
    if args.game:
        for p in install(args.game, reports, args.apply):
            print(("WROTE " if args.apply else "WOULD WRITE ") + p)
    return 0 if all(r["check"]["ok"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
