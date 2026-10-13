"""Showdown records (face-up opponent cards + award) flow into the observed-event history, and only what was seen."""
from pokervision.history import derive_events
from pokervision.reconcile import Reconciler

from .helpers import state


def _sd(slot, cards, ranks, award=None, label="Maksik_zt"):
    return {"slot": slot, "label": label, "label_source": "profile_by_position", "cards": cards, "ranks": ranks,
            "why_unknown": [], "award": award, "t_ms": 0}


def test_showdown_is_recorded_per_seat_with_the_board_and_never_invented():
    rec = Reconciler()
    board = ("2s", "6h", "5d", "Kc")
    for i in range(3):
        st = state(i, hero=("7d", "7h"), board=board)
        rec.push(st)
    st = state(3, hero=("7d", "7h"), board=board)
    st.showdown = [_sd("top", [None, None], ["8", None])]
    rec.push(st)
    st = state(4, hero=("7d", "7h"), board=board)
    st.showdown = [_sd("top", ["8s", "8h"], ["8", "8"], 303.10), _sd("lower_right", [None, None], [None, None], None)]
    rec.push(st)
    hands = rec.finish()
    sds = hands[0]["showdowns"]
    assert [s["slot"] for s in sds] == ["top", "lower_right"] or [s["slot"] for s in sds] == ["lower_right", "top"] or len(sds) == 2
    top = next(s for s in sds if s["slot"] == "top")
    assert top["cards"] == ["8s", "8h"] and top["award"] == 303.10          # the more complete reading replaced the first
    assert top["board"] == list(board) and top["hand_id"] == hands[0]["id"]
    ev = [e for e in derive_events(hands[0])["events"] if e["type"] == "showdown"]
    assert {e["slot"] for e in ev} == {"top", "lower_right"}
    nothing = next(e for e in ev if e["slot"] == "lower_right")
    assert nothing["cards"] == [None, None]                                   # unreadable stays unreadable
    assert all("never recorded" in e["note"] for e in ev)


def test_no_showdown_means_no_event():
    rec = Reconciler()
    for i in range(3):
        rec.push(state(i, hero=("As", "Kd")))
    hands = rec.finish()
    assert hands[0]["showdowns"] == []
    assert not [e for e in derive_events(hands[0])["events"] if e["type"] == "showdown"]
