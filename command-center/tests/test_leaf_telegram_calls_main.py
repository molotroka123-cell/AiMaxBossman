"""authored_by_lane jeffb: ``python -I -m bcc.telegram_calls`` entry (bcc.telegram_calls.__main__)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CC = Path(__file__).resolve().parents[1]


def test_worker_process_starts_and_exits_clean_on_closed_stdin(tmp_path):
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))}
    env.update(LOCALAPPDATA=str(tmp_path), APPDATA=str(tmp_path), BCC_DATA_DIR=str(tmp_path),
               PYTHONPATH=str(CC), PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-I", "-m", "bcc.telegram_calls"], input=b"", env=env,
                       cwd=str(tmp_path), capture_output=True, timeout=60)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-500:]
    first = json.loads(p.stdout.decode("utf-8").splitlines()[0])
    assert first["event"] == "state" and first["state"] == "worker_ready"


def test_importing_the_module_does_not_start_the_worker():
    import importlib
    m = importlib.import_module("bcc.telegram_calls.__main__")
    assert m.__name__ == "bcc.telegram_calls.__main__"
    assert callable(m.main)
