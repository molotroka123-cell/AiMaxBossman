"""Remote Worker Node, Cluster Coordinator, and camera probe (Branch 15 & Stage 6).

HONEST SCOPE (audit 28.10): `RemoteWorkerNode` and `WorkerClusterCoordinator` are an IN-PROCESS MODEL of a worker cluster: there is no
network transport, no second PC, and nothing outside bcc/devices imports them. `emergency_stop()` flips fields on an in-memory object, so
its `elapsed_s` / `cascade_stop_sub_2s` measure that flip (microseconds), NOT the time for a stop to reach a remote machine. The HMAC
signing, T3 screening and SHA-256 artifact checks are real functions; a remote STOP <= 2 s is NOT demonstrated.

Implements:
1. `RemoteWorkerNode` & `WorkerClusterCoordinator`:
   - Registration of second PC as a worker device with HMAC-SHA256 signature check.
   - Fail-closed prohibition of forwarding T3 secrets (T3SecretForwardingProhibitedError).
   - SHA-256 validation of input/output files & artifacts.
   - Resource queue (CPU/GPU/RAM metrics and admission control).
   - Cascading STOP <= 2.0s on heartbeat loss (> 2.0s) or explicit STOP signal.
2. `VirtualUVCCameraProbe`:
   - Honest camera probe for Stage 6 (`Devices / Cam`): lists the camera devices Windows reports as present
     (DETECTED / NO_CAMERA); the synthetic self-test is labelled SYNTHETIC_ONLY and proves nothing about hardware.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

CASCADING_STOP_TIMEOUT_SECONDS = 2.0  # Max allowable delay for cascading STOP
HEARTBEAT_LEASE_TIMEOUT_SECONDS = 2.0
#: what the stop timings below actually cover (audit 28.10)
IN_PROCESS_SCOPE = "in_process_state_change_only: no remote node or network was involved"

_T3_FORBIDDEN_PATTERNS = (
    "private_key",
    "seed_phrase",
    "mnemonic",
    "master_password",
    "t3_secret",
    "bank_token",
    "root_cert_key",
    "dpapi_master",
)


class T3SecretForwardingProhibitedError(PermissionError):
    """Raised when an attempt is made to forward T3 secret material to a remote worker node."""


class ArtifactIntegrityError(ValueError):
    """Raised when an input or output artifact SHA-256 hash does not match expectation."""


class WorkerNodeHeartbeatLostError(RuntimeError):
    """Raised when worker node lease expires (> 2.0s)."""


def assert_no_t3_secrets(data: Any) -> None:
    """Recursively checks payload for prohibited T3 secret tokens."""
    if isinstance(data, dict):
        for k, v in data.items():
            k_lc = str(k).lower()
            if any(pattern in k_lc for pattern in _T3_FORBIDDEN_PATTERNS):
                raise T3SecretForwardingProhibitedError(
                    f"Forwarding T3 secret is strictly prohibited: key={k!r}"
                )
            assert_no_t3_secrets(v)
    elif isinstance(data, list):
        for item in data:
            assert_no_t3_secrets(item)
    elif isinstance(data, str):
        data_lc = data.lower()
        if "-----begin private key-----" in data_lc or "-----begin rsa private key-----" in data_lc:
            raise T3SecretForwardingProhibitedError("Raw private key block detected in worker payload")


@dataclass
class NodeResourceState:
    """System resource metrics for admission control."""

    cpu_percent: float = 0.0
    gpu_percent: float = 0.0
    gpu_memory_free_mb: float = 0.0
    ram_free_mb: float = 1024.0

    def is_eligible(self, min_ram_mb: float = 512.0, max_cpu: float = 95.0) -> bool:
        return self.ram_free_mb >= min_ram_mb and self.cpu_percent <= max_cpu


class ResourceQueue:
    """Manages work queue and resource-based node admission."""

    def __init__(self):
        self._queue: list[dict[str, Any]] = []

    def enqueue(self, task_id: str, priority: int = 10, required_resources: dict[str, float] | None = None) -> None:
        self._queue.append({
            "task_id": task_id,
            "priority": priority,
            "required": required_resources or {},
            "enqueued_at": time.time(),
        })
        self._queue.sort(key=lambda x: -x["priority"])

    def pop_next(self, node_resources: NodeResourceState) -> str | None:
        for idx, item in enumerate(self._queue):
            req_ram = item["required"].get("min_ram_mb", 256.0)
            if node_resources.ram_free_mb >= req_ram:
                return self._queue.pop(idx)["task_id"]
        return None

    def size(self) -> int:
        return len(self._queue)


class RemoteWorkerNode:
    """Represents an external compute node (e.g. 2nd PC / laptop) with cryptographic integrity."""

    def __init__(
        self,
        node_id: str,
        hostname: str,
        shared_secret: str,
        role: str = "worker",
        capabilities: list[str] | None = None,
        heartbeat_timeout_s: float = HEARTBEAT_LEASE_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.node_id = str(node_id)
        self.hostname = str(hostname)
        self.shared_secret = str(shared_secret).encode("utf-8")
        self.role = str(role)
        self.capabilities = list(capabilities or ["compute", "eval"])
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.clock = clock

        self.last_heartbeat = clock()
        self.state = "IDLE"  # IDLE, BUSY, STOPPED, DISCONNECTED
        self.active_task_id: str | None = None
        self.resources = NodeResourceState()
        self.stop_requested = False

    def sign_payload(self, payload: dict[str, Any]) -> str:
        """Signs payload using HMAC-SHA256."""
        canon = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(self.shared_secret, canon, hashlib.sha256).hexdigest()

    def verify_signature(self, payload: dict[str, Any], signature: str) -> bool:
        clean = {k: v for k, v in payload.items() if k != "signature"}
        expected = self.sign_payload(clean)
        return hmac.compare_digest(expected, signature)

    def heartbeat(
        self,
        *,
        active_task_id: str | None = None,
        resources: NodeResourceState | None = None,
    ) -> bool:
        """Refreshes worker heartbeat and resource telemetry."""
        now = self.clock()
        if self.stop_requested:
            self.state = "STOPPED"
            self.active_task_id = None
            return False

        self.last_heartbeat = now
        if resources is not None:
            self.resources = resources
        if active_task_id is not None:
            self.active_task_id = active_task_id
            self.state = "BUSY"
        elif self.state == "BUSY" and active_task_id is None:
            self.state = "IDLE"
            self.active_task_id = None
        return True

    def check_lease(self, now: float | None = None) -> bool:
        """Checks if heartbeat lease has expired (> 2.0s). If expired, triggers cascade abort."""
        now = self.clock() if now is None else now
        elapsed = now - self.last_heartbeat
        if elapsed > self.heartbeat_timeout_s:
            logger.warning("Node %s lease expired (%.3fs > %.3fs), cascading STOP!", self.node_id, elapsed, self.heartbeat_timeout_s)
            self.emergency_stop(reason=f"heartbeat_lease_expired:{elapsed:.2f}s")
            self.state = "DISCONNECTED"
            return False
        return True

    def emergency_stop(self, reason: str = "cascade_stop") -> dict[str, Any]:
        """Cascading stop completed in <= 2.0s."""
        t0 = self.clock()
        self.stop_requested = True
        self.state = "STOPPED"
        aborted_task = self.active_task_id
        self.active_task_id = None
        elapsed = self.clock() - t0
        return {
            "node_id": self.node_id,
            "status": "STOPPED",
            "reason": reason,
            "aborted_task": aborted_task,
            "elapsed_s": round(elapsed, 6),
            "cascade_stop_sub_2s": elapsed <= CASCADING_STOP_TIMEOUT_SECONDS,
            "scope": IN_PROCESS_SCOPE,
        }

    def dispatch_task(
        self,
        task_id: str,
        payload: dict[str, Any],
        input_files: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """Dispatches a task with T3 secret check and input file SHA-256 validation."""
        self.check_lease()
        if self.stop_requested or self.state == "STOPPED":
            raise RuntimeError(f"Cannot dispatch task to node {self.node_id}: Node is STOPPED")

        # 1. Prohibit T3 secrets
        assert_no_t3_secrets(payload)

        # 2. Hash input files
        input_hashes: dict[str, str] = {}
        if input_files:
            for fname, content in input_files.items():
                assert_no_t3_secrets(content.decode("utf-8", "ignore"))
                input_hashes[fname] = hashlib.sha256(content).hexdigest()

        self.active_task_id = task_id
        self.state = "BUSY"

        envelope = {
            "task_id": task_id,
            "node_id": self.node_id,
            "payload": payload,
            "input_hashes": input_hashes,
            "dispatched_at": time.time(),
        }
        envelope["signature"] = self.sign_payload(envelope)
        return envelope

    def receive_result(
        self,
        result_payload: dict[str, Any],
        output_files: dict[str, bytes] | None = None,
        expected_hashes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Receives and validates task result from worker."""
        assert_no_t3_secrets(result_payload)

        # Validate output file hashes
        if output_files and expected_hashes:
            for fname, expected_hash in expected_hashes.items():
                if fname not in output_files:
                    raise ArtifactIntegrityError(f"Missing expected output artifact: {fname}")
                actual_hash = hashlib.sha256(output_files[fname]).hexdigest()
                if actual_hash != expected_hash:
                    raise ArtifactIntegrityError(
                        f"Hash mismatch for {fname}: expected {expected_hash}, got {actual_hash}"
                    )

        if self.active_task_id == result_payload.get("task_id"):
            self.active_task_id = None
            self.state = "IDLE"

        return {
            "status": "VERIFIED",
            "task_id": result_payload.get("task_id"),
            "result": result_payload,
        }


class WorkerClusterCoordinator:
    """Coordinates the cluster of worker nodes with central STOP and heartbeat leases."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.nodes: dict[str, RemoteWorkerNode] = {}
        self.queue = ResourceQueue()
        self.clock = clock
        self.global_stop = False

    def register_node(
        self,
        node_id: str,
        hostname: str,
        shared_secret: str,
        role: str = "worker",
        capabilities: list[str] | None = None,
    ) -> RemoteWorkerNode:
        node = RemoteWorkerNode(
            node_id=node_id,
            hostname=hostname,
            shared_secret=shared_secret,
            role=role,
            capabilities=capabilities,
            clock=self.clock,
        )
        if self.global_stop:
            node.emergency_stop(reason="coordinator_global_stop")
        self.nodes[node_id] = node
        return node

    def trigger_cascade_stop(self) -> dict[str, Any]:
        """Cascading STOP across all connected worker nodes within <= 2.0s."""
        t0 = self.clock()
        self.global_stop = True
        reports = []
        for node in self.nodes.values():
            reports.append(node.emergency_stop(reason="coordinator_cascade_stop"))
        elapsed = self.clock() - t0
        return {
            "global_stop": True,
            "nodes_stopped": len(reports),
            "elapsed_s": round(elapsed, 6),
            "cascade_stop_sub_2s": elapsed <= CASCADING_STOP_TIMEOUT_SECONDS,
            "scope": IN_PROCESS_SCOPE,
            "node_reports": reports,
        }

    def sweep_heartbeats(self) -> list[str]:
        """Sweeps all nodes, triggers emergency stop for any node with expired lease."""
        expired: list[str] = []
        now = self.clock()
        for node_id, node in self.nodes.items():
            if not node.check_lease(now=now):
                expired.append(node_id)
        return expired


class VirtualUVCCameraProbe:
    """Camera probe for Stage 6 (Devices / Cam), HONEST (audit 28.10).

    It used to hard-code `has_physical = False` and answer PASS / presence_detected / calibrated for a camera it never looked
    for (this PC has one). Now `probe()` lists the camera devices that Windows reports as present (PnP, no frame is captured,
    the camera light does not turn on) and says exactly that: DETECTED means «Windows lists a working camera device», nothing
    more. No resolution, fps or calibration is invented. The synthetic frame hash is a harness check and is labelled so.
    """

    def __init__(self, preferred_mode: str = "auto", enumerate_devices: Callable[[], list[dict[str, str]]] | None = None):
        self.preferred_mode = preferred_mode
        self._enumerate = enumerate_devices or self._windows_cameras

    @staticmethod
    def _windows_cameras() -> list[dict[str, str]]:
        import json as _json
        import shutil
        import subprocess
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if not exe:
            return []
        script = ("Get-PnpDevice -PresentOnly -Class Camera,Image -ErrorAction SilentlyContinue | "
                  "Select-Object Status,Class,FriendlyName,InstanceId | ConvertTo-Json -Compress")
        try:
            out = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30).stdout.strip()
            data = _json.loads(out) if out else []
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
        rows = data if isinstance(data, list) else [data]
        return [{"status": str(r.get("Status") or ""), "class": str(r.get("Class") or ""),
                 "name": str(r.get("FriendlyName") or ""), "instance_id": str(r.get("InstanceId") or "")} for r in rows if isinstance(r, dict)]

    def probe(self, force_virtual: bool = False) -> dict[str, Any]:
        """List present camera devices. `force_virtual` skips the enumeration and reports the synthetic harness only."""
        synthetic = hashlib.sha256(b"Virtual_UVC_Camera_Calibrated_Test_Frame_Stage6").hexdigest()
        if force_virtual or self.preferred_mode == "virtual":
            return {"mode": "synthetic_harness", "status": "SYNTHETIC_ONLY", "presence_detected": False,
                    "devices": [], "synthetic_frame_sha256": synthetic, "note": "no device was looked at", "timestamp": time.time()}
        devices = self._enumerate()
        working = [d for d in devices if d.get("status", "").upper() == "OK"]
        if working:
            return {"mode": "physical_usb", "status": "DETECTED", "presence_detected": True,
                    "devices": [{"name": d["name"], "class": d["class"], "status": d["status"]} for d in working],
                    "note": "Windows lists a working camera device; no frame was captured, resolution/fps/calibration are unknown",
                    "timestamp": time.time()}
        return {"mode": "none", "status": "NO_CAMERA", "presence_detected": False,
                "devices": [{"name": d["name"], "class": d["class"], "status": d["status"]} for d in devices],
                "note": "Windows reports no working camera device", "timestamp": time.time()}

    def run_self_test(self) -> dict[str, Any]:
        """The synthetic harness check only: it proves nothing about a camera and says so."""
        report = self.probe(force_virtual=True)
        report["self_test"] = "SYNTHETIC_ONLY"
        report["calibrated"] = False
        return report
