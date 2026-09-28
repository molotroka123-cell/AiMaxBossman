"""Jeff desktop window: the participant chat in its own browser profile.

Starts (or attaches to) the loopback Jeff web server (``bcc.pit.web``) from
THIS checkout and opens ``/jeff.html`` in a separate Chromium profile. It never
starts, attaches to or stops the Bossman Command Center backend, never reads
the owner access token and never writes ``desktop.lock``: the owner's Bossman
window and the Jeff window cannot tear each other down.

Build-bound: an already running Jeff server is reused only when it reports the
same app id and the same proven source SHA as this checkout; otherwise the
launcher refuses instead of opening a window on foreign code.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

DEFAULT_PORT = 8850


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bossman-jeff", description="Jeff — окно участника")
    p.add_argument("--data-dir", default=None, help="каталог данных Bossman (PIT внутри pit-v1.7)")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--browser", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("--window-size", default="1440,900")
    p.add_argument("--no-server", action="store_true",
                   help="открыть окно только если сервер Jeff этой сборки уже запущен")
    p.add_argument("--no-window", action="store_true",
                   help="только сервер Jeff (для проверки и автотестов)")
    return p


def _get_json(url: str, timeout: float = 2.0) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:  # noqa: S310 — loopback only
            data = json.loads(resp.read(64_000).decode("utf-8", "replace"))
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _port_answers(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1.5):  # noqa: S310
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def local_identity() -> dict:
    from .build_identity import source_identity
    from .pit.web import WEB_APP_ID
    return {"app": WEB_APP_ID, **source_identity(fresh=True)}


def same_build(local: dict, server: dict | None) -> bool:
    if not server or server.get("app") != local.get("app"):
        return False
    sha = local.get("build_sha")
    return (local.get("source_identity") == "PASS" and server.get("source_identity") == "PASS"
            and isinstance(sha, str) and len(sha) == 40 and server.get("build_sha") == sha)


class JeffServer:
    """uvicorn for bcc.pit.web in a thread; stops with the window it serves."""

    def __init__(self, settings, port: int):
        import uvicorn

        from .pit.web import create_app
        self.server = uvicorn.Server(uvicorn.Config(create_app(settings, port=port),
                                                    host="127.0.0.1", port=port, log_level="warning"))
        self.error: str | None = None
        self.thread = threading.Thread(target=self._serve, name="jeff-web-server", daemon=True)

    def _serve(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:  # noqa: BLE001 — SystemExit from uvicorn too
            self.error = f"{type(exc).__name__}: {exc}"

    def start(self, identity_url: str, timeout: float = 30.0) -> bool:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.server.started and _get_json(identity_url):
                return True
            if not self.thread.is_alive():
                return False
            time.sleep(0.2)
        self.error = self.error or f"сервер Jeff не ответил за {timeout:.0f} с"
        return False

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


def run(argv: Sequence[str] | None = None, *, launcher=None, out=sys.stdout) -> int:
    from .pit.config import config_path, default_data_dir, load

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    port = args.port or int(os.environ.get("BOSSMAN_JEFF_PORT", DEFAULT_PORT))
    base_url = f"http://127.0.0.1:{port}/"
    identity_url = base_url + "api/jeff/identity"
    jeff_url = base_url + "jeff.html"
    profile = Path(args.profile) if args.profile else data_dir / "jeff-desktop-profile"
    config = config_path(data_dir)
    if not config.is_file():
        print(f"[jeff] Jeff не настроен в {data_dir}. Один раз выполните:\n"
              f"  python -m bcc.pit.cli web-setup --data-dir \"{data_dir}\" --local-model <модель Ollama>",
              file=out, flush=True)
        return 3

    local = local_identity()
    running = _get_json(identity_url)
    started = None
    if running:
        if not same_build(local, running):
            label = lambda d: (d or {}).get("build_sha_short") or (d or {}).get("source_identity") or "?"
            print(f"[jeff] на порту {port} работает Jeff другой сборки: local={label(local)} "
                  f"server={label(running)}. Окно не открываю.", file=out, flush=True)
            return 7
    elif _port_answers(base_url):
        print(f"[jeff] порт {port} занят другим приложением", file=out, flush=True)
        return 4
    elif args.no_server:
        print("[jeff] сервер Jeff не запущен", file=out, flush=True)
        return 3
    else:
        started = JeffServer(load(config), port)
        if not started.start(identity_url):
            print(f"[jeff] сервер Jeff не поднялся: {started.error or 'неизвестная причина'}",
                  file=out, flush=True)
            started.stop()
            return 3
    print(f"[jeff] Jeff {local.get('build_sha_short') or local.get('source_identity')} · {jeff_url} · "
          "окно участника, без прав владельца", file=out, flush=True)
    try:
        if args.no_window:
            while started is not None and started.thread.is_alive():
                time.sleep(0.5)
            return 0
        from .desktop import find_browser, launch_window
        browser = args.browser or find_browser()
        if not browser:
            print("[jeff] Chromium/Chrome/Edge не найден", file=out, flush=True)
            return 2
        return (launcher or launch_window)(browser, jeff_url, profile, window_size=args.window_size)
    except KeyboardInterrupt:
        return 0
    finally:
        # Only the server THIS launcher started is stopped; an attached one and
        # the Bossman Command Center backend are never touched.
        if started is not None:
            started.stop()


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
