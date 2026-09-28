"""RC19 owner run: the login screen names the token FILE, never the token.

The desktop shortcut starts the server without a console, so the old hint
«Токен напечатан в консоли сервера» was a dead end for the owner.
"""
import httpx
from pathlib import Path

from bcc.api import create_app


async def test_loopback_client_gets_the_token_file_path_not_the_token(env):
    r = await env.client.get("/api/login-hint")
    assert r.status_code == 200
    path = r.json()["token_file"]
    assert path and Path(path).name == "token"
    token = Path(path).read_text(encoding="utf-8").strip()
    assert token and token not in r.text


async def test_non_loopback_client_gets_nothing(env):
    transport = httpx.ASGITransport(app=env.client._transport.app, client=("192.168.1.50", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as remote:
        r = await remote.get("/api/login-hint")
    assert r.status_code == 200 and r.json() == {"token_file": None}


def test_login_page_no_longer_points_to_a_console_only():
    ui = Path(__file__).resolve().parents[1] / "ui"
    app_js = (ui / "app.js").read_text(encoding="utf-8")
    assert "api.loginHint()" in app_js and "Токен лежит в файле" in app_js
    assert 'id="login-hint"' in (ui / "index.html").read_text(encoding="utf-8")
