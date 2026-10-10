"""Generic pytest probe for capability-tree leaves in status code/branch (WS1 of the 2.1 FINAL spec, 2026-10-09).

    python tools/tree_proof/leaf_pytest_probe.py --leaf module-xxx [--leaf ...] [--lane l2-20261009] [--dry]

For every leaf: take its source module from the seed, find the test files that really IMPORT that module (no
name-guessing: an exact dotted import or a `from <package> import <module>`), run only those files, and write a
receipt that `tools/tree_apply_evidence.py` validates. A leaf with no importing test, or a failing run, gets NO receipt
(reported as NO_TEST / FAIL) - nothing is ever turned green by this tool itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"
EVID = ROOT / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
TEST_DIRS = ("command-center/tests", "bossman-core/tests", "tests", "apps")
PKG_ROOTS = ("command-center/", "bossman-core/")      # source prefixes that are import roots


def dotted(source: str) -> str | None:
    """`command-center/bcc/pit/discovery.py` -> `bcc.pit.discovery` (None when the path is not a python module)."""
    if not source.endswith(".py"):
        return None
    path = source
    for prefix in PKG_ROOTS:
        if path.startswith(prefix):
            path = path[len(prefix):]
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts and all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in parts) else None


def importing_tests(module: str, root: Path = ROOT) -> list[str]:
    """Test files that import `module` exactly (`import a.b.c`, `from a.b.c import x`, or `from a.b import c`)."""
    package, _, leaf = module.rpartition(".")
    pat = re.compile(rf"(?m)^\s*(?:import\s+{re.escape(module)}\b|from\s+{re.escape(module)}\s+import\b"
                     + (rf"|from\s+{re.escape(package)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b" if package else "") + ")")
    found = []
    for d in TEST_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for f in base.rglob("test_*.py"):
            try:
                if pat.search(f.read_text(encoding="utf-8", errors="replace")):
                    found.append(f.relative_to(root).as_posix())
            except OSError:
                continue
    return sorted(found)


def narrow_by_label(tests: list[str], label: str, root: Path = ROOT) -> list[str]:
    """For a source shared by several leaves: keep only the test files that name this leaf (its label) in their text."""
    out = []
    for t in tests:
        try:
            if label in (root / t).read_text(encoding="utf-8", errors="replace"):
                out.append(t)
        except OSError:
            continue
    return out


def source_of(node: dict) -> str | None:
    for s in node.get("sources", []):
        p = s.get("path", "")
        if p.endswith(".py") and not p.startswith("docs/"):
            return p
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--leaf", action="append", required=True)
    ap.add_argument("--lane", default="l2-20261009")
    ap.add_argument("--dry", action="store_true", help="only report which tests would run")
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    seed = {n["id"]: n for n in json.loads(SEED.read_text(encoding="utf-8"))["nodes"]}
    parents = {n.get("parent") for n in seed.values()}
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (EVID / "out").mkdir(parents=True, exist_ok=True)
    lane_file = EVID / f"{a.lane}.json"
    receipts = json.loads(lane_file.read_text(encoding="utf-8")) if lane_file.is_file() else []
    done = {r["node_id"] for r in receipts}
    summary = {}
    for nid in a.leaf:
        node = seed.get(nid)
        src = source_of(node) if node else None
        mod = dotted(src) if src else None
        tests = importing_tests(mod) if mod else []
        declared = [t for t in (node or {}).get("reference_paths", [])[1:] if t in tests]
        if declared:
            tests = declared        # the manifest named the tests that prove THIS leaf (and they really import its module)
        elif tests and sum(1 for o in seed.values() if o["id"] not in parents and source_of(o) == src) > 1:
            tests = narrow_by_label(tests, node.get("label", ""))     # shared module: the test must name THIS leaf
        if not tests:
            summary[nid] = f"NO_TEST ({src or 'no source'})"
            continue
        prior = EVID / "out" / f"{nid}.txt"
        if prior.is_file() and nid not in done:                          # never overwrite another probe's record (a FAIL stays on file)
            summary[nid] = f"CONFLICT_PRIOR_EVIDENCE ({prior.name} exists; adjudicate by hand, nothing written)"
            continue
        if a.dry:
            summary[nid] = f"WOULD_RUN {len(tests)} files"
            continue
        cmd = [a.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=300", *tests[:8]]
        started = datetime.now(timezone.utc).isoformat()
        t0 = time.time()
        cwd = ROOT / ("bossman-core" if tests[0].startswith("bossman-core/") else "command-center" if tests[0].startswith("command-center/") else ".")
        rel = [str(Path(ROOT / t).relative_to(cwd)) for t in tests[:8]]
        r = subprocess.run([a.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--timeout=300", *rel], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        text = (r.stdout + r.stderr)
        tail = "\n".join(text.strip().splitlines()[-6:])[-1400:]
        text_out = f"$ (cwd={cwd.relative_to(ROOT) if cwd != ROOT else '.'}) {' '.join(cmd[:3])} ... {' '.join(rel)}\nsha: {sha}\n\n{tail}\nexit_code: {r.returncode}\n"
        out = EVID / "out" / f"{nid}.txt"
        out.write_text(text_out, encoding="utf-8", newline="\n")
        summary[nid] = f"{'PASS' if r.returncode == 0 else 'FAIL'} {len(tests)} files, {round(time.time() - t0)}s: {tail.splitlines()[-1] if tail else ''}"
        if r.returncode == 0 and nid not in done:
            receipts.append({"node_id": nid, "sha": sha, "probe": f"pytest {len(tests)} test file(s) importing {mod}: {', '.join(tests[:3])}",
                             "command": " ".join(cmd[:4]) + " " + " ".join(rel), "exit_code": 0, "started_at": started,
                             "finished_at": datetime.now(timezone.utc).isoformat(),
                             "output_sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "output_tail": tail[-1400:],
                             "verdict": "PASS", "kind": "pytest"})
    if not a.dry and receipts:
        lane_file.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for k, v in summary.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
