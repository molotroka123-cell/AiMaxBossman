import random

from pokervision.history import derive_events, to_phh
from pokervision.reconcile import Config, Reconciler

from .helpers import state, unk


def feed(rec, states):
    out = None
    for st in states:
        out = rec.push(st)
    return out


def test_commit_needs_two_agreeing_frames_and_unknown_never_commits():
    rec = Reconciler()
    cm = rec.push(state(0, pot=15)); assert cm.pot is None                        # one reading is not enough
    cm = rec.push(state(1, pot=15)); assert cm.pot.amount == 15
    cm = rec.push(state(2, pot=None)); assert cm.pot.amount == 15                 # UNKNOWN keeps, does not zero
    rec2 = Reconciler(); feed(rec2, [state(i, pot=None) for i in range(5)]); assert rec2.committed.pot is None


def test_flicker_does_not_commit_but_real_change_does():
    rec = Reconciler()
    feed(rec, [state(0, pot=15), state(1, pot=15)])
    cm = feed(rec, [state(2, pot=999), state(3, pot=15)])                         # one-frame glitch
    assert cm.pot.amount == 15
    cm = feed(rec, [state(4, pot=40), state(5, pot=40)])
    assert cm.pot.amount == 40


def test_board_never_shrinks_inside_a_hand():
    rec = Reconciler()
    flop = ("Qs", "7c", "2d")
    cm = feed(rec, [state(i, board=flop) for i in range(3)])
    assert cm.board == list(flop)
    cm = feed(rec, [state(3, board=()), state(4, board=flop[:1])])               # animation/occlusion noise
    assert cm.board[:3] == list(flop) and cm.hand_id == 1                          # noise neither shrinks the board nor starts a hand


def test_new_hero_cards_start_a_new_hand_and_never_merge():
    rec = Reconciler()
    feed(rec, [state(i, hero=("As", "Kd")) for i in range(4)])
    feed(rec, [state(10 + i, hero=("2c", "7h")) for i in range(4)])
    hands = rec.finish()
    assert len(hands) == 2 and hands[0]["end_reason"].startswith("hero cards")
    ids = {rec.frame_hand[f"f{10 + i}"] for i in range(1, 4)} | {rec.frame_hand["f1"]}
    assert len(ids) == 2                                                            # frames of the two hands got different ids


def test_ambiguous_gap_frames_are_unassigned_not_guessed():
    rec = Reconciler()
    feed(rec, [state(i, hero=("As", "Kd")) for i in range(3)])
    feed(rec, [state(5, hero=None, pot=15, fid="gap1"), state(6, hero=None, pot=15, fid="gap2")])      # dealing animation
    feed(rec, [state(10 + i, hero=("2c", "7h")) for i in range(3)])
    assert rec.frame_hand["gap1"] is None and rec.frame_hand["gap2"] is None


def test_stale_and_frozen_frames_block_action():
    rec = Reconciler(Config(stale_ms=1000, freeze_ms=2000))
    cm = rec.push(state(0), now_ms=5000)
    assert any("STALE_FRAME" in b for b in cm.blocked)
    rec2 = Reconciler(Config(freeze_ms=2000))
    for t in range(0, 3000, 500):
        cm = rec2.push(state(t), frame_bytes=b"same")
    assert any("FROZEN_SOURCE" in b for b in cm.blocked)
    cm = rec2.push(state(4000), frame_bytes=b"different")
    assert not any("FROZEN" in b for b in cm.blocked)


def test_gap_is_recorded_not_invented():
    rec = Reconciler(Config(max_gap_ms=1000))
    feed(rec, [state(0, seats={1: (500, None)}), state(200, seats={1: (500, None)}), state(9000, seats={1: (500, 20)}), state(9200, seats={1: (500, 20)})])
    h = rec.finish()[0]
    derive_events(h)
    assert h["gaps"] and h["complete"] is False


def test_events_only_from_observation_and_flagged_incomplete():
    rec = Reconciler()
    seq = []
    for t in range(0, 4):
        seq.append(state(t, seats={1: (500, None), 2: (500, None)}))
    for t in range(4, 8):
        seq.append(state(t, seats={1: (500, 20), 2: (500, None)}))
    for t in range(8, 12):
        seq.append(state(t, seats={1: (500, 20), 2: (500, 20)}))
    feed(rec, seq)
    h = rec.finish()[0]
    derive_events(h, ["checks"])
    types = [(e["type"], e.get("slot")) for e in h["events"]]
    assert ("bet", 1) in types and ("call", 2) in types
    assert h["complete"] is False
    assert "complete = false" in to_phh(h)
