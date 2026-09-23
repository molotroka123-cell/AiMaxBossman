"""`bossman --help` must not crash on a cp1252 console (Windows local bundle)."""
import os
import subprocess
import sys


def test_help_survives_a_cp1252_console():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    res = subprocess.run([sys.executable, "-c", "import sys; sys.argv=['bossman','--help']; "
                          "from bossman.cli import main; main()"],
                         capture_output=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr.decode("utf-8", "replace")[-2000:]
    assert "Терминал".encode("utf-8") in res.stdout
