"""Owner's preflop charts: range parsing, chart lookup, and the 'not covered' answers."""
import pytest

from pokervision import preflop_chart as pc


def test_parse_range_notations():
    assert pc.parse_range("22+") == frozenset(r * 2 for r in pc.RANKS)
    assert pc.parse_range("K9s+") == {"K9s", "KTs", "KJs", "KQs"}
    assert pc.parse_range("ATo+") == {"ATo", "AJo", "AQo", "AKo"}
    assert pc.parse_range("22-JJ") == {"22", "33", "44", "55", "66", "77", "88", "99", "TT", "JJ"}
    assert pc.parse_range("A5s-A2s") == {"A2s", "A3s", "A4s", "A5s"}
    assert pc.parse_range("AKo, QQ") == {"AKo", "QQ"}
    assert len(pc.parse_range("A2s+")) == 12
    for bad in ("AK", "KAs", "A2x", "22-AKs", "Z2s"):
        with pytest.raises(ValueError):
            pc.parse_range(bad)


def test_hand_class():
    assert pc.hand_class("As", "Kd") == "AKo"
    assert pc.hand_class("5h", "Ah") == "A5s"
    assert pc.hand_class("Th", "Ts") == "TT"


def test_chart_sizes_are_plausible_and_nested():
    c = pc.chart()
    pct = {p: pc.combos(c["rfi"][p]["raise"]) / 1326 for p in ("UTG", "HJ", "CO", "BTN", "SB")}
    # measured on the owner's chart: UTG 19.8 %, BTN 49.9 %, SB 71.0 % (its comment says "~85%"; the listed classes give 71 %)
    assert 0.18 < pct["UTG"] < 0.21 and 0.49 < pct["BTN"] < 0.51 and 0.70 < pct["SB"] < 0.72
    assert pct["UTG"] <= pct["HJ"] <= pct["CO"] <= pct["BTN"] <= pct["SB"]
    assert c["rfi"]["UTG"]["raise"] <= c["rfi"]["BTN"]["raise"]                     # every UTG open is also a BTN open
    for row in c["vs_open"].values():
        assert not (row["threebet"] & row["call"])
    # the BB tables list some hands in both rows (mixed strategies); the chart then 3-bets, as the owner's app does
    assert "A5s" in c["bb_defense"]["vs_UTG"]["threebet"] & c["bb_defense"]["vs_UTG"]["call"]
    assert pc.decide(("As", "5s"), "BB", 15, 40, bb=10).action == "RAISE"
    # the dash ranges the owner's own JS parser drops ("22-JJ") are kept here
    assert "55" in c["vs_open"]["vs_UTG"]["call"]


@pytest.mark.parametrize("hero,pos,to_call,pot,expect", [
    (("As", "Kd"), "UTG", 10, 15, "RAISE"),        # AKo opens from UTG
    (("7h", "2c"), "UTG", 10, 15, "FOLD"),
    (("Kh", "7d"), "BTN", 10, 15, "RAISE"),        # K7o is a BTN open, not a CO open
    (("Kh", "7d"), "CO", 10, 15, "FOLD"),
    (("9h", "2d"), "SB", 5, 15, "FOLD"),
    (("9h", "3h"), "SB", 5, 15, "RAISE"),           # 92s+ in SB
    (("7h", "2c"), "BB", 0, 20, "CHECK"),           # BB option
    (("Qs", "Qd"), "BB", 15, 40, "RAISE"),          # vs a 2.5bb open: QQ 3-bets even vs UTG
    (("5s", "5d"), "BB", 15, 40, "CALL"),
    (("7h", "2c"), "BB", 15, 40, "FOLD"),
    (("Ah", "Kd"), "CO", 25, 40, "RAISE"),
    (("Ah", "9d"), "CO", 25, 40, "FOLD"),
])
def test_decisions(hero, pos, to_call, pot, expect):
    a = pc.decide(hero, pos, to_call, pot, bb=10)
    assert a is not None and a.action == expect, a
    if a.action == "RAISE":
        assert a.raise_to_bb and a.raise_to_bb >= 2.5


def test_open_sizes_follow_the_chart():
    assert pc.decide(("As", "Ad"), "UTG", 10, 15, bb=10).raise_to_bb == 2.5
    assert pc.decide(("As", "Ad"), "SB", 5, 15, bb=10).raise_to_bb == 3.5
    a = pc.decide(("As", "Ad"), "BB", 15, 40, bb=10)                 # 3-bet vs a 2.5bb open, out of position: 3.5x
    assert a.raise_to_bb == pytest.approx(8.75)


def test_reason_says_opener_position_is_assumed():
    a = pc.decide(("5s", "5d"), "BB", 15, 40, bb=10)
    assert "НЕ прочитана" in a.reason and a.table == "bb_defense.vs_UTG"


@pytest.mark.parametrize("hero,pos,to_call,bb", [
    (("As", "Kd"), None, 10, 10),          # position unknown
    (("As", None), "UTG", 10, 10),         # a card unknown
    (("As", "Kd"), "BTN", 120, 10),        # facing a 3-bet+: not in these charts
    (("As", "Kd"), "XYZ", 10, 10),
    (("As", "Kd"), "UTG", 10, 0),
])
def test_not_covered_returns_none(hero, pos, to_call, bb):
    assert pc.decide(hero, pos, to_call, 30, bb=bb) is None


def test_limpers_are_said_out_loud():
    a = pc.decide(("As", "Kd"), "BTN", 10, 35, bb=10)
    assert a.action == "RAISE" and "лимпер" in a.reason


def test_position_aliases_for_larger_tables():
    a = pc.decide(("As", "Kd"), "UTG+1", 10, 15, bb=10)
    assert a.action == "RAISE" and "UTG+1 → HJ" in a.reason
