"""Per-user background supervisor. Fixed local config; never logs credentials."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "codex-telegram"
config = json.loads((root / "launcher.json").read_text(encoding="utf-8"))
command = [config["python"], str(root / "bridge.py"),
           "--companion-home", config["companion_home"], "--data-dir", str(root),
           "--cwd", config["cwd"], "--codex", config["codex"], "--model", config["model"]]

# A distinct supervisor lock prevents duplicate retry loops after repeated login/start.
import msvcrt
with (root / "supervisor.lock").open("a+b") as lock:
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(0)
    while not (root / "DISABLED").exists():
        with (root / "bridge.log").open("ab") as log:
            result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            break
        time.sleep(15)
