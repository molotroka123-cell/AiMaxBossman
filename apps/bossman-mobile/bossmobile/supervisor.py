"""In-process supervisor for `bossmobile serve` (incident 23.10: process alive, listener gone).

Runs uvicorn's Server in a loop. A watchdog thread GETs http://127.0.0.1:<port>/m/api/health every
`interval_s`; after `failures` misses in a row while the server believes it is up, it asks the server to exit
and the loop starts a fresh one (new app from `app_factory`, because the app's lifespan shutdown closes its
httpx clients). Phone sessions live in <data_dir>/sessions.json, so a restart keeps the phone logged in.

A real exit (SIGINT/SIGTERM from the main thread, or `stop()`) ends the loop; only watchdog-requested exits
restart. If the fresh server cannot start (port taken), the supervisor retries a few times and then exits
non-zero so the launcher log shows it.
"""
from __future__ import annotations

import http.client
import sys
import threading
import time
from collections.abc import Callable

import uvicorn

from .serve_loop import LOOP_IMPORT_STRING

STARTUP_RETRIES = 5
STARTUP_RETRY_DELAY_S = 2.0
GRACEFUL_SHUTDOWN_S = 10.0     # a stuck SSE/websocket must not block the restart forever


def probe_health(host: str, port: int, path: str = "/m/api/health", timeout_s: float = 5.0) -> bool:
    """One HTTP GET to the local listener; True only on 200. Stdlib only (runs on the watchdog thread)."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout_s)
    try:
        conn.request("GET", path, headers={"Host": f"{host}:{port}", "User-Agent": "bossmobile-watchdog"})
        resp = conn.getresponse()
        resp.read()
        return resp.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _log(msg: str) -> None:
    print(f"[bossman-mobile] {msg}", file=sys.stderr, flush=True)


class Supervisor:
    def __init__(self, app_factory: Callable[[], object], host: str, port: int, *, interval_s: float = 15.0,
                 failures: int = 3, probe_timeout_s: float = 5.0, loop: str = LOOP_IMPORT_STRING, **uvicorn_kw):
        self.app_factory, self.host, self.port = app_factory, host, port
        self.interval_s, self.failures, self.probe_timeout_s = interval_s, max(1, failures), probe_timeout_s
        self.loop_import, self.uvicorn_kw = loop, uvicorn_kw
        self.server: uvicorn.Server | None = None
        self.restarts = 0
        self.consecutive_failures = 0
        self._restart_requested = False
        self._stopped = threading.Event()
        self._generation = 0

    # ------------------------------------------------------------------ control
    def stop(self) -> None:
        """Real exit: end the loop (used by tests; the owner's Ctrl+C/SIGTERM goes through uvicorn's handler)."""
        self._stopped.set()
        srv = self.server
        if srv is not None:
            srv.should_exit = True

    def request_restart(self, reason: str) -> None:
        srv = self.server
        if srv is None or self._stopped.is_set():
            return
        _log(f"watchdog: {reason} — restarting the listener on {self.host}:{self.port}")
        self._restart_requested = True
        srv.should_exit = True

    # ------------------------------------------------------------------ watchdog
    def _watch(self, server: uvicorn.Server, generation: int) -> None:
        while not self._stopped.is_set() and self.server is server and self._generation == generation:
            if self._stopped.wait(self.interval_s):
                return
            if not server.started or server.should_exit:
                continue                         # starting up or already going down: not a verdict
            if probe_health(self.host, self.port, timeout_s=self.probe_timeout_s):
                self.consecutive_failures = 0
                continue
            self.consecutive_failures += 1
            _log(f"watchdog: /m/api/health unreachable ({self.consecutive_failures}/{self.failures})")
            if self.consecutive_failures >= self.failures:
                self.consecutive_failures = 0
                self.request_restart(f"/m/api/health failed {self.failures} times in a row while the process is alive")
                return

    # ------------------------------------------------------------------ run
    def _make_server(self) -> uvicorn.Server:
        kw = {"log_level": "info", "proxy_headers": False, "ws": "websockets", **self.uvicorn_kw}
        config = uvicorn.Config(self.app_factory(), host=self.host, port=self.port, loop=self.loop_import,
                                timeout_graceful_shutdown=int(GRACEFUL_SHUTDOWN_S), **kw)
        return uvicorn.Server(config)

    def run(self) -> int:
        startup_failures = 0
        while not self._stopped.is_set():
            self._restart_requested = False
            self._generation += 1
            server = self.server = self._make_server()
            threading.Thread(target=self._watch, args=(server, self._generation), name="bossmobile-watchdog",
                             daemon=True).start()
            try:
                server.run()
            except SystemExit as exc:              # uvicorn: could not bind / lifespan failed
                startup_failures += 1
                if self._stopped.is_set():
                    return 0
                if startup_failures >= STARTUP_RETRIES:
                    _log(f"could not start the listener {startup_failures} times (last exit {exc.code}); giving up")
                    return 3
                _log(f"listener did not start (exit {exc.code}); retry {startup_failures}/{STARTUP_RETRIES} "
                     f"in {STARTUP_RETRY_DELAY_S:.0f}s")
                time.sleep(STARTUP_RETRY_DELAY_S)
                continue
            if server.started:
                startup_failures = 0
            if not self._restart_requested or self._stopped.is_set():
                return 0                           # signal / stop(): a real exit
            self.restarts += 1
            _log(f"listener restarted ({self.restarts} so far)")
        return 0
