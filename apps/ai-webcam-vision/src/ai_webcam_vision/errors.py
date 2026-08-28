"""Application errors. Every message is scrubbed at construction time.

An exception text is an emission channel like any other: it reaches logs, HTTP
responses and tracebacks. Scrubbing at construction means no call site can
forget to do it.
"""

from __future__ import annotations

from .secretstore import scrub


class VisionError(Exception):
    """Base error. ``str(exc)`` and ``exc.args`` are always credential-free."""

    def __init__(self, message: object = "", *, code: str = "vision_error") -> None:
        safe = scrub(message)
        super().__init__(safe)
        self.code = code
        self.safe_message = safe

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.safe_message!r})"


class ConfigError(VisionError):
    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="config_error")


class DependencyMissing(VisionError):
    """A required external binary (ffmpeg) is absent. Report, never pretend."""

    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="dependency_missing")


class CaptureError(VisionError):
    def __init__(self, message: object = "", *, code: str = "capture_failed") -> None:
        super().__init__(message, code=code)


class CaptureTimeout(CaptureError):
    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="capture_timeout")


class BaselineMissing(VisionError):
    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="baseline_missing")


class EgressBlocked(VisionError):
    """An outbound call was attempted while egress is not explicitly enabled."""

    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="egress_blocked")


class PrivacyDenied(VisionError):
    """A capability denied by design was requested (audio, face id, recording)."""

    def __init__(self, message: object = "") -> None:
        super().__init__(message, code="privacy_denied")
