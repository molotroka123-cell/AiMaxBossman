import subprocess
import sys
from pathlib import Path


def test_preparation_live_flag_fails_before_tests_or_report(monkeypatch, tmp_path):
    from solana_volume_suite.scripts.prepare_for_gemini import build_report
    import pytest
    monkeypatch.setenv("LIVE_EXECUTION_ENABLED", "true")
    output = tmp_path / "report.json"
    with pytest.raises(PermissionError):
        build_report(output, tmp_path / "results.csv")
    assert not output.exists()


def test_mainnet_launcher_rejected_without_network_or_setup():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "solana_volume_suite/start_prototype.py"), "--mainnet"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "SECURITY_VIOLATION" in result.stderr
    assert "VIRTUAL_ONLY" in result.stderr
