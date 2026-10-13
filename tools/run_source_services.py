"""Run the Bossman backend or the pult (companion) from a SOURCE checkout of `main`, with the launcher's own
environment (data dir, secrets file of the companion). Used on 13.10.2026 when the installed archive 94e40cd1 did not
yet contain pult generation, the Qwen operator and the progress bar.

usage: python tools/run_source_services.py backend|pult <checkout root> [--python <python.exe>]

The installed backend must be stopped first and held down so the periodic ensure does not respawn it:
  python tools/owner_one_bossman.py stop --data-dir %LOCALAPPDATA%/Bossman/CommandCenter --kinds backend --hold --force
To go back to the installed build: python tools/owner_one_bossman.py launch ... (see that tool's --help).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import owner_one_bossman as ob  # noqa: E402

LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
DATA = LOCAL / "Bossman" / "CommandCenter"
CONFIG = LOCAL / "Bossman" / "telegram-companion" / "config.json"
DEFAULT_HOME = Path(r"C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-94e40cd17822")
DEFAULT_PY = r"C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=("backend", "pult"))
    ap.add_argument("root", type=Path)
    ap.add_argument("--python", default=DEFAULT_PY)
    ap.add_argument("--home", type=Path, default=DEFAULT_HOME)
    a = ap.parse_args(argv)
    root = a.root.resolve()
    env = ob.child_env(a.home, DATA)
    env["PYTHONPATH"] = os.pathsep.join([str(root / "command-center"), str(root / "bossman-core"), str(root)])
    if a.kind == "backend":
        cmd = [a.python, "-X", "utf8", "-m", "bcc", "--host", "127.0.0.1", "--port", "8801"]
    else:
        env.update(ob.companion_env(CONFIG))
        cmd = [a.python, "-X", "utf8", "-m", "bcc.telegram_companion", "--config", str(CONFIG)]
    log = LOCAL / "Bossman" / "one-bossman-logs" / f"{a.kind}-source.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        proc = subprocess.Popen(cmd, env=env, cwd=str(root), stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=0x08000000 | 0x00000200, close_fds=True)
    print(f"{a.kind}: pid {proc.pid}, code from {root}, log {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
