"""CI must distinguish clean dependency scans from incomplete ones."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("astra_security_gate", Path(__file__).resolve().parents[1] / "tools/astra_security_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize("dependencies,expected", [
    ([{"name": "httpx", "vulns": []}], 0),
    ([{"name": "httpx", "vulns": []}, {"name": "bossman-core", "skip_reason": "editable"}], 0),
    ([{"name": "httpx", "vulns": []}, {"name": "unavailable-third-party", "skip_reason": "not found"}], 1),
    ([], 1),
    ([{"name": "bossman-core", "skip_reason": "editable"}], 1),
    ([{"name": "httpx", "vulns": [{"id": "synthetic-advisory"}]}], 1),
])
def test_incomplete_sca_cannot_pass(tmp_path, monkeypatch, dependencies, expected):
    monkeypatch.setattr(gate.sys, "argv", ["gate", "--component", "bossman-core", "--output", str(tmp_path)])
    def run(cmd, **kwargs):
        if "bandit" in cmd:
            (tmp_path / "bandit.json").write_text(json.dumps({"errors": [], "results": []}))
        else:
            (tmp_path / "pip-audit.json").write_text(json.dumps({"dependencies": dependencies}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == expected
    report = json.loads((tmp_path / "summary.json").read_text())
    assert (report["pip-audit"]["status"] == "PASS") == (expected == 0)
