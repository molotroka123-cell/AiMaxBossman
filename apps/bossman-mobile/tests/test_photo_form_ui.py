"""Runs the jsdom UI tests of the photo tab (tests/ui/*.test.mjs) with node. Skipped without node or jsdom."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

UI_TESTS = Path(__file__).resolve().parent / "ui"
CANDIDATES = [os.environ.get("BM_JSDOM_DIR") or "", str(Path(__file__).resolve().parents[1] / "node_modules"),
              "C:/Users/asd/Bossman/_tools/jsdom/node_modules"]


def _jsdom_available() -> bool:
    return any(p and (Path(p) / "jsdom").is_dir() for p in CANDIDATES)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.skipif(not _jsdom_available(), reason="jsdom is not installed (npm i jsdom; BM_JSDOM_DIR=<node_modules>)")
def test_photo_form_node_suite():
    run = subprocess.run(["node", "--test", str(UI_TESTS)], capture_output=True, text=True, timeout=180, check=False)
    assert run.returncode == 0, (run.stdout + run.stderr)[-3000:]
    assert "# fail 0" in run.stdout and "# pass 13" in run.stdout, run.stdout[-1500:]
