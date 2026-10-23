"""Event loop for `serve` that survives a failed accept (incident 23.10, owner PC).

On Windows the stdlib ProactorEventLoop treats ANY OSError from a pending AcceptEx (here `WinError 64`, the
Tailscale proxy resetting a connection while it was being accepted, under heavy disk load) as fatal for the
listener: `BaseProactorEventLoop._start_serving.loop` logs "Accept failed on a socket", closes the listening
socket and never re-arms accept. uvicorn's `Server.main_loop` keeps ticking, so the process stays alive while
the port is gone — exactly what the mobile pult did.

`ResilientProactorEventLoop` is the same accept loop, except that a failed accept on a still-open listener is
logged and accept is re-armed with a short backoff. Only after MAX_CONSECUTIVE_FAILURES in a row (the listener
itself is broken) does it fall back to the stdlib behaviour (close the socket), where `bossmobile.supervisor`'s
health probe takes over and restarts the server. Nothing else changes: same proactor, same transports, so
HTTP and websockets behave as before.

uvicorn gets this loop via `loop="bossmobile.serve_loop:new_event_loop"` (import string, uvicorn >= 0.36).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from asyncio import exceptions, trsock

log = logging.getLogger("bossmobile.serve_loop")

MAX_CONSECUTIVE_FAILURES = 20          # ~30 s of backed-off retries before giving the socket up
BACKOFF_FIRST_S = 0.05
BACKOFF_MAX_S = 2.0


def backoff_s(consecutive_failures: int) -> float:
    """Delay before re-arming accept after the n-th consecutive failure (0.05 s, 0.1, 0.2 ... capped at 2 s)."""
    return min(BACKOFF_FIRST_S * (2 ** max(consecutive_failures - 1, 0)), BACKOFF_MAX_S)


if sys.platform == "win32":

    class ResilientProactorEventLoop(asyncio.ProactorEventLoop):
        """ProactorEventLoop whose accept loop re-arms after a per-connection OSError instead of closing the listener."""

        def __init__(self, proactor=None):
            super().__init__(proactor)
            self.accept_failures = 0           # total failed accepts (diagnostics / tests)
            self.accept_recoveries = 0         # how many times accept was re-armed after a failure

        # Mirrors asyncio.proactor_events.BaseProactorEventLoop._start_serving (CPython 3.12/3.13); only the
        # `except OSError` branch differs.
        def _start_serving(self, protocol_factory, sock, sslcontext=None, server=None, backlog=100,
                           ssl_handshake_timeout=None, ssl_shutdown_timeout=None):
            consecutive = [0]

            def loop(f=None):
                try:
                    if f is not None:
                        conn, addr = f.result()
                        if self._debug:
                            log.debug("%r got a new connection from %r: %r", server, addr, conn)
                        protocol = protocol_factory()
                        if sslcontext is not None:
                            self._make_ssl_transport(conn, protocol, sslcontext, server_side=True, extra={"peername": addr},
                                                     server=server, ssl_handshake_timeout=ssl_handshake_timeout,
                                                     ssl_shutdown_timeout=ssl_shutdown_timeout)
                        else:
                            self._make_socket_transport(conn, protocol, extra={"peername": addr}, server=server)
                    if self.is_closed():
                        return
                    f = self._proactor.accept(sock)
                except OSError as exc:
                    if sock.fileno() == -1:
                        # the listener was closed on purpose (stop_serving) — nothing to recover
                        if self._debug:
                            log.debug("Accept failed on closed socket %r", sock, exc_info=True)
                        return
                    self.accept_failures += 1
                    consecutive[0] += 1
                    if consecutive[0] >= MAX_CONSECUTIVE_FAILURES:
                        # the listening socket itself is broken: stdlib behaviour, the supervisor restarts the server
                        self.call_exception_handler({"message": "Accept failed on a socket %d times in a row; closing it"
                                                                % consecutive[0],
                                                     "exception": exc, "socket": trsock.TransportSocket(sock)})
                        sock.close()
                        return
                    delay = backoff_s(consecutive[0])
                    log.warning("accept failed on %s (%s); re-arming accept in %.2fs (failure %d in a row)",
                                sock.getsockname() if sock.fileno() != -1 else "?", exc, delay, consecutive[0])
                    self.accept_recoveries += 1
                    self.call_later(delay, loop)
                except exceptions.CancelledError:
                    sock.close()
                else:
                    consecutive[0] = 0
                    self._accept_futures[sock.fileno()] = f
                    f.add_done_callback(loop)

            self.call_soon(loop)

    _LOOP_CLASS = ResilientProactorEventLoop
else:  # pragma: no cover - the selector loop has no such failure mode
    ResilientProactorEventLoop = None  # type: ignore[assignment,misc]
    _LOOP_CLASS = asyncio.SelectorEventLoop


def loop_factory(use_subprocess: bool = False):
    """Same contract as uvicorn.loops.asyncio.asyncio_loop_factory: returns the loop class to instantiate."""
    if sys.platform == "win32" and use_subprocess:
        return asyncio.SelectorEventLoop
    return _LOOP_CLASS


def new_event_loop() -> asyncio.AbstractEventLoop:
    """What uvicorn calls for a custom `loop="module:attr"` string: Config.get_loop_factory returns the imported
    attribute itself (no use_subprocess call) and asyncio.Runner calls it with no arguments."""
    return loop_factory()()


LOOP_IMPORT_STRING = "bossmobile.serve_loop:new_event_loop"
