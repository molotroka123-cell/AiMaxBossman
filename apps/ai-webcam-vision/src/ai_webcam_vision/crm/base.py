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
    UNKNOWN = "unknown"


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

    def procedure(self) -> tuple[str, float, ProcedureProvenance]:
        if not self.available:
            return "unknown", 0.0, ProcedureProvenance.UNKNOWN
        if self.confirmed_service:
            return self.confirmed_service, 1.0, ProcedureProvenance.CONFIRMED
        if self.appointment_active and self.planned_service:
            return self.planned_service, 0.85, ProcedureProvenance.PLANNED
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
