"""Non-finite or negative prices are client errors (422), never a 500 or a NaN «true price»."""
import pytest
from fastapi.testclient import TestClient
from travel_architect.api import build_app

NAN_BODIES = [
    '{"kind":"diy","provider":"A","base_price":NaN}',
    '{"kind":"diy","provider":"A","base_price":Infinity}',
    '{"kind":"diy","provider":"A","base_price":"nan"}',
    '{"kind":"diy","provider":"A","base_price":-50}',
    '{"kind":"diy","provider":"A","base_price":100,"fees":{"bag":"nan"}}',
    '{"kind":"diy","provider":"A","base_price":100,"fees":{"bag":-30}}',
    '{"kind":"diy","provider":"A","base_price":100,"fees":{"bag":"abc"}}',
    '{"kind":"diy","provider":"A","base_price":100,"quality_score":NaN}',
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    return TestClient(build_app(), raise_server_exceptions=False)


def _trip(c):
    return c.post("/api/trips", json={"origin": ["PRG"], "destination": "PMI",
                                      "start": "2026-09-01", "end": "2026-09-05"}).json()


@pytest.mark.parametrize("body", NAN_BODIES)
def test_non_finite_or_negative_offer_price_is_422_and_not_stored(client, body):
    t = _trip(client)
    r = client.post(f"/api/trips/{t['id']}/offers", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    cmp = client.get(f"/api/trips/{t['id']}/compare")
    assert cmp.status_code == 200 and cmp.json()["offers"] == []


@pytest.mark.parametrize("path,body", [
    ("/api/trips", '{"origin":["PRG"],"destination":"PMI","start":"2026-09-01","end":"2026-09-05","budget":NaN}'),
    ("WATCH", '{"max_price":NaN}'),
    ("WATCH", '{"max_price":-1}'),
])
def test_non_finite_budget_and_watch_limit_are_422(client, path, body):
    if path == "WATCH":
        path = f"/api/trips/{_trip(client)['id']}/watches"
    r = client.post(path, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert client.get("/api/watches/evaluate").status_code == 200
    assert client.get("/api/trips").status_code == 200


def test_valid_offer_still_gets_true_price(client):
    t = _trip(client)
    o = client.post(f"/api/trips/{t['id']}/offers",
                    json={"kind": "diy", "provider": "A", "base_price": 100, "fees": {"bag": 30.5, "seat": None}}).json()
    assert o["true_price"] == 130.5
