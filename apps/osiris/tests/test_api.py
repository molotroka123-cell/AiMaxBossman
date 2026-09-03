from fastapi.testclient import TestClient

from osiris.api import build_app
from osiris.store import Store


def client(tmp_path):
    return TestClient(build_app(store=Store(tmp_path)))


def test_health(tmp_path):
    r = client(tmp_path).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["level0"] == "sealed"
    assert body["local_session_cap"] is None
    assert body["bind"] == "127.0.0.1"


def test_ui(tmp_path):
    r = client(tmp_path).get("/")
    assert r.status_code == 200
    assert "OSIRIS" in r.text


def test_grant_l0_rejected(tmp_path):
    r = client(tmp_path).post(
        "/api/grants",
        json={
            "author": "o",
            "source_or_subject": "x",
            "reason": "no",
            "clause": "leaked_dump",
            "ttl_hours": 1,
        },
    )
    assert r.status_code == 403


def test_fact_roundtrip(tmp_path):
    c = client(tmp_path)
    r = c.post(
        "/api/facts",
        json={
            "subject": "org:9",
            "predicate": "legal_name",
            "object": "Acme",
            "source": "site",
            "url": "https://acme.example/",
            "method": "http_get",
            "license": "public",
            "confidence": 0.6,
        },
    )
    assert r.status_code == 200
    g = c.get("/api/graph").json()
    assert any(n["id"] == "org:9" for n in g["nodes"])


def test_export_403_without_grant(tmp_path):
    r = client(tmp_path).post("/api/export", json={"subject": "org:9"})
    assert r.status_code == 403


def test_twitter_frozen(tmp_path):
    c = client(tmp_path)
    r = c.get("/api/twitter/status")
    assert r.status_code == 200
    assert r.json()["status"] == "frozen"
    assert c.post("/api/twitter/lookup").status_code == 423
