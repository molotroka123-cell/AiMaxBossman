"""Facts recall must rank before truncating: fresh specific lessons beat old rows that share one common word."""
from bcc.v2.memory.lifecycle_wiring import FactsSnapshot


def test_specific_fact_survives_truncation_among_common_word_matches():
    rows = [{"id": i, "subject": f"старый факт {i}", "statement": "игрок открыл сайт для отчёта"} for i in range(40)]
    rows.append({"id": 99, "subject": "Roblox монетизация",
                 "statement": "стартовый набор 49 Robux для новых игроков SwapMe Coin Tycoon увеличивает первую покупку"})
    snap = FactsSnapshot(rows)
    hits = snap.search("Предложи изменения монетизации Roblox SwapMe Coin Tycoon для игрок", limit=5)
    assert hits[0]["id"] == 99
    assert len(hits) == 5


def test_no_match_returns_nothing_and_empty_query_keeps_old_behaviour():
    snap = FactsSnapshot([{"id": 1, "statement": "abc"}])
    assert snap.search("zzz qqq", limit=5) == []
    assert snap.search("", limit=5) == [{"id": 1, "statement": "abc"}]


def test_ties_prefer_newer_rows():
    snap = FactsSnapshot([{"id": 1, "statement": "roblox"}, {"id": 2, "statement": "roblox"}])
    assert [r["id"] for r in snap.search("roblox", limit=2)] == [2, 1]
