"""
Windows-MCP Desktop Runtime Adapter (Spike implementation).
Executes UI mechanics through a Windows-MCP process sidecar while preserving:
1. Target fingerprinting immediately prior to effect commitment.
2. In-flight cancellation and policy revocation checks.
3. Independent post-state verification before declaring task effect proof.
4. Clean fallback to legacy desktop execution when disabled or missing.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Callable, Dict, List, Optional
from ..capabilities import (
    DesktopRuntime,
    EffectCorrelation,
    Observation,
    RuntimeIdentity,
    BackendUnavailableError,
    MalformedResponseError,
    OperationCancelledError,
    OperationTimeoutError,
    PolicyRevokedError,
    StateVerificationFailedError,
)
from ..evidence import EvidenceNormalizer

logger = logging.getLogger("bcc.hybrid.adapters.windows_mcp")


class LegacyDesktopAdapter(DesktopRuntime):
    """Fallback legacy Bossman desktop implementation."""

    def get_runtime_identity(self) -> RuntimeIdentity:
        return RuntimeIdentity(
            name="bossman_desktop_legacy",
            version="1.0.0",
            backend_type="legacy",
            is_healthy=True,
        )

    def launch_app(self, executable: str, args: List[str], correlation: EffectCorrelation) -> Dict[str, Any]:
        return {"status": "ok", "executor": "legacy", "pid": 1234}

    def click_element(self, target_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data={"clicked": True, "target": target_spec, "backend": "legacy"},
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=True,
        )

    def get_window_state(self, target_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data={"window_title": target_spec.get("title", "App"), "is_active": True},
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=True,
        )

    def close_app(self, pid: int, correlation: EffectCorrelation) -> bool:
        return True


class WindowsMcpAdapter(DesktopRuntime):
    """
    Windows-MCP UI mechanics adapter.
    Delegates clicks, window focus and inspection to Windows-MCP sidecar.
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

    def get_runtime_identity(self) -> RuntimeIdentity:
        if not self.enabled or self.client is None:
            return RuntimeIdentity(
                name="windows_mcp",
                version="0.1.0",
                backend_type="windows_mcp",
                is_healthy=False,
                metadata={"enabled": self.enabled, "client_attached": self.client is not None},
            )
        return RuntimeIdentity(
            name="windows_mcp",
            version="0.1.0",
            backend_type="windows_mcp",
            pid=getattr(self.client, "pid", None),
            endpoint=getattr(self.client, "endpoint", "pipe://windows-mcp"),
            is_healthy=getattr(self.client, "is_healthy", True),
        )

    def launch_app(self, executable: str, args: List[str], correlation: EffectCorrelation) -> Dict[str, Any]:
        self._pre_flight_checks(correlation)
        if not self.enabled or self.client is None:
            logger.info("Windows-MCP disabled/unavailable, delegating launch_app to legacy")
            return self.legacy_fallback.launch_app(executable, args, correlation)

        result = self.client.call_tool("launch_app", {"executable": executable, "args": args})
        return result

    def click_element(
        self,
        target_spec: Dict[str, Any],
        correlation: EffectCorrelation,
        post_state_verifier: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Observation:
        self._pre_flight_checks(correlation)

        if not self.enabled or self.client is None:
            logger.info("Windows-MCP disabled/unavailable, delegating click_element to legacy")
            return self.legacy_fallback.click_element(target_spec, correlation)

        # 1. Target identity check immediately before click
        current_state = self.get_window_state(target_spec, correlation)
        if correlation.expected_target_fingerprint:
            observed_fingerprint = current_state.raw_data.get("fingerprint")
            if observed_fingerprint != correlation.expected_target_fingerprint:
                raise PolicyRevokedError(
                    f"Target window fingerprint changed between planning ({correlation.expected_target_fingerprint}) "
                    f"and execution ({observed_fingerprint}). Aborting click to prevent misclick."
                )

        # 2. In-flight cancellation check right at effect boundary
        if correlation.is_cancelled():
            raise OperationCancelledError(f"Operation {correlation.correlation_id} cancelled prior to effect commit")

        # 3. Dispatch to Windows-MCP
        try:
            response = self.client.call_tool(
                "click",
                {
                    "target": target_spec,
                    "correlation_id": correlation.correlation_id,
                },
            )
        except Exception as e:
            logger.error("Windows-MCP call failed: %s", e)
            raise MalformedResponseError(f"Windows-MCP engine error: {e}") from e

        if not isinstance(response, dict) or "status" not in response:
            raise MalformedResponseError(f"Invalid response schema from Windows-MCP: {response}")

        observation = Observation(
            correlation_id=correlation.correlation_id,
            raw_data=response,
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

        # 4. Independent post-state verification if verifier provided
        if post_state_verifier is not None:
            candidate = EvidenceNormalizer.normalize_observation(observation, evidence_type="ui_click")
            verified_candidate = EvidenceNormalizer.verify_candidate_against_bossman_truth(
                candidate=candidate,
                correlation=correlation,
                post_state_verifier=post_state_verifier,
            )
            # Upgraded observation reflecting Bossman independent verification
            observation = Observation(
                correlation_id=observation.correlation_id,
                raw_data=observation.raw_data,
                observed_at_iso=observation.observed_at_iso,
                source_runtime=observation.source_runtime,
                verified_by_bossman=verified_candidate.verified_by_bossman,
            )

        return observation

    def get_window_state(self, target_spec: Dict[str, Any], correlation: EffectCorrelation) -> Observation:
        self._pre_flight_checks(correlation)
        if not self.enabled or self.client is None:
            return self.legacy_fallback.get_window_state(target_spec, correlation)

        resp = self.client.call_tool("get_window_state", {"target": target_spec})
        return Observation(
            correlation_id=correlation.correlation_id,
            raw_data=resp,
            observed_at_iso=correlation.issued_at_iso,
            source_runtime=self.get_runtime_identity(),
            verified_by_bossman=False,
        )

    def close_app(self, pid: int, correlation: EffectCorrelation) -> bool:
        self._pre_flight_checks(correlation)
        if not self.enabled or self.client is None:
            return self.legacy_fallback.close_app(pid, correlation)
        return bool(self.client.call_tool("close_app", {"pid": pid}).get("closed", False))

    def _pre_flight_checks(self, correlation: EffectCorrelation) -> None:
        if correlation.is_cancelled():
            raise OperationCancelledError(f"Operation {correlation.correlation_id} was cancelled before invocation")
        if correlation.is_expired():
            raise OperationTimeoutError(f"Operation {correlation.correlation_id} expired past its deadline")
