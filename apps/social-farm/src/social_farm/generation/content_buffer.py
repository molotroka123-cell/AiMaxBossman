"""Rolling content-buffer accounting for autonomous streamer/media workflows."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class BufferHealth(str, Enum):
    READY = "ready"
    LOW = "low"
    CRITICAL = "critical"
    EMPTY = "empty"


@dataclass(frozen=True, slots=True)
class BufferedAsset:
    asset_id: str
    duration_seconds: float
    ready: bool = True
    blocked: bool = False
    expires_at_epoch_s: float | None = None


@dataclass(frozen=True, slots=True)
class BufferSnapshot:
    playable_seconds: float
    target_seconds: float
    health: BufferHealth
    deficit_seconds: float
    asset_count: int


def evaluate_buffer(
    assets: Iterable[BufferedAsset],
    *,
    now_epoch_s: float,
    target_seconds: float = 6 * 3600,
    low_ratio: float = 0.5,
    critical_ratio: float = 0.2,
) -> BufferSnapshot:
    if target_seconds <= 0:
        raise ValueError("target_seconds must be positive")
    if not 0 < critical_ratio < low_ratio < 1:
        raise ValueError("expected 0 < critical_ratio < low_ratio < 1")

    playable = []
    for asset in assets:
        if not asset.ready or asset.blocked or asset.duration_seconds <= 0:
            continue
        if asset.expires_at_epoch_s is not None and asset.expires_at_epoch_s <= now_epoch_s:
            continue
        playable.append(asset)

    total = sum(asset.duration_seconds for asset in playable)
    ratio = total / target_seconds
    if total <= 0:
        health = BufferHealth.EMPTY
    elif ratio < critical_ratio:
        health = BufferHealth.CRITICAL
    elif ratio < low_ratio:
        health = BufferHealth.LOW
    else:
        health = BufferHealth.READY

    return BufferSnapshot(
        playable_seconds=total,
        target_seconds=target_seconds,
        health=health,
        deficit_seconds=max(0.0, target_seconds - total),
        asset_count=len(playable),
    )


def recommended_generation_slots(
    snapshot: BufferSnapshot,
    *,
    average_asset_seconds: float,
    max_batch: int = 12,
) -> int:
    if average_asset_seconds <= 0:
        raise ValueError("average_asset_seconds must be positive")
    if max_batch < 1:
        raise ValueError("max_batch must be positive")
    if snapshot.deficit_seconds <= 0:
        return 0
    needed = int((snapshot.deficit_seconds + average_asset_seconds - 1) // average_asset_seconds)
    return min(max_batch, max(1, needed))


__all__ = [
    "BufferHealth",
    "BufferedAsset",
    "BufferSnapshot",
    "evaluate_buffer",
    "recommended_generation_slots",
]
