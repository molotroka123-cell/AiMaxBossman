"""Generic metric probes that measure the code IN a checkout (a repository or a candidate worktree).

Only metrics that can be measured honestly are provided. A goal whose target metric has no probe is not started
(the metrics probe returns None: "ambiguous state"), never scored with an invented number.

* ``tests.failed`` / ``tests.passed``: pytest on the goal's ``pytest:`` acceptance ids, run inside the checkout with its
  own code first on the path (the candidate's tests, not the installed ones).
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

_SUMMARY = re.compile(r"(\d+)\s+(failed|passed|error|errors)", re.I)


def pytest_counts(output: str) -> dict[str, int]:
    """{'failed': n, 'passed': m, 'error': k} from pytest's last summary line."""
    lines = [ln for ln in (output or "").strip().splitlines() if ln.strip()]
    counts = {"failed": 0, "passed": 0, "error": 0}
    for ln in reversed(lines):
        found = _SUMMARY.findall(ln)
        if found:
            for n, kind in found:
                key = "error" if kind.lower().startswith("error") else kind.lower()
                counts[key] += int(n)
            return counts
    return counts


async def probe_tests_failed(checkout: Path, acceptance_tests: Iterable[Any], *, python: str | None = None,
                             timeout_s: float = 900.0) -> dict | None:
    from .hands import _pytest_ids
    ids = _pytest_ids(list(acceptance_tests))
    checkout = Path(checkout)
    cc = checkout / "command-center"
    base = cc if cc.is_dir() else checkout
    if not ids or any(not (base / i.split("::")[0]).exists() for i in ids):
        return None                  # a test that does not exist in this checkout cannot be measured here
    paths = [str(base)] + ([str(checkout / "bossman-core")] if (checkout / "bossman-core").is_dir() else [])
    from ..rave.connectors import child_env
    env = {**child_env(), "PYTHONPATH": os.pathsep.join(paths), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    argv = [python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=no", *ids]

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(argv, cwd=str(base), env=env, capture_output=True, timeout=timeout_s, check=False)

    try:
        proc = await asyncio.to_thread(run)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode not in (0, 1):            # 2+: interrupted / usage error / no tests: not a measurement
        return None
    counts = pytest_counts((proc.stdout or b"").decode("utf-8", "replace"))
    return {"tests.failed": float(counts["failed"] + counts["error"]), "tests.passed": float(counts["passed"])}


__all__ = ["probe_tests_failed", "pytest_counts"]
