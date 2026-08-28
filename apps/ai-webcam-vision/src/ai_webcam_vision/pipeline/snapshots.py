"""Privacy-safe snapshots.

Off by default. When enabled, a snapshot is a downscaled, grayscale, blurred
still produced inside ffmpeg — the full-resolution image never reaches this
process. Files are written 0600 and the directory is pruned to a fixed count.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import PrivacyConfig
from ..errors import PrivacyDenied
from ..logging_setup import get_logger
from ..transport.base import FrameSource

log = get_logger("snapshots")


@dataclass(frozen=True)
class SnapshotResult:
    path: Path
    bytes_written: int
    max_width: int
    blur_sigma: float
    grayscale: bool = True

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "bytes": self.bytes_written,
            "max_width": self.max_width,
            "blur_sigma": self.blur_sigma,
            "grayscale": self.grayscale,
        }


class SnapshotStore:
    def __init__(self, directory: Path, privacy: PrivacyConfig) -> None:
        self.directory = Path(directory)
        self.privacy = privacy

    @property
    def enabled(self) -> bool:
        return self.privacy.snapshots_enabled

    def policy(self) -> dict:
        return {
            "enabled": self.privacy.snapshots_enabled,
            "max_width": self.privacy.snapshot_max_width,
            "blur_sigma": self.privacy.snapshot_blur_sigma,
            "retention": self.privacy.snapshot_retention,
            "grayscale": True,
            "note": "downscaled, grayscale and blurred in ffmpeg; never full resolution",
        }

    async def capture(self, source: FrameSource) -> SnapshotResult:
        if not self.privacy.snapshots_enabled:
            raise PrivacyDenied("snapshots are disabled; set AWV_SNAPSHOTS_ENABLED=true to allow them")
        payload = await source.grab_snapshot_jpeg(
            self.privacy.snapshot_max_width,
            self.privacy.snapshot_blur_sigma,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = self.directory / f"snapshot-{stamp}.jpg"
        with open(path, "wb") as handle:
            handle.write(payload)
        path.chmod(0o600)
        self.prune()
        return SnapshotResult(
            path=path,
            bytes_written=len(payload),
            max_width=self.privacy.snapshot_max_width,
            blur_sigma=self.privacy.snapshot_blur_sigma,
        )

    def list(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("snapshot-*.jpg"))

    def prune(self) -> int:
        files = self.list()
        excess = len(files) - self.privacy.snapshot_retention
        removed = 0
        for path in files[:max(0, excess)]:
            try:
                path.unlink()
                removed += 1
            except OSError:  # pragma: no cover - defensive
                log.warning("could not remove snapshot")
        return removed
