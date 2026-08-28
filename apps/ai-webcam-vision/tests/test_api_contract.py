"""The control contract BOSSMAN drives: health, capabilities, jobs, artifacts, metrics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ai_webcam_vision.api import build_app
from ai_webcam_vision.config import Settings
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

from conftest import wait_for_job


@pytest.fixture
def client(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True, chair_occupied=True))
    service = VisionService(settings, source=source)
    with TestClient(build_app(settings, service=service)) as test_client:
        test_client.service = service
        yield test_client


def test_healthz_is_minimal_and_unauthenticated(client):
    payload = client.get("/healthz").json()
    assert payload["status"] in {"ok", "degraded", "unavailable"}
    assert payload["app"]["id"] == "ai-webcam-vision"


def test_health_declares_mock_versus_real(client):
    health = client.get("/api/v1/health").json()
    assert health["camera"]["mode"] == "mock"
    assert health["camera"]["is_mock_camera"] is True
    assert health["camera"]["uses_real_transport"] is False
    assert health["crm"]["is_mock"] is True
    assert health["crm"]["kind"] == "disabled"
    assert health["analyzer"]["is_mock"] is False
    assert health["compute"]["mode"] in {"cpu", "gpu"}
    assert health["compute"]["used_by_pipeline"] == "cpu"
    assert "empty-room baseline not captured" in health["blockers"]
    assert health["status"] == "degraded"


def test_capabilities_lists_the_contract(client):
    caps = client.get("/api/v1/capabilities").json()
    assert set(caps["job_types"]) == {"probe", "baseline", "sample", "observe", "snapshot"}
    for key in ("health", "capabilities", "jobs.create", "jobs.status", "jobs.cancel",
                "artifacts.list", "metrics"):
        assert key in caps["endpoints"]
    assert caps["role"] == "workload"
    assert caps["model"]["provider"] == "none"
    assert caps["privacy"]["recording_enabled"] is False
    assert caps["privacy"]["egress"]["crm_enabled"] is False
    assert caps["limits"]["frame_queue_max"] == client.service.settings.frame_queue_max
    assert caps["limits"]["retry_backoff_seconds"]


def test_metrics_reports_resources_and_counters(client):
    metrics = client.get("/api/v1/metrics").json()
    assert "counters" in metrics and "queue" in metrics
    assert metrics["resources"]["cpu_count"] >= 1
    assert metrics["compute"]["used_by_pipeline"] == "cpu"


def test_job_lifecycle_baseline_then_sample(client):
    created = client.post("/api/v1/jobs", json={"type": "baseline"})
    assert created.status_code == 202
    job = wait_for_job(client, created.json()["id"])
    assert job["status"] == "succeeded"
    assert job["result"]["width"] == 160

    artifacts = client.get("/api/v1/artifacts").json()["artifacts"]
    assert artifacts and artifacts[0]["kind"] == "baseline"
    assert artifacts[0]["meta"]["format"] == "npy-gray"

    sample = wait_for_job(client, client.post("/api/v1/jobs", json={"type": "sample"}).json()["id"])
    assert sample["status"] == "succeeded"
    assert sample["result"]["classification"]["crm"]["available"] is False
    assert sample["result"]["source"]["is_mock_camera"] is True

    health = client.get("/api/v1/health").json()
    assert health["status"] == "ok"
    assert health["counters"]["observations_stored"] == 1


def test_observe_job_can_be_cancelled(client):
    wait_for_job(client, client.post("/api/v1/jobs", json={"type": "baseline"}).json()["id"])
    created = client.post("/api/v1/jobs", json={"type": "observe",
                                                "params": {"duration_seconds": 30}}).json()
    cancel = client.post(f"/api/v1/jobs/{created['id']}/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["cancelled"] is True
    assert wait_for_job(client, created["id"])["status"] == "cancelled"


def test_observe_job_produces_samples(client):
    wait_for_job(client, client.post("/api/v1/jobs", json={"type": "baseline"}).json()["id"])
    created = client.post(
        "/api/v1/jobs",
        json={"type": "observe", "params": {"duration_seconds": 1.0, "max_samples": 3}},
    ).json()
    job = wait_for_job(client, created["id"], timeout=60)
    assert job["status"] == "succeeded", job
    assert job["result"]["samples"] >= 1
    metrics = client.get(f"/api/v1/rooms/{client.service.settings.room_id}/metrics/today").json()
    assert metrics["samples"] >= 1


def test_unknown_job_type_is_rejected(client):
    response = client.post("/api/v1/jobs", json={"type": "definitely-not-a-job"})
    assert response.status_code == 400
    assert "unknown job type" in response.json()["detail"]


def test_unknown_job_id_is_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404
    assert client.post("/api/v1/jobs/does-not-exist/cancel").status_code == 404


def test_bad_bodies_are_rejected(client):
    assert client.post("/api/v1/jobs", content=b"not json").status_code == 400
    assert client.post("/api/v1/jobs", content=b"[1,2,3]").status_code == 400
    assert client.post("/api/v1/jobs", json={"type": "sample", "params": 5}).status_code == 400
    assert client.post("/hooks/motion", content=b"x" * 70_000).status_code == 413


def test_motion_hook_opens_the_sampling_window(client):
    response = client.post("/hooks/motion", json={"source": "onvif-bridge"})
    assert response.status_code == 200
    assert response.json()["motion"]["active"] is True
    assert client.get("/api/v1/health").json()["motion"]["source"] == "onvif-bridge"


def test_snapshot_job_is_denied_while_snapshots_are_off(client):
    job = wait_for_job(client, client.post("/api/v1/jobs", json={"type": "snapshot"}).json()["id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "privacy_denied"


def test_bearer_auth_is_enforced_when_configured(base_env):
    env = dict(base_env, AWV_API_TOKEN="contract-token-value")
    settings = Settings.from_env(env)
    service = VisionService(settings, source=SyntheticFrameSource())
    with TestClient(build_app(settings, service=service)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/health").status_code == 401
        assert client.post("/api/v1/jobs", json={"type": "probe"}).status_code == 401
        ok = client.get("/api/v1/health", headers={"Authorization": "Bearer contract-token-value"})
        assert ok.status_code == 200
        assert client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_service_is_closed_when_the_app_stops(settings):
    source = SyntheticFrameSource()
    service = VisionService(settings, source=source)
    with TestClient(build_app(settings, service=service)):
        pass
    assert source.closed is True
