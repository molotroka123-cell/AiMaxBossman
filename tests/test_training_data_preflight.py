"""training_data_preflight on SYNTHETIC data only (generated here; no owner photos, captions or voice)."""
from __future__ import annotations

import json
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import training_data_preflight as tdp  # noqa: E402

Image = pytest.importorskip("PIL.Image")


def _photo(folder: Path, name: str, side: int = 640, shade: int = 10, caption: str | None = "a synthetic caption") -> None:
    Image.new("RGB", (side, side), (shade, shade, shade)).save(folder / f"{name}.png")
    if caption is not None:
        (folder / f"{name}.txt").write_text(caption, encoding="utf-8")


def _wav(folder: Path, name: str, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(folder / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))


def test_well_formed_data_is_ready_and_manifest_has_no_text_or_audio(tmp_path):
    photos, voice = tmp_path / "p", tmp_path / "v"
    photos.mkdir()
    voice.mkdir()
    for i in range(3):
        _photo(photos, f"img{i}", shade=10 + i * 40, caption=f"secret caption {i}")
    _wav(voice, "a", 40)
    _wav(voice, "b", 30)
    out = tmp_path / "m.json"
    assert tdp.main(["--photos", str(photos), "--voice", str(voice), "--out", str(out),
                     "--expect-photos", "3", "--min-voice-minutes", "1"]) == 0
    text = out.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["verdict"] == "READY" and data["cost_usd"] == 0 and data["gpu"] == "none"
    assert data["voice"]["total_minutes"] == pytest.approx(70 / 60, abs=0.01)
    assert "secret caption" not in text            # lengths only, never caption text


def test_every_kind_of_defect_is_named(tmp_path):
    photos, voice = tmp_path / "p", tmp_path / "v"
    photos.mkdir()
    voice.mkdir()
    _photo(photos, "ok", shade=30)
    _photo(photos, "nocap", shade=60, caption=None)
    _photo(photos, "empty", shade=90, caption="  ")
    _photo(photos, "small", side=100, shade=120)
    _photo(photos, "dup", shade=30)                # byte-identical to "ok"
    (photos / "orphan.txt").write_text("x", encoding="utf-8")
    (photos / "raw.heic").write_bytes(b"x")
    _wav(voice, "short", 1.0)
    res = tdp.run(photos, voice, expect_photos=45, min_voice_minutes=10)
    blob = " | ".join(res["photos"]["problems"] + res["voice"]["problems"])
    for needle in ("no caption file nocap.txt", "empty caption", "shorter side 100", "ok.png: byte-identical to dup.png",
                   "caption without an image", "HEIC", "expected 45 photos", "shorter than", "< required 10"):
        assert needle in blob, needle
    assert res["verdict"] == "NEEDS_FIX"


def test_missing_folders_and_nothing_to_check_are_not_ready(tmp_path):
    assert tdp.run(tmp_path / "nope", None, None, None)["verdict"] == "NEEDS_FIX"
    assert tdp.run(None, tmp_path / "nope", None, None)["verdict"] == "NEEDS_FIX"
    assert tdp.run(None, None, None, None)["verdict"] == "NEEDS_FIX"
