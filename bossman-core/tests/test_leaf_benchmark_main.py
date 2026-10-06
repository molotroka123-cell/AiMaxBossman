"""authored_by_lane (opsplug): python -m bossman.benchmark - the CLI entry shim.

Real subprocess of `python -m bossman.benchmark`; checks the entry point is wired to the real CLI and that
the honesty guard (a run bound to a different commit is refused) is reachable from it.
"""
import os
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]


def _run(*args, cwd=None, timeout=90):
    env = {**os.environ, "PYTHONPATH": str(CORE), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-m", "bossman.benchmark", *args], cwd=cwd or CORE, env=env,
                          capture_output=True, text=True, timeout=timeout)


def test_help_lists_every_subcommand_and_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    for sub in ("run", "run-isolated", "compare-isolated", "compare", "report"):
        assert sub in r.stdout, sub


def test_no_command_is_a_usage_error():
    r = _run()
    assert r.returncode == 2 and "required" in r.stderr.lower()


def test_unknown_tier_is_refused_by_argument_validation():
    r = _run("run", "--tier", "bogus")
    assert r.returncode == 2 and "invalid choice" in r.stderr


def test_a_run_pinned_to_another_commit_is_refused_not_relabelled(tmp_path):
    r = _run("run", "--tier", "smoke", "--sha", "0" * 40, "--output", str(tmp_path))
    assert r.returncode != 0
    assert "requested 000000000000" in (r.stderr + r.stdout) or "ShaMismatch" in (r.stderr + r.stdout)
    assert list(tmp_path.rglob("*.json")) == []                    # no result file was produced for the wrong commit
