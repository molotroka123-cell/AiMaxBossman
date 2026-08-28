"""CRM client implementations: disabled, mock and real HTTP."""

from __future__ import annotations

from datetime import datetime

from ..config import CrmKind, Settings
from ..errors import EgressBlocked, VisionError
from ..logging_setup import get_logger
from ..secretstore import Secret, scrub
from .base import CrmClient, CrmContext, CrmDescriptor

log = get_logger("crm")


class DisabledCrm(CrmClient):
    """No CRM. Says so loudly instead of returning a plausible empty context."""

    def __init__(self) -> None:
        self.descriptor = CrmDescriptor(
            kind=CrmKind.DISABLED.value,
            is_mock=True,
            detail="no CRM configured; every context is marked available=false",
        )

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        return CrmContext(available=False, source="disabled", is_mock=True)

    async def aclose(self) -> None:
        return None


class MockCrm(CrmClient):
    """Scripted CRM for tests and demos. Always flagged as mock."""

    def __init__(self, contexts: list[CrmContext] | None = None, default: CrmContext | None = None) -> None:
        self.descriptor = CrmDescriptor(
            kind=CrmKind.MOCK.value,
            is_mock=True,
            detail="scripted CRM answers; not a real clinic system",
        )
        self._contexts = list(contexts or [])
        self._default = default or CrmContext(available=True, source="mock", is_mock=True)
        self._index = 0
        self.calls = 0

    def push(self, context: CrmContext) -> None:
        self._contexts.append(context)

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        self.calls += 1
        if self._index < len(self._contexts):
            value = self._contexts[self._index]
            self._index += 1
        else:
            value = self._default
        return CrmContext(**{**value.__dict__, "is_mock": True, "available": value.available})

    async def aclose(self) -> None:
        return None


class HttpCrm(CrmClient):
    """Real outbound HTTP to a clinic CRM.

    Refuses to make any call unless egress was explicitly enabled, so the
    default deployment sends nothing anywhere.
    """

    ENDPOINT = "/api/bossman/room-context"

    def __init__(
        self,
        base_url: str,
        token: Secret,
        *,
        timeout: float,
        egress_enabled: bool,
    ) -> None:
        self.descriptor = CrmDescriptor(
            kind=CrmKind.GENERIC_HTTP.value,
            is_mock=False,
            detail="real HTTP CRM",
            base_url=base_url,
        )
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._egress_enabled = egress_enabled
        self._client = None

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        if not self._egress_enabled:
            raise EgressBlocked("CRM egress is disabled; set AWV_CRM_EGRESS_ENABLED=true to allow it")
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token.reveal()}"
        try:
            response = await self._client.get(
                f"{self._base_url}{self.ENDPOINT}",
                params={"room_id": room_id, "at": at.isoformat()},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise VisionError(f"CRM request failed: {scrub(exc)}") from None
        if not isinstance(payload, dict):
            raise VisionError("CRM returned a non-object payload")
        return CrmContext(
            available=True,
            source=str(payload.get("source", "crm"))[:80],
            is_mock=False,
            employee_id=str(payload.get("employee_id", ""))[:80],
            clinician_id=str(payload.get("clinician_id", ""))[:80],
            shift_active=bool(payload.get("shift_active", False)),
            appointment_id=str(payload.get("appointment_id", ""))[:80],
            appointment_active=bool(payload.get("appointment_active", False)),
            planned_service=str(payload.get("planned_service", ""))[:160],
            confirmed_service=str(payload.get("confirmed_service", ""))[:160],
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def build_crm(settings: Settings) -> CrmClient:
    if settings.crm_kind is CrmKind.GENERIC_HTTP:
        return HttpCrm(
            settings.crm_base_url,
            settings.crm_token,
            timeout=settings.crm_timeout,
            egress_enabled=settings.privacy.crm_egress_enabled,
        )
    if settings.crm_kind is CrmKind.MOCK:
        return MockCrm()
    return DisabledCrm()
