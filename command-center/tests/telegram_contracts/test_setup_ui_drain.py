"""A refused setup POST must answer 403, not abort the connection (Windows RST).

Regression for the flaky failure of
test_browser_setup_real_local_http_requires_origin_host_and_nonce: the handler
answered before reading the request body, so the still-unread bytes made
Windows reset the socket and the client saw WinError 10053 instead of 403.
"""
from __future__ import annotations

import json
import threading

import httpx
import pytest

from bcc.telegram_companion.setup_ui import make_server

BODY = json.dumps({"filler": "x" * 9000}).encode()


@pytest.mark.parametrize("bad", ["origin", "token", "host"])
def test_refused_post_with_a_body_always_answers_403(tmp_path, bad):
    server, token = make_server(tmp_path / "config.json")
    base = "http://" + server.expected_host
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    headers = {"Host": server.expected_host, "Origin": base, "X-Setup-Token": token,
               "Content-Type": "application/json"}
    if bad == "origin":
        headers["Origin"] = "https://attacker.example"
    elif bad == "token":
        headers["X-Setup-Token"] = "wrong"
    else:
        headers["Host"] = "evil.example"
    try:
        # Repeated because the reset is a race: one pass proves nothing.
        with httpx.Client(timeout=10) as client:
            for _ in range(15):
                response = client.post(base + "/setup", content=BODY, headers=headers)
                assert response.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
