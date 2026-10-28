"""Music 2.0 (27.10): measured mastering and the save -> master wiring.

The mastering result must carry MEASURED loudness (EBU R128 via FFmpeg), land within the tolerance of the preset's
target, keep the true peak under the ceiling, and a different preset must land on its own target (negative control).
Synthetic audio only (FFmpeg-generated noise); no owner media.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bcc.features import music_studio as music

FFMPEG = music._find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is required for real loudness measurement")


def _noise(path: Path, seconds: int = 12, gain: float = 1.0) -> Path:
    # Pink noise + a kick-like low sine: dense enough for loudnorm to behave like on music. `gain` scales the WHOLE mix.
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.08:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=55:duration={seconds}",
                    "-filter_complex", f"[0:a][1:a]amix=inputs=2:normalize=0,volume={gain}",
                    "-ar", "44100", "-ac", "2", str(path)],
                   check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_measure_loudness_reads_real_numbers(tmp_path: Path) -> None:
    quiet = music.measure_loudness(_noise(tmp_path / "quiet.wav", gain=0.1))
    loud = music.measure_loudness(_noise(tmp_path / "loud.wav", gain=1.0))
    assert 11 <= quiet["duration_s"] <= 13
    assert loud["lufs"] > quiet["lufs"] + 10          # a 10x amplitude is ~20 dB louder: the meter is real
    assert loud["true_peak_dbtp"] > quiet["true_peak_dbtp"]


@needs_ffmpeg
def test_hype_master_lands_on_minus_9_with_true_peak_under_ceiling(tmp_path: Path) -> None:
    res = music.master_audio_track(_noise(tmp_path / "src.wav"), tmp_path / "out.mp3", preset="hype")
    assert res["target_lufs"] == -9.0
    assert abs(res["measured_lufs"] - -9.0) <= music.LUFS_TOLERANCE, res
    assert res["true_peak_dbtp"] <= -0.5, res          # mp3 re-encode can add a little overshoot
    assert res["within_tolerance"] is True
    # the reported number is the file's real loudness, re-measured independently
    assert abs(music.measure_loudness(Path(res["output_path"]))["lufs"] - res["measured_lufs"]) < 0.2


@needs_ffmpeg
def test_negative_control_streaming_preset_keeps_its_own_minus_14_target(tmp_path: Path) -> None:
    res = music.master_audio_track(_noise(tmp_path / "src.wav"), tmp_path / "out.mp3", preset="studio_veo")
    assert res["target_lufs"] == -14.0
    assert abs(res["measured_lufs"] - -14.0) <= music.LUFS_TOLERANCE, res


class _Req:
    def __init__(self, body, data_dir: Path):
        self._body = body
        emitted = []
        self.app = SimpleNamespace(state=SimpleNamespace(svc=SimpleNamespace(
            settings=SimpleNamespace(data_dir=str(data_dir)),
            bus=SimpleNamespace(emit=lambda *a, **k: _noop(emitted, a, k)))))

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


async def _noop(store, a, k):
    store.append((a, k))


def test_save_rejects_unknown_master_preset_before_touching_the_provider(tmp_path: Path, monkeypatch) -> None:
    async def boom(*a, **k):
        raise AssertionError("provider must not be called for an invalid request")
    monkeypatch.setattr(music, "task", boom)
    with pytest.raises(HTTPException) as e:
        asyncio.run(music.save("t1", _Req({"master": "loudest-ever"}, tmp_path)))
    assert e.value.status_code == 422


def _fake_download(monkeypatch, payload: bytes) -> None:
    async def fake_task(task_id, request):
        return {"status": "completed", "outputs": ["/v1/audio?path=x.mp3"]}

    class _Resp:
        content = payload

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(music, "task", fake_task)
    monkeypatch.setattr(music.httpx, "AsyncClient", _Client)


@needs_ffmpeg
def test_save_with_master_returns_measured_master_next_to_the_raw_track(tmp_path: Path, monkeypatch) -> None:
    src = _noise(tmp_path / "gen.wav")
    _fake_download(monkeypatch, src.read_bytes())
    out = asyncio.run(music.save("t2", _Req({"master": "hype"}, tmp_path / "data")))
    assert Path(out["path"]).is_file()                                  # raw track kept
    assert out["master"]["status"] == "mastered"
    assert Path(out["master"]["output_path"]).is_file()
    assert abs(out["master"]["measured_lufs"] - -9.0) <= music.LUFS_TOLERANCE


def test_save_without_master_keeps_the_old_contract(tmp_path: Path, monkeypatch) -> None:
    _fake_download(monkeypatch, b"RIFF" + b"\0" * 4096)
    out = asyncio.run(music.save("t3", _Req(None, tmp_path / "data")))
    assert out["status"] == "saved" and "master" not in out
