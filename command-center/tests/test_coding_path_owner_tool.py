"""tools/coding_path_owner.py — the shipped check the Windows installed-product
job runs from app-support — must itself pass on a working product, including
its negative control. Run exactly as the job does: a separate `python -I`."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("bossman.apprentice.local_sidecar", reason="bossman-core runtime not installed")
TOOL = Path(__file__).resolve().parents[2] / "tools" / "coding_path_owner.py"


def test_the_installed_coding_path_check_passes_with_its_negative_control(tmp_path):
    out = tmp_path / "coding-path.json"
    proc = subprocess.run([sys.executable, "-I", str(TOOL), "--output", str(out)],
                          capture_output=True, text=True, encoding="utf-8", timeout=900)
    report = json.loads(out.read_text(encoding="utf-8"))
    assert proc.returncode == 0 and report["verdict"] == "PASS", (report, proc.stderr[-2000:])
    assert report["checks"]["negative_control_liar_failed"] is True
    assert report["fix_record"]["sidecar"]["deterministic_test_model"] is True
    assert report["fix_record"]["sidecar"]["model_kind"] == "MOCK_MODEL"
    assert report["leftover_model_processes"] == []
    assert "BOSSMAN_CODING_PATH=PASS (MOCK_MODEL" in proc.stdout
