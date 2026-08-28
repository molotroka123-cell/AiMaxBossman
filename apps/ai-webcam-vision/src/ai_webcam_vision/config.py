"""Configuration.

Two rules:

* every value is read from the environment (or an explicit mapping) at
  ``Settings.from_env()`` time, never at import time;
* secrets come from the environment only. There is no constructor argument, no
  CLI flag and no literal in code that can supply a camera password.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from .errors import ConfigError, PrivacyDenied
from .secretstore import Secret

ENV_PREFIX = "AWV_"

#: Environment variables that carry secret material. Nothing else may.
SECRET_ENV_VARS = ("AWV_CAMERA_PASSWORD", "AWV_CRM_TOKEN", "AWV_API_TOKEN")


class CameraMode(StrEnum):
    """How frames are obtained. Never ambiguous, never inferred."""

    RTSP = "rtsp"          # real camera over RTSP, real ffmpeg transport
    FILE = "file"          # local video fixture through real ffmpeg transport
    MOCK = "mock"          # synthetic frames, no transport at all


class CrmKind(StrEnum):
    DISABLED = "disabled"      # no CRM, and every consumer is told so
    MOCK = "mock"              # scripted answers, explicitly fake
    GENERIC_HTTP = "generic_http"  # real outbound HTTP


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    return str(env.get(name, default)).strip()


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name, "true" if default else "false").lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}")


def _get_float(env: Mapping[str, str], name: str, default: float, *, minimum: float | None = None) -> float:
    raw = _get(env, name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _get_int(env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    raw = _get(env, name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def parse_zone(raw: str, name: str) -> tuple[float, float, float, float]:
    parts = [p for p in raw.split(",") if p.strip() != ""]
    if len(parts) != 4:
        raise ConfigError(f"{name} must be x1,y1,x2,y2 normalised to 0..1")
    try:
        values = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise ConfigError(f"{name} must contain four numbers") from exc
    if not all(0.0 <= v <= 1.0 for v in values):
        raise ConfigError(f"{name} values must be within 0..1")
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        raise ConfigError(f"{name} must satisfy x1<x2 and y1<y2")
    return (x1, y1, x2, y2)


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 5
    base_delay: float = 0.5
    factor: float = 2.0
    max_delay: float = 30.0


@dataclass(frozen=True)
class PrivacyConfig:
    """Deny-by-default switches. Each one is read by code that can act on it."""

    recording_enabled: bool = False
    snapshots_enabled: bool = False
    snapshot_max_width: int = 160
    snapshot_blur_sigma: float = 6.0
    snapshot_retention: int = 20
    telemetry_enabled: bool = False
    crm_egress_enabled: bool = False

    # Denied by design. The build has no implementation for them at all; the
    # flags exist so that asking for them fails loudly instead of silently.
    audio_capture: bool = False
    face_identification: bool = False
    patient_identification: bool = False


@dataclass(frozen=True)
class Settings:
    room_id: str = "dental-1"

    camera_mode: CameraMode = CameraMode.MOCK
    camera_host: str = "127.0.0.1"
    camera_port: int = 554
    camera_stream: str = "stream2"
    camera_username: str = ""
    camera_password: Secret = field(default_factory=lambda: Secret("", "camera_password"))
    camera_fixture: Path | None = None

    ffmpeg_path: str = "ffmpeg"
    connect_timeout: float = 8.0
    capture_timeout: float = 15.0
    #: A frame older than this describes the past, not the present.
    max_frame_age: float = 30.0

    frame_width: int = 160
    frame_height: int = 90

    #: Run the persistent sampling loop, not only on-demand jobs.
    runtime_enabled: bool = False

    active_interval: float = 1.0
    idle_interval: float = 10.0
    max_sample_rate_hz: float = 5.0
    frame_queue_max: int = 8
    motion_hold_seconds: float = 90.0

    retry: RetryConfig = field(default_factory=RetryConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)

    chair_zone: tuple[float, float, float, float] = (0.25, 0.25, 0.78, 0.90)
    work_zone: tuple[float, float, float, float] = (0.15, 0.10, 0.90, 0.95)
    room_threshold: float = 0.035
    chair_threshold: float = 0.055
    work_threshold: float = 0.012
    debounce_samples: int = 2
    #: Temporal policy for the state machine. Counting samples alone is not
    #: hysteresis: at 5 Hz two samples is 0.4 s.
    min_dwell_seconds: float = 6.0
    clinical_dwell_seconds: float = 30.0
    turnover_dwell_seconds: float = 15.0
    turnover_lookback_seconds: float = 600.0
    dropout_grace_seconds: float = 45.0

    state_dir: Path = Path("./data")
    #: The clinic's timezone. Daily metrics are cut on its midnight.
    timezone_name: str = "UTC"

    crm_kind: CrmKind = CrmKind.DISABLED
    crm_base_url: str = ""
    crm_token: Secret = field(default_factory=lambda: Secret("", "crm_token"))
    crm_timeout: float = 5.0

    api_token: Secret = field(default_factory=lambda: Secret("", "api_token"))
    host: str = "127.0.0.1"
    port: int = 8870
    log_level: str = "INFO"
    log_file: Path | None = None

    # ---------------------------------------------------------------- paths
    @property
    def db_path(self) -> Path:
        return self.state_dir / "vision.sqlite3"

    @property
    def baseline_path(self) -> Path:
        return self.state_dir / "baseline.npy"

    @property
    def snapshot_dir(self) -> Path:
        return self.state_dir / "snapshots"

    # --------------------------------------------------------------- egress
    @property
    def uses_real_camera(self) -> bool:
        return self.camera_mode is CameraMode.RTSP

    @property
    def uses_real_crm(self) -> bool:
        return self.crm_kind is CrmKind.GENERIC_HTTP

    # ------------------------------------------------------------ rendering
    def public_dict(self) -> dict:
        """Configuration as it may be shown over the API. No secrets, ever."""
        return {
            "room_id": self.room_id,
            "camera": {
                "mode": self.camera_mode.value,
                "host": self.camera_host if self.uses_real_camera else None,
                "port": self.camera_port if self.uses_real_camera else None,
                "stream": self.camera_stream,
                "username_configured": bool(self.camera_username),
                "password_configured": bool(self.camera_password),
                "fixture": str(self.camera_fixture) if self.camera_fixture else None,
            },
            "timeouts": {
                "connect_seconds": self.connect_timeout,
                "capture_seconds": self.capture_timeout,
                "crm_seconds": self.crm_timeout,
                "max_frame_age_seconds": self.max_frame_age,
            },
            "sampling": {
                "active_interval_seconds": self.active_interval,
                "idle_interval_seconds": self.idle_interval,
                "max_sample_rate_hz": self.max_sample_rate_hz,
                "frame_queue_max": self.frame_queue_max,
                "frame_size": [self.frame_width, self.frame_height],
                "motion_hold_seconds": self.motion_hold_seconds,
                "runtime_enabled": self.runtime_enabled,
            },
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "base_delay_seconds": self.retry.base_delay,
                "factor": self.retry.factor,
                "max_delay_seconds": self.retry.max_delay,
            },
            "privacy": {
                "recording_enabled": self.privacy.recording_enabled,
                "snapshots_enabled": self.privacy.snapshots_enabled,
                "snapshot_max_width": self.privacy.snapshot_max_width,
                "snapshot_blur_sigma": self.privacy.snapshot_blur_sigma,
                "snapshot_retention": self.privacy.snapshot_retention,
                "telemetry_enabled": self.privacy.telemetry_enabled,
                "crm_egress_enabled": self.privacy.crm_egress_enabled,
                "audio_capture": self.privacy.audio_capture,
                "face_identification": self.privacy.face_identification,
                "patient_identification": self.privacy.patient_identification,
            },
            "crm": {
                "kind": self.crm_kind.value,
                "base_url_configured": bool(self.crm_base_url),
                "token_configured": bool(self.crm_token),
            },
            "storage": {"state_dir": str(self.state_dir), "timezone": self.timezone_name},
            "api": {"host": self.host, "port": self.port, "auth_required": bool(self.api_token)},
        }

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)

    # ----------------------------------------------------------- validation
    def validate(self) -> "Settings":
        if not self.room_id:
            raise ConfigError("AWV_ROOM_ID must not be empty")
        if self.camera_stream not in {"stream1", "stream2"}:
            raise ConfigError("AWV_CAMERA_STREAM must be stream1 or stream2")
        if self.camera_mode is CameraMode.RTSP and not self.camera_host:
            raise ConfigError("AWV_CAMERA_HOST is required in rtsp mode")
        if self.camera_mode is CameraMode.FILE and self.camera_fixture is None:
            raise ConfigError("AWV_CAMERA_FIXTURE is required in file mode")
        if self.max_sample_rate_hz <= 0:
            raise ConfigError("AWV_MAX_SAMPLE_RATE_HZ must be > 0")
        if self.active_interval < 1.0 / self.max_sample_rate_hz:
            raise ConfigError("AWV_ACTIVE_INTERVAL_SECONDS violates AWV_MAX_SAMPLE_RATE_HZ")
        if self.frame_queue_max < 1:
            raise ConfigError("AWV_FRAME_QUEUE_MAX must be >= 1")
        if self.crm_kind is CrmKind.GENERIC_HTTP:
            if not self.crm_base_url:
                raise ConfigError("AWV_CRM_BASE_URL is required for generic_http CRM")
            if not self.crm_base_url.startswith("https://") and not self.crm_base_url.startswith("http://"):
                raise ConfigError("AWV_CRM_BASE_URL must be an http(s) URL")
            if not self.privacy.crm_egress_enabled:
                raise ConfigError(
                    "generic_http CRM requires AWV_CRM_EGRESS_ENABLED=true (outbound "
                    "traffic is off unless explicitly configured)"
                )
        if self.privacy.audio_capture:
            raise PrivacyDenied("audio capture is denied by design in this build")
        if self.privacy.face_identification:
            raise PrivacyDenied("face identification is denied by design in this build")
        if self.privacy.patient_identification:
            raise PrivacyDenied("patient identification from pixels is denied by design")
        from .storage.store import resolve_timezone

        resolve_timezone(self.timezone_name)  # raises ConfigError on a bad name
        parse_zone(",".join(str(v) for v in self.chair_zone), "AWV_CHAIR_ZONE")
        parse_zone(",".join(str(v) for v in self.work_zone), "AWV_WORK_ZONE")
        return self

    # ----------------------------------------------------------------- load
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        mode_raw = _get(env, "AWV_CAMERA_MODE", CameraMode.MOCK.value).lower()
        try:
            camera_mode = CameraMode(mode_raw)
        except ValueError as exc:
            allowed = ", ".join(m.value for m in CameraMode)
            raise ConfigError(f"AWV_CAMERA_MODE must be one of {allowed}") from exc

        crm_raw = _get(env, "AWV_CRM_KIND", CrmKind.DISABLED.value).lower()
        if crm_raw == "none":  # accepted spelling from the legacy pack
            crm_raw = CrmKind.DISABLED.value
        try:
            crm_kind = CrmKind(crm_raw)
        except ValueError as exc:
            allowed = ", ".join(k.value for k in CrmKind)
            raise ConfigError(f"AWV_CRM_KIND must be one of {allowed}") from exc

        fixture_raw = _get(env, "AWV_CAMERA_FIXTURE", "")
        log_file_raw = _get(env, "AWV_LOG_FILE", "")

        settings = cls(
            room_id=_get(env, "AWV_ROOM_ID", "dental-1"),
            camera_mode=camera_mode,
            camera_host=_get(env, "AWV_CAMERA_HOST", "127.0.0.1"),
            camera_port=_get_int(env, "AWV_CAMERA_PORT", 554, minimum=1),
            camera_stream=_get(env, "AWV_CAMERA_STREAM", "stream2"),
            camera_username=_get(env, "AWV_CAMERA_USERNAME", ""),
            camera_password=Secret(env.get("AWV_CAMERA_PASSWORD", ""), "camera_password"),
            camera_fixture=Path(fixture_raw) if fixture_raw else None,
            ffmpeg_path=_get(env, "AWV_FFMPEG_PATH", "ffmpeg"),
            connect_timeout=_get_float(env, "AWV_CONNECT_TIMEOUT_SECONDS", 8.0, minimum=0.1),
            capture_timeout=_get_float(env, "AWV_CAPTURE_TIMEOUT_SECONDS", 15.0, minimum=0.1),
            max_frame_age=_get_float(env, "AWV_MAX_FRAME_AGE_SECONDS", 30.0, minimum=0.1),
            frame_width=_get_int(env, "AWV_FRAME_WIDTH", 160, minimum=16),
            frame_height=_get_int(env, "AWV_FRAME_HEIGHT", 90, minimum=16),
            runtime_enabled=_get_bool(env, "AWV_RUNTIME_ENABLED", False),
            active_interval=_get_float(env, "AWV_ACTIVE_INTERVAL_SECONDS", 1.0, minimum=0.0),
            idle_interval=_get_float(env, "AWV_IDLE_INTERVAL_SECONDS", 10.0, minimum=0.0),
            max_sample_rate_hz=_get_float(env, "AWV_MAX_SAMPLE_RATE_HZ", 5.0, minimum=0.001),
            frame_queue_max=_get_int(env, "AWV_FRAME_QUEUE_MAX", 8, minimum=1),
            motion_hold_seconds=_get_float(env, "AWV_MOTION_HOLD_SECONDS", 90.0, minimum=0.0),
            retry=RetryConfig(
                max_attempts=_get_int(env, "AWV_RETRY_MAX_ATTEMPTS", 5, minimum=1),
                base_delay=_get_float(env, "AWV_RETRY_BASE_DELAY_SECONDS", 0.5, minimum=0.0),
                factor=_get_float(env, "AWV_RETRY_FACTOR", 2.0, minimum=1.0),
                max_delay=_get_float(env, "AWV_RETRY_MAX_DELAY_SECONDS", 30.0, minimum=0.0),
            ),
            privacy=PrivacyConfig(
                recording_enabled=_get_bool(env, "AWV_RECORDING_ENABLED", False),
                snapshots_enabled=_get_bool(env, "AWV_SNAPSHOTS_ENABLED", False),
                snapshot_max_width=_get_int(env, "AWV_SNAPSHOT_MAX_WIDTH", 160, minimum=32),
                snapshot_blur_sigma=_get_float(env, "AWV_SNAPSHOT_BLUR_SIGMA", 6.0, minimum=0.0),
                snapshot_retention=_get_int(env, "AWV_SNAPSHOT_RETENTION", 20, minimum=1),
                telemetry_enabled=_get_bool(env, "AWV_TELEMETRY_ENABLED", False),
                crm_egress_enabled=_get_bool(env, "AWV_CRM_EGRESS_ENABLED", False),
                audio_capture=_get_bool(env, "AWV_CAPTURE_AUDIO", False),
                face_identification=_get_bool(env, "AWV_FACE_IDENTIFICATION", False),
                patient_identification=_get_bool(env, "AWV_PATIENT_IDENTIFICATION", False),
            ),
            chair_zone=parse_zone(_get(env, "AWV_CHAIR_ZONE", "0.25,0.25,0.78,0.90"), "AWV_CHAIR_ZONE"),
            work_zone=parse_zone(_get(env, "AWV_WORK_ZONE", "0.15,0.10,0.90,0.95"), "AWV_WORK_ZONE"),
            room_threshold=_get_float(env, "AWV_ROOM_THRESHOLD", 0.035, minimum=0.0),
            chair_threshold=_get_float(env, "AWV_CHAIR_THRESHOLD", 0.055, minimum=0.0),
            work_threshold=_get_float(env, "AWV_WORK_THRESHOLD", 0.012, minimum=0.0),
            debounce_samples=_get_int(env, "AWV_DEBOUNCE_SAMPLES", 2, minimum=1),
            min_dwell_seconds=_get_float(env, "AWV_MIN_DWELL_SECONDS", 6.0, minimum=0.0),
            clinical_dwell_seconds=_get_float(env, "AWV_CLINICAL_DWELL_SECONDS", 30.0, minimum=0.0),
            turnover_dwell_seconds=_get_float(env, "AWV_TURNOVER_DWELL_SECONDS", 15.0, minimum=0.0),
            turnover_lookback_seconds=_get_float(env, "AWV_TURNOVER_LOOKBACK_SECONDS", 600.0, minimum=0.0),
            dropout_grace_seconds=_get_float(env, "AWV_DROPOUT_GRACE_SECONDS", 45.0, minimum=0.0),
            state_dir=Path(_get(env, "AWV_STATE_DIR", "./data")),
            timezone_name=_get(env, "AWV_TIMEZONE", "UTC"),
            crm_kind=crm_kind,
            crm_base_url=_get(env, "AWV_CRM_BASE_URL", "").rstrip("/"),
            crm_token=Secret(env.get("AWV_CRM_TOKEN", ""), "crm_token"),
            crm_timeout=_get_float(env, "AWV_CRM_TIMEOUT_SECONDS", 5.0, minimum=0.1),
            api_token=Secret(env.get("AWV_API_TOKEN", ""), "api_token"),
            host=_get(env, "AWV_HOST", "127.0.0.1"),
            port=_get_int(env, "AWV_PORT", 8870, minimum=1),
            log_level=_get(env, "AWV_LOG_LEVEL", "INFO"),
            log_file=Path(log_file_raw) if log_file_raw else None,
        )
        return settings.validate()
