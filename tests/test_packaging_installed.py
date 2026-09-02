"""Audit P0: the shared contracts ship in the wheels. Builds bossman-shared and
bossman-core wheels, installs them into a fresh venv (no deps) and imports them
from a directory that is NOT the repository, so no checkout path can leak in."""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.timeout(600)


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=str(cwd or ROOT), env=env, capture_output=True, text=True, timeout=540)


def test_installed_wheels_resolve_shared_contracts_without_the_checkout(tmp_path):
    wheels = tmp_path / "wheels"
    for src in (ROOT, ROOT / "bossman-core"):
        r = _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(src)])
        assert r.returncode == 0, r.stderr[-2000:]
    built = sorted(p.name for p in wheels.glob("*.whl"))
    assert any(n.startswith("bossman_shared-") for n in built) and any(n.startswith("bossman_core-") for n in built), built
    venv.EnvBuilder(with_pip=False, clear=True).create(tmp_path / "venv")
    py = tmp_path / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = _run([sys.executable, "-m", "pip", "--python", str(py), "install", "-q", "--no-deps", *map(str, wheels.glob("*.whl"))])
    assert r.returncode == 0, r.stderr[-2000:]
    probe = ("import bossman_shared.cache_observation as co, learning.trace as lt, bossman_schemas, json, pathlib;"
             "import bossman._shared as s; assert s.AVAILABLE, 'bossman._shared degraded';"
             "assert lt.SCHEMA_PATH.exists(), lt.SCHEMA_PATH;"
             "print(lt.SCHEMA_PATH, pathlib.Path(bossman_schemas.__path__[0]).exists())")
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    r = _run([str(py), "-c", probe], cwd=tmp_path, env=env)
    assert r.returncode == 0, (r.stdout[-1000:], r.stderr[-2000:])
    assert "site-packages" in r.stdout and str(ROOT) not in r.stdout
