"""resource_snapshot() must work on a platform without os.getloadavg (Windows).

Before the fix the metrics payload raised AttributeError on Windows, so every
endpoint that embeds the resource snapshot (metrics, health facts) failed.
"""
from __future__ import annotations

import os

from ai_webcam_vision.runtime.resources import resource_snapshot


def test_snapshot_without_getloadavg_reports_none_instead_of_crashing(monkeypatch):
    monkeypatch.delattr(os, "getloadavg", raising=False)
    data = resource_snapshot()
    assert data["load_average"] is None
    assert data["cpu_count"] == os.cpu_count()


def test_snapshot_when_getloadavg_is_unavailable_at_runtime(monkeypatch):
    def unavailable():
        raise OSError("load average unobtainable")

    monkeypatch.setattr(os, "getloadavg", unavailable, raising=False)
    assert resource_snapshot()["load_average"] is None
