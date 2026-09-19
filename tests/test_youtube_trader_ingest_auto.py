from tools.youtube_trader_ingest_auto import _segments_to_vtt, _vtt_ts
from tools.youtube_trader_ingest import parse_vtt


def test_vtt_timestamp_format():
    assert _vtt_ts(65.432) == "00:01:05.432"


def test_asr_segments_round_trip_to_parser():
    vtt = _segments_to_vtt({
        "segments": [
            {"start": 1.0, "end": 2.5, "text": "OI is falling"},
            {"start": 3.0, "end": 4.0, "text": "price holds"},
        ]
    })
    cues = parse_vtt(vtt)
    assert [cue.text for cue in cues] == ["OI is falling", "price holds"]
    assert cues[0].start == 1.0
    assert cues[1].end == 4.0
