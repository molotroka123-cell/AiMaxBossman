#!/usr/bin/env python3
"""One command: apply evidence -> regenerate the public site data -> run site tests -> (optionally) push.

  python tools/tree_publish_site.py                 # dry: regenerates data in the site clone, no commit, no push
  python tools/tree_publish_site.py --push          # commit + push site main, only if tests pass and the clone is clean

Safety: never commits, checks out or pushes the Bossman repo (tree_apply_evidence only rewrites the seed /
evidence files in the working tree, exactly as when run by hand). The site is pushed only with --push,
only when the site tests pass, only from branch main, and only when the clone has no uncommitted changes
outside the generated paths (data/, ai/, llms.txt). Nothing is scheduled; no services are touched.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE = Path(r"C:\Users\asd\AppData\Local\Temp\claude\C--Users-asd\88c0997b-4206-413b-933a-31f0613060f8\scratchpad\site-repo")
GENERATED = ("data/", "ai/", "llms.txt")
SEED_REL = "command-center/bcc/capability_tree_seed.json"
EVID_REL = "docs/architecture/bossman-tree-20261005/evidence"
TRAILERS = ("Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_01UqQQW9548KbURNRTWtzTo8")


class Refused(Exception):
    pass


def _run(cmd, cwd=None, check=False):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if check and r.returncode:
        raise Refused(f"{' '.join(map(str, cmd[:4]))} failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    return r


def _git(site: Path, *a):
    return _run(["git", "-C", str(site), *a])


def unrelated_changes(site: Path) -> list[str]:
    """Uncommitted paths in the site clone that are not generated data (bytecode caches ignored)."""
    r = _git(site, "status", "--porcelain", "--untracked-files=all")
    if r.returncode:
        raise Refused(f"not a git repo: {site}")
    out = []
    for line in r.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1]
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if not path.startswith(GENERATED):
            out.append(path)
    return out


def run_apply(source: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, str(Path(__file__).with_name("tree_apply_evidence.py")),
                 "--seed", str(source / SEED_REL), "--evidence", str(source / EVID_REL), "--repo", str(source)])


def run_sync(site: Path, source: Path, registry: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, str(site / "scripts" / "sync_bossman.py"), "--source", str(source),
                 "--registry", str(registry), "--out", str(site)], cwd=site)


def run_site_tests(site: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], cwd=site)


def data_summary(site: Path) -> dict:
    try:
        meta = json.loads((site / "data" / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"by_status": {}, "head": None}
    return {"by_status": meta.get("counts", {}).get("by_status", {}), "head": (meta.get("source") or {}).get("head")}


def publish(site: Path, source: Path = ROOT, registry: Path | None = None, push: bool = False,
            python: str = sys.executable, log=print) -> dict:
    site, source = Path(site).resolve(), Path(source).resolve()
    registry = Path(registry) if registry else source / "docs" / "architecture" / "tree-registry.json"
    if not (site / ".git").exists():
        raise Refused(f"site clone not found: {site}")
    if push:
        dirty = unrelated_changes(site)
        if dirty:
            raise Refused("site clone has unrelated uncommitted changes: " + ", ".join(dirty[:8]))
        branch = _git(site, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            raise Refused(f"site clone is on '{branch}', not main")
    r = run_apply(source, python)
    log(r.stdout.strip() or r.stderr.strip())
    if r.returncode:
        raise Refused("tree_apply_evidence failed")
    r = run_sync(site, source, registry, python)
    log(r.stdout.strip() or r.stderr.strip())
    if r.returncode or "SYNC=OK" not in r.stdout:
        raise Refused("site sync failed")
    t = run_site_tests(site, python)
    tests_ok = t.returncode == 0
    log("site tests: PASS" if tests_ok else "site tests: FAIL\n" + (t.stderr or t.stdout)[-1500:])
    summary = data_summary(site)
    result = {"tests_ok": tests_ok, "pushed": False, "committed": None, **summary}
    if push:
        if not tests_ok:
            raise Refused("site tests failed; nothing committed or pushed")
        dirty = unrelated_changes(site)
        if dirty:
            raise Refused("site clone has unrelated uncommitted changes: " + ", ".join(dirty[:8]))
        paths = [g.rstrip("/") for g in GENERATED if (site / g.rstrip("/")).exists()]
        if paths:
            _run(["git", "-C", str(site), "add", "--", *paths], check=True)
        if _git(site, "diff", "--cached", "--quiet").returncode:
            msg = f"data: tree snapshot @ {summary['head']}, " + ", ".join(
                f"{k}={v}" for k, v in sorted(summary["by_status"].items())) + "\n\n" + TRAILERS
            _run(["git", "-C", str(site), "commit", "-q", "-m", msg], check=True)
        result["committed"] = _git(site, "rev-parse", "HEAD").stdout.strip()
        _run(["git", "-C", str(site), "push", "origin", "HEAD:main"], check=True)
        result["pushed"] = True
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply evidence, regenerate the site data, test, optionally push")
    ap.add_argument("--site", type=Path, default=DEFAULT_SITE)
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--registry", type=Path, default=None)
    ap.add_argument("--push", action="store_true", help="commit and push site main (default: dry)")
    a = ap.parse_args(argv)
    try:
        res = publish(a.site, a.source, a.registry, a.push)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    print(f"{'PUSHED' if res['pushed'] else 'DRY (no commit, no push)'} head={res['head']} site_commit={res['committed']}")
    print("by_status: " + ", ".join(f"{k}={v}" for k, v in sorted(res["by_status"].items())))
    return 0 if res["tests_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
