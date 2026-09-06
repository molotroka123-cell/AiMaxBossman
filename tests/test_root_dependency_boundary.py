"""root-ci installs no Core/Command Center dependencies. Keep it that way.

`.github/workflows/root-ci.yml` installs exactly `-e .` plus pytest,
pytest-timeout, psutil and httpx. That is deliberate: the root suite covers the
shared contracts, the learning layer and the tools, and it must stay runnable
without FastAPI, SQLAlchemy, a database driver or a browser.

It is very easy to break by accident — `bossman.gateway.__init__` imports the
Gateway app and `bossman.computer_operator.__init__` imports its FastAPI
routes, so a root test that reaches for one symbol inside either package drags
the whole web stack in and root-ci fails at collection with
`ModuleNotFoundError: No module named 'fastapi'`. That is exactly how it broke
at 22e2ea30. This test reproduces the workflow's dependency set and fails here
instead of in CI.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD_ENV = "BOSSMAN_ROOT_DEPENDENCY_BOUNDARY_CHILD"

# The packages root-ci does NOT install. Blocking them reproduces the runner.
ABSENT_IN_ROOT_CI = (
    "fastapi", "starlette", "pydantic", "pydantic_core", "yaml", "sqlalchemy",
    "aiosqlite", "asyncpg", "redis", "playwright", "greenlet", "cryptography",
    "jsonschema", "uvicorn", "mcp",
)

CHILD = textwrap.dedent(
    """
    import builtins, sys
    blocked = set(%(blocked)r)
    real = builtins.__import__

    def guard(name, *args, **kwargs):
        if name.partition(".")[0] in blocked:
            raise ModuleNotFoundError("No module named %%r" %% name)
        return real(name, *args, **kwargs)

    builtins.__import__ = guard
    import pytest
    sys.exit(pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider", "tests"]))
    """
)


@pytest.mark.timeout(300)
def test_the_root_suite_collects_without_core_or_command_center_dependencies():
    if os.environ.get(GUARD_ENV):
        pytest.skip("child process: it is the one doing the collecting")
    env = dict(os.environ, **{GUARD_ENV: "1", "PYTHONDONTWRITEBYTECODE": "1"})
    proc = subprocess.run([sys.executable, "-c", CHILD % {"blocked": ABSENT_IN_ROOT_CI}],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=280)
    combined = proc.stdout + proc.stderr
    assert "ModuleNotFoundError" not in combined, (
        "a root test reaches a dependency root-ci does not install:\n"
        + combined[-4000:])
    assert proc.returncode == 0, combined[-4000:]


def test_the_workflow_still_declares_the_narrow_dependency_set():
    """If someone widens root-ci's install line, this test is the place to
    decide that deliberately rather than discovering it as drift."""
    workflow = (ROOT / ".github" / "workflows" / "root-ci.yml").read_text(encoding="utf-8")
    install = [line.strip() for line in workflow.splitlines() if "pip install" in line]
    assert install == ["python -m pip install --quiet -e .",
                       "python -m pip install --quiet pytest pytest-timeout psutil httpx"], install
