"""
Centralized sidecar process lifecycle abstraction for Bossman Hybrid OSS.
Provides bounded restarts, heartbeat verification, graceful shutdown,
orphan cleanup, and circular buffer log capture.

External sidecars are capability mechanics, not trusted children of Bossman.
By default they receive only a small allowlist of non-secret OS environment
variables plus values explicitly declared in SidecarConfig.env. This prevents
ambient Bossman/provider credentials from leaking across the process boundary.
"""

from __future__ import annotations
import collections
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional
from .capabilities import (
    BackendUnavailableError,
    OperationTimeoutError,
    RuntimeIdentity,
)

logger = logging.getLogger("bcc.hybrid.sidecar")


# Deliberately small. Provider credentials, PYTHONPATH, loader injection
# variables, and Bossman-specific environment values are not inherited.
_SAFE_INHERITED_ENV_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "LANG",
        "LANGUAGE",
        "TERM",
    }
)
_SAFE_INHERITED_ENV_PREFIXES = ("LC_",)


class SidecarStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRASHED = "crashed"


@dataclass(frozen=True)
class SidecarConfig:
    name: str
    command: List[str]
    working_dir: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    startup_timeout_s: float = 15.0
    heartbeat_interval_s: float = 5.0
    heartbeat_timeout_s: float = 10.0
    max_restarts: int = 3
    restart_window_s: float = 60.0
    log_buffer_size: int = 1000
    readiness_probe: Optional[Callable[[], bool]] = None
    # Escape hatch for a deliberately trusted sidecar only. Keep False for
    # third-party OSS integrations such as Windows-MCP / AI File Sorter.
    inherit_parent_env: bool = False


class SidecarProcessManager:
    """Manages an external OSS sidecar process with strict Bossman ownership."""

    def __init__(self, config: SidecarConfig) -> None:
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._status = SidecarStatus.STOPPED
        self._status_lock = threading.Lock()
        self._restart_timestamps: List[float] = []
        self._stdout_buffer: Deque[str] = collections.deque(maxlen=config.log_buffer_size)
        self._stderr_buffer: Deque[str] = collections.deque(maxlen=config.log_buffer_size)
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_heartbeat_epoch_s: float = 0.0

    @property
    def status(self) -> SidecarStatus:
        with self._status_lock:
            return self._status

    @property
    def pid(self) -> Optional[int]:
        if self._process is not None:
            return self._process.pid
        return None

    def _build_process_env(self) -> Dict[str, str]:
        """Build the child environment without ambient secret inheritance."""
        if self.config.inherit_parent_env:
            child_env = dict(os.environ)
        else:
            child_env: Dict[str, str] = {}
            for key, value in os.environ.items():
                normalized = key.upper()
                if (
                    normalized in _SAFE_INHERITED_ENV_KEYS
                    or normalized.startswith(_SAFE_INHERITED_ENV_PREFIXES)
                ):
                    child_env[key] = value

        # Explicit configuration is authoritative and intentional. Popen gets
        # an argv list and env mapping directly; no shell command is created.
        child_env.update({str(key): str(value) for key, value in self.config.env.items()})
        return child_env

    def start(self) -> bool:
        """Start sidecar process with readiness verification."""
        with self._status_lock:
            if self._status in (SidecarStatus.STARTING, SidecarStatus.HEALTHY):
                return True
            self._status = SidecarStatus.STARTING

        # Check restart budget
        now = time.time()
        self._restart_timestamps = [
            t for t in self._restart_timestamps if now - t < self.config.restart_window_s
        ]
        if len(self._restart_timestamps) >= self.config.max_restarts:
            with self._status_lock:
                self._status = SidecarStatus.DEGRADED
            logger.error("Sidecar '%s' exceeded max restarts within window", self.config.name)
            return False

        process_env = self._build_process_env()

        try:
            self._process = subprocess.Popen(
                self.config.command,
                cwd=self.config.working_dir,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            self._restart_timestamps.append(now)
        except (FileNotFoundError, PermissionError) as e:
            with self._status_lock:
                self._status = SidecarStatus.CRASHED
            logger.error("Failed to spawn sidecar '%s': %s", self.config.name, e)
            return False

        # Spawn log consumer threads
        threading.Thread(
            target=self._drain_stream,
            args=(self._process.stdout, self._stdout_buffer),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_stream,
            args=(self._process.stderr, self._stderr_buffer),
            daemon=True,
        ).start()

        # Await readiness
        deadline = time.time() + self.config.startup_timeout_s
        is_ready = False
        while time.time() < deadline:
            if self._process.poll() is not None:
                with self._status_lock:
                    self._status = SidecarStatus.CRASHED
                logger.error("Sidecar '%s' exited prematurely with exit code %s", self.config.name, self._process.returncode)
                return False

            if self.config.readiness_probe is not None:
                try:
                    if self.config.readiness_probe():
                        is_ready = True
                        break
                except Exception:
                    pass
            else:
                is_ready = True
                break
            time.sleep(0.2)

        if not is_ready:
            self.stop()
            with self._status_lock:
                self._status = SidecarStatus.CRASHED
            raise OperationTimeoutError(f"Sidecar '{self.config.name}' failed readiness check in {self.config.startup_timeout_s}s")

        with self._status_lock:
            self._status = SidecarStatus.HEALTHY
            self._last_heartbeat_epoch_s = time.time()

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._monitor_thread.start()
        return True

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop sidecar with bounded graceful SIGTERM -> SIGKILL fallback."""
        self._stop_event.set()
        if self._process is None:
            with self._status_lock:
                self._status = SidecarStatus.STOPPED
            return

        with self._status_lock:
            self._status = SidecarStatus.STOPPED

        try:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    logger.warning("Sidecar '%s' did not terminate, sending kill", self.config.name)
                    self._process.kill()
                    self._process.wait(timeout=2.0)
        except ProcessLookupError:
            pass
        finally:
            self._process = None

    def record_heartbeat(self) -> None:
        """Record a successful heartbeat from or to the sidecar."""
        with self._status_lock:
            self._last_heartbeat_epoch_s = time.time()
            if self._status == SidecarStatus.DEGRADED:
                self._status = SidecarStatus.HEALTHY

    def get_logs(self) -> Dict[str, List[str]]:
        return {
            "stdout": list(self._stdout_buffer),
            "stderr": list(self._stderr_buffer),
        }

    def _drain_stream(self, stream, buffer: Deque[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                if line:
                    buffer.append(line.rstrip("\r\n"))
        except Exception:
            pass
        finally:
            stream.close()

    def _health_loop(self) -> None:
        while not self._stop_event.wait(self.config.heartbeat_interval_s):
            if self._process is None or self._process.poll() is not None:
                with self._status_lock:
                    self._status = SidecarStatus.CRASHED
                logger.error("Sidecar '%s' process died unexpectedly", self.config.name)
                break

            now = time.time()
            if now - self._last_heartbeat_epoch_s > self.config.heartbeat_timeout_s:
                with self._status_lock:
                    self._status = SidecarStatus.DEGRADED
                logger.warning("Sidecar '%s' missed heartbeat deadline", self.config.name)
