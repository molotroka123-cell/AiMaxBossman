"""
Windows-MCP Desktop Runtime Adapter (spike implementation).

External runtimes provide mechanics only. Bossman remains authoritative for
effect-time policy, completion truth, and independent post-state verification.

IMPORTANT: the built-in LegacyDesktopAdapter below is intentionally fail-closed.
It is only a placeholder used when a real legacy Bossman desktop runtime has not
been injected. It must never simulate successful effects.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..capabilities import (
    BackendUnavailableError,
    DesktopRuntime,
    EffectCorrelation,
    MalformedResponseError,
    Observation,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyRevokedError,
    RuntimeIdentity,
)
from ..evidence import EvidenceNormalizer

logger = logging.getLogger("bcc.hybrid.adapters.windows_mcp")


class LegacyDesktopAdapter(DesktopRuntime):
    """Fail-closed placeholder for the real Bossman legacy desktop runtime.

    The hybrid spike previously returned fabricated successful effects here.
    That is unsafe because an unavailable optional backend could then appear to
    have launched/clicked/closed real applications. Production callers must
    inject the actual legacy desktop implementation explicitly.
    """

    _ERROR = (
        "No real legacy desktop runtime is configured for the hybrid adapter. "
        "Refusing to simulate a desktop effect."
    )

    def get_runtime_identity(self) -> RuntimeIdentity:
        return RuntimeIdentity(
            name="bossman_desktop_legacy_unconfigured",
            version="0",
            backend_type="legacy",
            is_healthy=False,
            metadata={"configured": False, "fail_closed": True},
        )

    def _unavailable(self, operation: str) -> None:
        raise BackendUnavailableError(f"{self._ERROR} operation={operation}")

    def launch_app(
        self,
        executable: str,
        args: List[str],
        correlation: EffectCorrelation,
    ) -> Dict[str, Any]:
        self._unavailable("launch_app")

    def click_element(
        self,
        target_spec: Dict[str, Any],
        correlation: EffectCorrelation,
    ) -> Observation:
        self._unavailable("click_element")

    def get_window_state(
        self,
        target_spec: Dict[str, Any],
        correlation: EffectCorrelation,
    ) -> Observation:
        self._unavailable("get_window_state")

    def close_app(self, pid: int, correlation: EffectCorrelation) -> bool:
        self._unavailable("close_app")


class WindowsMcpAdapter(DesktopRuntime):
    """Windows-MCP mechanics adapter with fail-closed legacy fallback.

    The optional Windows-MCP sidecar is used only when enabled, attached, and
    healthy. Otherwise execution is delegated to an explicitly configured real
    legacy runtime. If no such runtime exists, the operation is blocked instead
    of being simulated.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        legacy_fallback: Optional[DesktopRuntime] = None,
        enabled: bool = True,
    ) -> None:
        self.client = client
        self.legacy_fallback = legacy_fallback or LegacyDesktopAdapter()
        self.enabled = enabled

    def _mcp_available(self) -> bool:
        if not self.enabled or self.client is None:
            return False
        return bool(getattr(self.client, "is_healthy", True))

    def _fallback_reason(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.client is None:
            return "client_missing"
        if not bool(getattr(self.client, "is_healthy", True)):
            return "client_unhealthy"
        return "unavailable"

    def get_runtime_identity(self) -> RuntimeIdentity:
        if not self._mcp_available():
            return RuntimeIdentity(
                name="windows_mcp",
                version="0.1.0",
                backend_type="windows_mcp",
                is_healthy=False,
                metadata={
                    "enabled": self.enabled,
                    "client_attached": self.client is not None,
                    "fallback_reason": self._fallback_reason(),
                },
            )
        return RuntimeIdentity(
            name="windows_mcp",
            version="0.1.0",
            backend_type="windows_mcp",
            pid=getattr(self.client, "pid", None),
            endpoint=getattr(self.client, "endpoint", "pipe://windows-mcp"),
            is_healthy=True,
        )

    def launch_app(
        self,
        executable: str,
        args: List[str],
        correlation: EffectCorrelation,
    ) -> Dict[str, Any]:
        self._pre_flight_checks(correlation)
        if not self._mcp_available():
            logger.info(
                "Windows-MCP %s; delegating launch_app to configured legacy runtime",
                self._fallback_reason(),
            )
            return self.legacy_fallback.launch_app(executable, args, correlation)

        try:
            result = self.client.call_tool(
                "launch_app",
                {"executable": executable, "args": list(args)},
            )
        except Exception as exc:
            raise MalformedResponseError(f"Windows-MCP launch_app failed: {exc}") from exc

        if not isinstance(result, dict) or "status" not in result:
            raise MalformedResponseError(
                f"Invalid launch_app response schema from Windows-MCP: {result}"
            )
        return result

    def click_element(
        self,
        target_spec: Dict[str, Any],
        correlation: EffectCorrelation,
        post_state_verifier: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Observation:
        self._pre_flight_checks(correlation)

        if not self._mcp_available():
            logger.info(
                "Windows-MCP %s; delegating click_element to configured legacy runtime",
                self._fallback_reason(),
            )
            return self.legacy_fallback.click_element(target_spec, correlation)

        # 1. Target identity check immediately before click.
        current_state = self.get_window_state(target_spec, correlation)
        if correlation.expected_target_fingerprint:
            observed_fingerprint = current_state.raw_data.get("fingerprint")
            if observed_fingerprint != correlation.expected_target_fingerprint:
                raise PolicyRevokedError(
                    "Target window fingerprint changed between planning "
                    f"({correlation.expected_target_fingerprint}) and execution "
                    f"({observed_fingerprint}). Aborting click to prevent misclick."
                )

        # 2. In-flight cancellation check at the effect boundary.
        if correlation.is_cancelled():
            raise OperationCancelledError(
                f"Operation {correlation.correlation_id} cancelled prior to effect commit"
            )

        # 3. Dispatch mechanics to Windows-MCP.
        try:
            response = self.client.call_tool(
                "click",
                {
                    "target": target_spec,
                    "correlation_id": correlation.correlation_id,
                },
            )
        except Exception as exc:
            logger.error("Windows-MCP call failed: %s", exc)
            raise MalformedResponseError(f"Windows-MCP engine error: {exc}") from exc

        if not isinstance(response, dict) or "status" not in response:
            raise MalformedResponseError(
                f"Invalid response schema from Windows-MCP: {response}"
            )

        observation = Observation(
            correlation_id=correlation.correlation_id,
            raw_data=response,
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

        # 4. Only Bossman's independent verifier may upgrade the observation.
        if post_state_verifier is not None:
            candidate = EvidenceNormalizer.normalize_observation(
                observation,
                evidence_type="ui_click",
            )
            verified_candidate = EvidenceNormalizer.verify_candidate_against_bossman_truth(
                candidate=candidate,
                correlation=correlation,
                post_state_verifier=post_state_verifier,
            )
            observation = Observation(
                correlation_id=observation.correlation_id,
                raw_data=observation.raw_data,
                observed_at_iso=observation.observed_at_iso,
                source_runtime=observation.source_runtime,
                verified_by_bossman=verified_candidate.verified_by_bossman,
            )

        return observation

    def get_window_state(
        self,
        target_spec: Dict[str, Any],
        correlation: EffectCorrelation,
    ) -> Observation:
        self._pre_flight_checks(correlation)
        if not self._mcp_available():
            return self.legacy_fallback.get_window_state(target_spec, correlation)

        try:
            response = self.client.call_tool("get_window_state", {"target": target_spec})
        except Exception as exc:
            raise MalformedResponseError(
                f"Windows-MCP get_window_state failed: {exc}"
            ) from exc

        if not isinstance(response, dict):
            raise MalformedResponseError(
                f"Invalid get_window_state response schema from Windows-MCP: {response}"
            )

        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data=response,
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

    def close_app(self, pid: int, correlation: EffectCorrelation) -> bool:
        self._pre_flight_checks(correlation)
        if not self._mcp_available():
            return self.legacy_fallback.close_app(pid, correlation)

        try:
            response = self.client.call_tool("close_app", {"pid": pid})
        except Exception as exc:
            raise MalformedResponseError(f"Windows-MCP close_app failed: {exc}") from exc

        if not isinstance(response, dict) or "closed" not in response:
            raise MalformedResponseError(
                f"Invalid close_app response schema from Windows-MCP: {response}"
            )
        return bool(response["closed"])

    def _pre_flight_checks(self, correlation: EffectCorrelation) -> None:
        if correlation.is_cancelled():
            raise OperationCancelledError(
                f"Operation {correlation.correlation_id} was cancelled before invocation"
            )
        if correlation.is_expired():
            raise OperationTimeoutError(
                f"Operation {correlation.correlation_id} expired past its deadline"
            )
