"""Fusing camera evidence with the CRM.

The CRM is the only source of intent and of identity. Everything it says is
therefore load-bearing, and everything it gets wrong becomes a wrong number in
the owner's report. These tests are the ones that hurt: a schema that lies, an
answer that is old, two appointments over the same minute, and a network that
drops one request in three.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.crm.base import (
    PROVENANCE_PRIORITY,
    CrmContext,
    ProcedureProvenance,
)
from ai_webcam_vision.crm.clients import CrmSchemaError, HttpCrm, MockCrm
from ai_webcam_vision.errors import VisionError
from ai_webcam_vision.secretstore import Secret

NOW = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, (bytes, str)):
            return json.loads(self._payload)
        return self._payload


class FakeClient:
    """A scripted httpx.AsyncClient stand-in. Records every call."""

    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls = 0

    async def get(self, url, params=None, headers=None):  # noqa: ANN001
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self) -> None:
        return None


def http_crm(script, **kwargs) -> HttpCrm:
    crm = HttpCrm(
        "https://crm.internal",
        Secret("crm-token-value", "crm_token"),
        timeout=kwargs.pop("timeout", 1.0),
        egress_enabled=True,
        **kwargs,
    )
    crm._client = FakeClient(script)
    return crm


# ------------------------------------------------------------ schema checks
async def test_a_string_false_is_not_accepted_as_true():
    """`bool("false")` is True. A CRM that stringifies booleans must not
    silently turn an empty room into an active appointment."""
    crm = http_crm([FakeResponse({
        "source": "clinic-crm",
        "shift_active": "false",
        "appointment_active": "false",
    })])
    with pytest.raises(CrmSchemaError):
        await crm.context("dental-1", NOW)


async def test_a_non_object_payload_is_rejected():
    crm = http_crm([FakeResponse([1, 2, 3])])
    with pytest.raises(CrmSchemaError):
        await crm.context("dental-1", NOW)


async def test_wrong_types_are_rejected_not_coerced():
    crm = http_crm([FakeResponse({"appointment_id": {"nested": "object"}})])
    with pytest.raises(CrmSchemaError):
        await crm.context("dental-1", NOW)


async def test_a_valid_payload_round_trips():
    crm = http_crm([FakeResponse({
        "source": "clinic-crm",
        "employee_id": "assistant-7",
        "clinician_id": "doctor-2",
        "shift_active": True,
        "appointment_id": "appt-123",
        "appointment_active": True,
        "planned_service": "endodontics-3-channel",
        "confirmed_service": "",
    })])
    context = await crm.context("dental-1", NOW)
    assert context.available is True
    assert context.is_mock is False
    assert context.clinician_id == "doctor-2"
    assert context.appointment_active is True
    assert context.stale is False


async def test_unknown_fields_are_ignored_without_failing():
    crm = http_crm([FakeResponse({
        "appointment_active": True,
        "a_field_from_a_future_crm_version": {"anything": [1, 2]},
    })])
    context = await crm.context("dental-1", NOW)
    assert context.appointment_active is True


# ---------------------------------------------------------------- staleness
async def test_a_dated_answer_is_marked_stale_not_treated_as_current():
    crm = http_crm(
        [FakeResponse({
            "appointment_active": True,
            "as_of": (NOW - timedelta(minutes=30)).isoformat(),
        })],
        max_age_seconds=300.0,
    )
    context = await crm.context("dental-1", NOW)
    assert context.stale is True
    assert context.age_seconds == pytest.approx(1800.0, abs=1.0)
    assert context.available is True


async def test_a_fresh_answer_is_not_stale():
    crm = http_crm(
        [FakeResponse({"appointment_active": True, "as_of": (NOW - timedelta(seconds=5)).isoformat()})],
        max_age_seconds=300.0,
    )
    context = await crm.context("dental-1", NOW)
    assert context.stale is False


async def test_an_answer_older_than_the_hard_limit_is_not_available():
    """Beyond a point, old is the same as absent — and must say so."""
    crm = http_crm(
        [FakeResponse({
            "appointment_active": True,
            "as_of": (NOW - timedelta(hours=6)).isoformat(),
        })],
        max_age_seconds=300.0,
        hard_max_age_seconds=3600.0,
    )
    context = await crm.context("dental-1", NOW)
    assert context.available is False
    assert context.stale is True
    assert "stale" in context.source


async def test_a_stale_context_is_visible_in_the_classification():
    from ai_webcam_vision.pipeline.analysis import Evidence
    from ai_webcam_vision.pipeline.classifier import State, classify

    evidence = Evidence(ts=NOW, room_change=0.09, chair_change=0.09, work_motion=0.05,
                        motion_gate=True, frame_seq=1)
    fresh = classify(evidence, CrmContext(available=True, appointment_active=True, shift_active=True))
    stale = classify(evidence, CrmContext(available=True, appointment_active=True,
                                          shift_active=True, stale=True, age_seconds=1800.0))
    assert fresh.state is State.CLINICAL_WORK
    assert "crm_stale" in stale.reasons
    assert stale.confidence < fresh.confidence


# ------------------------------------------------------- overlapping records
async def test_overlapping_appointments_are_resolved_by_priority_not_by_order():
    crm = http_crm([FakeResponse({
        "appointments": [
            {"appointment_id": "planned-first", "appointment_active": True,
             "planned_service": "checkup"},
            {"appointment_id": "confirmed-second", "appointment_active": True,
             "confirmed_service": "extraction"},
        ],
    })])
    context = await crm.context("dental-1", NOW)
    assert context.overlapping is True
    assert context.candidates == 2
    assert context.appointment_id == "confirmed-second"
    label, confidence, provenance = context.procedure()
    assert (label, provenance) == ("extraction", ProcedureProvenance.CONFIRMED)


async def test_a_single_appointment_is_not_reported_as_overlapping():
    crm = http_crm([FakeResponse({
        "appointments": [
            {"appointment_id": "only", "appointment_active": True, "planned_service": "checkup"},
        ],
    })])
    context = await crm.context("dental-1", NOW)
    assert context.overlapping is False
    assert context.candidates == 1
    assert context.appointment_id == "only"


# -------------------------------------------------------- timeout and retry
async def test_a_transport_failure_is_retried_with_backoff():
    import httpx

    slept: list[float] = []

    async def sleeper(delay: float) -> None:
        slept.append(delay)

    crm = http_crm(
        [
            httpx.ConnectError("connection refused"),
            httpx.ConnectError("connection refused"),
            FakeResponse({"appointment_active": True}),
        ],
        retries=3,
        retry_base_delay=0.1,
        sleep=sleeper,
    )
    context = await crm.context("dental-1", NOW)
    assert context.available is True
    assert crm._client.calls == 3
    assert slept == pytest.approx([0.1, 0.2])


async def test_the_retry_budget_is_bounded():
    import httpx

    async def sleeper(_delay: float) -> None:
        return None

    crm = http_crm(
        [httpx.ConnectError("still down")],
        retries=3,
        retry_base_delay=0.0,
        sleep=sleeper,
    )
    with pytest.raises(VisionError):
        await crm.context("dental-1", NOW)
    assert crm._client.calls == 3


async def test_a_schema_error_is_never_retried():
    """A malformed answer will be malformed again. Retrying wastes the budget."""
    async def sleeper(_delay: float) -> None:
        return None

    crm = http_crm([FakeResponse({"appointment_active": "false"})], retries=5, sleep=sleeper)
    with pytest.raises(CrmSchemaError):
        await crm.context("dental-1", NOW)
    assert crm._client.calls == 1


async def test_the_timeout_is_the_configured_one(base_env):
    settings = Settings.from_env(dict(
        base_env,
        AWV_CRM_KIND="generic_http",
        AWV_CRM_BASE_URL="https://crm.internal",
        AWV_CRM_EGRESS_ENABLED="true",
        AWV_CRM_TIMEOUT_SECONDS="2.5",
    ))
    from ai_webcam_vision.crm import build_crm

    crm = build_crm(settings)
    assert crm._timeout == 2.5


async def test_the_crm_token_never_appears_in_an_error():
    import httpx

    async def sleeper(_delay: float) -> None:
        return None

    crm = HttpCrm(
        "https://crm.internal",
        Secret("CRM_TOKEN_CANARY_e41b9a", "crm_token"),
        timeout=1.0,
        egress_enabled=True,
        retries=1,
        sleep=sleeper,
    )
    crm._client = FakeClient([httpx.ConnectError("failed for CRM_TOKEN_CANARY_e41b9a")])
    with pytest.raises(VisionError) as excinfo:
        await crm.context("dental-1", NOW)
    assert "CRM_TOKEN_CANARY_e41b9a" not in str(excinfo.value)


# -------------------------------------------------------------- provenance
def test_procedure_priority_is_explicit_and_ordered():
    assert PROVENANCE_PRIORITY == (
        ProcedureProvenance.CONFIRMED,
        ProcedureProvenance.PLANNED,
        ProcedureProvenance.MODEL_INFERRED,
        ProcedureProvenance.UNKNOWN,
    )


def test_confirmed_beats_planned_beats_model_beats_unknown():
    both = CrmContext(available=True, appointment_active=True,
                      planned_service="planned", confirmed_service="confirmed")
    assert both.procedure()[2] is ProcedureProvenance.CONFIRMED

    planned = CrmContext(available=True, appointment_active=True, planned_service="planned")
    assert planned.procedure()[2] is ProcedureProvenance.PLANNED

    inferred = CrmContext(available=True, appointment_active=True,
                          inferred_service="model-guess", inferred_confidence=0.9)
    assert inferred.procedure() == ("model-guess", 0.9, ProcedureProvenance.MODEL_INFERRED)

    assert CrmContext(available=True).procedure()[2] is ProcedureProvenance.UNKNOWN


def test_a_model_guess_never_outranks_the_crm():
    context = CrmContext(available=True, appointment_active=True,
                         planned_service="planned", inferred_service="model-guess",
                         inferred_confidence=0.99)
    label, _confidence, provenance = context.procedure()
    assert (label, provenance) == ("planned", ProcedureProvenance.PLANNED)


def test_a_model_guess_is_worthless_without_a_crm_answer():
    context = CrmContext(available=False, inferred_service="model-guess", inferred_confidence=0.99)
    assert context.procedure() == ("unknown", 0.0, ProcedureProvenance.UNKNOWN)


# ---------------------------------------------------------------- identity
def test_the_context_has_no_place_to_put_a_patient_identity():
    fields = set(CrmContext().__dict__)
    forbidden = {"patient_id", "patient_name", "patient", "face_id", "face_embedding"}
    assert not (fields & forbidden), fields & forbidden


async def test_staff_identity_comes_only_from_the_crm_never_from_pixels(settings):
    """The same pixels, two CRM answers: identity follows the CRM, not the frame."""
    from ai_webcam_vision.runtime.service import VisionService
    from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

    source = SyntheticFrameSource(scene=SyntheticScene(chair_occupied=True, work_activity=True))
    crm = MockCrm([
        CrmContext(available=True, source="mock", clinician_id="doctor-2",
                   employee_id="assistant-7", shift_active=True, appointment_active=True),
        CrmContext(available=True, source="mock", clinician_id="doctor-9",
                   employee_id="assistant-1", shift_active=True, appointment_active=True),
    ])
    service = VisionService(settings, source=source, crm=crm)
    try:
        service.baseline.save(await SyntheticFrameSource().grab())
        first = await service.sample_once()
        second = await service.sample_once()
        assert first["crm"]["clinician_id"] == "doctor-2"
        assert second["crm"]["clinician_id"] == "doctor-9"
        # And nothing in the visual evidence carries an identity at all.
        assert not any("id" in key for key in first["evidence"])
    finally:
        await service.aclose()


async def test_no_crm_means_no_identity_at_all(settings):
    from ai_webcam_vision.runtime.service import VisionService
    from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

    source = SyntheticFrameSource(scene=SyntheticScene(chair_occupied=True, work_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await SyntheticFrameSource().grab())
        result = await service.sample_once()
        assert result["crm"]["available"] is False
        assert result["crm"]["clinician_id"] == ""
        assert result["crm"]["employee_id"] == ""
        assert result["classification"]["state"] != "CLINICAL_WORK"
    finally:
        await service.aclose()
