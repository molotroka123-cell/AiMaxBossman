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


@pytest.mark.parametrize("crashes,expected_status,expected_calls", [
    (1, "PASS", 2),          # one network crash, then a real report: the scan counts
    (3, "TOOL_ERROR", 3),    # never a report: bounded retries, still fails closed
])
def test_pip_audit_crash_without_report_is_retried_but_never_passes_silently(
        tmp_path, monkeypatch, crashes, expected_status, expected_calls):
    monkeypatch.setattr(gate.sys, "argv", ["gate", "--component", "bossman-core", "--output", str(tmp_path)])
    calls = {"pip-audit": 0}
    def run(cmd, **kwargs):
        if "bandit" in cmd:
            (tmp_path / "bandit.json").write_text(json.dumps({"errors": [], "results": []}))
            return SimpleNamespace(returncode=0)
        calls["pip-audit"] += 1
        if calls["pip-audit"] <= crashes:
            return SimpleNamespace(returncode=1)          # died before writing the report
        (tmp_path / "pip-audit.json").write_text(json.dumps({"dependencies": [{"name": "httpx", "vulns": []}]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gate.subprocess, "run", run)
    rc = gate.main()
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["pip-audit"]["status"] == expected_status
    assert calls["pip-audit"] == expected_calls
    assert rc == (0 if expected_status == "PASS" else 1)


def test_windows_bundle_audits_the_shipped_lock_not_this_runner(tmp_path, monkeypatch):
    # The CI gate audited the Linux runner's environment; the owner's archive installs from the
    # hash-pinned lock. 2026-10-05: the lock carried pyjwt 2.14.0 (PYSEC-2026-4141) the gate never saw.
    monkeypatch.setattr(gate.sys, "argv", ["gate", "--component", "windows-bundle", "--output", str(tmp_path)])
    seen = []
    def run(cmd, **kwargs):
        seen.append(cmd)
        (tmp_path / "pip-audit.json").write_text(json.dumps({"dependencies": [
            {"name": "pyjwt", "version": "2.14.0", "vulns": [{"id": "PYSEC-2026-4141"}]}]}))
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(gate.subprocess, "run", run)
    assert gate.main() == 1                                   # a finding in the shipped lock blocks
    assert len(seen) == 1 and "bandit" not in seen[0]         # no SAST of source for a lock audit
    cmd = seen[0]
    assert cmd[cmd.index("-r") + 1].endswith("tools/windows_bundle_lock.txt")
    assert "--require-hashes" in cmd and "--disable-pip" in cmd
    report = json.loads((tmp_path / "summary.json").read_text())
    assert report["pip-audit"]["status"] == "FINDINGS" and report["pip-audit"]["findings"] == 1
