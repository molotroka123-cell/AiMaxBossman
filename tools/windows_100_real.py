#!/usr/bin/env python3
"""Run exactly 100 real, deterministic Bossman checks on Windows.

This replaces the historical fake workflow that printed "OK" 100 times.
The script asks pytest to collect real test nodeids from five product areas,
takes a fixed quota from each area, writes the exact nodeids to evidence JSON,
then executes those exact 100 tests. A missing quota is a hard failure.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]

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

TOTAL = sum(quota for _, quota, _ in GROUPS)
assert TOTAL == 100


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _collect(paths: list[str]) -> list[str]:
    proc = _run([sys.executable, "-m", "pytest", "--collect-only", "-q", *paths], timeout=300)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"pytest collection failed with exit {proc.returncode}")
    nodeids: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if "::" not in line:
            continue
        # pytest -q --collect-only emits one nodeid per line.
        if line.startswith(("=", "<")):
            continue
        nodeids.append(line)
    return sorted(dict.fromkeys(nodeids))


def main() -> int:
    selected: list[str] = []
    evidence: dict[str, object] = {
        "schema": 1,
        "sha": os.environ.get("BOSSMAN_ACCEPTANCE_SHA") or os.environ.get("GITHUB_SHA") or "",
        "runner": "windows",
        "groups": [],
    }

    for name, quota, paths in GROUPS:
        nodeids = _collect(paths)
        if len(nodeids) < quota:
            print(f"{name}: collected {len(nodeids)}, need {quota}", file=sys.stderr)
            return 2
        chosen = nodeids[:quota]
        selected.extend(chosen)
        evidence["groups"].append({
            "name": name,
            "quota": quota,
            "collected": len(nodeids),
            "selected": chosen,
        })

    if len(selected) != 100 or len(set(selected)) != 100:
        print(f"selection invariant failed: total={len(selected)} unique={len(set(selected))}", file=sys.stderr)
        return 2

    out_dir = ROOT / "windows-100-results"
    out_dir.mkdir(exist_ok=True)
    evidence["selected_total"] = 100
    (out_dir / "selected-tests.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    started = time.monotonic()
    proc = _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "--maxfail=1", *selected],
        timeout=1800,
    )
    elapsed = round(time.monotonic() - started, 3)
    (out_dir / "pytest.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out_dir / "pytest.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    summary = {
        "schema": 1,
        "sha": evidence["sha"],
        "selected_total": 100,
        "elapsed_seconds": elapsed,
        "pytest_returncode": proc.returncode,
        "verdict": "PASS" if proc.returncode == 0 else "FAIL",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    print(f"WINDOWS_100_REAL: {summary['verdict']} — 100 real tests in {elapsed}s")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
