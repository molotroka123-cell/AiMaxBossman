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

from .api_client import (BossmanError, _holder, _holder_url, backend_mismatch, candidate_urls,
                         default_data_dir, default_port, identify)

import httpx


def _port_busy(url: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=1.5) as c:
            c.get(url)
        return True
    except httpx.HTTPError:
        return False


def _running(url: str | None, base: Path) -> str | None:
    """URL of THIS data root's Command Center if it answers now. A Command
    Center of another data root or a proven other build is an error, never
    «уже работает»."""
    holder = _holder(base)
    for candidate in candidate_urls(url, base):
        ident = identify(candidate)
        if ident is None:
            continue
        problem = backend_mismatch(ident, holder, base, candidate)
        if problem is not None:
            raise problem
        return candidate
    return None


def _await_holder(base: Path, wait_seconds: float) -> str | None:
    """A holder of this data root that is still starting is awaited, not raced."""
    deadline = time.monotonic() + wait_seconds
    while True:
        holder = _holder(base)
        if not holder:
            return None
        own = _holder_url(holder)
        if own and identify(own) is not None:
            return own
        if time.monotonic() >= deadline:
            raise BossmanError(
                f"Bossman для этих данных уже запущен (порт {holder.get('port') or '?'}, "
                f"pid {holder.get('pid') or '?'}), но не отвечает", kind="disconnected",
                hint="подождите запуска или остановите его и повторите")
        time.sleep(0.3)


def start_backend(*, url: str | None = None, data_dir: str | None = None, port: int | None = None,
                  wait_seconds: float = 60.0) -> dict:
    base = Path(data_dir).expanduser() if data_dir else default_data_dir()
    found = _running(url, base)
    if found:
        return {"url": found, "already_running": True, "data_dir": str(base)}
    if _holder(base):
        # Another process already serves this data root (maybe still starting):
        # a second server on the same data is never started — it is awaited.
        _await_holder(base, wait_seconds)
        found = _running(url, base)
        if found:
            return {"url": found, "already_running": True, "data_dir": str(base)}
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
            # Код 5: данные в ту же секунду занял другой запуск (окно, второй
            # терминал). Это не сбой — Bossman этих данных есть, подключаемся.
            won = _await_holder(base, max(0.0, deadline - time.monotonic())) if _holder(base) else None
            if won:
                return {"url": won, "already_running": True, "data_dir": str(base)}
            raise BossmanError(f"Bossman завершился при старте (код {proc.returncode})", kind="disconnected",
                               hint=f"журнал: {log_path}")
        ident = identify(target)
        if ident and backend_mismatch(ident, _holder(base), base, target) is None:
            return {"url": target, "already_running": False, "pid": proc.pid, "data_dir": str(base),
                    "log": str(log_path)}
        time.sleep(0.3)
    raise BossmanError(f"Bossman не ответил за {int(wait_seconds)} с", kind="disconnected",
                       hint=f"журнал: {log_path}")
