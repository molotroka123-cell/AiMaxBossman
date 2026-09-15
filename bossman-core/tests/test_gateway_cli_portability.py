"""Exercise the real CLI with Windows-compatible redirected output encodings."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
def test_gateway_help_runs_from_foreign_cwd_with_redirected_output(tmp_path, encoding):
    env = dict(os.environ, PYTHONIOENCODING=f"{encoding}:strict")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-m", "bossman.gateway.main", "--help"],
                            cwd=tmp_path, env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(encoding, errors="replace")
    output = result.stdout.decode(encoding)
    assert "--host" in output and "bind address" in output and "--env" in output
    assert not result.stderr


def test_gateway_startup_handles_unrepresentable_path_without_printing_keys(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict", ANTHROPIC_API_KEY="do-not-print-fixture-key")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    # The actual CLI path runs; uvicorn's serving loop alone is substituted so
    # this is a portable startup-output contract, not a live server acceptance.
    program = (
        "import sys; from bossman.gateway import main as gateway; "
        "gateway.uvicorn.run=lambda *args, **kwargs: None; "
        "sys.argv=['bossman-gateway','--env','missing-\\u0411\\u043e\\u0441\\u0441.env']; "
        "gateway.main()"
    )
    result = subprocess.run([sys.executable, "-c", program], cwd=tmp_path, env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    output = result.stdout.decode("cp1252")
    assert "[Gateway] providers:" in output and "CONFIGURED anthropic" in output
    assert "do-not-print-fixture-key" not in output
    assert "do-not-print-fixture-key" not in result.stderr.decode("cp1252")
