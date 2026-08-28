"""CLI and server entrypoints.

    ai-3d-maker serve                       start the HTTP control surface
    ai-3d-maker capabilities                what is actually available here
    ai-3d-maker build spec.json [--slice]   run the pipeline on a DesignSpec
    ai-3d-maker validate model.stl          inspect an existing mesh
    ai-3d-maker jobs                        job history
    ai-3d-maker artifacts JOB               artifacts and checksums
    ai-3d-maker scan model.gcode            G-code safety scan
    ai-3d-maker confirm JOB                 show the human confirmation token

There is no CLI verb that starts a physical print. `printer.execute_physical`
is reachable only through the confirmed control operation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .control import ControlPlane
from .errors import Ai3dError
from .mesh import load_stl
from .meshcheck import inspect_mesh


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _cmd_serve(args, plane: ControlPlane) -> int:
    try:
        import uvicorn
    except Exception as exc:
        print(f"uvicorn is not installed: {exc}", file=sys.stderr)
        return 2
    from .api import build_app

    uvicorn.run(build_app(plane), host=plane.settings.host, port=plane.settings.port)
    return 0


def _cmd_capabilities(args, plane: ControlPlane) -> int:
    _print(plane.capabilities())
    return 0


def _cmd_health(args, plane: ControlPlane) -> int:
    _print(plane.health())
    return 0


def _cmd_metrics(args, plane: ControlPlane) -> int:
    _print(plane.metrics())
    return 0


def _cmd_build(args, plane: ControlPlane) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    payload = {
        "kind": "design",
        "spec": spec,
        "slice": args.slice,
        "wait": True,
    }
    if args.job_id:
        payload["job_id"] = args.job_id
    result = asyncio.run(plane.jobs_create(payload))
    _print(result)
    return 0 if result.get("result", {}).get("printable") else 1


def _cmd_import(args, plane: ControlPlane) -> int:
    payload = {
        "kind": "import",
        "source_stl": args.stl,
        "source_units": args.units,
        "scale": args.scale,
        "slice": args.slice,
        "wait": True,
    }
    if args.job_id:
        payload["job_id"] = args.job_id
    result = asyncio.run(plane.jobs_create(payload))
    _print(result)
    return 0 if result.get("result", {}).get("printable") else 1


def _cmd_validate(args, plane: ControlPlane) -> int:
    try:
        mesh = load_stl(args.stl, max_triangles=plane.settings.max_triangles)
    except Ai3dError as exc:
        _print(exc.as_dict())
        return 1
    report = inspect_mesh(mesh)
    fits, orientation = plane.profile.fits(report.extents_mm)
    _print({
        "mesh": report.as_dict(),
        "fits_build_volume": fits,
        "orientation_mm": list(orientation) if orientation else None,
        "printer": plane.profile.model,
    })
    return 0 if report.status != "FAIL" and fits else 1


def _cmd_jobs(args, plane: ControlPlane) -> int:
    _print(plane.jobs_list(limit=args.limit))
    return 0


def _cmd_artifacts(args, plane: ControlPlane) -> int:
    try:
        _print(plane.artifacts_list(args.job_id))
    except Ai3dError as exc:
        _print(exc.as_dict())
        return 1
    return 0


def _cmd_cancel(args, plane: ControlPlane) -> int:
    try:
        _print(plane.jobs_cancel(args.job_id))
    except Ai3dError as exc:
        _print(exc.as_dict())
        return 1
    return 0


def _cmd_scan(args, plane: ControlPlane) -> int:
    text = Path(args.gcode).read_text(encoding="utf-8", errors="replace")
    result = plane.gcode_scan(text)
    _print(result)
    return 0 if result["status"] != "FAILED" else 1


def _cmd_confirm(args, plane: ControlPlane) -> int:
    try:
        _print(plane.confirmation_for(args.job_id, args.artifact))
    except Ai3dError as exc:
        _print(exc.as_dict())
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai-3d-maker", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the HTTP control surface").set_defaults(func=_cmd_serve)
    sub.add_parser("capabilities", help="report what is actually available").set_defaults(func=_cmd_capabilities)
    sub.add_parser("health", help="health check").set_defaults(func=_cmd_health)
    sub.add_parser("metrics", help="resource and job metrics").set_defaults(func=_cmd_metrics)

    p = sub.add_parser("build", help="build a DesignSpec into a printable artifact")
    p.add_argument("spec")
    p.add_argument("--slice", action="store_true")
    p.add_argument("--job-id", dest="job_id", default=None)
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("import", help="import and validate an existing STL")
    p.add_argument("stl")
    p.add_argument("--units", default="mm")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--slice", action="store_true")
    p.add_argument("--job-id", dest="job_id", default=None)
    p.set_defaults(func=_cmd_import)

    p = sub.add_parser("validate", help="inspect an STL without creating a job")
    p.add_argument("stl")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("jobs", help="list job history")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=_cmd_jobs)

    p = sub.add_parser("artifacts", help="list artifacts and checksums for a job")
    p.add_argument("job_id")
    p.set_defaults(func=_cmd_artifacts)

    p = sub.add_parser("cancel", help="cancel a running job")
    p.add_argument("job_id")
    p.set_defaults(func=_cmd_cancel)

    p = sub.add_parser("scan", help="scan a G-code file for safety violations")
    p.add_argument("gcode")
    p.set_defaults(func=_cmd_scan)

    p = sub.add_parser("confirm", help="show the human confirmation token for a job artifact")
    p.add_argument("job_id")
    p.add_argument("--artifact", default=None)
    p.set_defaults(func=_cmd_confirm)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    plane = ControlPlane()
    return args.func(args, plane)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
