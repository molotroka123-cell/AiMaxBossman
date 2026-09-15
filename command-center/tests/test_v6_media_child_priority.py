"""V6 §G: FFmpeg children run below normal CPU priority on both platforms.

Not a speed claim — a scheduling rule: the owner's clicks outrank an export.
Checked deterministically by capturing what `process()` hands the OS.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from bcc.video_studio import media


class _FakeProc:
    pid = 4242
    returncode = 0

    class _Stream:
        async def readline(self):
            return b""

        async def read(self, n):
            return b""

    stdout = _Stream()
    stderr = _Stream()

    async def wait(self):
        return 0


@pytest.mark.anyio
async def test_posix_child_is_reniced_right_after_spawn(monkeypatch):
    monkeypatch.setattr(media, "WINDOWS", False)
    spawned = {}
    calls = []

    async def fake_exec(*argv, **kw):
        spawned["argv"] = argv
        spawned["kw"] = kw
        return _FakeProc()

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(media.os, "setpriority", lambda which, pid, nice: calls.append((which, pid, nice)), raising=False)
    monkeypatch.setattr(media.os, "PRIO_PROCESS", 0, raising=False)
    await media.process(["ffmpeg", "-version"], timeout=5)
    assert spawned["argv"] == ("ffmpeg", "-version")
    assert "creationflags" not in spawned["kw"]
    assert calls == [(0, 4242, media.CHILD_NICE)]


@pytest.mark.anyio
async def test_windows_child_gets_below_normal_priority_class_at_spawn(monkeypatch):
    monkeypatch.setattr(media, "WINDOWS", True)
    spawned = {}
    calls = []

    async def fake_exec(*argv, **kw):
        spawned["kw"] = kw
        return _FakeProc()

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(media.os, "setpriority", lambda *a: calls.append(a), raising=False)
    await media.process(["ffmpeg", "-version"], timeout=5)
    assert spawned["kw"]["creationflags"] == 0x4000       # BELOW_NORMAL_PRIORITY_CLASS
    assert calls == []


def test_renice_failure_never_breaks_the_export(monkeypatch):
    monkeypatch.setattr(media, "WINDOWS", False)

    def boom(*a):
        raise PermissionError("not allowed")

    monkeypatch.setattr(media.os, "setpriority", boom, raising=False)
    monkeypatch.setattr(media.os, "PRIO_PROCESS", 0, raising=False)
    assert media.lower_child_priority(1) is False


@pytest.mark.skipif(os.name == "nt" or not hasattr(os, "setpriority"), reason="POSIX only")
@pytest.mark.anyio
async def test_a_real_child_really_runs_at_lower_priority():
    proc = await asyncio.create_subprocess_exec("sleep", "2")
    try:
        assert media.lower_child_priority(proc.pid) is True
        assert os.getpriority(os.PRIO_PROCESS, proc.pid) == media.CHILD_NICE
    finally:
        proc.kill()
        await proc.wait()
