"""Highlight windows, LLM-assisted ranking with heuristic fallback, schedule slots."""
from datetime import datetime, timezone

from shortsfactory import highlights, schedule
from conftest import FakeChat, fake_transcript


def _energy(duration, loud=(20, 30)):
    t = [i * 0.5 for i in range(int(duration * 2))]
    return {"t": t, "z": [1.5 if loud[0] <= x < loud[1] else -0.3 for x in t]}


def test_windows_respect_15_to_60_seconds_and_phrase_bounds():
    tr = fake_transcript(200)
    wins = highlights.candidate_windows(tr["segments"], 15, 60)
    starts = {s["start"] for s in tr["segments"]}
    ends = {s["end"] for s in tr["segments"]}
    assert wins and all(15 <= w["dur"] <= 60 for w in wins)
    assert all(w["start"] in starts and w["end"] in ends for w in wins)


def test_find_highlights_non_overlapping_with_reasons_and_llm():
    tr = fake_transcript(200)
    chat = FakeChat()
    res = highlights.find_highlights(tr, _energy(200), [25.0, 26.0], k=3, chat=chat)
    hs = res["highlights"]
    assert res["llm_status"] == "OK" and len(hs) == 3
    for a in hs:
        assert 15 <= a["dur"] <= 60 and a["why"] and a["llm"]["hook_score"] >= 0
        for b in hs:
            if a is not b:
                assert min(a["end"], b["end"]) <= max(a["start"], b["start"])     # no overlap
    assert hs == sorted(hs, key=lambda h: -h["score"])


def test_llm_down_still_ranks_by_signals_and_reports_it():
    tr = fake_transcript(120)
    res = highlights.find_highlights(tr, _energy(120), [], k=2, chat=FakeChat(fail_rate=True))
    assert res["llm_status"] == "ERROR" and "offline" in res["llm_error"] and len(res["highlights"]) == 2


def test_louder_part_is_preferred_by_signals():
    tr = fake_transcript(200)
    res = highlights.find_highlights(tr, _energy(200, loud=(120, 160)), [], k=1, chat=None)
    h = res["highlights"][0]
    assert res["llm_status"] == "NOT_CONNECTED"
    assert h["start"] < 160 and h["end"] > 120


def test_schedule_slots_per_day_unique_future():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    s = schedule.next_slots({"per_day": 2, "times": ["10:00", "18:00"], "tz": "Europe/Prague"}, [], 5, now=now)
    assert len(s) == 5 and len(set(s)) == 5 and all(x > "2026-10-17T12:15" for x in s)
    days = [x[:10] for x in s]
    assert max(days.count(d) for d in set(days)) <= 2
    s2 = schedule.next_slots({"per_day": 2, "times": ["10:00", "18:00"]}, [s[0]], 1, now=now)
    assert s2[0] != s[0]


def test_short_source_still_yields_k_disjoint_windows():
    tr = fake_transcript(110)
    res = highlights.find_highlights(tr, _energy(110), [], k=3, chat=FakeChat())
    hs = res["highlights"]
    assert len(hs) == 3 and all(15 <= h["dur"] <= 60 for h in hs)
    spans = sorted((h["start"], h["end"]) for h in hs)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
