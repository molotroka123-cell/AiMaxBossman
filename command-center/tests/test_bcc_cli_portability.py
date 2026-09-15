"""Run the installed entrypoint functions through real Windows-codepage pipes."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("encoding", ["cp1252", "ascii", "utf-8"])
@pytest.mark.parametrize("entry", ["bcc", "bcc-desktop", "bcc-open"])
def test_help_is_portable_and_never_starts_the_server(tmp_path, encoding, entry):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONIOENCODING=f"{encoding}:strict",
               BCC_DATA_DIR=str(tmp_path / "data"),
               PYTHONPATH=os.pathsep.join(map(str, [root, root / "bossman-core", root / "command-center"])))
    module = "bcc.app" if entry == "bcc" else "bcc.desktop"
    program = f"import sys; from {module} import main; sys.argv=[{entry!r},'--help']; main()"
    result = subprocess.run([sys.executable, "-c", program], cwd=tmp_path,
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode(encoding, "replace")
    # Desktop deliberately normalizes its output stream to UTF-8. This test
    # requires usable CLI output, not preservation of a parent pipe's codec.
    assert b"--help" in result.stdout and b"--port" in result.stdout
    assert not (tmp_path / "data" / "bcc.db").exists()
    assert not (tmp_path / "data" / "token").exists()
