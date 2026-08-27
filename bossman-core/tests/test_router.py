"""Маршрутизатор (9.2/9.3): жёсткие ограничения, потом качество; детерминированный."""
import pytest

from bossman.projects.router import choose, load_registry


def test_private_never_routes_to_cloud():
    route = choose("i2v", private=True, clip_seconds=5, total_clips=96)
    assert route.spec["where"] == "home"


def test_long_series_goes_to_cloud():
    # 96 клипов по 6 с: локально это ~54 часа машинного времени — облако
    route = choose("i2v", private=False, clip_seconds=6, total_clips=96)
    assert route.spec["where"] == "cloud"
    assert route.tool in ("seedance", "kling3")


def test_frames_always_home_best_quality_first():
    route = choose("frame")
    assert route.tool == "flux_local"    # качество 8 против 6 у sdxl


def test_qa_prefers_home():
    route = choose("qa_clip", total_clips=96)
    assert route.tool == "vision_qa_local"


def test_clip_length_ceiling_filters_candidates():
    with pytest.raises(LookupError):
        choose("t2v", private=True, clip_seconds=60)   # дома нет инструмента на минуту


def test_registry_has_model_windows():
    reg = load_registry()
    assert reg["model_windows"]["bossman-fast"] >= 8192
