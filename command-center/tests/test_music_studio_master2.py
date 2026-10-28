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
    assert res["true_peak_dbtp"] <= -0.9, res          # oversampled limiter keeps mp3 under the ceiling (27.10 proof: was +0.4)
    assert res["within_tolerance"] is True
    # the reported number is the file's real loudness, re-measured independently
    assert abs(music.measure_loudness(Path(res["output_path"]))["lufs"] - res["measured_lufs"]) < 0.2


@needs_ffmpeg
def test_negative_control_streaming_preset_keeps_its_own_minus_14_target(tmp_path: Path) -> None:
    res = music.master_audio_track(_noise(tmp_path / "src.wav"), tmp_path / "out.mp3", preset="studio_veo")
    assert res["target_lufs"] == -14.0
    assert abs(res["measured_lufs"] - -14.0) <= music.LUFS_TOLERANCE, res


@needs_ffmpeg
def test_trailing_silence_is_cut_and_track_ends_at_the_requested_length(tmp_path: Path) -> None:
    # 16 s of music + 6 s of dead air, like ACE-Step tails measured 27.10; ask for 14 s.
    music_part = _noise(tmp_path / "m.wav", seconds=16)
    padded = tmp_path / "padded.wav"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(music_part), "-af", "apad=pad_dur=6",
                    str(padded)], check=True, capture_output=True)
    res = music.master_audio_track(padded, tmp_path / "out.mp3", preset="hype", target_duration_s=14)
    assert abs(res["duration_s"] - 14) <= 0.3, res
    log = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", res["output_path"], "-af", "silencedetect=n=-45dB:d=2",
                          "-f", "null", "-"], capture_output=True, text=True, errors="replace").stderr
    assert "silence_start" not in log


def _clicks(path: Path, bpm: float, seconds: int = 20) -> Path:
    # A kick-like 60 ms burst on every beat over quiet noise: a known tempo to measure against.
    beat = 60.0 / bpm
    expr = f"if(lt(mod(t\\,{beat:.6f})\\,0.06)\\,0.8*sin(2*PI*60*t)*exp(-40*mod(t\\,{beat:.6f}))\\,0)+0.01*random(0)"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"aevalsrc={expr}:s=44100:d={seconds}", str(path)], check=True, capture_output=True)
    return path


@needs_ffmpeg
@pytest.mark.parametrize("bpm", [128.0, 90.0])
def test_measure_tempo_finds_the_beat_of_a_known_click_track(tmp_path: Path, bpm: float) -> None:
    got = music.measure_tempo(_clicks(tmp_path / f"c{int(bpm)}.wav", bpm))
    assert got is not None and abs(got - bpm) <= 0.02 * bpm, got


@needs_ffmpeg
def test_negative_control_silence_has_no_tempo(tmp_path: Path) -> None:
    p = tmp_path / "silence.wav"
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", "10", str(p)], check=True, capture_output=True)
    assert music.measure_tempo(p) is None


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
