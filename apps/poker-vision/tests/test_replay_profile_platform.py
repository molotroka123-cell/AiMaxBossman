"""A replay is read with the profile of the renderer that produced the recording, not of the host OS.

Found 11.10: the sample frames were rendered on Linux; on Windows the host profile (profiles/win32) read the
hero card as rank_ambiguous (k-NN margin 0.067 < 0.10) while Linux CI read '6d'. The label says where the
frames come from; the service picks that profile for replay and keeps the host profile for live modes.
"""
from __future__ import annotations

import json
from pathlib import Path

from pokervision.adapters.poker_train import PROFILES_DIR, Profile, platform_profile
from pokervision.service import VisionService
from pokervision.sources import DirSource

SAMPLES = Path(__file__).resolve().parents[1] / "evidence" / "sample_frames"
NL = chr(10)


def test_dirsource_exposes_the_platform_of_the_recording(tmp_path):
    (tmp_path / "0000.png").write_bytes((SAMPLES / "0000.png").read_bytes())
    (tmp_path / "labels.jsonl").write_text(json.dumps({"frame": "0000.png", "t_ms": 1, "platform": "linux"}) + NL,
                                           encoding="utf-8")
    assert DirSource(tmp_path).platform == "linux"
    (tmp_path / "labels.jsonl").write_text(json.dumps({"frame": "0000.png", "t_ms": 1}) + NL, encoding="utf-8")
    assert DirSource(tmp_path).platform is None


def test_sample_frames_are_labelled_with_their_renderer():
    assert DirSource(SAMPLES).platform == "linux"


def test_replay_uses_the_recordings_profile_on_any_host(tmp_path):
    expected = Profile.load(platform_profile("poker_train.json", "linux")).id
    svc = VisionService(tmp_path / "data")
    svc.start("replay", adapter="poker_train", path=str(SAMPLES), max_hands=1)
    try:
        assert svc.adapter.profile.id == expected
    finally:
        svc.stop()


def test_a_win32_recording_would_use_the_win32_profile_when_it_exists():
    assert platform_profile("poker_train.json", "win32") == PROFILES_DIR / "win32" / "poker_train.json"
    assert platform_profile("poker_train.json", "linux") == PROFILES_DIR / "poker_train.json"
