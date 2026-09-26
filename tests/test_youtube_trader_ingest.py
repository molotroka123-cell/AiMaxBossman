import pytest

from tools.youtube_trader_ingest import (
    Cue,
    attach_future_outcomes,
    build_cases,
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


def test_decision_transcript_never_uses_future_teacher_language():
    cues = [
        Cue(90.0, 99.0, "price is testing the level"),
        Cue(101.0, 104.0, "perfect winner target hit"),
    ]
    decision = transcript_near(cues, 100.0, radius=35.0)
    assert "testing the level" in decision
    assert "perfect winner" not in decision


def test_future_outcomes_use_later_video_frames():
    same_chart = {"instrument": "BTC", "venue": "CME", "timeframe": "30m"}
    rows = [
        {"timestamp_seconds": 0.0, "observation": {"chart_price": 100.0, **same_chart}},
        {"timestamp_seconds": 300.0, "observation": {"chart_price": 101.0, **same_chart}},
        {"timestamp_seconds": 900.0, "observation": {"chart_price": 99.0, **same_chart}},
    ]
    attach_future_outcomes(rows, horizons=(300, 900))
    assert rows[0]["future_outcomes"]["300s"]["return_pct"] == 1.0
    assert rows[0]["future_outcomes"]["900s"]["return_pct"] == -1.0
    assert rows[0]["future_outcomes"]["300s"]["evidence_status"] == "UNVERIFIED_VIDEO_FRAME_DELTA"


def test_chart_switch_or_missing_identity_yields_unknown_outcome():
    rows = [
        {"timestamp_seconds": 0.0, "observation": {"chart_price": 100.0, "instrument": "BTC", "venue": "CME", "timeframe": "30m"}},
        {"timestamp_seconds": 300.0, "observation": {"chart_price": 101.0, "instrument": "BTC", "venue": "Coinbase", "timeframe": "1D"}},
        {"timestamp_seconds": 900.0, "observation": {"chart_price": 99.0, "instrument": "BTC", "venue": "CME", "timeframe": "30m"}},
        {"timestamp_seconds": 1200.0, "observation": {"chart_price": 98.0, "instrument": "BTC", "venue": "CME"}},
    ]
    attach_future_outcomes(rows, horizons=(300, 900))
    assert rows[0]["future_outcomes"] == {}
    assert rows[0]["future_outcome_status"] == "UNKNOWN"
    assert rows[2]["future_outcomes"] == {}
    assert rows[2]["future_outcome_status"] == "UNKNOWN"
    cases = build_cases(rows, "6pYfEfZof3c", "test")
    assert all(case["deterministic_analysis"] is None for case in cases)
    assert cases[3]["series_identity_status"] == "UNKNOWN"
