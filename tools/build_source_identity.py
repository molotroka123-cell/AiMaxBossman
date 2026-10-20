"""Build identity from HEAD objects and observed files, never index flags."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def _git(repository: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repository), *args],
                            capture_output=True, timeout=30, check=True)
    return result.stdout


# Per-file blob SHA-1 cache keyed by (resolved_path, st_mtime_ns, st_size, st_ino).
# Preserves full byte verification when a file is first seen or its stat changes, while
# avoiding re-reading 1,000+ unchanged tracked files on repeated calls in the same process.
_BLOB_HASH_CACHE: dict[tuple[str, int, int, int], str] = {}


def source_identity(repository: Path) -> dict:
    """Unknown Git identity is unmeasured; any extra packaged input is dirty.

    Setuptools outputs live outside these input directories. Only generated
    Python bytecode is excluded; Git ignore/exclude rules cannot hide source.
    """
    repository = repository.resolve()
    try:
        sha = _git(repository, "rev-parse", "HEAD").decode().strip()
        if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
            raise ValueError("unmeasured source SHA")
        tracked = set()
        dirty = bool(_git(repository, "status", "--porcelain", "--untracked-files=normal"))
        # Detect hidden index flags (--assume-unchanged -> 'h', --skip-worktree -> 'S')
        has_hidden_index_flags = False
        for line in _git(repository, "ls-files", "-v", "-z").split(b"\0"):
            if line and line[:1] in (b"h", b"S"):
                has_hidden_index_flags = True
                break
        repo_str = str(repository)
        for entry in _git(repository, "ls-tree", "-r", "-z", sha).split(b"\0"):
            if not entry:
                continue
            header, encoded_path = entry.split(b"\t", 1)
            mode, kind, expected = header.split()
            relative = os.fsdecode(encoded_path)
            tracked.add(relative)
            path = repository / relative
            if kind != b"blob":
                dirty = True  # submodule content is not attested here
                continue
            try:
                st = path.lstat()
                is_link = (st.st_mode & 0o170000) == 0o120000
                if mode == b"120000":
                    data = os.fsencode(os.readlink(path))
                    actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data,
                                          usedforsecurity=False).hexdigest()
                else:
                    cache_key = (f"{repo_str}/{relative}", st.st_mtime_ns, st.st_size, st.st_ino)
                    actual = None if has_hidden_index_flags else _BLOB_HASH_CACHE.get(cache_key)
                    if actual is None:
                        data = path.read_bytes()
                        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data,
                                              usedforsecurity=False).hexdigest()
                        if not has_hidden_index_flags and len(_BLOB_HASH_CACHE) < 16384:
                            _BLOB_HASH_CACHE[cache_key] = actual
                dirty |= (actual != expected.decode() or (mode == b"120000") != is_link)
                if os.name != "nt" and mode in {b"100644", b"100755"}:
                    dirty |= bool(st.st_mode & 0o111) != (mode == b"100755")
            except OSError:
                dirty = True
        inputs = [repository / name for name in
                  ("bossman_shared", "learning", "schemas", "bossman-core/bossman",
                   "command-center/bcc", "command-center/ui")]
        inputs.extend((repository / "apps").glob("*/src"))
        inputs.extend((repository / "apps").glob("*/profiles"))
        for pattern in ("*/app.manifest.yaml", "*/pyproject.toml"):
            for path in (repository / "apps").glob(pattern):
                if path.relative_to(repository).as_posix() not in tracked:
                    dirty = True
        for root in inputs:
            for path in root.rglob("*"):
                if not (path.is_file() or path.is_symlink()):
                    continue
                if path.suffix in {".pyc", ".pyo"} and "__pycache__" in path.parts:
                    continue
                if path.relative_to(repository).as_posix() not in tracked:
                    dirty = True
        return {"source_sha": sha, "source_dirty": bool(dirty)}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"source_sha": None, "source_dirty": None}


def _read_json(path: Path | None) -> dict:
    """Missing or unreadable documents are unmeasured (empty), not matching."""
    if path is None:
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _is_ancestor_or_equal(repository: Path, candidate_sha: str | None, head_sha: str | None) -> bool:
    if not candidate_sha or not head_sha:
        return False
    if candidate_sha == head_sha:
        return True
    if re.fullmatch(r"[0-9a-f]{40}", str(candidate_sha)) is None:
        return False
    try:
        res = subprocess.run(
            ["git", "-C", str(repository), "merge-base", "--is-ancestor", str(candidate_sha), str(head_sha)],
            capture_output=True, timeout=10, check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def reconcile_four_identities(repository: Path, *, seed_path: Path | None = None,
                              registry_path: Path | None = None,
                              bundle_manifest_path: Path | None = None) -> dict:
    """Compare source, capability-tree seed, registry head and bundle SHAs.

    An unmeasured source SHA matches nothing; a dirty source or bundle never
    counts as matching even when its SHA is equal. Because committing updated
    seed/registry JSON files creates a new commit whose parent is the stamped
    SHA, `seed_sha` and `registry_head` match when they equal `source_sha` or
    are a verified git ancestor of `source_sha` in `repository`.
    """
    repository = repository.resolve()
    source = source_identity(repository)
    source_sha, source_dirty = source["source_sha"], source["source_dirty"]
    seed = _read_json(seed_path or repository / "command-center/bcc/capability_tree_seed.json")
    registry = _read_json(registry_path or repository / "docs/architecture/tree-registry.json")
    bundle = _read_json(bundle_manifest_path)
    seed_sha, registry_head = seed.get("source_sha"), registry.get("head")
    bundle_sha, bundle_dirty = bundle.get("source_sha"), bundle.get("source_dirty")

    measured = source_sha is not None
    seed_ok = measured and _is_ancestor_or_equal(repository, seed_sha, source_sha)
    reg_ok = measured and _is_ancestor_or_equal(repository, registry_head, source_sha)
    bundle_ok = measured and bundle_sha == source_sha and bundle_dirty is False
    matches = {
        "source_sha": measured and source_dirty is False,
        "seed_sha": seed_ok,
        "registry_head": reg_ok,
        "bundle_sha": bundle_ok,
    }
    mismatches = {}
    if not seed_ok:
        mismatches["seed_sha"] = seed_sha
    if not reg_ok:
        mismatches["registry_head"] = registry_head
    if bundle_sha != source_sha:
        mismatches["bundle_sha"] = bundle_sha
    matched_count = sum(matches.values())
    return {
        "source_sha": source_sha,
        "source_dirty": source_dirty,
        "seed_sha": seed_sha,
        "registry_head": registry_head,
        "bundle_sha": bundle_sha,
        "bundle_dirty": bundle_dirty,
        "matched_count": matched_count,
        "all_four_match": matched_count == 4 and source_dirty is False,
        "mismatches": mismatches,
    }


DEFAULT_FAST_OPS_TESTS = [
    "tests/test_acceptance_registry.py",
    "tests/test_build_source_identity.py",
    "tests/test_tree_registry_in_sync.py",
    "tests/test_tree_registry_sync.py",
    "command-center/tests/test_tree_apply_evidence.py",
    "tests/test_tree_publish_site.py",
    "tests/test_ci_secret_scan.py",
]


def run_fast_ops_receipt(repository: Path, *, workers: int = 4, out_path: Path | None = None,
                         bundle_manifest_path: Path | None = None,
                         test_files: list[str] | None = None) -> dict:
    repository = repository.resolve()
    files = list(DEFAULT_FAST_OPS_TESTS if test_files is None else test_files)
    command = [sys.executable, "-m", "pytest"]
    if workers > 1:
        command += ["-n", str(workers)]
    command += ["-q", "-p", "no:cacheprovider", *files]
    started = time.monotonic()
    try:
        exit_code = subprocess.run(command, cwd=repository).returncode
    except OSError:
        exit_code = 127
    elapsed_s = time.monotonic() - started
    identity = reconcile_four_identities(repository, bundle_manifest_path=bundle_manifest_path)
    source_dirty = identity["source_dirty"]
    receipt = {
        "schema": "bossman-ops-receipt-1",
        "source_sha": identity["source_sha"],
        "source_dirty": source_dirty,
        "identity_reconciliation": identity,
        "pytest": {
            "workers": workers,
            "elapsed_s": round(elapsed_s, 3),
            "exit_code": exit_code,
            "passed": exit_code == 0 and source_dirty is False,
        },
    }
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    receipt["receipt_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--reconcile", action="store_true",
                        help="print the four-identity reconciliation")
    parser.add_argument("--bundle-manifest", type=Path, default=None)
    parser.add_argument("--fast-receipt", action="store_true",
                        help="run the fast ops tests and emit a receipt")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.fast_receipt:
        result = run_fast_ops_receipt(args.repository, workers=args.workers, out_path=args.out,
                                      bundle_manifest_path=args.bundle_manifest)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["pytest"]["passed"] else 1
    result = reconcile_four_identities(args.repository, bundle_manifest_path=args.bundle_manifest)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_four_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
