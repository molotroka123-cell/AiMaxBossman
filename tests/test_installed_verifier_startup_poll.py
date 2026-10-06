"""The installed-product acceptance must retry a reset during boot, yet still fail when the server never answers."""
import http.client
import importlib.util
from pathlib import Path
import socket
import struct
import threading
import urllib.error
import urllib.request

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "installed_verifier", Path(__file__).resolve().parents[1] / "tools" / "verify_installed_product.py")
verifier = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verifier)


def _resetting_server():
    """Accepts one connection, reads the request and answers with a TCP RST (what a booting server does on Windows)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv


def test_a_reset_during_boot_is_a_retryable_not_ready_signal():
    srv = _resetting_server()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(BaseException) as raised:
        opener.open(f"http://127.0.0.1:{srv.getsockname()[1]}/api/identity", timeout=5)
    srv.close()
    # urllib lets this one escape unwrapped; the previous (URLError, TimeoutError) tuple did not cover it.
    assert not isinstance(raised.value, (urllib.error.URLError, TimeoutError))
    assert isinstance(raised.value, verifier.NOT_READY_YET)


def test_real_failures_are_not_swallowed_as_not_ready():
    # negative control: wrong identity / assertion failures and programming errors must still abort the run
    for fatal in (AssertionError("identity"), KeyError("app"), ValueError("bad json"), RuntimeError("boom")):
        assert not isinstance(fatal, verifier.NOT_READY_YET)
    assert isinstance(http.client.RemoteDisconnected("closed"), verifier.NOT_READY_YET)
    assert isinstance(ConnectionRefusedError(), verifier.NOT_READY_YET)
