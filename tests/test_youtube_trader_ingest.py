import pytest

from tools.youtube_trader_ingest import (
    Cue,
    attach_future_outcomes,
    extract_video_id,
    parse_vtt,
    transcript_near,
)


def test_extract_video_id_short_url_with_extra_query():
    assert extract_video_id("https://youtu.be/wbl-wFVtmmk?is=TkS5yz0kwCaVa0iM") == "wbl-wFVtmmk"


def test_extract_video_id_watch_url():
    assert extract_video_id("https://www.youtube.com/watch?v=wbl-wFVtmmk&t=10") == "wbl-wFVtmmk"


def test_extract_video_id_rejects_non_youtube_host():
    with pytest.raises(ValueError):
        extract_video_id("https://example.com/watch?v=wbl-wFVtmmk")


def test_parse_vtt_and_nearby_transcript():
    text = """WEBVTT

00:00:10.000 --> 00:00:12.000
OI is dropping here

00:00:20.000 --> 00:00:23.000
CVD is selling but price holds
"""
    cues = parse_vtt(text)
    assert cues == [
        Cue(10.0, 12.0, "OI is dropping here"),
        Cue(20.0, 23.0, "CVD is selling but price holds"),
    ]
    nearby = transcript_near(cues, 20.0, radius=5.0)
    assert "CVD is selling" in nearby
    assert "OI is dropping" not in nearby


def test_future_outcomes_use_later_video_frames():
    rows = [
        {"timestamp_seconds": 0.0, "observation": {"chart_price": 100.0}},
        {"timestamp_seconds": 300.0, "observation": {"chart_price": 101.0}},
        {"timestamp_seconds": 900.0, "observation": {"chart_price": 99.0}},
    ]
    attach_future_outcomes(rows, horizons=(300, 900))
    assert rows[0]["future_outcomes"]["300s"]["return_pct"] == 1.0
    assert rows[0]["future_outcomes"]["900s"]["return_pct"] == -1.0
