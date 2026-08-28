"""Health must name which part is broken, not only that something is.

"degraded" tells an owner nothing actionable. Camera offline, CRM unreachable
and detector not ready are three different phone calls.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.crm.base import CrmContext, CrmDescriptor
from ai_webcam_vision.errors import CaptureError, VisionError
from ai_webcam_vision.runtime.service import HealthState, VisionService
from ai_webcam_vision.transport.mock import FaultScript, SyntheticFrameSource, SyntheticScene

VOCABULARY = {
    HealthState.HEALTHY,
    HealthState.DEGRADED,
    HealthState.CAMERA_OFFLINE,
    HealthState.CRM_UNAVAILABLE,
    HealthState.DETECTOR_UNAVAILABLE,
}


class BrokenCrm:
    def __init__(self) -> None:
        self.descriptor = CrmDescriptor(kind="generic_http", is_mock=False, detail="real HTTP CRM")

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        raise VisionError("CRM request failed: connection refused")

    async def aclose(self) -> None:
        return None


class WorkingCrm:
    def __init__(self) -> None:
        self.descriptor = CrmDescriptor(kind="mock", is_mock=True, detail="scripted")

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        return CrmContext(available=True, source="mock", is_mock=True, shift_active=True)

    async def aclose(self) -> None:
        return None


def test_every_reported_health_state_is_in_the_vocabulary(settings):
    assert {s.value for s in HealthState} == {s.value for s in VOCABULARY}


async def test_detector_unavailable_without_a_baseline(settings):
    service = VisionService(settings, source=SyntheticFrameSource())
    try:
        health = service.health()
        assert health["health_state"] == HealthState.DETECTOR_UNAVAILABLE.value
        assert health["components"]["detector"]["state"] == "unavailable"
        assert "baseline" in health["components"]["detector"]["detail"].lower()
        # The camera itself is not being blamed for the detector.
        assert health["components"]["camera"]["state"] != "offline"
    finally:
        await service.aclose()


async def test_healthy_once_the_baseline_exists_and_a_sample_succeeded(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source, crm=WorkingCrm())
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        health = service.health()
        assert health["health_state"] == HealthState.HEALTHY.value
        assert health["status"] == "ok"
        assert health["components"]["camera"]["state"] == "ok"
        assert health["components"]["crm"]["state"] == "ok"
        assert health["components"]["detector"]["state"] == "ok"
    finally:
        await service.aclose()


async def test_camera_offline_is_its_own_state(settings):
    async def sleeper(_delay: float) -> None:
        return None

    source = SyntheticFrameSource(script=FaultScript(steps=[], default="fail"))
    service = VisionService(settings, source=source, sleep=sleeper, crm=WorkingCrm())
    try:
        service.baseline.save(await SyntheticFrameSource().grab())
        with pytest.raises(CaptureError):
            await service.sample_once()
        health = service.health()
        assert health["health_state"] == HealthState.CAMERA_OFFLINE.value
        assert health["components"]["camera"]["state"] == "offline"
        assert health["components"]["detector"]["state"] == "ok"
    finally:
        await service.aclose()


async def test_crm_unavailable_is_its_own_state(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source, crm=BrokenCrm())
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        health = service.health()
        assert health["health_state"] == HealthState.CRM_UNAVAILABLE.value
        assert health["components"]["crm"]["state"] == "unavailable"
        assert health["components"]["camera"]["state"] == "ok"
        assert health["components"]["detector"]["state"] == "ok"
    finally:
        await service.aclose()


async def test_a_disabled_crm_is_not_an_outage(settings):
    """No CRM configured is a decision, not a fault."""
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        health = service.health()
        assert health["components"]["crm"]["state"] == "disabled"
        assert health["health_state"] == HealthState.HEALTHY.value
    finally:
        await service.aclose()


async def test_camera_outranks_crm_when_both_are_down(settings):
    async def sleeper(_delay: float) -> None:
        return None

    source = SyntheticFrameSource(script=FaultScript(steps=[], default="fail"))
    service = VisionService(settings, source=source, sleep=sleeper, crm=BrokenCrm())
    try:
        service.baseline.save(await SyntheticFrameSource().grab())
        with pytest.raises(CaptureError):
            await service.sample_once()
        assert service.health()["health_state"] == HealthState.CAMERA_OFFLINE.value
    finally:
        await service.aclose()


async def test_crm_recovers_and_health_follows(settings):
    class Flaky:
        def __init__(self) -> None:
            self.descriptor = CrmDescriptor(kind="generic_http", is_mock=False, detail="real HTTP CRM")
            self.fail = True

        async def context(self, room_id: str, at: datetime) -> CrmContext:
            if self.fail:
                raise VisionError("CRM request failed")
            return CrmContext(available=True, source="crm", is_mock=False)

        async def aclose(self) -> None:
            return None

    crm = Flaky()
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source, crm=crm)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        assert service.health()["components"]["crm"]["state"] == "unavailable"
        crm.fail = False
        await service.sample_once()
        assert service.health()["components"]["crm"]["state"] == "ok"
        assert service.health()["health_state"] == HealthState.HEALTHY.value
    finally:
        await service.aclose()


async def test_ffmpeg_missing_makes_the_detector_unavailable(tmp_path, video_fixture):
    service = VisionService(Settings.from_env({
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(video_fixture),
        "AWV_FFMPEG_PATH": str(tmp_path / "no-such-ffmpeg"),
        "AWV_STATE_DIR": str(tmp_path / "state"),
    }))
    try:
        health = service.health()
        assert health["status"] == "unavailable"
        assert health["health_state"] == HealthState.DETECTOR_UNAVAILABLE.value
        assert health["components"]["detector"]["state"] == "unavailable"
    finally:
        await service.aclose()


async def test_manifest_launcher_facts_resolve_against_live_payloads(settings):
    """The BOSSMAN launcher card reads these paths. They must exist."""
    import yaml

    from pathlib import Path

    manifest = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "app.manifest.yaml").read_text(encoding="utf-8")
    )
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        payloads = {"health": service.health(), "metrics": service.metrics()}
        missing = []
        for fact in manifest["ui"]["facts"]:
            node = payloads
            for part in fact["from"].split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    missing.append(fact["from"])
                    break
        assert not missing, f"launcher facts point at fields the API never returns: {missing}"
    finally:
        await service.aclose()
