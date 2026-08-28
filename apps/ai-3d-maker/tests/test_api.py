"""HTTP surface. Skipped entirely when fastapi/httpx are unavailable."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from ai_3d_maker.api import build_app  # noqa: E402


@pytest.fixture
def client(control):
    with TestClient(build_app(control)) as c:
        yield c


def payload(job_id: str) -> dict:
    return {
        "kind": "design",
        "job_id": job_id,
        "spec": {
            "name": "plate",
            "features": [{"primitive": {"id": "b", "kind": "box", "size_mm": [40, 30, 8]}, "operation": "add"}],
        },
    }


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["physical_printing_enabled"] is False


def test_capabilities(client):
    body = client.get("/capabilities").json()
    assert body["features"]["physical_print"] is False
    assert "operations" in body


def test_metrics(client):
    assert client.get("/metrics").json()["app"] == "ai-3d-maker"


def test_profile_route_keeps_the_two_sections(client):
    body = client.get("/api/profile").json()
    assert body["verified_machine_limits"]["max_nozzle_temp_c"] == 260
    assert "process_defaults_unverified" in body


def test_job_create_status_and_artifacts(client):
    created = client.post("/api/jobs", json=payload("http1")).json()
    assert created["accepted"] is True
    assert created["result"]["printable"] is True

    status = client.get("/api/jobs/http1").json()
    assert status["status"] == "succeeded"

    artifacts = client.get("/api/jobs/http1/artifacts").json()
    assert any(a["path"] == "model.stl" for a in artifacts["artifacts"])

    listing = client.get("/api/jobs").json()
    assert listing["count"] >= 1


def test_artifact_download(client):
    client.post("/api/jobs", json=payload("http2"))
    response = client.get("/api/jobs/http2/artifacts/model.stl")
    assert response.status_code == 200
    assert len(response.content) > 84


def test_artifact_traversal_is_refused(client):
    client.post("/api/jobs", json=payload("http3"))
    response = client.get("/api/jobs/http3/artifacts/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code in {400, 404}


def test_unknown_job_returns_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_gcode_scan_route(client):
    body = client.post("/api/gcode/scan", json={"gcode": "M104 S400"}).json()
    assert body["status"] == "FAILED"


def test_printer_confirm_requires_a_token(client):
    client.post("/api/jobs", json=payload("http4"))
    body = client.post("/api/printer/confirm", json={"job_id": "http4", "action": "start_print"}).json()
    assert body["error"] == "PHYSICAL_CONFIRMATION_REQUIRED"


def test_printer_confirm_with_the_token_is_only_simulated(client):
    client.post("/api/jobs", json=payload("http5"))
    token = client.get("/api/jobs/http5/confirmation").json()["confirmation"]
    body = client.post(
        "/api/printer/confirm",
        json={"job_id": "http5", "action": "start_print", "confirmation": token},
    ).json()
    assert body["status"] == "SIMULATED"
    assert body["performed_physical_action"] is False


def test_invalid_spec_is_rejected_with_a_reason(client):
    bad = payload("http6")
    bad["spec"]["features"][0]["primitive"]["size_mm"] = [10, -1, 3]
    response = client.post("/api/jobs", json=bad)
    assert response.status_code in {400, 422}


def test_cancel_route(client):
    client.post("/api/jobs", json=payload("http7"))
    body = client.post("/api/jobs/http7/cancel").json()
    assert body["job_id"] == "http7"
