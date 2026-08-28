"""Typed failure codes for AI 3D Maker.

Every refusal in this application carries a machine-readable code so that a
caller (BOSSMAN control plane, CLI, HTTP client) can distinguish
"this is broken" from "this is unavailable" from "this is unsafe".
"""

from __future__ import annotations


class Ai3dError(Exception):
    """Base error. Carries a stable code and a human message."""

    code = "AI3D_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "detail": self.detail}


class UnsafePathError(Ai3dError):
    """Requested path escapes the job sandbox or uses a forbidden name."""

    code = "UNSAFE_PATH"


class InvalidSpecError(Ai3dError):
    """DesignSpec failed schema/semantic validation."""

    code = "INVALID_SPEC"


class MeshLoadError(Ai3dError):
    """Mesh file could not be parsed. Corrupt, truncated or unsupported."""

    code = "MESH_LOAD_FAILED"


class CapabilityUnavailableError(Ai3dError):
    """A required external tool or optional dependency is not installed."""

    code = "CAPABILITY_UNAVAILABLE"


class NotPrintableError(Ai3dError):
    """Geometry exists but failed printability gating."""

    code = "NOT_PRINTABLE"


class JobNotFoundError(Ai3dError):
    code = "JOB_NOT_FOUND"


class JobCancelledError(Ai3dError):
    code = "JOB_CANCELLED"


class JobTimeoutError(Ai3dError):
    code = "JOB_TIMEOUT"


class DiskQuotaError(Ai3dError):
    code = "DISK_QUOTA_EXCEEDED"


class ConfirmationRequiredError(Ai3dError):
    """A physical printer action was requested without explicit human confirmation."""

    code = "PHYSICAL_CONFIRMATION_REQUIRED"


class UnsafeGcodeError(Ai3dError):
    code = "UNSAFE_GCODE"
