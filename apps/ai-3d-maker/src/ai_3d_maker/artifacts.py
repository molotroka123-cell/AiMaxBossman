"""Artifact manifest, checksums and the human-readable print report.

Rule inherited from the source pack and kept: never list a file that does not
exist. The manifest is generated from the filesystem, not from intentions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .mesh import sha256_file

MANIFEST_NAME = "manifest.json"

# Files that describe the job itself rather than being produced output.
_MANIFEST_EXCLUDES = {MANIFEST_NAME}


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    path: str
    bytes: int
    sha256: str
    kind: str

    def as_dict(self) -> dict:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256, "kind": self.kind}


_KINDS = {
    ".stl": "mesh",
    ".obj": "mesh",
    ".3mf": "mesh",
    ".step": "cad",
    ".stp": "cad",
    ".scad": "cad-source",
    ".gcode": "machine-code",
    ".json": "metadata",
    ".md": "report",
    ".txt": "text",
}


def classify(path: Path) -> str:
    return _KINDS.get(path.suffix.lower(), "other")


def list_artifacts(directory: str | Path) -> list[ArtifactEntry]:
    root = Path(directory)
    if not root.is_dir():
        return []
    entries: list[ArtifactEntry] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root).as_posix()
        if rel in _MANIFEST_EXCLUDES:
            continue
        entries.append(ArtifactEntry(rel, p.stat().st_size, sha256_file(p), classify(p)))
    return entries


def build_manifest(directory: str | Path, *, extra: dict | None = None) -> dict:
    entries = list_artifacts(directory)
    manifest = {
        "schema": "ai-3d-maker/manifest/1",
        "files": [e.as_dict() for e in entries],
        "file_count": len(entries),
        "total_bytes": sum(e.bytes for e in entries),
    }
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(directory: str | Path, *, extra: dict | None = None) -> Path:
    root = Path(directory)
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(build_manifest(root, extra=extra), indent=2, sort_keys=True), encoding="utf-8")
    return path


def write_report(path: str | Path, data: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def block(title: str, rows: list[str]) -> list[str]:
        return [f"## {title}", ""] + (rows or ["- none"]) + [""]

    lines = [
        "# AI 3D Maker — Print Report",
        "",
        f"Job: `{data.get('job_id', 'unknown')}`",
        f"Design: {data.get('design_name', 'unknown')}",
        f"Status: **{data.get('status', 'UNKNOWN')}**",
        f"Printability: **{data.get('printability', 'UNKNOWN')}**",
        "",
    ]
    lines += block("Printer", [
        f"- model: {data.get('printer', 'unknown')}",
        f"- profile: {data.get('profile_id', 'unknown')}",
        f"- verified build volume: {data.get('build_volume_mm', 'unknown')} mm",
    ])
    lines += block("Geometry", [
        f"- triangles: {data.get('triangles', 'n/a')}",
        f"- extents: {data.get('extents_mm', 'n/a')} mm",
        f"- watertight: {data.get('watertight', 'n/a')}",
        f"- manifold: {data.get('manifold', 'n/a')}",
        f"- components: {data.get('components', 'n/a')}",
        f"- volume: {data.get('volume_mm3', 'n/a')} mm^3",
    ])
    lines += block("Stages", [f"- {name}: {status}" for name, status in (data.get("stages") or {}).items()])
    lines += block("Blocking reasons", [f"- {r}" for r in (data.get("reasons") or [])])
    lines += block("Warnings", [f"- {w}" for w in (data.get("warnings") or [])])
    lines += block("Artifacts", [
        f"- `{a['path']}` ({a['bytes']} bytes, sha256 {a['sha256'][:16]}...)"
        for a in (data.get("artifacts") or [])
    ])
    lines += block("Physical printing", [
        "- This report is a digital result. No heater, motor or media was touched.",
        f"- Physical action requires explicit human confirmation: `{data.get('confirmation_token', 'n/a')}`",
        f"- Transport: {data.get('transport', 'simulator')}",
    ])
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return p
