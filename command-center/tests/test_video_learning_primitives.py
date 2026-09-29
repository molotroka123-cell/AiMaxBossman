from bcc.market.learning_episode import LearningEpisode, TeacherClaim, can_promote
from bcc.market.video_timeline import TimelineCandidate, build_candidates, cue_priority, dedupe_phash, thin


def test_video_timeline_prioritizes_trading_visual_cues():
    segs = [
        {"start": 21.0, "text": "normal commentary"},
        {"start": 42.0, "text": "look here, CVD is changing at this level"},
    ]
    rows = build_candidates(duration=60, scene_times=[10, 40], transcript_segments=segs, periodic=15)
    cue = next(r for r in rows if r.kind == "cue")
    assert cue.t == 42 and cue.priority >= 65
    kept = thin(rows, 4)
    assert any(r.t == 0 for r in kept) and any(r.t == 60 for r in kept)
    assert any(r.t == 42 for r in kept)


def test_phash_dedupe_keeps_high_priority_cue():
    a = TimelineCandidate(0, "periodic", 10)
    b = TimelineCandidate(1, "periodic", 10)
    cue = TimelineCandidate(2, "cue", 65)
    rows = dedupe_phash([(a, "00"), (b, "01"), (cue, "01")], threshold=2)
    assert [r[0].t for r in rows] == [0, 2]


def test_learning_episode_cannot_self_promote_without_outcome_verifier():
    ep = LearningEpisode(source_url="https://youtu.be/x", video_id="x", timestamp_s=10,
                         frame_sha256="a" * 64, extractor_version="v4", observation={},
                         claims=[TeacherClaim("buy here", "TRIGGER", .9)], status="PROMOTED")
    assert "promotion_without_outcome_and_verifier" in ep.validate()
    assert not can_promote(ep)
    ep.outcome = {"15m": {"ret": 0.01}}
    ep.verifier_refs = ["ledger:abc"]
    assert ep.validate() == [] and can_promote(ep)


def test_deep_command_contract():
    from bcc.market.deep_request import parse_deep_command
    req = parse_deep_command("/market_deep BTC 24h")
    assert req is not None and req.symbol == "BTC" and req.context_hours == 24
    assert req.read_only and req.bypass_notification_cooldown
    assert parse_deep_command("/market_deep BTC 99h") is None


def test_level_parser_and_temporal_events():
    from bcc.market.levels import parse_level, relation, track
    assert parse_level("dPOC 84,620") == ("dPOC", 84620.0)
    assert parse_level("garbage 84,620") is None
    assert relation(84715, 84620) == "ABOVE"
    previous = {"price": 84500.0, "levels": {"dPOC": 84620.0}}
    current = {"price": 84715.0, "levels": {"dPOC": 84620.0}}
    assert track(previous, current)["dPOC"] == "RECLAIM"


def test_purged_split_removes_overlap_and_embargo():
    from bcc.market.stat_guard import TimedSample, purged_split
    rows = [
        TimedSample("train", "d1", 10, 20),
        TimedSample("overlap", "d1", 40, 55),
        TimedSample("test", "d2", 61, 70),
    ]
    train, test, purged = purged_split(rows, split_ts=50, embargo_s=10)
    assert [x.sample_id for x in train] == ["train"]
    assert [x.sample_id for x in test] == ["test"]
    assert [x.sample_id for x in purged] == ["overlap"]


def test_sparse_bayesian_rate_is_not_raw_certainty():
    from bcc.market.stat_guard import beta_posterior_interval
    mean, lo, hi = beta_posterior_interval(9, 1)
    assert mean < .9 and lo < mean < hi


def test_multiple_testing_fdr_gate():
    from bcc.market.stat_guard import benjamini_hochberg
    accepted = benjamini_hochberg([.001, .01, .2, .8], alpha=.05)
    assert accepted == [True, True, False, False]
