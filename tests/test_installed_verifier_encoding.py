"""A Windows console must not reject a successful installed-product report."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex


def test_report_stdout_survives_cp1252_and_preserves_utf8_evidence(tmp_path):
    verifier = Path(__file__).resolve().parents[1] / "tools" / "verify_installed_product.py"
    report = tmp_path / "installed-acceptance.json"
    # Exercise the real main/report serialization in a fresh cp1252 process.
    # The narrow fixture replaces expensive product probing, not its evidence.
    code = """
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("installed_verifier", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.verify = lambda *args: {"status": "PASS", "health": {"detail": "тик 0 с назад"}}
sys.argv = [sys.argv[1], "--out", sys.argv[2]]
module.main()
"""
    env = dict(os.environ, PYTHONIOENCODING="cp1252:strict")
    result = subprocess.run([sys.executable, "-c", code, str(verifier), str(report)],
                            cwd=tmp_path, env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode("cp1252", "replace")
    assert json.loads(result.stdout.decode("cp1252")) == json.loads(report.read_text(encoding="utf-8"))
    assert "тик 0 с назад" in report.read_text(encoding="utf-8")


def test_manifest_ci_display_survives_cp1252(tmp_path):
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/local-bundle.yml"
    lines = workflow.read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if "name: Show what is being shipped" in line)
    command = shlex.split(lines[index + 1].strip().removeprefix("run: "))
    command[0] = sys.executable
    path = tmp_path / "dist/bossman-local/MANIFEST.json"
    path.parent.mkdir(parents=True)
    report = {"source_sha": "a" * 40, "health": {"detail": "тик 0 с назад"}}
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(command, cwd=tmp_path,
                            env=dict(os.environ, PYTHONIOENCODING="cp1252:strict"),
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode("cp1252", "replace")
    assert json.loads(result.stdout.decode("cp1252")) == report
