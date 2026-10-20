"""Branch 6 «Computer»: PC-Agent child-process cleanup and record_screen guards (2026-10-20).

Fakes only: no real command is spawned, no screen is captured, Telegram is never opened.
"""
from __future__ import annotations

import asyncio

import pytest

import bcc.features.pc_agent as pca

from .test_pc_agent_argon import fake_svc  # noqa: F401 — fixture


class HangingProc:
    """A child that never finishes on its own (e.g. a stuck `git` waiting for input)."""

    def __init__(self, pid=424242):
        self.pid = pid
        self.returncode = None
        self.killed = False
        self._done = asyncio.Event()

    async def communicate(self):
        await self._done.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done.set()

    async def wait(self):
        await self._done.wait()
        return self.returncode


@pytest.fixture
def hanging(monkeypatch):
    proc = HangingProc()
    tree_kills: list[int] = []

    async def fake_exec(*argv, **kw):
        return proc

    monkeypatch.setattr(pca.asyncio, "create_subprocess_exec", fake_exec)
    # never call the real taskkill with a fake pid (neither via the helper nor directly)
    monkeypatch.setattr(pca, "_taskkill_tree", lambda pid: tree_kills.append(pid), raising=False)
    monkeypatch.setattr(pca.subprocess, "run",
                        lambda argv, **kw: tree_kills.append(int(argv[-1])))
    return proc, tree_kills


async def _start_run_command(fake_svc, timeout_s=60):  # noqa: F811
    rt = pca.get_runtime(fake_svc)
    job = asyncio.create_task(pca.execute_pc_step(
        fake_svc, rt, task_id=901, run_id=901, step_idx=0, tool="pc.run_command",
        args={"argv": ["git", "status"], "timeout_s": timeout_s}, approved=True))
    for _ in range(200):
        if rt.active_procs:
            break
        await asyncio.sleep(0.005)
    assert rt.active_procs, "command never started"
    return rt, job


async def test_cancelled_run_command_kills_the_child_tree(fake_svc, hanging):  # noqa: F811
    proc, tree_kills = hanging
    rt, job = await _start_run_command(fake_svc)
    job.cancel()                                   # STOP / outer timeout cancels the step
    with pytest.raises(asyncio.CancelledError):
        await job
    assert proc.killed is True, "the child kept running after its step was cancelled"
    assert tree_kills == [proc.pid]
    assert proc.pid not in rt.active_procs


async def test_timed_out_run_command_kills_and_reaps_the_child(fake_svc, hanging):  # noqa: F811
    proc, tree_kills = hanging
    rt, job = await _start_run_command(fake_svc, timeout_s=1)
    res = await asyncio.wait_for(job, timeout=10)
    assert res["ok"] is False and "таймаут" in res["error"]
    assert proc.killed is True and tree_kills == [proc.pid]
    assert proc.pid not in rt.active_procs


# ------------------------------------------------------------------ pc.record_screen guards (fakes only)

class FakeStdin:
    def __init__(self):
        self.frames = 0

    def write(self, data):
        self.frames += 1

    def close(self):
        pass


class FakeFfmpeg:
    hang = False
    made: list["FakeFfmpeg"] = []

    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.pid = 515151
        self.stdin = FakeStdin()
        self.killed = False
        FakeFfmpeg.made.append(self)

    def wait(self, timeout=None):
        import subprocess
        if FakeFfmpeg.hang and not self.killed:
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture
def capture(monkeypatch):
    """Screen grab, ffmpeg and window enumeration are fakes: nothing real is captured."""
    from PIL import Image, ImageGrab

    grabs = {"n": 0}

    def fake_grab(*a, **k):
        grabs["n"] += 1
        return Image.new("RGB", (64, 48), (10, 20, 30))

    FakeFfmpeg.made = []
    FakeFfmpeg.hang = False
    monkeypatch.setattr(ImageGrab, "grab", fake_grab)
    monkeypatch.setattr(pca.subprocess, "Popen", FakeFfmpeg)
    monkeypatch.setattr(pca.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(pca.tc, "_top_windows", lambda: [])
    monkeypatch.setattr(pca.tc, "_bind_default_desktop", lambda: None)
    monkeypatch.setattr(pca, "_capture_protected_windows", lambda: [], raising=False)
    monkeypatch.setattr(pca, "_foreground_capture_protected", lambda: False, raising=False)
    return grabs


async def _record(fake_svc, **args):  # noqa: F811
    import time
    rt = pca.get_runtime(fake_svc)
    return rt, await pca._execute_record_screen(fake_svc, rt, task_id=950, t0=time.perf_counter(),
                                                args={"duration_s": 1.5, "fps": 5, **args})


async def test_record_screen_refuses_when_a_capture_protected_window_is_visible(
        fake_svc, capture, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pca, "_capture_protected_windows", lambda: ["Secret chat"])
    _rt, res = await _record(fake_svc)
    assert res["ok"] is False and res.get("protected") is True
    assert "защищённые от захвата" in res["error"]
    assert capture["n"] == 0, "the screen was grabbed despite a protected window"
    assert FakeFfmpeg.made == [], "an encoder was started despite a protected window"


async def test_record_screen_stops_and_discards_when_a_protected_window_comes_forward(
        fake_svc, capture, monkeypatch):  # noqa: F811
    calls = {"n": 0}

    def protected_after_two_frames():
        calls["n"] += 1
        return calls["n"] > 2

    monkeypatch.setattr(pca, "_foreground_capture_protected", protected_after_two_frames)
    rt, res = await _record(fake_svc)
    assert res["ok"] is False and res.get("protected") is True
    assert FakeFfmpeg.made and FakeFfmpeg.made[0].stdin.frames == 2
    assert not list((rt.data_dir / "pc-agent" / "recordings").glob("*.mp4"))
    assert not list((rt.data_dir / "pc-agent" / "recordings").glob("*.png"))


async def test_record_screen_kills_an_encoder_that_does_not_exit(fake_svc, capture, monkeypatch):  # noqa: F811
    tree_kills: list[int] = []
    monkeypatch.setattr(pca, "_taskkill_tree", lambda pid: tree_kills.append(pid))
    FakeFfmpeg.hang = True
    rt, res = await _record(fake_svc)
    enc = FakeFfmpeg.made[0]
    assert enc.killed is True, "a hung ffmpeg was left running after the recording step"
    assert tree_kills == [enc.pid]
    assert enc.pid not in rt.active_procs
    assert res["verified"] is False                 # no valid MP4 was produced


async def test_record_screen_normal_encoder_exit_is_not_killed(fake_svc, capture, monkeypatch):  # noqa: F811
    monkeypatch.setattr(pca, "_taskkill_tree", lambda pid: pytest.fail("killed a healthy encoder"))
    _rt, _res = await _record(fake_svc)
    assert FakeFfmpeg.made[0].killed is False


def test_protection_probe_is_fail_closed(monkeypatch):
    """A window whose affinity cannot be read counts as protected; a closed one is skipped."""
    monkeypatch.setattr(pca.os, "name", "nt")
    monkeypatch.setattr(pca.tc, "_top_windows", lambda: [(1, "Notepad", 10), (2, "Chat", 11),
                                                          (3, "Gone", 12)])
    monkeypatch.setattr(pca, "_display_affinity", lambda h: {1: 0, 2: None, 3: None}[h])
    monkeypatch.setattr(pca.tc, "_is_window", lambda h: h != 3)
    assert pca._capture_protected_windows() == ["Chat"]
    monkeypatch.setattr(pca, "_display_affinity", lambda h: {1: 0, 2: 0x11, 3: 0}[h])
    assert pca._capture_protected_windows() == ["Chat"]          # WDA_EXCLUDEFROMCAPTURE
    monkeypatch.setattr(pca, "_display_affinity", lambda h: 0)
    assert pca._capture_protected_windows() == []

    def boom():
        raise RuntimeError("no uia")
    monkeypatch.setattr(pca.tc, "_top_windows", boom)
    assert pca._capture_protected_windows(), "enumeration failure must not mean 'nothing protected'"
