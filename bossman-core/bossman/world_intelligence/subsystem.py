from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..lifecycle import Subsystem, SubsystemState
from .. import obs, events

try:
    import httpx
except Exception:  # pragma: no cover – optional dependency
    httpx = None


@dataclass
class PythiaWorldConfig:
    base_url: str = "http://127.0.0.1:8080"
    timeout: float = 5.0
    # Pythia is optional: critical=False → degraded mode if DOWN, not boot failure
    critical: bool = False


def build_subsystem() -> Subsystem:
    """Factory function called by _register_subsystems() in api.py."""
    return PythiaWorldSubsystem()


class PythiaWorldSubsystem:
    """World Intelligence adapter — intelligence source, NOT action authority.

    Connects to local Pythia API (127.0.0.1) and provides machine-readable
    endpoints for Bossman context, planner, and notification pipeline.

    Key design:
    - INTELLIGENCE SOURCE only: predictions → context, never direct actions
    - Fail-soft: if Pythia DOWN → degraded, Bossman continues
    - Local-first: 127.0.0.1, no public exposure
    - Events ≠ predictions: strict semantic boundary maintained
    - Context budget: relevance filtering, not dump-all
    """

    name = "world_intelligence"
    critical = False

    def __init__(self, config: PythiaWorldConfig | None = None) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = PythiaWorldConfig()
        self._client = httpx.AsyncClient(timeout=self.config.timeout) if httpx else None
        self._state: dict[str, Any] = {"status": "offline", "detail": "not probed"}
        self._last_probe: float = 0.0

    # ---- Subsystem protocol ----

    async def validate(self) -> None:
        """Check Pythia availability — optional, non-blocking.

        Does NOT raise on failure (critical=False). Only logs status.
        """
        if self._client is None:
            self._state = {"status": "offline", "detail": "httpx unavailable"}
            return
        try:
            resp = await self._client.get(f"{self.config.base_url}/health")
            if resp.status_code == 200:
                self._state = {"status": "online", "detail": "Pythia reachable"}
                self._last_probe = __import__("time").time()
            else:
                self._state = {"status": "degraded", "detail": f"HTTP {resp.status_code}"}
        except Exception as exc:  # noqa: BLE001 — сети нет или Pythia не запущен
            self._state = {"status": "offline", "detail": f"{type(exc).__name__}: {exc}"}
            # deliberately do NOT raise — critical=False

    async def start(self) -> None:
        """Probe Pythia connectivity. Idempotent — safe to call multiple times."""
        await self.validate()
        events.emit("world_intelligence.state", state=self._state)

    async def stop(self) -> None:
        """Graceful shutdown — ideempotent."""
        if self._client is not None:
            with __import__("contextlib").suppress(Exception):
                await self._client.aclose()
        events.emit("world_intelligence.stopped", state=self._state)

    # ---- Pythia API endpoints ----

    async def _get(self, path: str) -> dict[str, Any] | None:
        """Safe GET to Pythia API; returns parsed JSON or None on failure."""
        if self._client is None:
            return None
        try:
            resp = await self._client.get(f"{self.config.base_url}{path}")
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:  # noqa: BLE001
            return None

    # ---- machine-readable endpoint: /agent/view ----

    async def agent_view(self) -> dict[str, Any] | None:
        """Main machine-readable input for Bossman.

        Contains: summary, domains, events_by_domain, event_count, predictions,
        market_watch — relevance filtered, not raw dump.
        """
        data = await self._get("/agent/view")
        if data is None:
            return None
        # Ensure required fields exist with defaults
        return {
            "summary": data.get("summary", ""),
            "domains": data.get("domains", []),
            "events_by_domain": data.get("events_by_domain", {}),
            "event_count": data.get("event_count", 0),
            "predictions": data.get("predictions", []),
            "market_watch": data.get("market_watch", {}),
            "source": "pythia",
            "timestamp": __import__("time").time(),
        }

    # ---- other Pythia endpoints ----

    async def health(self) -> dict[str, Any] | None:
        return await self._get("/health")

    async def events(self) -> dict[str, Any] | None:
        return await self._get("/agent/events")

    async def predictions(self) -> dict[str, Any] | None:
        return await self._get("/predictions")

    async def world(self) -> dict[str, Any] | None:
        return await self._get("/world")

    async def health_score(self) -> dict[str, Any] | None:
        return await self._get("/health-score")

    async def state(self) -> dict[str, Any] | None:
        return await self._get("/state")

    async def state_stream(self) -> dict[str, Any] | None:
        return await self._get("/state/stream")


# Global instance — lazily instantiated
_pythia_instance: PythiaWorldSubsystem | None = None


def get_pythia() -> PythiaWorldSubsystem:
    """Get the global Pythia world intelligence instance."""
    global _pythia_instance
    if _pythia_instance is None:
        _pythia_instance = PythiaWorldSubsystem()
    return _pythia_instance


# Convenience functions for fastapi dependency injection
async def get_pythia_view() -> dict[str, Any] | None:
    """FastAPI dependency: returns /agent/view data from Pythia."""
    return get_pythia().agent_view()


async def get_pythia_health() -> dict[str, Any] | None:
    """FastAPI dependency: returns /health from Pythia."""
    return await get_pythia().health()


async def get_pythia_events() -> dict[str, Any] | None:
    """FastAPI dependency: returns /agent/events from Pythia."""
    return await get_pythia().events()


async def get_pythia_predictions() -> dict[str, Any] | None:
    """FastAPI dependency: returns /predictions from Pythia."""
    return await get_pythia().predictions()