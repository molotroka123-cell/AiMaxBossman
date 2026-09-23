"""The shipped owner CLI must reach the existing loop in isolated Python."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_owner_cli_uses_same_loop_and_controls_under_isolated_python(tmp_path):
    script = ROOT / "tools" / "bossman_evolve.py"
    source = '\n'.join((
        'import runpy, sys',
        f'sys.path[:0] = {[str(ROOT), str(ROOT / "bossman-core")]!r}',
        f'sys.argv = {[str(script)]!r} + sys.argv[1:]',
        'runpy.run_path(sys.argv[0], run_name="__main__")',
    ))

    def invoke(*args):
        return subprocess.run([sys.executable, "-I", "-c", source, *args, "--work", str(tmp_path / "campaign")],
                              capture_output=True, text=True, timeout=30)

    initial = invoke("status")
    assert initial.returncode == 0, initial.stderr
    assert json.loads(initial.stdout)["status"] == "NO_CAMPAIGN"
    stopped = invoke("stop")
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(invoke("status").stdout)["stop_requested"]["by"] == "owner-cli"
    resumed = invoke("resume", "--no-start")
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(invoke("status").stdout)["stop_requested"] is None


def test_bundle_installs_evolution_cli_and_suite(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    from build_windows_bundle import install_support

    support = tmp_path / "BOSSMAN-Windows-x64" / "app-support"
    install_support(support)
    assert (support / "bossman_evolve.py").is_file()
    suite = json.loads((support / "config/evolution/owner-v1.1.json").read_text(encoding="utf-8"))
    assert suite["cases"]
