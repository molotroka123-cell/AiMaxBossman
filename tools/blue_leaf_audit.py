"""Per-leaf audit of the «code written» (blue) leaves of the capability tree.

For every leaf with status ``code`` it answers, from the checkout and from JUnit files produced by an actual test run:
which source files the leaf names, which test files import them, and whether those tests PASSED on the run.
It decides nothing about usefulness and never changes a status: "tests that import the module passed" is evidence of
presence and of regression coverage, not of benefit or of live operation.

    python tools/blue_leaf_audit.py --junit core.xml cc.xml root.xml ... --out docs/audits/blue-leaf-audit.json

No network, no models, no secrets.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"

# (path prefix of a source file) -> (import root stripped from the path)
PACKAGE_ROOTS = [
    "command-center/", "bossman-core/", "apps/poker-vision/", "apps/poker-lora/", "apps/poker-botlab/",
    "apps/ai-webcam-vision/src/", "apps/ai-webcam-vision/", "apps/exam-trainer-ai/", "apps/social-farm/",
    "apps/file-commander-mini/", "apps/bossman-accountant/", "apps/pc-autopilot-mini/", "apps/travel-architect/",
    "apps/ai-3d-maker/", "apps/solana-volume-suite/", "solana_volume_suite/", "src/",
]
TEST_DIRS = [
    "command-center/tests", "tests", "bossman-core/tests", "apps/poker-vision/tests", "apps/poker-lora/tests",
    "apps/poker-botlab/tests", "apps/ai-webcam-vision/tests", "solana_volume_suite/tests",
]


def dotted(path: str) -> str | None:
    p = path.replace("\\", "/")
    if not p.endswith(".py"):
        return None
    for pre in PACKAGE_ROOTS:
        if p.startswith(pre):
            p = p[len(pre):]
            break
    p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def test_imports(path: Path) -> set[str]:
    """Every dotted name a test file imports, plus ``package.name`` for ``from package import name``."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def test_path_mentions(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_index() -> tuple[dict[str, set[str]], dict[str, str]]:
    imports: dict[str, set[str]] = {}
    texts: dict[str, str] = {}
    for d in TEST_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("test_*.py"):
            rel = f.relative_to(ROOT).as_posix()
            imports[rel] = test_imports(f)
            texts[rel] = test_path_mentions(f)
    return imports, texts


def tests_for(source: str, imports: dict[str, set[str]], texts: dict[str, str]) -> list[str]:
    mod = dotted(source)
    found: list[str] = []
    if mod:
        for t, names in imports.items():
            if any(n == mod or n.startswith(mod + ".") for n in names):
                found.append(t)
        return sorted(set(found))
    # scripts (tools/x.py, tools/x.ps1): tests load them by path
    s = source.replace("\\", "/")
    for t, txt in texts.items():
        if s in txt or re.search(r"[\"'/]" + re.escape(Path(s).name) + r"[\"']", txt):
            found.append(t)
    return sorted(set(found))


def read_junit(files: list[Path]) -> dict[str, dict[str, int]]:
    """test file (repo-relative, best effort) -> {passed, failed, skipped}."""
    res: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
    for jf in files:
        root = ET.parse(jf).getroot()
        for case in root.iter("testcase"):
            cls = case.get("classname", "")
            parts = cls.split(".")
            # classname is dotted path to the module (maybe plus a class): find the longest prefix that is a file
            key = None
            for i in range(len(parts), 0, -1):
                cand = "/".join(parts[:i]) + ".py"
                key = cand
                break
            kind = "passed"
            if case.find("failure") is not None or case.find("error") is not None:
                kind = "failed"
            elif case.find("skipped") is not None:
                kind = "skipped"
            res[(jf.stem, key)][kind] += 1  # type: ignore[index]
    return res  # type: ignore[return-value]


def outcome_by_file(junit: dict, test_files: list[str]) -> dict[str, dict[str, int]]:
    """Resolve junit classnames (dotted, relative to each run's cwd) to repo-relative test files."""
    out: dict[str, dict[str, int]] = {}
    for tf in test_files:
        stem = tf[:-3].replace("/", ".")
        agg = {"passed": 0, "failed": 0, "skipped": 0}
        hit = False
        for (_run, key), counts in junit.items():
            k = key[:-3].replace("/", ".")
            # a run started inside command-center/ reports "tests.test_x"; the repo-relative path is command-center/tests/test_x
            if stem == k or stem.endswith("." + k) or k.endswith("." + stem):
                hit = True
                for n in agg:
                    agg[n] += counts[n]
        if hit:
            out[tf] = agg
    return out


def classify(leaf: dict, files: list[str], outcomes: dict[str, dict[str, int]]) -> dict:
    py = [s for s in leaf["sources"] if s.endswith(".py")]
    ran = {f: o for f, o in outcomes.items() if (o["passed"] + o["failed"]) > 0}
    failed = {f: o for f, o in ran.items() if o["failed"]}
    if not leaf["sources"]:
        verdict, note = "unclear", "no source path recorded"
    elif failed:
        verdict, note = "fix", "tests importing it FAIL on this run"
    elif ran:
        verdict, note = "covered", "tests that import it passed (regression coverage only; benefit and live use not shown)"
    elif files:
        verdict, note = "covered-skipped", "importing tests exist but none executed (skipped or not run)"
    elif not py:
        verdict, note = "non-python", "non-Python source; no automated test mapped"
    else:
        verdict, note = "untested", "no test file imports this module"
    return {"verdict": verdict, "note": note}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--junit", nargs="*", default=[])
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    nodes = json.loads(TREE.read_text(encoding="utf-8"))["nodes"]
    leaves = [n for n in nodes if n["status"] == "code"]
    imports, texts = build_index()
    junit = read_junit([Path(j) for j in a.junit]) if a.junit else {}
    rows = []
    for n in leaves:
        srcs = [s["path"] for s in n.get("sources", []) if s.get("path")]
        files = sorted({t for s in srcs for t in tests_for(s, imports, texts)})
        outcomes = outcome_by_file(junit, files) if junit else {}
        v = classify({"sources": srcs}, files, outcomes)
        rows.append({"id": n["id"], "label": n["label"], "zone": n["parent"], "sources": srcs, "tests": files,
                     "passed": sum(o["passed"] for o in outcomes.values()),
                     "failed": sum(o["failed"] for o in outcomes.values()),
                     "skipped": sum(o["skipped"] for o in outcomes.values()), **v})
    summary: dict[str, int] = defaultdict(int)
    for r in rows:
        summary[r["verdict"]] += 1
    doc = {"leaves": len(rows), "summary": dict(summary), "junit_files": [Path(j).name for j in a.junit],
           "meaning": "covered = tests importing the module passed; NOT proof of benefit or of live operation",
           "rows": rows}
    if a.out:
        Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"leaves": len(rows), "summary": dict(summary)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
