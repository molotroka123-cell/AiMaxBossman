"""Offline tests: Stream Tube ideas in bcc.learning_video (SponsorBlock strip, RYD prior, fetch control plane).
Synthetic inputs only. Every loosening has a negative control next to it."""
from __future__ import annotations

import hashlib
import json

import pytest

from bcc.learning_video import run_pipeline
from bcc.learning_video.fetch_control import Breaker, FetchController, classify
from bcc.learning_video.stream_signals import (
    Segment, fetch_sponsor_segments, fetch_votes, rank_sources, source_prior, strip_segments, wilson_lower,
)
from bcc.learning_video.transcript import Cue

VID = "AbCdEfGhIj0"
OTHER = "ZzZzZzZzZz9"

LESSON = [
    "Резервная копия это отдельная копия данных на другом носителе.",
    "Правило три два один означает три копии на двух носителях и одну копию в другом месте.",
    "Сначала проверьте что архив открывается затем сравните контрольные суммы файлов.",
    "Шифрование нужно включать до отправки копии в облако а не после.",
    "Проверка восстановления раз в месяц показывает что копии действительно работают.",
    "Никогда не храните пароль от архива рядом с самим архивом это грубая ошибка.",
]
AD = "Промокод BOSS это скидка 20 процентов на первый месяц сервиса, сначала перейдите по ссылке в описании."


def vtt(lines, step=5.0):
    out, t = ["WEBVTT", ""], 0.0
    for s in lines:
        a, b = t, t + step
        out += ["00:%02d:%06.3f --> 00:%02d:%06.3f" % (a // 60, a % 60, b // 60, b % 60), s, ""]
        t = b
    return "\n".join(out)


def http_fake(routes):
    calls = []

    def get(url, timeout):
        calls.append(url)
        for key, resp in routes.items():
            if key in url:
                return resp
        return 0, b""
    get.calls = calls
    return get


# ---------------- SponsorBlock

def test_sponsorblock_uses_hash_prefix_not_video_id():
    prefix = hashlib.sha256(VID.encode()).hexdigest()[:4]
    body = json.dumps([{"videoID": VID, "segments": [{"segment": [5, 10], "category": "sponsor", "actionType": "skip",
                                                       "votes": 3, "UUID": "u"}]}]).encode()
    get = http_fake({"/api/skipSegments/" + prefix: (200, body)})
    res = fetch_sponsor_segments(VID, http_get=get)
    assert res["status"] == "OK" and res["segments"] == [Segment(5.0, 10.0, "sponsor", 3, False)]
    assert VID not in get.calls[0]  # k-anonymity: the exact id never leaves the PC


def test_sponsorblock_ignores_other_videos_sharing_prefix_and_bad_rows():
    body = json.dumps([
        {"videoID": OTHER, "segments": [{"segment": [0, 50], "category": "sponsor", "actionType": "skip"}]},
        {"videoID": VID, "segments": [
            {"segment": [9, 3], "category": "sponsor", "actionType": "skip"},          # reversed
            {"segment": [1, 2], "category": "poi_highlight", "actionType": "poi"},     # not a skip
            {"segment": "x", "category": "sponsor"},                                   # malformed
        ]},
    ]).encode()
    res = fetch_sponsor_segments(VID, http_get=http_fake({"skipSegments": (200, body)}))
    assert res["status"] == "NONE" and res["segments"] == []


@pytest.mark.parametrize("code,status", [(404, "NONE"), (429, "RATE_LIMITED"), (500, "UNAVAILABLE"), (0, "UNAVAILABLE")])
def test_sponsorblock_http_outcomes_are_statuses(code, status):
    assert fetch_sponsor_segments(VID, http_get=http_fake({"skip": (code, b"")}))["status"] == status


def test_sponsorblock_garbage_body_is_bad_response():
    assert fetch_sponsor_segments(VID, http_get=http_fake({"skip": (200, b"<html>")}))["status"] == "BAD_RESPONSE"


def test_invalid_video_id_refused_before_any_request():
    get = http_fake({})
    with pytest.raises(ValueError):
        fetch_sponsor_segments("../../etc", http_get=get)
    assert get.calls == []


def test_strip_removes_sponsor_cue_keeps_lesson():
    cues = [Cue(0, 5, "урок"), Cue(5, 10, "реклама"), Cue(10, 15, "урок два")]
    res = strip_segments(cues, [Segment(5, 10, "sponsor", 5)])
    assert res["status"] == "STRIPPED" and [c.text for c in res["cues"]] == ["урок", "урок два"]
    assert res["removed_s"] == 5.0


def test_strip_partial_overlap_keeps_cue():  # negative control for overlap_drop
    cues = [Cue(0, 10, "урок")]
    assert strip_segments(cues, [Segment(8, 12, "sponsor", 5)])["status"] == "UNCHANGED"


def test_strip_ignores_downvoted_segment_but_trusts_locked():
    cues = [Cue(0, 5, "a"), Cue(5, 10, "b"), Cue(10, 15, "c")]
    assert strip_segments(cues, [Segment(5, 10, "sponsor", -2)])["status"] == "UNCHANGED"
    assert strip_segments(cues, [Segment(5, 10, "sponsor", -2, locked=True)])["status"] == "STRIPPED"


def test_strip_refuses_troll_segment_that_eats_the_lesson():
    cues = [Cue(i * 5, i * 5 + 5, "x") for i in range(10)]
    res = strip_segments(cues, [Segment(0, 45, "sponsor", 1)])
    assert res["status"] == "SUSPICIOUS" and len(res["cues"]) == 10 and res["removed_cues"] == 0


def test_pipeline_ad_never_becomes_a_claim(tmp_path):
    lines = LESSON[:3] + [AD] + LESSON[3:]
    text = vtt(lines)
    url = "https://youtu.be/" + VID
    plain = run_pipeline(text, url=url, kind="manual_subs", store_path=tmp_path / "a.jsonl")
    clean = run_pipeline(text, url=url, kind="manual_subs", store_path=tmp_path / "b.jsonl",
                         skip_segments=[Segment(15.0, 20.0, "sponsor", 4)])
    assert clean["sponsor_strip"]["status"] == "STRIPPED"
    assert clean["sponsor_strip"]["removed_cues"] == 1
    assert plain["sponsor_strip"]["status"] == "NOT_REQUESTED"  # default path unchanged
    def has_ad(rep):
        return any("промокод" in (c.statement + " " + c.quote).lower() for c in rep["claims"])
    assert has_ad(plain), "positive control: without SponsorBlock the ad DOES leak into claims"
    assert not has_ad(clean)


# ---------------- Return YouTube Dislike

def test_votes_ok_and_labelled_estimate():
    body = json.dumps({"id": VID, "likes": 900, "dislikes": 100, "viewCount": 5000, "deleted": False}).encode()
    v = fetch_votes(VID, http_get=http_fake({"votes": (200, body)}))
    assert v["status"] == "OK" and v["source"] == "ryd_estimate"


def test_votes_wrong_id_or_negative_is_bad_response():
    for d in ({"id": OTHER, "likes": 1, "dislikes": 1}, {"id": VID, "likes": -1, "dislikes": 1}, {"likes": "x"}):
        assert fetch_votes(VID, http_get=http_fake({"votes": (200, json.dumps(d).encode())}))["status"] == "BAD_RESPONSE"


def test_prior_needs_enough_votes_no_fake_numbers():
    few = source_prior({"status": "OK", "likes": 10, "dislikes": 0})
    assert few["status"] == "INSUFFICIENT_EVIDENCE" and few["prior"] is None
    assert source_prior({"status": "RATE_LIMITED"})["prior"] is None
    good = source_prior({"status": "OK", "likes": 950, "dislikes": 50})
    bad = source_prior({"status": "OK", "likes": 300, "dislikes": 700})
    assert good["status"] == "OK" and good["prior"] > 0.9 > bad["prior"]


def test_wilson_penalises_small_samples():
    assert wilson_lower(10, 10) < wilson_lower(1000, 1000)
    assert wilson_lower(0, 0) == 0.0


def test_rank_known_first_unknown_last_stable():
    rows = [{"video_id": "u1", "prior": {"status": "INSUFFICIENT_EVIDENCE"}},
            {"video_id": "lo", "prior": {"status": "OK", "prior": 0.3}},
            {"video_id": "hi", "prior": {"status": "OK", "prior": 0.9}},
            {"video_id": "u2", "prior": None}]
    assert [r["video_id"] for r in rank_sources(rows)] == ["hi", "lo", "u1", "u2"]


# ---------------- fetch control plane

@pytest.mark.parametrize("rc,err,files,want", [
    (0, "", 1, "OK"),
    (0, "", 0, "NO_SUBS"),
    (1, "ERROR: Sign in to confirm you're not a bot. Use --cookies-from-browser", 0, "BOT_CHECK"),
    (1, "ERROR: HTTP Error 429: Too Many Requests", 0, "RATE_LIMITED"),
    (1, "ERROR: Private video", 0, "UNAVAILABLE"),
    (1, "ERROR: Unable to download webpage: timed out", 0, "NETWORK"),
    (1, "ERROR: something new", 0, "UNKNOWN"),
    # verbatim from yt-dlp 2026.11.23 behind a refusing proxy (live run 2026-11-24): must not loop through clients
    (1, "ERROR: [youtube] dQw4w9WgXcQ: Unable to download API page: ('Unable to connect to proxy', "
        "OSError('Tunnel connection failed: 403 Forbidden'))", 0, "NETWORK"),
    (1, "HTTP Error 429 on ru", 1, "OK"),  # partial success keeps what was written
])
def test_classify(rc, err, files, want):
    assert classify(rc, err, files) == want


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def runner_script(tmp_path, outcomes):
    """outcomes: list of (rc, stderr, write_file). Records argv."""
    seen = []

    def run(argv, timeout):
        seen.append(argv)
        rc, err, write = outcomes[len(seen) - 1]
        if write:
            (tmp_path / ("subs.%s.ru.vtt" % VID)).write_text("WEBVTT\n", encoding="utf-8")
        return rc, err
    run.seen = seen
    return run


def ctrl(tmp_path, outcomes, **kw):
    clock = kw.pop("clock", Clock())
    r = runner_script(tmp_path, outcomes)
    c = FetchController(ytdlp="yt-dlp", runner=r, clock=clock,
                        breaker=kw.pop("breaker", Breaker(path=tmp_path / "b.json", clock=clock)), **kw)
    return c, r


def test_bot_check_falls_back_to_next_client(tmp_path):
    c, r = ctrl(tmp_path, [(1, "Sign in to confirm you're not a bot", False), (0, "", True)])
    rep = c.fetch_subtitles("https://youtu.be/" + VID, tmp_path)
    assert rep["verdict"] == "OK" and len(rep["files"]) == 1
    assert [a["client"] for a in rep["attempts"]] == ["default", "android_vr"]
    assert "youtube:player_client=android_vr" in r.seen[1]
    assert r.seen[1][-2] == "--"  # URL can never be read as an option


def test_unavailable_is_terminal_no_retry(tmp_path):
    c, r = ctrl(tmp_path, [(1, "Private video", False)])
    assert c.fetch_subtitles("u", tmp_path)["verdict"] == "UNAVAILABLE" and len(r.seen) == 1


def test_rate_limit_stops_and_opens_breaker(tmp_path):
    clock = Clock()
    c, r = ctrl(tmp_path, [(1, "HTTP Error 429", False)], clock=clock)
    assert c.fetch_subtitles("u", tmp_path)["verdict"] == "RATE_LIMITED" and len(r.seen) == 1
    again = c.fetch_subtitles("u", tmp_path)
    assert again["verdict"] == "BLOCKED_BREAKER_OPEN" and len(r.seen) == 1  # nothing was run
    clock.t += 1801
    c.runner = runner_script(tmp_path, [(0, "", True)])
    assert c.fetch_subtitles("u", tmp_path)["verdict"] == "OK"  # breaker closes after cooldown


def test_breaker_state_survives_restart(tmp_path):
    clock = Clock()
    b = Breaker(path=tmp_path / "b.json", clock=clock)
    b.record("RATE_LIMITED")
    assert Breaker(path=tmp_path / "b.json", clock=clock).is_open()


def test_corrupt_breaker_state_starts_closed(tmp_path):
    (tmp_path / "b.json").write_text("{not json", encoding="utf-8")
    assert not Breaker(path=tmp_path / "b.json").is_open()


def test_all_clients_bot_checked_is_blocked_not_success(tmp_path):
    outs = [(1, "confirm you're not a bot", False)] * 5
    c, r = ctrl(tmp_path, outs)
    rep = c.fetch_subtitles("u", tmp_path)
    assert rep["verdict"] == "BLOCKED_BOT_CHECK" and rep["files"] == [] and len(r.seen) == 5
    assert not any("cookies" in " ".join(a) for a in r.seen)  # a bot wall is reported, never bypassed


def test_three_bot_walls_open_breaker(tmp_path):
    clock = Clock()
    b = Breaker(path=None, clock=clock)
    for _ in range(3):
        c, _ = ctrl(tmp_path, [(1, "Sign in to confirm you're not a bot", False)] * 5, breaker=b, clock=clock)
        c.fetch_subtitles("u", tmp_path)
    assert b.is_open()


def test_success_resets_strikes(tmp_path):
    b = Breaker(path=None, clock=Clock())
    b.record("BOT_CHECK")
    b.record("BOT_CHECK")
    b.record("OK")
    b.record("BOT_CHECK")
    assert not b.is_open()


def test_binary_pin_blocks_mismatch_and_allows_match(tmp_path):
    exe = tmp_path / "yt-dlp"
    exe.write_bytes(b"fake binary")
    good = hashlib.sha256(b"fake binary").hexdigest()
    r = runner_script(tmp_path, [(0, "", True)])
    bad = FetchController(ytdlp=str(exe), runner=r, expected_sha256="0" * 64, breaker=Breaker())
    assert bad.fetch_subtitles("u", tmp_path / "w")["verdict"] == "BLOCKED_BINARY_MISMATCH" and r.seen == []
    ok = FetchController(ytdlp=str(exe), runner=r, expected_sha256=good.upper(), breaker=Breaker())
    assert ok.fetch_subtitles("u", tmp_path)["verdict"] == "OK"
