"""Desktop and backend must be the same proven build before a window opens."""
from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bcc import build_identity, desktop
from bcc.config import settings


def _identity(sha: str | None, *, version: str = "0.1.0") -> dict:
    return {"app": desktop.APP_IDENTITY, "version": version,
            "source_identity": "PASS" if sha is not None else build_identity.UNKNOWN,
            "build_sha": sha}


@pytest.mark.parametrize("server", [
    _identity("b" * 40),
    _identity("a" * 40, version="0.2.0"),
    _identity(None),
    _identity("not-a-sha"),
    {"app": desktop.APP_IDENTITY, "version": "0.1.0", "build_sha": "a" * 40},
])
def test_different_or_unproven_server_build_never_opens_window(server, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: _identity("a" * 40))
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: server)
    launched = []
    out = io.StringIO()

    code = desktop.run(["--no-server", "--port", "18973", "--browser", "dummy-browser",
                        "--profile", str(tmp_path / "profile"), "--no-show-token"],
                       launcher=lambda *a, **k: launched.append(True) or 0, out=out)

    assert code == 7 and not launched
    assert "другой сборки" in out.getvalue()
    assert "backend-build-mismatch" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_same_proven_build_reconnects_without_starting_another_server(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    same = _identity("a" * 40)
    monkeypatch.setattr(desktop, "_local_identity", lambda: same)
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: same)
    launched = []

    code = desktop.run(["--no-server", "--port", "18973", "--browser", "dummy-browser",
                        "--profile", str(tmp_path / "profile"), "--no-show-token"],
                       launcher=lambda *a, **k: launched.append(True) or 0, out=io.StringIO())

    assert code == 0 and launched == [True]


def test_unknown_local_source_cannot_claim_match(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: _identity(None))
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: _identity("a" * 40))
    assert desktop.run(["--no-server", "--port", "18973", "--browser", "dummy-browser",
                        "--no-show-token"], launcher=lambda *a, **k: 0, out=io.StringIO()) == 7


def test_new_window_identifies_but_refuses_a_legacy_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(desktop, "_local_identity", lambda: _identity("a" * 40))
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: True)
    monkeypatch.setattr(desktop, "_get_json", lambda *a, **k: {
        "app": "bossman-command-center", "version": "0.1.0",
        "source_identity": "PASS", "build_sha": "b" * 40})
    launched = []
    out = io.StringIO()

    code = desktop.run(["--no-server", "--port", "18973", "--browser", "dummy-browser",
                        "--no-show-token"],
                       launcher=lambda *a, **k: launched.append(True) or 0, out=out)

    assert code == 7 and not launched
    assert "прежней сборкой Command Center" in out.getvalue()
    assert "backend-legacy-identity" in (tmp_path / "desktop-run.log").read_text(encoding="utf-8")


def test_legacy_launcher_refuses_the_new_identity_marker():
    # Frozen older desktop versions only accept the original name from
    # /api/identity. This marker change protects a new backend from them too.
    assert desktop.APP_IDENTITY == build_identity.DESKTOP_APP_IDENTITY
    assert desktop.APP_IDENTITY != "bossman-command-center"


def test_foreign_responder_during_bind_is_not_mistaken_for_our_server(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{tmp_path / 'bcc.db'}")

    class Occupant(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps(_identity("b" * 40)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    occupant = HTTPServer(("127.0.0.1", 0), Occupant)
    thread = threading.Thread(target=occupant.serve_forever, daemon=True)
    thread.start()
    port = occupant.server_port
    server = desktop._BackgroundServer("127.0.0.1", port)
    try:
        assert desktop.server_alive(f"http://127.0.0.1:{port}/")
        assert server.start(f"http://127.0.0.1:{port}/", timeout=10) is False
        assert server.error
    finally:
        server.stop()
        occupant.shutdown()
        occupant.server_close()
        thread.join(timeout=5)
