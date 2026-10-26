from fastapi.testclient import TestClient

from travel_architect.api import build_app


def test_manually_entered_offers_compare_full_price_without_booking(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    client = TestClient(build_app())
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="create-trip"' in page.text
    assert 'id="add-offer"' in page.text
    assert 'id="compare"' in page.text
    assert "живого поиска цен здесь нет" in page.text
    assert "не бронирует поездки" in page.text

    trip = client.post("/api/trips", json={
        "origin": ["PRG"], "destination": "LIS", "start": "2026-11-01", "end": "2026-11-05",
        "travelers": 1, "budget": 1000, "constraints": {},
    }).json()
    package = client.post(f"/api/trips/{trip['id']}/offers", json={
        "kind": "package", "provider": "Synthetic package", "base_price": 780, "currency": "EUR",
        "fees": {"bag": 80, "seat": 20}, "quality_score": 4,
    }).json()
    diy = client.post(f"/api/trips/{trip['id']}/offers", json={
        "kind": "diy", "provider": "Synthetic DIY", "base_price": 820, "currency": "EUR",
        "fees": {"bag": 10}, "quality_score": 3,
    }).json()
    result = client.get(f"/api/trips/{trip['id']}/compare").json()
    assert package["true_price"] == 880
    assert diy["true_price"] == 830
    assert result["best"]["id"] == diy["id"]


def test_mixed_currency_offers_are_not_ranked_as_if_they_were_comparable(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    client = TestClient(build_app())
    trip = client.post("/api/trips", json={
        "origin": ["PRG"], "destination": "LIS", "start": "2026-11-01", "end": "2026-11-05",
    }).json()
    client.post(f"/api/trips/{trip['id']}/offers", json={
        "kind": "package", "provider": "Synthetic EUR", "base_price": 500, "currency": "EUR",
    })
    client.post(f"/api/trips/{trip['id']}/offers", json={
        "kind": "diy", "provider": "Synthetic CZK", "base_price": 12000, "currency": "CZK",
    })

    comparison = client.get(f"/api/trips/{trip['id']}/compare").json()
    smart = client.get(f"/api/trips/{trip['id']}/smart-compare").json()

    assert comparison["comparable"] is False
    assert comparison["reason"] == "mixed_currencies"
    assert comparison["best"] is None
    assert smart["comparable"] is False
    assert smart["recommended"] is None


def test_budget_check_reports_currency_mismatch_instead_of_comparing_raw_numbers(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_APPS_DATA", str(tmp_path))
    client = TestClient(build_app())
    trip = client.post("/api/trips", json={
        "origin": ["PRG"], "destination": "LIS", "start": "2026-11-01", "end": "2026-11-05",
        "budget": 1000, "budget_currency": "EUR",
    }).json()
    offer = client.post(f"/api/trips/{trip['id']}/offers", json={
        "kind": "diy", "provider": "Synthetic CZK", "base_price": 500, "currency": "CZK",
    }).json()
    result = client.get(f"/api/trips/{trip['id']}/offers/{offer['id']}/constraint-check").json()
    assert result["pass"] is False
    assert result["failures"] == ["budget_currency_mismatch"]
