"""Isolated Jeff desktop window.

Reuses the exact Command Center backend and opens only /jeff.html in a separate
Chromium profile. It does not add a backend, task engine, memory store, or authority.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .desktop import (
    _BackgroundServer,
    _identity_label,
    _local_identity,
    _same_build,
    access_banner,
    find_browser,
    identify_server,
    launch_window,
    port_busy,
    read_access_token,
    TOKEN_FILE_NAME,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bossman-jeff", description="Jeff — isolated participant UX")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--browser", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("--window-size", default="1440,900")
    p.add_argument("--no-server", action="store_true",
                   help="open Jeff only if the exact-build Bossman backend is already running")
    return p


def run(argv: Sequence[str] | None = None, *, launcher=launch_window, out=sys.stdout) -> int:
    from .config import settings

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    host = args.host or settings.host
    port = args.port or settings.port
    base_url = f"http://{host}:{port}/"
    jeff_url = base_url + "jeff.html"
    data_dir = Path(settings.data_dir)
    profile = Path(args.profile) if args.profile else data_dir / "jeff-desktop-profile"

    browser = args.browser or find_browser()
    if not browser:
        print("[jeff] Chromium/Chrome/Edge not found", file=out, flush=True)
        return 2

    local = _local_identity()
    running = identify_server(base_url)
    started = None

    if running:
        if not _same_build(local, running):
            print(
                f"[jeff] backend build mismatch: local={_identity_label(local)} "
                f"server={_identity_label(running)}. Refusing to attach.",
                file=out, flush=True,
            )
            return 7
    elif port_busy(base_url):
        print(f"[jeff] port {port} is occupied by a foreign service", file=out, flush=True)
        return 4
    elif args.no_server:
        print("[jeff] Bossman backend is not running", file=out, flush=True)
        return 3
    else:
        started = _BackgroundServer(host, port)
        if not started.start(base_url):
            reason = started.error or "unknown startup error"
            print(f"[jeff] backend failed: {reason}", file=out, flush=True)
            started.stop()
            return 3
        print(
            access_banner(base_url, read_access_token(data_dir), data_dir / TOKEN_FILE_NAME,
                          console_owns_app=True),
            file=out, flush=True,
        )

    print("[jeff] participant UX only; owner authority remains in Command Center",
          file=out, flush=True)
    try:
        return launcher(browser, jeff_url, profile, window_size=args.window_size)
    finally:
        if started is not None:
            started.stop()


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
