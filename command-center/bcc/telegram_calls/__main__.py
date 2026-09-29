"""``python -I -m bcc.telegram_calls`` - the calls worker process (JSON lines on stdin/stdout).

stdout carries protocol lines ONLY: the real stdout is captured here and ``sys.stdout`` is pointed at
stderr, so a stray ``print`` in any library can neither corrupt the protocol nor leak anything into it.
The data dir comes from ``BCC_DATA_DIR`` (set by the CallsManager) or the Command Center default.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path


def _data_dir() -> Path:
    configured = os.environ.get("BCC_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    from ..config import settings
    return Path(settings.data_dir)


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    stdin = sys.stdin.buffer
    from .addons import activate
    activate(_data_dir())          # python -I ignores PYTHONPATH: make the add-on dir importable first
    from .call.worker import Worker

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    lines: asyncio.Queue[str] = asyncio.Queue()
    write_lock = threading.Lock()

    def reader() -> None:
        try:
            for raw in iter(stdin.readline, b""):
                loop.call_soon_threadsafe(lines.put_nowait, raw.decode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        loop.call_soon_threadsafe(lines.put_nowait, "")      # EOF -> self-stop

    def write(obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with write_lock:
            out.write(data)
            out.flush()

    threading.Thread(target=reader, name="calls-stdin", daemon=True).start()
    worker = Worker(_data_dir())
    try:
        loop.run_until_complete(worker.serve(lines.get, write))
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
