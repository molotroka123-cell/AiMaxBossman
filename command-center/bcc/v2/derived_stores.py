"""Allowlisted snapshot support for rebuildable BOSSMAN derived stores."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path
from typing import Any

DERIVED_DIRNAME = "derived"
ALLOWLIST = (
    "memory/*.sqlite3",
    "memory/*.sqlite",
    "memory/index-*.json",
    "code-index/*.json",
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _safe_relative(data_dir: Path, path: Path) -> Path:
    root = data_dir.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"derived store outside data_dir: {path}") from exc


def discover(data_dir: Path) -> list[Path]:
    data_dir = Path(data_dir)
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in ALLOWLIST:
        for path in data_dir.glob(pattern):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            _safe_relative(data_dir, resolved)
            seen.add(resolved)
            out.append(resolved)
    return sorted(out)


async def copy_into_snapshot(*, data_dir: Path, snapshot_base: Path,
                             per_file_limit: int, total_limit: int) -> list[dict[str, Any]]:
    data_dir = Path(data_dir)
    dest_root = Path(snapshot_base) / DERIVED_DIRNAME
    entries: list[dict[str, Any]] = []
    copied_total = 0

    for source in discover(data_dir):
        rel = _safe_relative(data_dir, source)
        size = source.stat().st_size
        entry: dict[str, Any] = {
            "relative_path": rel.as_posix(),
            "size_bytes": size,
            "sha256": sha256_file(source),
            "copied": False,
            "rebuildable": True,
        }
        if size > per_file_limit or copied_total + size > total_limit:
            entry["reason"] = "size limit; derived store can be rebuilt"
            entries.append(entry)
            continue

        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, dest)
        entry["snapshot_file"] = (Path(DERIVED_DIRNAME) / rel).as_posix()
        entry["copied"] = True
        copied_total += size
        entries.append(entry)
    return entries


async def safety_copy_current(*, data_dir: Path, safety_dir: Path,
                              entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data_dir = Path(data_dir)
    out: list[dict[str, Any]] = []
    for entry in entries:
        rel_raw = entry.get("relative_path")
        if not rel_raw:
            continue
        rel = Path(rel_raw)
        source = (data_dir / rel).resolve()
        try:
            source.relative_to(data_dir.resolve())
        except ValueError:
            continue
        if not source.is_file():
            continue
        dest = Path(safety_dir) / DERIVED_DIRNAME / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, dest)
        out.append({"relative_path": rel.as_posix(), "sha256": sha256_file(dest),
                    "size_bytes": dest.stat().st_size})
    return out


async def restore_from_snapshot(*, data_dir: Path, snapshot_base: Path,
                                entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data_dir = Path(data_dir)
    base = Path(snapshot_base)
    results: list[dict[str, Any]] = []

    for entry in entries:
        rel_raw = entry.get("relative_path")
        if not rel_raw:
            continue
        rel = Path(rel_raw)
        target = (data_dir / rel).resolve()
        try:
            target.relative_to(data_dir.resolve())
        except ValueError:
            results.append({"relative_path": rel.as_posix(), "restored": False,
                            "reason": "unsafe target path"})
            continue

        if not entry.get("copied"):
            results.append({"relative_path": rel.as_posix(), "restored": False,
                            "reason": "not copied; rebuild required"})
            continue

        snap_rel = Path(str(entry.get("snapshot_file") or ""))
        source = (base / snap_rel).resolve()
        try:
            source.relative_to(base.resolve())
        except ValueError:
            results.append({"relative_path": rel.as_posix(), "restored": False,
                            "reason": "unsafe snapshot path"})
            continue
        if not source.is_file():
            results.append({"relative_path": rel.as_posix(), "restored": False,
                            "reason": "snapshot file missing"})
            continue

        expected = str(entry.get("sha256") or "")
        actual = sha256_file(source)
        if expected and actual != expected:
            results.append({"relative_path": rel.as_posix(), "restored": False,
                            "reason": "checksum mismatch"})
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        results.append({"relative_path": rel.as_posix(), "restored": True,
                        "sha256": sha256_file(target)})
    return results
