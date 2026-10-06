"""The batch limit applies to videos inside the date window, not to the raw channel listing."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import k1m6a_youtube_batch as batch  # noqa: E402


def _listing() -> str:
    rows = [{"id": f"new{i}", "upload_date": "20260929", "title": "24/7"} for i in range(5)]
    rows += [{"id": f"old{i}", "upload_date": f"202608{15 + i:02d}", "title": f"stream {i}"} for i in range(6)]
    return "\n".join(json.dumps(r) for r in rows)


def test_limit_counts_only_in_window_videos(monkeypatch):
    seen = {}

    def fake_run(cmd, *, timeout):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, _listing(), "")

    monkeypatch.setattr(batch.shutil, "which", lambda name: "yt-dlp")
    monkeypatch.setattr(batch, "_run", fake_run)
    rows = batch.discover("https://example.invalid/@x/streams", dt.date(2026, 8, 14), dt.date(2026, 8, 27), limit=3)
    assert [r["video_id"] for r in rows] == ["old0", "old1", "old2"]
    assert "--playlist-end" not in seen["cmd"], "cutting the listing before the date filter found nothing"


def test_no_limit_returns_every_in_window_video(monkeypatch):
    monkeypatch.setattr(batch.shutil, "which", lambda name: "yt-dlp")
    monkeypatch.setattr(batch, "_run", lambda cmd, *, timeout: subprocess.CompletedProcess(cmd, 0, _listing(), ""))
    rows = batch.discover("https://example.invalid/@x/streams", dt.date(2026, 8, 14), dt.date(2026, 8, 27))
    assert len(rows) == 6
