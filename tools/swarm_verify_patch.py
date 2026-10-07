"""Independent re-check of a worker patch (stdlib only, no network, no servers).

    python tools/swarm_verify_patch.py --repo <checkout> --patch <file.diff> --holdout discovery|goal-budget \
        [--base <rev>] [--zone-test command-center/tests/test_x.py ...] [--allowed path ...] [--python <exe>]

Steps: clean copy of --base (git worktree-free: `git archive`) -> `git apply` the patch -> holdout (plain python, no
pytest) -> zone pytest -> verdict.  PASS = patch applies, touches only allowed paths (if given), holdout exit 0, zone tests
green.  PARTIAL = applies but holdout or zone tests are not both green (or the holdout improved without passing).
FAIL = empty/unappliable patch, forbidden path touched, holdout/zone crashed, or nothing improved over base.
Exit code: 0 PASS, 1 PARTIAL, 2 FAIL.  The checkout is never modified; work happens in a temp dir.
"""
from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HOLDOUTS = {"discovery": "tools/tree_holdout/discovery_nonfinite.py",
            "goal-budget": "tools/tree_holdout/goal_budget_nonfinite.py"}
RESULT = re.compile(r"HOLDOUT \S+: (\d+)/(\d+) passed")


def run(cmd: list[str], cwd: Path, env: dict | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env)


def holdout(py: str, work: Path, rel: str) -> tuple[int, int | None, int | None]:
    p = run([py, rel, "--repo", str(work)], work)
    m = RESULT.search(p.stdout)
    return p.returncode, (int(m.group(1)) if m else None), (int(m.group(2)) if m else None)


def verdict(args: argparse.Namespace) -> tuple[str, list[str]]:
    notes: list[str] = []
    repo = args.repo.resolve()
    patch = args.patch.read_text(encoding="utf-8", errors="replace") if args.patch.is_file() else ""
    if not patch.strip():
        return "FAIL", ["patch is empty or missing"]
    files = sorted(set(re.findall(r"^diff --git a/(\S+) b/", patch, re.M)))
    if not files:
        return "FAIL", ["patch has no 'diff --git' file headers"]
    bad = [f for f in files if f.startswith("tools/tree_holdout/") or (args.allowed and f not in args.allowed)]
    if bad:
        return "FAIL", [f"forbidden/out-of-scope paths touched: {bad}"]
    with tempfile.TemporaryDirectory(prefix="swarm-verify-") as tmp:
        work = Path(tmp) / "w"
        work.mkdir()
        arc = subprocess.run(["git", "-C", str(repo), "archive", args.base], capture_output=True, check=False)
        if arc.returncode:
            return "FAIL", [f"git archive {args.base} failed: {arc.stderr.decode(errors='replace')[:200]}"]
        tarfile.open(fileobj=io.BytesIO(arc.stdout)).extractall(work, filter="data")
        rel = HOLDOUTS[args.holdout]
        _, base_ok, base_tot = holdout(args.python, work, rel)
        notes.append(f"base holdout {base_ok}/{base_tot}")
        pf = Path(tmp) / "p.diff"
        pf.write_text(patch, encoding="utf-8", newline="")
        ap = run(["git", "apply", "--whitespace=nowarn", str(pf)], work)
        if ap.returncode:
            return "FAIL", [f"git apply failed: {ap.stderr[:300]}"]
        notes.append(f"applied; files={files}")
        rc, ok, tot = holdout(args.python, work, rel)
        notes.append(f"holdout rc={rc} {ok}/{tot}")
        if ok is None:
            return "FAIL", notes + ["holdout crashed / produced no result"]
        tests = args.zone_test or [f for f in files if "/tests/" in f]
        zone_green = True
        if tests:
            sep = ";"
            env = dict(__import__("os").environ, PYTHONPATH=sep.join(
                [str(work / "command-center"), str(work / "bossman-core"), str(work)]))
            z = run([args.python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests], work, env)
            tail = (z.stdout.strip().splitlines() or ["<no output>"])[-1]
            zone_green = z.returncode == 0
            notes.append(f"zone pytest rc={z.returncode}: {tail}")
        else:
            zone_green = False
            notes.append("no zone tests given and none in the patch -> cannot be PASS")
        if rc == 0 and zone_green:
            return "PASS", notes
        return ("PARTIAL" if rc != 2 and (ok or 0) > (base_ok or 0) else "FAIL"), notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--patch", required=True, type=Path)
    ap.add_argument("--holdout", required=True, choices=sorted(HOLDOUTS))
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--zone-test", action="append")
    ap.add_argument("--allowed", nargs="*")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    v, notes = verdict(args)
    print(f"VERDICT {v}")
    for n in notes:
        print("  -", n)
    return {"PASS": 0, "PARTIAL": 1}.get(v, 2)


if __name__ == "__main__":
    sys.exit(main())
