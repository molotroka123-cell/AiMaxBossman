"""Start the backend with the product's own mechanism (`python -m bcc.app`),
only when no Command Center answers for this data root and the port is free.

It runs detached (it is backend-owned work, not the terminal's): closing the
terminal does not stop Bossman, and the desktop window later attaches to the
same server (bcc.desktop reuses an identified Command Center)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .api_client import BossmanError, candidate_urls, default_data_dir, default_port, identify

import httpx


def _port_busy(url: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=1.5) as c:
            c.get(url)
        return True
    except httpx.HTTPError:
        return False


def start_backend(*, url: str | None = None, data_dir: str | None = None, port: int | None = None,
                  wait_seconds: float = 60.0) -> dict:
    base = Path(data_dir).expanduser() if data_dir else default_data_dir()
    for candidate in candidate_urls(url, base):
        if identify(candidate):
            return {"url": candidate, "already_running": True, "data_dir": str(base)}
    port = port or default_port()
    target = f"http://127.0.0.1:{port}"
    if _port_busy(target):
        raise BossmanError(f"порт {port} занят другим приложением (это не Bossman)", kind="usage",
                           hint="укажите другой --port")
    base.mkdir(parents=True, exist_ok=True)
    log_path = base / "terminal-backend.log"
    env = os.environ.copy()
    env["BCC_DATA_DIR"] = str(base)
    env["BCC_TOKEN_STDOUT"] = "0"          # журнал — файл; токен в него не пишется
    kwargs: dict = {"stdin": subprocess.DEVNULL, "cwd": str(base), "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                   | getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "bcc.app", "--host", "127.0.0.1",
                                 "--port", str(port)], stdout=log, stderr=log, **kwargs)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise BossmanError(f"Bossman завершился при старте (код {proc.returncode})", kind="disconnected",
                               hint=f"журнал: {log_path}")
        if identify(target):
            return {"url": target, "already_running": False, "pid": proc.pid, "data_dir": str(base),
                    "log": str(log_path)}
        time.sleep(0.3)
    raise BossmanError(f"Bossman не ответил за {int(wait_seconds)} с", kind="disconnected",
                       hint=f"журнал: {log_path}")
