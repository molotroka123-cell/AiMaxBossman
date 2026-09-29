"""Planning primitives for channel-scale video-learning batches.

No network calls here. Discovery/download is delegated to yt-dlp adapters on the
owner machine; this module makes prioritization/resume deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class VideoJob:
    video_id: str
    url: str
    title: str = ""
    upload_date: str | None = None  # YYYYMMDD when yt-dlp provides it
    duration_s: float | None = None
    is_live_replay: bool = False


def age_days(job: VideoJob, now: datetime | None = None) -> int | None:
    if not job.upload_date:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        d = datetime.strptime(job.upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (now - d).days)


def priority(job: VideoJob, now: datetime | None = None) -> tuple:
    """Recent streams first; stable video id tie-break makes runs reproducible."""
    age = age_days(job, now)
    return (age is None, age if age is not None else 10**9,
            0 if job.is_live_replay else 1, job.video_id)


def plan(jobs: Iterable[VideoJob], completed_ids: set[str], *, max_videos: int | None = None,
         now: datetime | None = None) -> list[VideoJob]:
    pending = [j for j in jobs if j.video_id not in completed_ids]
    pending.sort(key=lambda j: priority(j, now))
    return pending[:max_videos] if max_videos is not None else pending


def batch(jobs: list[VideoJob], size: int) -> list[list[VideoJob]]:
    if size <= 0:
        raise ValueError("batch size must be positive")
    return [jobs[i:i + size] for i in range(0, len(jobs), size)]
