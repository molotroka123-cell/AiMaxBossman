import json

from tools import youtube_trader_ingest_lessons as lessons


def test_auto_merges_original_captions_and_local_asr_with_provenance(tmp_path, monkeypatch):
    (tmp_path / "subs.video.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
        "Price is testing the level here\n", encoding="utf-8")
    (tmp_path / "asr.segments.json").write_text(json.dumps([
        {"start": 1.0, "end": 3.0, "text": "Wait for a close above value area high"},
    ]), encoding="utf-8")
    prompts = []

    def fake_ollama(_model, prompt):
        prompts.append(prompt)
        return {"items": [{"line_id": 1, "quote": "Wait for a close above value area high",
                           "class": "transferable_principle", "learning_value": 4,
                           "idea": "Wait for confirmation"}]}, 0.01

    monkeypatch.setattr(lessons, "ollama", fake_ollama)
    result = lessons.run(tmp_path, window_seconds=240)

    assert result["source"] == ["original_subtitles", "local_asr"]
    assert result["accepted_items"] == 1
    assert result["items"][0]["evidence_source"] == "local_asr"
    assert "[original_subtitles]" in prompts[0]
    assert "[local_asr]" in prompts[0]


def test_auto_uses_captions_when_no_local_asr_exists(tmp_path, monkeypatch):
    (tmp_path / "subs.video.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
        "Price is testing the level here\n", encoding="utf-8")
    monkeypatch.setattr(lessons, "ollama", lambda *_: ({"items": []}, 0.01))

    result = lessons.run(tmp_path)

    assert result["source"] == ["original_subtitles"]
    assert result["windows"] == 1


def test_explicit_asr_source_fails_closed_when_asr_is_missing(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError, match="local asr"):
        lessons.run(tmp_path, transcript_source="asr")
