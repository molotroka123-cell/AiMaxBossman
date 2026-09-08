"""Trading Lab must never answer a 500, with or without the trading core.

Owner session `6cbb17ce84db` recorded `GET /api/trading-lab/status` returning
**500 `ModuleNotFoundError: No module named 'bossman_v3'`**. Command Center does
not depend on bossman-core by packaging, so a build without the core is a
supported configuration — and in that configuration the screen used to take the
server's error path instead of saying "not wired".

The fix is a lazy import with an honest UNWIRED answer, and it had no
regression test. This is that test. It runs the routes in both worlds: with the
core importable, and with the import forced to fail the way the owner's machine
failed it.

The second half is the part that matters more: an UNWIRED module must not be
able to look ready, and it must not carry trading authority.
"""
from __future__ import annotations

import builtins

import pytest

from bcc.features import trading_lab

pytestmark = pytest.mark.anyio

ROUTES = ("/api/trading-lab/status", "/api/trading-lab/seed",
          "/api/trading-lab/benchmark", "/api/trading-lab/memory")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path):
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings
    settings = Settings(data_dir=tmp_path / "data",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 't.db'}",
                        ui_dir=tmp_path / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    svc = app.state.svc
    await svc.start()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://t",
                                     headers={HEADER: svc.auth.token}) as http:
            yield http
    finally:
        await svc.stop()


@pytest.fixture
def core_missing(monkeypatch):
    """Make the trading core unimportable, the way a build without bossman-core
    is. `ModuleNotFoundError` is raised from the import machinery itself, not
    faked with a sentinel, so the route meets the real exception type."""
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        # Deeper modules only: this models the owner's actual failure, where
        # `bossman` imported fine and `bossman.trading_learning.<x>` then died
        # on a dependency (`No module named 'bossman_v3'`). Refusing the top
        # package instead would have tested a case the owner never hit.
        if name.startswith("bossman.trading_learning"):
            raise ModuleNotFoundError("No module named 'bossman_v3'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    monkeypatch.setattr(trading_lab.importlib, "import_module",
                        lambda dotted: refuse(dotted))
    return refuse


# ------------------------------------------------------------- never a 500

@pytest.mark.parametrize("route", ROUTES)
async def test_the_route_answers_without_the_core(client, core_missing, route):
    """The exact regression: a missing core is a supported configuration, not a
    server error."""
    response = await client.get(route)
    assert response.status_code == 200, response.text
    assert "ModuleNotFoundError" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("route", ROUTES)
async def test_the_route_answers_with_the_core(client, route):
    response = await client.get(route)
    assert response.status_code == 200, response.text


async def test_a_missing_core_reads_unwired_not_ready(client, core_missing):
    """The worst outcome would not be the 500 — it would be a cheerful stub. An
    unwired trading module is not better than an absent one, and the badge has
    to say so."""
    body = (await client.get("/api/trading-lab/status")).json()
    assert body["available"] is False
    assert body["evidence_class"] == "DEAD_OR_UNWIRED"
    assert body["badge"] == "UNWIRED"
    assert "bossman" in body["reason"]


@pytest.mark.parametrize("route", ROUTES[1:])
async def test_every_other_route_is_unwired_too(client, core_missing, route):
    """One honest route and three optimistic ones would be worse than none."""
    body = (await client.get(route)).json()
    assert body.get("available") is False and body.get("badge") == "UNWIRED"


async def test_unwired_never_claims_paper_or_live(client, core_missing):
    """`PAPER` would imply a working simulator behind the screen."""
    for route in ROUTES:
        body = (await client.get(route)).json()
        assert str(body.get("badge", "")).upper() not in ("PAPER", "LIVE", "READY")


# --------------------------------------------------------- no trading authority

async def test_the_module_exposes_no_write_route(client):
    """Trading Lab is a reading module. A POST/PUT/DELETE here would be an
    order-placing surface, which it must never grow by accident."""
    writes = [r for r in trading_lab.router.routes
              if set(getattr(r, "methods", ())) - {"GET", "HEAD", "OPTIONS"}]
    assert writes == [], writes


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("route", ROUTES)
async def test_write_verbs_are_refused(client, method, route):
    response = await client.request(method, route, json={})
    assert response.status_code in (404, 405), (method, route, response.status_code)


async def test_the_unwired_payload_is_a_constant_not_a_guess(core_missing):
    """`status_payload()` must return the shared UNWIRED literal rather than
    assembling a hopeful-looking dict of its own."""
    payload = trading_lab.status_payload()
    assert payload == dict(trading_lab.UNWIRED)
    assert payload is not trading_lab.UNWIRED, "callers must not mutate the constant"
