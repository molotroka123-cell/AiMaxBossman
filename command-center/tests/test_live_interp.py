"""Live call interpreter (Cicada ideas on Bossman's local stack): segmenter, router, session, pult routes.
Synthetic audio (tones/silence) and a scripted ASR; the real faster-whisper/Marian path is proven on the owner PC."""
from __future__ import annotations

import math
from array import array
from pathlib import Path

import pytest

from bcc.features import live_interp as feat
from bcc.live_interp import LiveSession, NoSpeech, Router, Segmenter
from bcc.live_interp.capture import downmix_resample
from bcc.live_interp.segmenter import RATE, frame_dbfs
from bcc.owner_stop import global_stop_path
from bcc.tools import REGISTRY


def tone(ms: int, amp: int = 8000, hz: int = 220) -> bytes:
    n = RATE * ms // 1000
    return array("h", (int(amp * math.sin(2 * math.pi * hz * i / RATE)) for i in range(n))).tobytes()


def silence(ms: int) -> bytes:
    return bytes(RATE * ms // 1000 * 2)


def speech_stream(*phrase_ms: int, gap_ms: int = 900) -> bytes:
    out = silence(300)
    for ms in phrase_ms:
        out += tone(ms) + silence(gap_ms)
    return out


class ScriptedASR:
    """Returns scripted results in order; task="translate" answers from `english`."""

    def __init__(self, heard, english=None):
        self.heard = list(heard)
        self.english = list(english or [])
        self.calls = []

    def __call__(self, wav, task="transcribe"):
        self.calls.append(task)
        if task == "translate":
            if not self.english:
                raise NoSpeech()
            return {"text": self.english.pop(0)}
        item = self.heard.pop(0)
        if item is None:
            raise NoSpeech()
        if isinstance(item, Exception):
            raise item
        return item


class Upper:
    pairs = frozenset({("en", "ru")})

    def __call__(self, text):
        return "RU:" + text


# ---------------------------------------------------------------- segmenter

def test_segmenter_cuts_phrases_at_silence_with_correct_times():
    seg = Segmenter()
    utts = seg.feed(speech_stream(1200, 900)) + seg.flush()
    assert len(utts) == 2
    assert abs(utts[0].start_ms - 300) <= 180 and abs(utts[0].duration_ms - 1200) <= 240
    assert utts[1].start_ms > utts[0].end_ms


def test_segmenter_is_chunk_size_independent():
    whole = Segmenter().feed(speech_stream(1000, 700, 1500))
    parts, seg = [], Segmenter()
    data = speech_stream(1000, 700, 1500)
    for i in range(0, len(data), 3202):  # odd frame boundaries
        parts += seg.feed(data[i:i + 3202])
    assert [(u.start_ms, u.end_ms) for u in parts] == [(u.start_ms, u.end_ms) for u in whole]


def test_segmenter_drops_clicks_and_cuts_monologues():
    seg = Segmenter(max_ms=3000)
    assert seg.feed(silence(200) + tone(150) + silence(900)) == []          # click < min_ms
    long = seg.feed(tone(7000)) + seg.flush()
    assert len(long) >= 2 and all(u.duration_ms <= 3000 for u in long)


def test_quiet_noise_is_not_speech():
    assert Segmenter().feed(tone(2000, amp=40) + silence(900)) == []
    assert frame_dbfs(array("h", [0] * 480)) == -120.0


def test_odd_byte_count_refused():
    with pytest.raises(ValueError):
        Segmenter().feed(b"\x00\x01\x02")


def test_downmix_resample_48k_stereo_to_16k_mono():
    raw = array("h", [1000, 3000] * 4800).tobytes()          # 0.1 s stereo 48 kHz
    out = array("h")
    out.frombytes(downmix_resample(raw, 2, 48000, []))
    assert 1590 <= len(out) <= 1601 and set(out) == {2000}


# ---------------------------------------------------------------- router

def test_router_paths_and_honest_quality():
    r = Router([Upper()])
    assert r.translate("привет", source="ru", target="ru").quality == "source"
    assert r.translate("hello", source="en", target="ru").text == "RU:hello"
    two = r.translate("hola", source="es", target="ru", english_of=lambda: "hello")
    assert (two.text, two.quality) == ("RU:hello", "two_hop")
    to_en = r.translate("hola", source="es", target="en", english_of=lambda: "hello")
    assert (to_en.text, to_en.quality) == ("hello", "direct")
    none = Router([]).translate("hola", source="es", target="ru", english_of=lambda: "hello")
    assert (none.text, none.quality) == ("hola", "unsupported")     # nothing invented


def test_router_broken_model_reports_failed_and_keeps_original():
    class Broken(Upper):
        def __call__(self, text):
            raise RuntimeError("weights")
    t = Router([Broken()]).translate("hello", source="en", target="ru")
    assert (t.text, t.quality) == ("hello", "failed")


# ---------------------------------------------------------------- session

def _session(asr, **kw):
    events = []
    s = LiveSession(session_id="t1", target="ru", asr=asr, router=Router([Upper()]), sink=events.append, **kw)
    return s, events


def test_session_subtitles_translation_and_export():
    asr = ScriptedASR([{"text": "hello team", "language": "en", "confidence": 0.9},
                       {"text": "hola", "language": "es", "confidence": 0.8}], english=["hi"])
    s, events = _session(asr)
    subs = s.feed(speech_stream(1000, 1000))
    s.end("owner")
    assert [e["translation"] for e in subs] == ["RU:hello team", "RU:hi"]
    assert [e["quality"] for e in subs] == ["direct", "two_hop"]
    assert events[-1] == {"type": "ended", "session_id": "t1", "reason": "owner"}
    srt = s.export_srt()
    assert srt.startswith("1\n00:00:00,") and "RU:hello team\nhello team" in srt
    md = s.export_markdown()
    assert "→ RU:hi _(two_hop)_" in md and "Аудио не сохранялось" in md
    assert s.summary()["audio_stored"] is False and s.summary()["stored"] is False


def test_no_speech_and_asr_errors_skip_a_phrase_not_the_call():
    asr = ScriptedASR([None, RuntimeError("VOICE_BUSY"), {"text": "ok", "language": "ru"}])
    s, events = _session(asr)
    subs = s.feed(speech_stream(800, 800, 800))
    assert [e["text"] for e in subs] == ["ok"] and not s.ended
    assert any(e["type"] == "asr_error" for e in events)


def test_owner_stop_ends_session_before_the_next_phrase():
    flag = {"stop": False}
    asr = ScriptedASR([{"text": "one", "language": "ru"}, {"text": "two", "language": "ru"}])
    s, _ = _session(asr, stop_check=lambda: flag["stop"])
    s.feed(speech_stream(800))
    flag["stop"] = True
    assert s.feed(speech_stream(800)) == [] and s.ended == "owner_stop"
    assert [x["text"] for x in s.segments] == ["one"] and asr.calls == ["transcribe"]


def test_silence_timeout_ends_a_forgotten_session():
    now = {"t": 0.0}
    s, _ = _session(ScriptedASR([]), clock=lambda: now["t"], idle_end_s=600)
    s.feed(silence(1000))
    assert not s.ended
    now["t"] = 601
    s.feed(silence(1000))
    assert s.ended == "silence_timeout"


def test_transcript_on_disk_only_when_asked(tmp_path):
    asr = ScriptedASR([{"text": "a", "language": "ru"}, {"text": "b", "language": "ru"}])
    s, _ = _session(asr)
    s.feed(speech_stream(800))
    assert list(tmp_path.iterdir()) == []
    kept, _ = _session(asr, store_dir=tmp_path)
    kept.feed(speech_stream(800))
    files = list(tmp_path.iterdir())
    assert [f.name for f in files] == ["t1.jsonl"] and b"RIFF" not in files[0].read_bytes()


# ---------------------------------------------------------------- pult routes (real app)

async def test_no_agent_tool_can_listen_to_calls(env):
    assert not [n for n in REGISTRY.names() if "interp" in n or "live_interp" in n]


async def test_routes_end_to_end(env):
    env.svc._live_interp_asr = ScriptedASR([{"text": "hello", "language": "en", "confidence": 0.9}])
    r = await env.client.post("/api/live-interp/start", json={"target": "ru", "capture": False})
    assert r.status_code == 200, r.text
    again = await env.client.post("/api/live-interp/start", json={"target": "ru", "capture": False})
    assert again.status_code == 409
    fed = await env.client.post("/api/live-interp/feed", content=speech_stream(1000))
    assert fed.status_code == 200 and fed.json()["subtitles"] == 1
    ev = (await env.client.get("/api/live-interp/events")).json()["events"]
    assert ev[0]["type"] == "subtitle" and ev[0]["text"] == "hello" and ev[0]["quality"] in ("direct", "unsupported")
    md = await env.client.get("/api/live-interp/export?fmt=md")
    assert md.status_code == 200 and "hello" in md.text
    st = (await env.client.get("/api/live-interp/status")).json()
    assert st["agent_tool"] is False and st["audio_stored"] is False and st["active"]["segments"] == 1
    done = (await env.client.post("/api/live-interp/stop")).json()
    assert done["ended"] == "owner" and done["segments"] == 1
    assert (await env.client.post("/api/live-interp/feed", content=tone(200))).status_code == 409
    assert not (Path(env.svc.settings.data_dir) / "live_interp").exists()      # keep_transcript was off


async def test_start_refused_under_owner_stop_and_stop_ends_running_session(env):
    env.svc._live_interp_asr = ScriptedASR([{"text": "a", "language": "ru"}, {"text": "b", "language": "ru"}])
    stop = global_stop_path(env.svc.settings.data_dir)
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("x")
    try:
        assert (await env.client.post("/api/live-interp/start", json={"capture": False})).status_code == 409
    finally:
        stop.unlink()
    assert (await env.client.post("/api/live-interp/start", json={"capture": False})).status_code == 200
    assert (await env.client.post("/api/live-interp/feed", content=speech_stream(800))).json()["subtitles"] == 1
    stop.write_text("x")
    try:
        r = (await env.client.post("/api/live-interp/feed", content=speech_stream(800))).json()
    finally:
        stop.unlink()
    assert r == {"subtitles": 0, "ended": "owner_stop"}


async def test_bad_input_refused(env):
    env.svc._live_interp_asr = ScriptedASR([])
    assert (await env.client.post("/api/live-interp/start", json={"target": "1x", "capture": False})).status_code == 400
    assert (await env.client.post("/api/live-interp/start", json={"capture": False})).status_code == 200
    assert (await env.client.post("/api/live-interp/feed", content=b"\x00")).status_code == 400
    assert (await env.client.post("/api/live-interp/feed", content=bytes(feat.MAX_FEED_BYTES + 2))).status_code == 400
    assert (await env.client.get("/api/live-interp/export?fmt=exe")).status_code == 400
