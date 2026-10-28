"""MCP stdio servers (audit 28.10): the official SDK puts every spawned server in a Windows Job Object, but only when
pywin32 is importable (it silently falls back to a plain process otherwise). Pin both halves so an upgrade cannot drop it."""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")


def test_pywin32_is_importable_so_the_sdk_job_object_is_active():
    import win32job  # noqa: F401


def test_sdk_still_creates_a_job_object_per_server():
    import mcp.os.win32.utilities as u
    assert callable(getattr(u, "_create_job_object", None))
    assert hasattr(u, "_process_jobs")
