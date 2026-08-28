"""Motion ingress and what may be claimed about ONVIF.

There is no Tapo C200 in this environment. Nothing here may therefore assert
that ONVIF event subscription works on that firmware — and the code must say
so itself, machine-readably, so the claim cannot quietly drift.

What *is* proven is the vendor-neutral fallback: any bridge that can make one
HTTP POST can drive the sampling rate, with no ONVIF anywhere.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_webcam_vision.api import build_app
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.transport.mock import SyntheticFrameSource

DOCS = Path(__file__).resolve().parents[1] / "docs"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(settings):
    service = VisionService(settings, source=SyntheticFrameSource())
    with TestClient(build_app(settings, service=service)) as test_client:
        test_client.service = service
        yield test_client


# ----------------------------------------------------------------- honesty
def test_capabilities_declare_onvif_unimplemented_and_unverified(client):
    motion = client.get("/api/v1/capabilities").json()["motion"]
    onvif = motion["onvif_subscription"]
    assert onvif["implemented"] is False
    assert onvif["verified_on_tapo_c200"] is False
    assert onvif["evidence"] == "NOT RUN"
    assert onvif["blocked_by"] == "hardware"


def test_capabilities_declare_the_vendor_neutral_fallback(client):
    motion = client.get("/api/v1/capabilities").json()["motion"]
    assert motion["webhook"]["endpoint"] == "POST /hooks/motion"
    assert motion["webhook"]["implemented"] is True
    assert motion["webhook"]["vendor_neutral"] is True
    assert motion["primary_source"] == "webhook"


def test_no_document_claims_onvif_subscription_works_on_this_camera():
    """A grep, on purpose: the claim must not reappear in prose either."""
    banned = re.compile(
        r"(onvif[^.\n]{0,60}(is |are )?(supported|works|verified|tested)"
        r"|(supported|works|verified|tested)[^.\n]{0,40}onvif)",
        re.IGNORECASE,
    )
    offenders = []
    for path in [*DOCS.glob("*.md"), ROOT / "README.md"]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if banned.search(line) and "not" not in line.lower():
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_the_docs_state_the_onvif_verification_status_explicitly():
    text = (DOCS / "NETWORK_AND_TAPO.md").read_text(encoding="utf-8")
    assert "NOT RUN" in text or "BLOCKED BY HARDWARE" in text


# ---------------------------------------------------------------- fallback
def test_the_webhook_opens_the_sampling_window_without_any_onvif(client):
    assert client.get("/api/v1/health").json()["motion"]["active"] is False
    response = client.post("/hooks/motion", json={"source": "clinic-nvr-bridge"})
    assert response.status_code == 200
    motion = response.json()["motion"]
    assert motion["active"] is True
    assert motion["source"] == "clinic-nvr-bridge"
    assert motion["seconds_remaining"] > 0


def test_any_vendor_label_is_accepted_and_none_is_required(client):
    for label in ("onvif-bridge", "hikvision-nvr", "shell-script", "", None):
        payload = {} if label is None else {"source": label}
        response = client.post("/hooks/motion", json=payload)
        assert response.status_code == 200, label
    assert client.service.motion.state().triggers == 5


def test_the_webhook_never_takes_a_camera_address_or_credential(client):
    """The hook is a wake signal, not a transport: it must not be
    persuadable into connecting anywhere."""
    before = client.service.source.descriptor.to_dict()
    response = client.post("/hooks/motion", json={
        "source": "attacker",
        "rtsp_url": "rtsp://attacker:pw@10.0.0.9:554/stream1",
        "camera_host": "10.0.0.9",
    })
    assert response.status_code == 200
    assert client.service.source.descriptor.to_dict() == before
    assert "10.0.0.9" not in response.text


def test_motion_gate_falls_closed_when_nobody_calls(settings):
    """Without a bridge the service samples at the idle interval. Stated,
    not implied to be motion detection."""
    service = VisionService(settings, source=SyntheticFrameSource())
    try:
        assert service.motion.active() is False
        assert service._sample_interval() >= settings.idle_interval
    finally:
        import asyncio

        asyncio.get_event_loop_policy()
        del service


def test_the_motion_hook_is_authenticated_when_a_token_is_set(base_env):
    settings_with_token = __import__(
        "ai_webcam_vision.config", fromlist=["Settings"]
    ).Settings.from_env(dict(base_env, AWV_API_TOKEN="motion-hook-token"))
    service = VisionService(settings_with_token, source=SyntheticFrameSource())
    with TestClient(build_app(settings_with_token, service=service)) as client:
        assert client.post("/hooks/motion", json={"source": "x"}).status_code == 401
        ok = client.post(
            "/hooks/motion",
            json={"source": "x"},
            headers={"Authorization": "Bearer motion-hook-token"},
        )
        assert ok.status_code == 200
