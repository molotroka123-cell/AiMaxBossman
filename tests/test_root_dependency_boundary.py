"""root-ci installs no Core/Command Center dependencies. Keep it that way.

`.github/workflows/root-ci.yml` installs exactly `-e .` plus pytest,
pytest-timeout, psutil and httpx. That is deliberate: the root suite covers the
shared contracts, the learning layer and the tools, and it must stay runnable
without FastAPI, SQLAlchemy, a database driver or a browser.

It is very easy to break by accident. At 22e2ea30 root-ci failed at collection
with `ModuleNotFoundError: No module named 'fastapi'` because
`bossman.gateway.__init__` imported the Gateway app and
`bossman.computer_operator.__init__` imported its FastAPI routes, so reading one
pure model or setting dragged the whole web stack in. Those two packages now
export the heavy names lazily (PEP 562, f97b8c1), but nothing stops the next
root test from importing a genuinely heavy module. This test reproduces the
workflow's dependency set and fails here instead of in CI.
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
    "fastapi", "starlette", "pydantic", "pydantic_core", "sqlalchemy",
    "aiosqlite", "asyncpg", "redis", "playwright", "greenlet", "cryptography",
    "jsonschema", "uvicorn", "mcp",
)   # pyyaml IS installed by root-ci (f97b8c1), so `yaml` is not blocked here.

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
                       "python -m pip install --quiet pytest pytest-timeout psutil httpx pyyaml"], install
    # Anything named here must also be absent from ABSENT_IN_ROOT_CI, or the
    # sibling test would block a package the runner really has.
    installed = install[-1].split("--quiet", 1)[1].split()
    assert not ({"yaml" if n == "pyyaml" else n for n in installed} & set(ABSENT_IN_ROOT_CI))
