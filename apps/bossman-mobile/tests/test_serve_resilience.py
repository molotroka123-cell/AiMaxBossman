"""Incident 23.10 (owner PC): the mobile pult stopped accepting while the process lived; later the listener was gone.

Root cause: Windows ProactorEventLoop closes the listening socket after ONE failed accept (WinError 64 from the
Tailscale proxy resetting mid-accept) and never re-arms it; uvicorn keeps ticking. Two layers are tested here:
  * bossmobile.serve_loop.ResilientProactorEventLoop re-arms accept (the stock loop is the paired control);
  * bossmobile.supervisor restarts the listener when /m/api/health stops answering (black-box through cli.main).
No network beyond 127.0.0.1 on a free port; no real Bossman (health does not touch it).
"""
from __future__ import annotations

import asyncio
import gc
import http.client
import socket
import sys
import threading
import time

import pytest

from bossmobile import cli, serve_loop

WIN = sys.platform == "win32"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_health(port: int, timeout: float = 1.0) -> int | None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/m/api/health")
        r = conn.getresponse()
        r.read()
        return r.status
    except (OSError, http.client.HTTPException):
        return None
    finally:
        conn.close()


def wait_for(pred, timeout: float, step: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


# --------------------------------------------------------------------------- layer 2: the accept loop itself
def _run_accept_failure(loop_cls) -> dict:
    """Serve on a free port, make one good connection, then fail the pending accept exactly like the incident
    (the AcceptEx overlapped future ends with OSError 64) and try to connect again."""
    out: dict = {}

    async def echo(reader, writer):
        writer.close()

    async def main():
        loop = asyncio.get_running_loop()
        srv = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.close()
        await asyncio.sleep(0.05)
        fd = srv.sockets[0].fileno()
        pending = loop._accept_futures[fd]                    # the armed AcceptEx
        pending.set_exception(OSError(64, "Указанное сетевое имя более недоступно"))   # WinError 64
        await asyncio.sleep(0.3)                              # stdlib: closes the listener; ours: re-arms after backoff
        out["listener_open"] = srv.sockets[0].fileno() != -1 if srv.sockets else False
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2.0)
            w.close()
            out["second_connect"] = True
        except (OSError, asyncio.TimeoutError):
            out["second_connect"] = False
        out["recoveries"] = getattr(loop, "accept_recoveries", None)
        srv.close()
        await srv.wait_closed()

    asyncio.run(main(), loop_factory=loop_cls)
    return out


@pytest.mark.skipif(not WIN, reason="ProactorEventLoop accept failure mode is Windows-only")
def test_stock_proactor_drops_listener_after_one_failed_accept_control():
    """Paired control: proves the injection reproduces the incident on the stock loop (listener gone, connect refused)."""
    out = _run_accept_failure(asyncio.ProactorEventLoop)
    assert out["second_connect"] is False
    assert out["listener_open"] is False


@pytest.mark.skipif(not WIN, reason="ProactorEventLoop accept failure mode is Windows-only")
def test_resilient_proactor_survives_failed_accept():
    out = _run_accept_failure(serve_loop.ResilientProactorEventLoop)
    assert out["listener_open"] is True
    assert out["second_connect"] is True
    assert out["recoveries"] == 1


def test_backoff_schedule_is_bounded():
    assert serve_loop.backoff_s(1) == pytest.approx(0.05)
    assert serve_loop.backoff_s(2) == pytest.approx(0.1)
    assert serve_loop.backoff_s(100) == serve_loop.BACKOFF_MAX_S
    total = sum(serve_loop.backoff_s(n) for n in range(1, serve_loop.MAX_CONSECUTIVE_FAILURES))
    assert 10 < total < 60, "the loop gives the socket up within about half a minute, then the supervisor acts"


def test_loop_factory_contract():
    cls = serve_loop.loop_factory()
    if WIN:
        assert cls is serve_loop.ResilientProactorEventLoop
        assert issubclass(cls, asyncio.ProactorEventLoop)
        assert serve_loop.loop_factory(use_subprocess=True) is asyncio.SelectorEventLoop
    else:
        assert cls is asyncio.SelectorEventLoop


# --------------------------------------------------------------------------- layer 1: the supervisor, black-box via cli
def _find(cls):
    return [o for o in gc.get_objects() if isinstance(o, cls)]


def test_serve_restarts_listener_that_died_while_process_alive(tmp_path, monkeypatch):
    """Black-box: `python -m bossmobile serve --port P` keeps running after its listening socket is closed under it
    (what the proactor does after a failed accept). Old code (plain uvicorn.run) never answers again -> this test fails."""
    import uvicorn

    monkeypatch.setenv("BOSSMAN_MOBILE_DATA", str(tmp_path / "mobile"))
    monkeypatch.setenv("BOSSMAN_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "bcc"))
    monkeypatch.setenv("BOSSMAN_MOBILE_WATCHDOG_INTERVAL_S", "0.3")
    monkeypatch.setenv("BOSSMAN_MOBILE_WATCHDOG_FAILURES", "2")
    monkeypatch.setenv("BOSSMAN_MOBILE_WATCHDOG_TIMEOUT_S", "1")
    port = free_port()
    before = set(id(s) for s in _find(uvicorn.Server))
    t = threading.Thread(target=cli.main, args=(["serve", "--port", str(port)],), daemon=True, name="serve-under-test")
    t.start()
    try:
        assert wait_for(lambda: http_health(port) == 200, 20), "server never came up"
        srv = next(s for s in _find(uvicorn.Server) if id(s) not in before and s.started)
        listeners = list(srv.servers)
        assert listeners
        loop = listeners[0]._loop
        # the incident: the listening socket is closed while uvicorn's main loop keeps ticking
        loop.call_soon_threadsafe(lambda: [s.close() for s in listeners])
        assert wait_for(lambda: http_health(port) is None, 5), "listener did not die (simulation broken)"
        assert t.is_alive(), "process-equivalent died instead of staying alive"
        # the fix: the listener comes back on the same port without a process restart
        assert wait_for(lambda: http_health(port) == 200, 20), "listener never came back"
        assert t.is_alive()
        if WIN:   # serve runs on the accept-tolerant loop (layer 2), not the stock proactor
            assert type(loop).__name__ == "ResilientProactorEventLoop", type(loop)
        from bossmobile.supervisor import Supervisor
        sup = next(s for s in _find(Supervisor) if s.port == port)
        assert sup.restarts == 1
        assert sup.server is not srv, "a fresh uvicorn Server (fresh app, fresh httpx clients) must be serving"
        # the restarted app still serves the full API, not just health
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/m/")
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        try:
            from bossmobile.supervisor import Supervisor
            for s in _find(Supervisor):
                if s.port == port:
                    s.stop()
        except ImportError:
            for s in _find(uvicorn.Server):
                if id(s) not in before:
                    s.should_exit = True
        t.join(15)
    assert not t.is_alive(), "serve did not exit on stop()"


def test_probe_health_false_on_closed_port():
    from bossmobile.supervisor import probe_health
    assert probe_health("127.0.0.1", free_port(), timeout_s=0.5) is False
