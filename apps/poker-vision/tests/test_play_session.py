"""Play session pieces that run without a browser: chart route in the coach, position voting, platform profiles, cached anchor,
grading against the trainer's DOM state, annotation, owner-action parsing, and the boundaries around them."""
import random
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pokervision import play_report as pr
from pokervision.coach import recommend
from pokervision.reconcile import Committed, Reconciler
from pokervision.schema import Field
from tests.helpers import money, ok, state

ROOT = Path(__file__).resolve().parents[1]


def cm_(hero=("As", "Kd"), pos="UTG", actions=(("FOLD", None), ("CALL", 10.0), ("RAISE", None)), pot=15, stack=1000, board=()):
    c = Committed(hand_id=1, t_ms=1000, hero_cards=list(hero), board=list(board), pot=money(pot), hero_stack=money(stack), street="preflop",
                  actions=[list(a) for a in actions], hero_turn=True)
    c.hero_position = pos
    return c


# ---------------------------------------------------------------- coach: chart route
def test_chart_used_preflop_when_position_is_read():
    rc = recommend(cm_(), random.Random(1), 50, preflop_bb=10)
    assert rc.ok and rc.source == "preflop_chart" and rc.decision.kind == "RAISE"
    assert rc.decision.size_min <= rc.decision.raise_to == 25 <= rc.decision.size_max
    assert "Bossman их не решал" in rc.disclaimer


def test_chart_fold_becomes_check_when_check_is_free():
    rc = recommend(cm_(hero=("7h", "2c"), pos="BB", actions=(("FOLD", None), ("CHECK", None), ("RAISE", None)), pot=20), random.Random(1), 50, preflop_bb=10)
    assert rc.source == "preflop_chart" and rc.decision.kind == "CHECK"


@pytest.mark.parametrize("kw", [dict(pos=None), dict(board=("Ah", "Kh", "2c"))])
def test_chart_not_used_without_position_or_postflop(kw):
    rc = recommend(cm_(**kw), random.Random(1), 50, preflop_bb=10)
    assert rc.source == "heuristic"


def test_chart_not_used_after_the_bot_already_acted_or_when_disabled():
    assert recommend(cm_(), random.Random(1), 50, preflop_bb=10, hero_acted_preflop=True).source == "heuristic"
    assert recommend(cm_(), random.Random(1), 50, preflop_bb=None).source == "heuristic"


def test_chart_raise_without_raise_button_falls_back():
    rc = recommend(cm_(actions=(("FOLD", None), ("CALL", 10.0), ("ALL IN", 1000.0))), random.Random(1), 50, preflop_bb=10)
    assert rc.source == "heuristic"


def test_control_waits_while_the_screen_is_ahead_of_the_committed_state():
    from pokervision.desk import screen_matches_committed
    cm = cm_(board=(), actions=(("FOLD", None), ("CHECK", None), ("RAISE", None)))
    st = state(0, board=("4h", "9d", "3h")); st.actions = ok([("FOLD", None), ("CHECK", None), ("RAISE", None)])
    assert not screen_matches_committed(st, cm)                        # flop on screen, committed still preflop
    st0 = state(0, board=()); st0.actions = ok([("FOLD", None), ("CHECK", None), ("RAISE", None)])
    assert screen_matches_committed(st0, cm)
    st1 = state(0, board=()); st1.actions = ok([("FOLD", None), ("CALL", 125.0), ("RAISE", None)])
    assert not screen_matches_committed(st1, cm)                       # a new bet appeared
    st2 = state(0, board=())                                            # buttons unreadable on the newest frame
    assert not screen_matches_committed(st2, cm)
    st3 = state(0, hero=("9h", "Kc"), board=()); st3.actions = st0.actions
    assert not screen_matches_committed(st3, cm)                       # a new hand is on screen, committed cards are the old hand's
    st4 = state(0, board=(), pot=40); st4.actions = st0.actions
    assert not screen_matches_committed(st4, cm)                       # the pot moved (a bet happened)
    st5 = state(0, hero=("As", None), board=()); st5.actions = st0.actions
    assert not screen_matches_committed(st5, cm)                       # a hero card unreadable now: wait


def test_recommendation_is_recomputed_when_stack_or_position_commit_later():
    from pokervision.desk import rec_key
    a, b, c = cm_(), cm_(stack=990), cm_(pos=None)
    assert rec_key(a) != rec_key(b) and rec_key(a) != rec_key(c)


def test_hero_turn_expires_when_nothing_is_readable_between_hands():
    rec = Reconciler()
    for t in (0, 100):
        cm = rec.push(state(t, hero_turn=True))
    assert cm.hero_turn is True
    for t in range(200, 200 + 100 * rec.cfg.window, 100):
        st = state(t); st.hero_turn = Field.unknown(t, "test", "no_anchor"); cm = rec.push(st)
    assert cm.hero_turn is None                                         # the deal assist may press DEAL again


def test_desk_bb_comes_from_the_table_the_bot_opened():
    from pokervision.desk import preflop_bb_for
    assert preflop_bb_for({"kind": "sandbox", "bootstrap": "cash_nl25"}) == 25.0
    assert preflop_bb_for({"kind": "sandbox", "bootstrap": "cash_nl10", "preflop_chart": False}) is None
    assert preflop_bb_for({"kind": "replay", "path": "x"}) is None


# ---------------------------------------------------------------- reconcile: position
def test_position_commits_after_two_agreeing_frames_and_a_disagreement_is_withheld_not_blocking():
    rec = Reconciler()
    for t in (0, 100):
        st = state(t); st.hero_position = ok("CO", t=t); cm = rec.push(st)
    assert cm.hero_position == "CO" and not any("hero_position" in b for b in cm.blocked)
    for t in (200, 300):
        st = state(t); st.hero_position = ok("BTN" if t == 200 else "CO", t=t); cm = rec.push(st)
    assert cm.hero_position is None                      # competing reading: the chart will not answer
    assert not any("hero_position" in b for b in cm.blocked)


def test_unreadable_buttons_drop_the_committed_action_set_instead_of_keeping_a_stale_one():
    rec = Reconciler()
    acts = [("FOLD", None), ("CALL", money(33)), ("CONFIRM", None)]
    for t in (0, 100):
        st = state(t, hero_turn=True); st.actions = ok([("FOLD", None), ("CALL", 33.0), ("CONFIRM", None)], t=t); cm = rec.push(st)
    assert cm.actions is not None
    for t in range(200, 200 + 100 * rec.cfg.window, 100):
        st = state(t, hero_turn=True); cm = rec.push(st)         # actions UNKNOWN on every new frame (e.g. an ALL IN label never learned)
    assert cm.actions is None
    assert recommend(cm, random.Random(1), 20, preflop_bb=10).ok is False


# ---------------------------------------------------------------- adapter: platform profile and cached anchor
def test_platform_profile_prefers_the_platform_directory(tmp_path, monkeypatch):
    from pokervision.adapters import poker_train as pt
    monkeypatch.setattr(pt, "PROFILES_DIR", tmp_path)
    (tmp_path / "poker_train.json").write_text("{}")
    assert pt.platform_profile("poker_train.json", "win32") == tmp_path / "poker_train.json"
    (tmp_path / "win32").mkdir(); (tmp_path / "win32" / "poker_train.json").write_text("{}")
    assert pt.platform_profile("poker_train.json", "win32") == tmp_path / "win32" / "poker_train.json"
    assert pt.platform_profile("poker_train.json", "linux") == tmp_path / "poker_train.json"


def test_shipped_windows_profile_reads_positions():
    from pokervision.adapters.poker_train import Profile
    p = Profile.load(ROOT / "pokervision" / "adapters" / "profiles" / "win32" / "poker_train.json")
    assert set(p.positions.exemplars) == {"UTG", "HJ", "CO", "BTN", "SB", "BB"} and p.data["position_cy_css"]
    assert "CONFlRM" in p.words.exemplars                 # learned also on frames where the open raise panel covers the hero cards


def _adapter_with_last_full(naive=False):
    from pokervision.adapters.poker_train import PokerTrainAdapter
    ad = PokerTrainAdapter.__new__(PokerTrainAdapter)
    ad.naive, ad.scale_hint = naive, 1.0
    ad._last_full = {"s": 1.0, "top": 430.0, "boxes": [], "wh": (520, 900), "age": 0}
    return ad


def test_cached_anchor_only_same_size_bounded_age_and_never_in_baseline():
    fr = SimpleNamespace(w=520, h=900)
    ad = _adapter_with_last_full()
    a = ad._cached_anchor(fr)
    assert a and a["cached"] and a["top"] == 430.0
    assert ad._cached_anchor(SimpleNamespace(w=640, h=900)) is None           # window size changed: no reuse
    for _ in range(ad.CACHED_ANCHOR_MAX_FRAMES):
        ad._cached_anchor(fr)
    assert ad._cached_anchor(fr) is None                                     # too old
    assert _adapter_with_last_full(naive=True)._cached_anchor(fr) is None


# ---------------------------------------------------------------- grading and annotation
TRUTH = {"hero_cards": [{"card": "As"}, {"card": "Kd"}], "board": [], "pot_text": "15", "to_call_text": "10", "hero_stack_text": "1,000",
         "position": "UTG", "hand_header": "3",
         "buttons": [{"label": "Dashboard"}, {"label": "FOLD"}, {"label": "CALL", "amount": 10}, {"label": "RAISE"}]}


def test_truth_view_and_grade():
    tv = pr.truth_view(TRUTH)
    assert tv == {"hero_cards": ["As", "Kd"], "board": [], "pot": 15.0, "to_call": 10, "hero_stack": 1000.0, "position": "UTG",
                  "actions": ["FOLD", "CALL", "RAISE"], "hand": "3"}
    read = pr.committed_view(cm_())
    assert set(pr.grade(read, tv).values()) == {"ok"}
    bad = dict(read, pot=16.0, position=None, hero_cards=["As", None])
    g = pr.grade(bad, tv)
    assert g["pot"] == "wrong" and g["position"] == "unknown" and g["hero_cards"] == "unknown"
    s = pr.summarize([{"grade": pr.grade(read, tv)}, {"grade": g}])
    assert s["pot"] == {"ok": 1, "wrong": 1, "unknown": 0, "na": 0, "acc_answered": 0.5}


def test_annotate_adds_a_side_panel_and_marks_clicks():
    img = np.zeros((300, 200, 3), np.uint8)
    out = pr.annotate(img, [{"x": 10, "y": 10, "w": 40, "h": 20, "label": "As", "ok": True}], [("Действие: RAISE", "h")], [(100, 150, "RAISE")], panel_w=300)
    assert out.shape == (300, 500, 3)
    assert out[150, 100].tolist() != [0, 0, 0]            # the click marker is drawn on the frame


def test_owner_action_from_hand_log():
    from pokervision.play_trainer import owner_action_from_log
    assert owner_action_from_log(["[UTG] Daniel raises to 25", "[SB] Hero calls 20"]) == "CALL"
    assert owner_action_from_log(["[SB] Hero raises to 75", "[BB] Bob folds"]) == "RAISE"
    assert owner_action_from_log(["[BTN] Hero folds"]) == "FOLD"
    assert owner_action_from_log(["[BTN] Bob checks"]) is None


def test_owner_action_counts_only_lines_added_since_the_decision_opened():
    from pokervision.play_trainer import owner_action_from_log
    before = ["[SB] Hero calls 10", "[BB] Bob checks", "--- FLOP ---"]
    after = before + ["[SB] Hero checks", "[BB] Bob bets 20"]
    assert owner_action_from_log(after, before) == "CHECK"             # not the earlier 'calls'
    assert owner_action_from_log(before, before) is None               # nothing new yet
    assert owner_action_from_log(before + ["[SB] Hero checks"], before) == "CHECK"
    same = ["[SB] Hero checks"]
    assert owner_action_from_log(same + ["[SB] Hero checks"], same) == "CHECK"   # identical text on a later street is still new


def test_eval_runs_on_every_platform_and_reports_peak_memory():
    from pokervision.eval.run_eval import peak_rss_mb           # importing used to fail on Windows (POSIX-only `resource`)
    v = peak_rss_mb()
    assert v is None or v > 1.0


# ---------------------------------------------------------------- boundaries
def test_play_session_refuses_non_loopback_before_a_browser_starts():
    from pokervision.play_trainer import Session
    from pokervision.sources import NotLoopback
    args = SimpleNamespace(url="https://www.wsop.com/", out="unused", bootstrap="cash_nl10", headful=False)
    with pytest.raises(NotLoopback):
        Session(args)


def test_policy_and_executor_never_see_the_dom_truth():
    for rel in ("coach.py", "preflop_chart.py", "strategy.py", "policy_route.py", "control/executor.py", "control/locator.py"):
        src = (ROOT / "pokervision" / rel).read_text(encoding="utf-8")
        assert not re.search(r"truth", src, re.I), rel
