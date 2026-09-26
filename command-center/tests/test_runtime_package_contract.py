"""The shipped runtime must expose the core to isolated child processes."""
from __future__ import annotations

from importlib import metadata
import os
import subprocess
import sys

from packaging.requirements import Requirement


def test_runtime_extra_declares_the_core_distribution():
    requirements = [Requirement(raw) for raw in metadata.requires("bossman-command-center") or []]
    core = [req for req in requirements if req.name == "bossman-core"
            and (req.marker is None or req.marker.evaluate({"extra": "runtime"}))]
    assert len(core) == 1
    assert core[0].marker is not None and not core[0].marker.evaluate({"extra": ""})
    assert "runtime" in core[0].extras
    assert metadata.version("bossman-core") in core[0].specifier


def test_isolated_child_imports_v15_and_the_coding_sidecar(tmp_path):
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "from bcc.features import v15_autonomy; "
         "from bossman.apprentice import local_sidecar; "
         "assert v15_autonomy.BossmanAutonomyKernel; "
         "assert local_sidecar.SCHEMA == 'bossman.openhands.v1'"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
