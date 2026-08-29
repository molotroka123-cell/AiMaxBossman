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


class CrmSchemaError(VisionError):
    """The CRM answered, but not in the shape the contract promises.

    Deliberately not retryable: a malformed answer will be malformed again,
    and spending the retry budget on it only delays the honest failure.
    """

    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="crm_schema_error")


def _require_bool(payload: dict, key: str) -> bool:
    """A JSON boolean, or an error.

    ``bool("false")`` is ``True``. A CRM that stringifies its booleans would
    otherwise turn every empty room into an active appointment, and the
    resulting numbers look entirely plausible.
    """
    value = payload.get(key, False)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raise CrmSchemaError(f"CRM field {key!r} must be a JSON boolean, got {type(value).__name__}")


def _require_str(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise CrmSchemaError(f"CRM field {key!r} must be a string, got {type(value).__name__}")
    return value[:limit]


def _appointment_rank(entry: dict) -> tuple[int, int]:
    """Priority within one instant: confirmed, then planned, then the rest."""
    if _require_str(entry, "confirmed_service", 160):
        return (0, 0)
    if _require_bool(entry, "appointment_active") and _require_str(entry, "planned_service", 160):
        return (1, 0)
    if _require_bool(entry, "appointment_active"):
        return (2, 0)
    return (3, 0)


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
        retries: int = 3,
        retry_base_delay: float = 0.25,
        retry_factor: float = 2.0,
        retry_max_delay: float = 5.0,
        max_age_seconds: float = 300.0,
        hard_max_age_seconds: float = 3600.0,
        sleep=None,
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
        self._retries = max(1, int(retries))
        self._retry_base_delay = retry_base_delay
        self._retry_factor = retry_factor
        self._retry_max_delay = retry_max_delay
        self._max_age = max_age_seconds
        self._hard_max_age = hard_max_age_seconds
        self._sleep = sleep
        self._client = None

    # ------------------------------------------------------------- transport
    async def _fetch(self, room_id: str, at: datetime) -> dict:
        """One bounded, retried request. Backoff is capped and finite."""
        import asyncio

        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        sleeper = self._sleep or asyncio.sleep
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token.reveal()}"

        delay = self._retry_base_delay
        last: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.get(
                    f"{self._base_url}{self.ENDPOINT}",
                    params={"room_id": room_id, "at": at.isoformat()},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - httpx raises many shapes
                last = exc
                if attempt >= self._retries:
                    break
                await sleeper(min(delay, self._retry_max_delay))
                delay *= self._retry_factor
                continue
            if not isinstance(payload, dict):
                raise CrmSchemaError("CRM returned a non-object payload")
            return payload
        raise VisionError(f"CRM request failed after {self._retries} attempts: {scrub(last)}")

    # ---------------------------------------------------------------- parsing
    def _freshness(self, payload: dict, at: datetime) -> tuple[str, bool, float | None, bool]:
        """``as_of``, staleness, age, and whether the answer still counts.

        An undated answer is taken at face value — the CRM did not claim a
        time, so inventing one would be worse than saying nothing.
        """
        raw = _require_str(payload, "as_of", 64)
        if not raw:
            return "", False, None, True
        try:
            as_of = datetime.fromisoformat(raw)
        except ValueError:
            raise CrmSchemaError("CRM field 'as_of' must be an ISO 8601 timestamp") from None
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=at.tzinfo)
        age = (at - as_of).total_seconds()
        stale = age > self._max_age
        usable = age <= self._hard_max_age
        return raw, stale, age, usable

    def _select(self, payload: dict) -> tuple[dict, int, bool]:
        """Which appointment describes this instant.

        Overlapping records are a fact of clinic scheduling, not an error.
        Picking the first one the CRM happened to list is what turns a
        confirmed extraction into a routine checkup in the report.
        """
        entries = payload.get("appointments")
        if entries is None:
            return payload, 1 if (payload.get("appointment_id") or payload.get("appointment_active")) else 0, False
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise CrmSchemaError("CRM field 'appointments' must be a list of objects")
        if not entries:
            return payload, 0, False
        ranked = sorted(entries, key=_appointment_rank)
        return {**payload, **ranked[0]}, len(entries), len(entries) > 1

    async def context(self, room_id: str, at: datetime) -> CrmContext:
        if not self._egress_enabled:
            raise EgressBlocked("CRM egress is disabled; set AWV_CRM_EGRESS_ENABLED=true to allow it")
        payload = await self._fetch(room_id, at)
        as_of, stale, age, usable = self._freshness(payload, at)
        chosen, candidates, overlapping = self._select(payload)

        source = _require_str(chosen, "source", 80) or "crm"
        if stale:
            source = f"{source}:stale"
        return CrmContext(
            # Past the hard limit an old answer is indistinguishable from no
            # answer, and must not be allowed to assert an appointment.
            available=usable,
            source=source,
            is_mock=False,
            employee_id=_require_str(chosen, "employee_id", 80),
            clinician_id=_require_str(chosen, "clinician_id", 80),
            shift_active=_require_bool(chosen, "shift_active"),
            appointment_id=_require_str(chosen, "appointment_id", 80),
            appointment_active=_require_bool(chosen, "appointment_active"),
            planned_service=_require_str(chosen, "planned_service", 160),
            confirmed_service=_require_str(chosen, "confirmed_service", 160),
            as_of=as_of,
            stale=stale,
            age_seconds=age,
            overlapping=overlapping,
            candidates=candidates,
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
            retries=settings.crm_retries,
            retry_base_delay=settings.crm_retry_base_delay,
            max_age_seconds=settings.crm_max_age,
            hard_max_age_seconds=settings.crm_hard_max_age,
        )
    if settings.crm_kind is CrmKind.MOCK:
        return MockCrm()
    return DisabledCrm()
