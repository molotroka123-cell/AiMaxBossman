"""Audit P0: the shared contracts ship in the wheels. Builds bossman-shared and
bossman-core wheels, installs them into a fresh venv (no deps) and imports them
from a directory that is NOT the repository, so no checkout path can leak in."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.timeout(600)


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, cwd=str(cwd or ROOT), env=env, capture_output=True, text=True, timeout=540)


def test_installed_wheels_resolve_shared_contracts_without_the_checkout(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    wheels = tmp_path / "wheels"
    for src in (ROOT, ROOT / "bossman-core"):
        r = _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels), str(src)])
        assert r.returncode == 0, r.stderr[-2000:]
    built = sorted(p.name for p in wheels.glob("*.whl"))
    assert any(n.startswith("bossman_shared-") for n in built) and any(n.startswith("bossman_core-") for n in built), built
    venv.EnvBuilder(with_pip=False, clear=True).create(tmp_path / "venv")
    py = tmp_path / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # An inherited source PYTHONPATH makes pip see checkout egg-info and report
    # "already installed" while this target venv is empty. Isolate installation
    # as well as the final import probe; keep every independent import assertion.
    r = _run([sys.executable, "-m", "pip", "--python", str(py), "install", "-q", "--no-deps",
              *map(str, wheels.glob("*.whl"))], cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    probe = ("import bossman_shared.cache_observation as co, learning.trace as lt, bossman_schemas, json, pathlib;"
             "import bossman._shared as s; assert s.AVAILABLE, 'bossman._shared degraded';"
             "assert lt.SCHEMA_PATH.exists(), lt.SCHEMA_PATH;"
             "print(lt.SCHEMA_PATH, pathlib.Path(bossman_schemas.__path__[0]).exists())")
    r = _run([str(py), "-c", probe], cwd=tmp_path, env=env)
    assert r.returncode == 0, (r.stdout[-1000:], r.stderr[-2000:])
    assert "site-packages" in r.stdout and str(ROOT) not in r.stdout


def test_command_center_wheel_carries_the_file_intelligence_pin(tmp_path):
    """The installed product must name the same pinned integration as the checkout.

    File Intelligence reads the pinned upstream identity of the external AI File
    Sorter sidecar from integration.json. The checkout resolved it relative to the
    repository; a wheel has no repository, the read failed softly to {}, and the
    installed Bossman answered /file-intelligence/status with an EMPTY
    pinned_upstream_sha. Losing a declared identity only in the build the owner
    actually runs is the failure this test exists for.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    wheels = tmp_path / "wheels"
    r = _run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(wheels),
              str(ROOT / "command-center")], env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    built = [p for p in wheels.glob("bossman_command_center-*.whl")]
    assert built, sorted(p.name for p in wheels.glob("*.whl"))
    with zipfile.ZipFile(built[0]) as wheel:
        names = set(wheel.namelist())
        packaged = "bcc/_integrations/ai-file-sorter/integration.json"
        assert packaged in names, sorted(n for n in names if "_integrations" in n)
        assert "bcc/_integrations/ai-file-sorter/NOTICE.md" in names, sorted(names)
        shipped = json.loads(wheel.read(packaged))
    checkout = json.loads(
        (ROOT / "integrations" / "ai-file-sorter" / "integration.json").read_text(encoding="utf-8"))
    assert shipped == checkout, "the wheel ships a different manifest than the checkout"
    # The pin is what the packaging exists to carry: an empty one is the defect.
    assert shipped["pinned_sha"], shipped
