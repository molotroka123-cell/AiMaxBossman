"""Runtime configuration, read from the environment with safe defaults."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_ROOT.parents[1]

PROFILES_ROOT = PACKAGE_ROOT / "_profiles"
if not PROFILES_ROOT.is_dir():
    PROFILES_ROOT = APP_ROOT / "profiles"
DEFAULT_PROFILE = PROFILES_ROOT / "elegoo_neptune_3_plus.json"
DEFAULT_MATERIALS = PROFILES_ROOT / "material_defaults.json"


def _data_dir() -> Path:
    explicit = os.getenv("AI3D_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser()
    shared = os.getenv("BOSSMAN_APPS_DATA")
    if shared:
        return Path(shared).expanduser() / "ai-3d-maker"
    if (APP_ROOT / "pyproject.toml").is_file():
        return APP_ROOT / "data"
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Bossman" / "Apps" / "ai-3d-maker"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Bossman" / "Apps" / "ai-3d-maker"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "bossman" / "apps" / "ai-3d-maker"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    printer_profile: Path = field(default_factory=lambda: Path(os.getenv("AI3D_PRINTER_PROFILE") or DEFAULT_PROFILE))
    material_profile: Path = field(default_factory=lambda: Path(os.getenv("AI3D_MATERIAL_PROFILE") or DEFAULT_MATERIALS))
    data_dir: Path = field(default_factory=_data_dir)
    openscad_bin: str = field(default_factory=lambda: os.getenv("AI3D_OPENSCAD_BIN", "openscad"))
    curaengine_bin: str = field(default_factory=lambda: os.getenv("AI3D_CURAENGINE_BIN", "CuraEngine"))
    cura_definition: str = field(default_factory=lambda: os.getenv("AI3D_CURA_DEFINITION", ""))
    prusaslicer_bin: str = field(default_factory=lambda: os.getenv("AI3D_PRUSASLICER_BIN", "prusa-slicer"))
    host: str = field(default_factory=lambda: os.getenv("AI3D_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("AI3D_PORT", 8890))
    strict_gcode: bool = field(default_factory=lambda: _env_bool("AI3D_STRICT_GCODE", True))

    # Resource limits.
    job_timeout_s: float = field(default_factory=lambda: _env_float("AI3D_JOB_TIMEOUT_S", 120.0))
    job_disk_quota_bytes: int = field(default_factory=lambda: _env_int("AI3D_JOB_DISK_QUOTA_BYTES", 256 * 1024 * 1024))
    total_disk_quota_bytes: int = field(default_factory=lambda: _env_int("AI3D_TOTAL_DISK_QUOTA_BYTES", 4 * 1024 * 1024 * 1024))
    max_upload_bytes: int = field(default_factory=lambda: _env_int("AI3D_MAX_UPLOAD_BYTES", 128 * 1024 * 1024))
    max_triangles: int = field(default_factory=lambda: _env_int("AI3D_MAX_TRIANGLES", 4_000_000))
    max_jobs_retained: int = field(default_factory=lambda: _env_int("AI3D_MAX_JOBS_RETAINED", 500))

    # Physical safety. Never enable by configuration alone: physical actions
    # additionally require a per-job human confirmation token.
    allow_physical_print: bool = field(default_factory=lambda: _env_bool("AI3D_ALLOW_PHYSICAL_PRINT", False))
    printer_transport: str = field(default_factory=lambda: os.getenv("AI3D_PRINTER_TRANSPORT", "simulator"))
    printer_media_dir: str = field(default_factory=lambda: os.getenv("AI3D_PRINTER_MEDIA_DIR", ""))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    def ensure_dirs(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()
