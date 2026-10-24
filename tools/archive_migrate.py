"""Copy a data tree to the new SSD, PROVE it is identical, then (optionally) switch the pointer. Never deletes (owner 24.10).

    python tools/archive_migrate.py --src <tree on C:> --dst <tree on D:> [--pointer <storage.json>] [--receipt <file.json>]

1. before: manifest of the source (relative path, size, sha256 for EVERY file) and its own hash;
2. copy with robocopy (/COPY:DAT, no purge, low thread count so a generation running next to it is not starved);
3. after: manifest of the copy; the two are compared file by file: missing / extra / size or hash mismatch;
4. only on a clean comparison, and only if ``--pointer`` is given: ``current_physical_root`` in that storage.json is set to the
   copy (the procedure written in the K1m6a archive's own DATA_FORMAT.md). The source tree is left untouched as the reserve;
   removing it is a separate owner decision. Rolling back = writing the old path into ``current_physical_root`` again.
Idle gate: the copy waits while the owner or a generation job needs the machine (bcc.owner_activity + Direct Gen queue).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUF = 16 << 20


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        while True:
            block = f.read(BUF)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def build_manifest(root: Path, workers: int = 4) -> dict[str, dict]:
    """{relative posix path: {size, sha256}} for every regular file (reparse points are not followed)."""
    root = Path(root)
    paths = []
    stack = [str(root)]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.is_symlink() or getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    paths.append(Path(e.path))

    def one(p: Path):
        return p.relative_to(root).as_posix(), {"size": p.stat().st_size, "sha256": file_sha256(p)}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(one, paths))


def manifest_hash(manifest: dict[str, dict]) -> str:
    h = hashlib.sha256()
    for rel in sorted(manifest):
        h.update(f"{rel}\t{manifest[rel]['size']}\t{manifest[rel]['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


def compare(before: dict[str, dict], after: dict[str, dict]) -> dict:
    missing = sorted(set(before) - set(after))
    extra = sorted(set(after) - set(before))
    changed = sorted(r for r in set(before) & set(after) if before[r] != after[r])
    return {"missing": missing, "extra": extra, "changed": changed, "identical": not (missing or extra or changed)}


def robocopy(src: Path, dst: Path, threads: int = 2) -> int:
    """Exit codes 0-7 are success for robocopy (bit 0 = files copied); >= 8 is a failure."""
    r = subprocess.run(["robocopy", str(src), str(dst), "/E", "/COPY:DAT", "/DCOPY:DAT", f"/MT:{threads}", "/R:2", "/W:3",
                        "/NP", "/NFL", "/NDL", "/NJH", "/NJS"], capture_output=True)
    return r.returncode


def switch_pointer(pointer: Path, new_root: Path) -> dict:
    """Set ``current_physical_root`` (and remember the previous value for rollback). Returns old and new."""
    doc = json.loads(Path(pointer).read_text(encoding="utf-8"))
    old = doc.get("current_physical_root")
    doc["previous_physical_root"] = old
    doc["current_physical_root"] = str(new_root)
    Path(pointer).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return {"old": old, "new": str(new_root)}


def migrate(src: Path, dst: Path, *, pointer: Path | None = None, copy=robocopy, gate=None, workers: int = 4) -> dict:
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise FileNotFoundError(src)
    receipt = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "src": str(src), "dst": str(dst), "deleted_anything": False}
    t0 = time.time()
    before = build_manifest(src, workers)
    receipt["before"] = {"files": len(before), "bytes": sum(m["size"] for m in before.values()), "manifest_sha256": manifest_hash(before)}
    if gate is not None:
        gate()
    rc = copy(src, dst)
    receipt["copy_exit_code"] = rc
    if rc >= 8:
        receipt.update(verdict="FAIL", reason=f"robocopy exit {rc}")
        return receipt
    after = build_manifest(dst, workers)
    receipt["after"] = {"files": len(after), "bytes": sum(m["size"] for m in after.values()), "manifest_sha256": manifest_hash(after)}
    cmp = compare(before, after)
    receipt["comparison"] = {k: (v if k == "identical" else {"count": len(v), "first": v[:5]}) for k, v in cmp.items()}
    if not cmp["identical"]:
        receipt.update(verdict="FAIL", reason="copy differs from the source; pointer NOT switched")
        return receipt
    receipt["pointer"] = switch_pointer(pointer, dst) if pointer else "not switched (no --pointer)"
    receipt.update(verdict="PASS", seconds=round(time.time() - t0, 1),
                   note="source tree kept as the reserve; removing it needs the owner's approval")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--pointer", default=None)
    ap.add_argument("--receipt", default=None)
    ap.add_argument("--no-gate", action="store_true")
    a = ap.parse_args(argv)
    gate = None
    if not a.no_gate:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import model_tournament as mt
        gate = mt.wait_idle
    receipt = migrate(Path(a.src), Path(a.dst), pointer=Path(a.pointer) if a.pointer else None, gate=gate)
    if a.receipt:
        Path(a.receipt).parent.mkdir(parents=True, exist_ok=True)
        Path(a.receipt).write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps({k: receipt[k] for k in ("verdict", "before", "after", "comparison", "pointer", "reason") if k in receipt}, ensure_ascii=False))
    return 0 if receipt["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
