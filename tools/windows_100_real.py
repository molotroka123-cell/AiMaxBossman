#!/usr/bin/env python3
"""Run exactly 100 real, deterministic Bossman checks on Windows.

This replaces the historical fake workflow that printed "OK" 100 times.

How it stays honest:
  * pytest collects real nodeids from five product areas. Each sub-project
    (`command-center`, `bossman-core`) is collected AND executed from its own
    directory — its own rootdir, conftest and `tests` package. Mixing them in
    one invocation from the repo root produced nodeids that did not exist
    (release blocker 2026-09-24: `tests/telegram_contracts/...` not found).
  * evidence carries stable repo-root nodeids (`command-center/tests/...`);
    every one is checked to name a real file.
  * a fixed quota per group, filled round-robin over the listed files; a
    missing file, a listed file without tests or a short group is a FAIL.
  * a small pytest plugin records the outcome of EVERY selected nodeid. PASS
    only when the executed set equals the selected set exactly and each test
    passed setup, call and teardown. Skipped, missing, extra, errored = FAIL.
  * `--negative-control` swaps one selected nodeid for a nonexistent one and
    requires the gate to go red (exit 0 only when it did).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
from collections import OrderedDict

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = "windows_100_plugin"

GROUPS: list[tuple[str, int, list[str]]] = [
    ("terminal_telegram", 25, [
        "command-center/tests/telegram_contracts",
        "command-center/tests/test_terminal_cli_unit.py",
        "command-center/tests/test_terminal_cli_e2e.py",
        "command-center/tests/test_terminal_review_cli.py",
    ]),
    ("studio_media", 20, [
        "command-center/tests/test_studio_vision_review.py",
        "command-center/tests/test_studio_integrations.py",
        "command-center/tests/test_studio_media_lifecycle.py",
        "command-center/tests/test_studio_partial_segments.py",
    ]),
    ("security_contracts", 20, [
        "command-center/tests/test_contract_keeps_agent_tools.py",
        "command-center/tests/test_snapshot_kind_containment.py",
        "command-center/tests/test_opencode_worktree_roots.py",
        "command-center/tests/test_p1_c3_terminal_roots.py",
        "command-center/tests/test_owner_stop_lifecycle.py",
        "command-center/tests/test_computer_stop_race.py",
    ]),
    ("coding", 20, [
        "bossman-core/tests/apprentice/test_local_sidecar.py",
        "bossman-core/tests/apprentice/test_openhands_snapshot_bounds.py",
        "command-center/tests/test_coding_apply.py",
        "command-center/tests/test_coding_recipes.py",
        "command-center/tests/test_coding_tasks_local_sidecar.py",
    ]),
    ("memory_skills", 15, [
        "command-center/tests/test_skill_catalog.py",
        "command-center/tests/test_skill_catalog_crlf.py",
        "command-center/tests/test_memory_lifecycle_wiring.py",
        "command-center/tests/test_memory_recall_engine.py",
        "command-center/tests/test_v21_memory.py",
    ]),
]
PROJECTS = ("command-center", "bossman-core")
# nodeid -> reason: tests this platform skips by marker; never selected.
EXCLUDED: dict[str, str] = {}
TOTAL = sum(quota for _, quota, _ in GROUPS)
assert TOTAL == 100


class GateError(Exception):
    """The selection itself is invalid — the gate is red before anything runs."""


def split_project(repo_path: str) -> tuple[str, str]:
    """`command-center/tests/x.py` -> (`command-center`, `tests/x.py`)."""
    project, _, rest = repo_path.partition("/")
    if project not in PROJECTS or not rest:
        raise GateError(f"path outside a known sub-project: {repo_path}")
    return project, rest


def _run(args: list[str], *, cwd: pathlib.Path, timeout: int,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(extra_env or {})}
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "tools"), env.get("PYTHONPATH", "")]))
    return subprocess.run(args, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, timeout=timeout, check=False)


def collect(repo_path: str) -> list[str]:
    """Repo-root nodeids of one listed file/dir, collected from its own project."""
    project, rel = split_project(repo_path)
    if not (ROOT / repo_path).exists():
        raise GateError(f"listed test path does not exist: {repo_path}")
    skips_file = ROOT / "windows-100-results" / f".skips-{abs(hash(repo_path))}.jsonl"
    skips_file.parent.mkdir(parents=True, exist_ok=True)
    skips_file.unlink(missing_ok=True)
    proc = _run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                 "-p", PLUGIN, "--rootdir", ".", rel], cwd=ROOT / project, timeout=300,
                extra_env={"BOSSMAN_W100_SKIPS": str(skips_file)})
    skipped: dict[str, str] = {}
    if skips_file.exists():
        for line in skips_file.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            skipped[f"{project}/{rec['nodeid']}"] = rec["reason"]
        skips_file.unlink()
    EXCLUDED.update(skipped)
    if proc.returncode != 0:
        raise GateError(f"collection of {repo_path} failed (exit {proc.returncode}):\n"
                        f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    out: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if "::" not in line or line.startswith(("=", "<")):
            continue
        nodeid = f"{project}/{line}"
        if nodeid in skipped:
            continue
        if not (ROOT / nodeid.split("::", 1)[0]).is_file():
            raise GateError(f"collected nodeid does not name a real file: {nodeid}")
        out.append(nodeid)
    if not out:
        raise GateError(f"listed test path has no tests: {repo_path}")
    return sorted(dict.fromkeys(out))


def select(per_path: "OrderedDict[str, list[str]]", quota: int) -> list[str]:
    """Deterministic round-robin over the listed files: every file contributes."""
    queues = [list(ids) for ids in per_path.values()]
    chosen: list[str] = []
    seen: set[str] = set()
    while len(chosen) < quota and any(queues):
        for q in queues:
            while q and q[0] in seen:
                q.pop(0)
            if q and len(chosen) < quota:
                nodeid = q.pop(0)
                chosen.append(nodeid)
                seen.add(nodeid)
    return chosen


def verify(selected: list[str], reports: list[dict]) -> dict:
    """Per-nodeid verdict from plugin records. PASS only if every selected test
    passed setup+call+teardown, nothing was skipped, missing or extra."""
    by_id: dict[str, dict[str, str]] = {}
    for rec in reports:
        by_id.setdefault(rec["nodeid"], {})[rec["when"]] = rec["outcome"]
    wanted = set(selected)
    missing = sorted(n for n in selected if n not in by_id)
    extra = sorted(n for n in by_id if n not in wanted)
    not_passed = {}
    for n in selected:
        phases = by_id.get(n)
        if not phases:
            continue
        bad = {w: o for w, o in phases.items() if o != "passed"}
        if bad or "call" not in phases:
            not_passed[n] = bad or {"call": "not_run"}
    passed = len(selected) - len(missing) - len(not_passed)
    ok = (len(selected) == 100 and len(wanted) == 100 and not missing and not extra
          and not not_passed and passed == 100)
    return {"verdict": "PASS" if ok else "FAIL", "selected": len(selected), "unique": len(wanted),
            "passed": passed, "missing": missing, "extra": extra, "not_passed": not_passed}


def build_selection() -> tuple[list[str], list[dict]]:
    selected: list[str] = []
    groups: list[dict] = []
    for name, quota, paths in GROUPS:
        per_path: "OrderedDict[str, list[str]]" = OrderedDict((p, collect(p)) for p in paths)
        chosen = select(per_path, quota)
        if len(chosen) != quota:
            raise GateError(f"{name}: selected {len(chosen)}, need {quota}")
        selected.extend(chosen)
        groups.append({"name": name, "quota": quota,
                       "collected": {p: len(ids) for p, ids in per_path.items()},
                       "selected": chosen})
    if len(selected) != 100 or len(set(selected)) != 100:
        raise GateError(f"selection invariant failed: total={len(selected)} unique={len(set(selected))}")
    return selected, groups


def execute(selected: list[str], out_dir: pathlib.Path) -> tuple[list[dict], dict]:
    """Run the nodeids per project from its own directory; return plugin records."""
    records: list[dict] = []
    runs: dict = {}
    for project in PROJECTS:
        ids = [n[len(project) + 1:] for n in selected if n.startswith(project + "/")]
        if not ids:
            continue
        log = out_dir / f"reports-{project}.jsonl"
        log.unlink(missing_ok=True)
        started = time.monotonic()
        proc = _run([sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
                     "-p", PLUGIN, "--rootdir", ".", *ids], cwd=ROOT / project, timeout=1800,
                    extra_env={"BOSSMAN_W100_REPORT": str(log)})
        (out_dir / f"pytest-{project}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (out_dir / f"pytest-{project}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        runs[project] = {"returncode": proc.returncode, "requested": len(ids),
                         "elapsed_seconds": round(time.monotonic() - started, 3)}
        sys.stdout.write(proc.stdout[-4000:])
        if log.exists():
            for line in log.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                rec["nodeid"] = f"{project}/{rec['nodeid']}"
                records.append(rec)
    return records, runs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--negative-control", action="store_true",
                    help="swap one nodeid for a nonexistent one; exit 0 only if the gate goes red")
    ap.add_argument("--out", default="windows-100-results")
    ns = ap.parse_args(argv)
    out_dir = ROOT / ns.out
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = os.environ.get("BOSSMAN_ACCEPTANCE_SHA") or os.environ.get("GITHUB_SHA") or ""
    evidence: dict = {"schema": 2, "sha": sha, "runner": sys.platform, "negative_control": ns.negative_control}
    try:
        selected, groups = build_selection()
    except GateError as exc:
        print(f"WINDOWS_100_REAL: FAIL — {exc}", file=sys.stderr)
        (out_dir / "summary.json").write_text(json.dumps({**evidence, "verdict": "FAIL", "error": str(exc)},
                                                         ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1 if not ns.negative_control else 2
    if ns.negative_control:
        planted = "command-center/tests/test_terminal_cli_unit.py::test_windows100_negative_control_does_not_exist"
        evidence["planted_bogus_nodeid"] = planted
        evidence["replaced_nodeid"] = selected[1]
        selected = [selected[0], planted, *selected[2:]]
    evidence.update(groups=groups, selected_total=len(selected), selected=selected,
                    excluded_skipped_by_marker_on_this_platform=EXCLUDED)
    (out_dir / "selected-tests.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
    started = time.monotonic()
    records, runs = execute(selected, out_dir)
    result = verify(selected, records)
    if any(r["returncode"] != 0 for r in runs.values()) and result["verdict"] == "PASS":
        result["verdict"] = "FAIL"          # pytest itself said no: never overrule it
    summary = {"schema": 2, "sha": sha, "negative_control": ns.negative_control,
               "elapsed_seconds": round(time.monotonic() - started, 3), "runs": runs, **result}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(f"WINDOWS_100_REAL: {result['verdict']} — passed {result['passed']}/100 real tests"
          f" (missing {len(result['missing'])}, not passed {len(result['not_passed'])},"
          f" extra {len(result['extra'])}) in {summary['elapsed_seconds']}s")
    if ns.negative_control:
        red = result["verdict"] == "FAIL"
        print(f"WINDOWS_100_NEGATIVE_CONTROL: {'PASS — bogus nodeid turned the gate red' if red else 'FAIL — gate stayed green'}")
        return 0 if red else 1
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
