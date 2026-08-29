"""CRM contracts.

The camera says what physically happened. The CRM says what was supposed to
happen and who was assigned. Fusing them is the whole product; guessing either
half from pixels is explicitly out of scope (no faces, no patient identity).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class ProcedureProvenance(StrEnum):
    CONFIRMED = "crm_confirmed"
    PLANNED = "crm_planned"
    #: Reserved for a future local model. Declared so the ranking is explicit
    #: and testable today rather than being invented when a model appears.
    MODEL_INFERRED = "model_inferred"
    UNKNOWN = "unknown"


#: The only ordering that may decide a procedure label. What the clinic
#: confirmed happened outranks what it planned, which outranks anything a
#: model thinks it saw, which outranks a guess. A model never wins.
PROVENANCE_PRIORITY: tuple[ProcedureProvenance, ...] = (
    ProcedureProvenance.CONFIRMED,
    ProcedureProvenance.PLANNED,
    ProcedureProvenance.MODEL_INFERRED,
    ProcedureProvenance.UNKNOWN,
)


@dataclass(frozen=True)
class CrmContext:
    """Room context at a point in time.

    ``available`` distinguishes "the CRM said there is no appointment" from
    "there is no CRM". The legacy pack conflated the two; downstream code and
    stored observations now always know which one it is.
    """

    available: bool = False
    source: str = "disabled"
    is_mock: bool = False
    employee_id: str = ""
    clinician_id: str = ""
    shift_active: bool = False
    appointment_id: str = ""
    appointment_active: bool = False
    planned_service: str = ""
    confirmed_service: str = ""

    #: When the CRM says this answer was true. Empty means the CRM did not say.
    as_of: str = ""
    #: The answer is older than the freshness budget. Still usable, never
    #: silently treated as current.
    stale: bool = False
    #: Age of the answer in seconds, when the CRM dated it.
    age_seconds: float | None = None
    #: More than one appointment covered the requested instant.
    overlapping: bool = False
    #: How many candidate appointments were considered.
    candidates: int = 0

    #: A future local model's guess. Never a source of identity, and never
    #: allowed to outrank the CRM — it exists so the ranking has a slot for
    #: it instead of one being improvised later.
    inferred_service: str = ""
    inferred_confidence: float = 0.0

    def procedure(self) -> tuple[str, float, ProcedureProvenance]:
        """The label, its confidence and where it came from.

        Strictly :data:`PROVENANCE_PRIORITY`. Without an available CRM answer
        there is no label at all — including no model guess, because a model
        guess about a room with no known appointment is not evidence of a
        procedure, it is speculation about a patient.
        """
        if not self.available:
            return "unknown", 0.0, ProcedureProvenance.UNKNOWN
        if self.confirmed_service:
            return self.confirmed_service, 1.0, ProcedureProvenance.CONFIRMED
        if self.appointment_active and self.planned_service:
            return self.planned_service, 0.85, ProcedureProvenance.PLANNED
        if self.inferred_service:
            return self.inferred_service, self.inferred_confidence, ProcedureProvenance.MODEL_INFERRED
        return "unknown", 0.0, ProcedureProvenance.UNKNOWN

    def to_dict(self) -> dict:
        data = asdict(self)
        label, confidence, provenance = self.procedure()
        data["procedure"] = {
            "label": label,
            "confidence": confidence,
            "provenance": provenance.value,
        }
        return data


@dataclass(frozen=True)
class CrmDescriptor:
    kind: str
    is_mock: bool
    detail: str
    base_url: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "is_mock": self.is_mock,
            "detail": self.detail,
            "base_url": self.base_url,
        }


@runtime_checkable
class CrmClient(Protocol):
    descriptor: CrmDescriptor

    async def context(self, room_id: str, at: datetime) -> CrmContext: ...

    async def aclose(self) -> None: ...
