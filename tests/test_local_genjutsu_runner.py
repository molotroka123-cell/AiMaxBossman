"""Regression tests for WanGP process supervision; no models or media are run."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from tools.motion_studio import local_genjutsu


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.001, seconds)


class FakeProcess:
    """An already-completed or stalled synthetic child, never an OS process."""

    def __init__(self, mode: str, clock: FakeClock) -> None:
        self.mode = mode
        self.clock = clock
        self.pid = 2_000_000_000  # Deliberately invalid; never a real process ID.
        self.returncode = None if mode == "timeout" else (7 if mode == "failure" else 0)
        self.killed = False
        self.terminated = False
        self.waited_after_stop = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            if timeout is None:
                raise AssertionError("A live child must not be waited for without a bound")
            self.clock.sleep(timeout)
            raise subprocess.TimeoutExpired("synthetic-wgp", timeout)
        if self.killed or self.terminated:
            self.waited_after_stop = True
        return self.returncode


def setup_child(monkeypatch: pytest.MonkeyPatch, outdir: Path, mode: str) -> FakeProcess:
    clock = FakeClock()
    process = FakeProcess(mode, clock)
    monkeypatch.setattr(local_genjutsu, "time", clock)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=128 * 2**30))

    def fake_popen(*args, **kwargs):
        # A writer may leave an MP4 even when it stalls or exits with an error.
        if mode in {"timeout", "failure"}:
            (outdir / "partial.mp4").write_bytes(b"incomplete synthetic output")
        if mode == "success":
            (outdir / "new.mp4").write_bytes(b"new synthetic output")
            kwargs["stdout"].write(b"Queue completed\n")
            kwargs["stdout"].flush()
        return process

    # No actual subprocess or process-tree command may run from these tests.
    def fake_run(args, *a, **kw):
        if Path(str(args[0])).name.lower() not in {"taskkill", "taskkill.exe"}:
            raise AssertionError(f"Unexpected subprocess: {args[0]}")
        process.kill()
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(local_genjutsu.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_genjutsu.subprocess, "run", fake_run)
    monkeypatch.setattr(local_genjutsu.os, "kill", lambda *args: process.kill())
    return process


def test_timeout_stops_and_reaps_child_and_never_returns_partial_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    child = setup_child(monkeypatch, outdir, "timeout")

    with pytest.raises((TimeoutError, RuntimeError), match=r"(?i)timeout|timed out|time limit"):
        local_genjutsu.run_wgp(tmp_path / "settings.json", outdir, timeout_s=0.2)

    assert child.killed or child.terminated
    assert child.waited_after_stop


def test_nonzero_exit_never_returns_old_or_partial_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "old.mp4").write_bytes(b"stale synthetic output")
    setup_child(monkeypatch, outdir, "failure")

    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        local_genjutsu.run_wgp(tmp_path / "settings.json", outdir, timeout_s=0.2)


def test_success_returns_newly_created_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    setup_child(monkeypatch, outdir, "success")
    monkeypatch.setattr(local_genjutsu, "_validate_wgp_output", lambda path: None, raising=False)

    output = local_genjutsu.run_wgp(tmp_path / "settings.json", outdir, timeout_s=0.2)

    assert output == outdir / "new.mp4"
    assert output.read_bytes() == b"new synthetic output"


def test_success_without_new_output_does_not_reuse_old_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "old.mp4").write_bytes(b"stale synthetic output")
    setup_child(monkeypatch, outdir, "empty_success")

    with pytest.raises(RuntimeError):
        local_genjutsu.run_wgp(tmp_path / "settings.json", outdir, timeout_s=0.2)


def test_real_synthetic_child_is_stopped_and_reaped_at_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real local process boundary without launching WanGP or a model."""
    script = tmp_path / "wgp.py"
    script.write_text(
        "import os, pathlib, sys, time\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
        "(out / 'pid.txt').write_text(str(os.getpid()))\n"
        "(out / 'partial.mp4').write_bytes(b'partial')\n"
        "time.sleep(60)\n", encoding="utf-8",
    )
    monkeypatch.setattr(local_genjutsu, "WGP", tmp_path)
    monkeypatch.setattr(local_genjutsu, "PY", Path(sys.executable))
    outdir = tmp_path / "out"
    with pytest.raises(TimeoutError):
        local_genjutsu.run_wgp(tmp_path / "settings.json", outdir, free_kill_gb=0, timeout_s=2)
    pid_file = outdir / "pid.txt"
    assert pid_file.is_file(), "the synthetic child did not start"
    assert not psutil.pid_exists(int(pid_file.read_text(encoding="utf-8")))


def test_output_validator_rejects_partial_mp4(tmp_path: Path) -> None:
    partial = tmp_path / "partial.mp4"
    partial.write_bytes(b"partial")
    with pytest.raises(RuntimeError, match="(?i)invalid|decode"):
        local_genjutsu._validate_wgp_output(partial)


def test_output_validator_accepts_decodable_synthetic_video(tmp_path: Path) -> None:
    output = tmp_path / "valid.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=blue:s=64x96:r=4:d=0.5", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(output)], check=True)
    local_genjutsu._validate_wgp_output(output)
