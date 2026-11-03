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


def test_query_punctuation_does_not_hide_a_fact():
    """02.11.2026: task prompts carry «Roblox:» / «освещение,» — split() kept the punctuation and matched nothing."""
    from bcc.v2.memory.lifecycle_wiring import FactsSnapshot
    snap = FactsSnapshot([{"id": 148, "subject": "Roblox environment lighting readability", "predicate": "lesson",
                           "object": "", "statement": "освещение будки на телефоне"}])
    assert [r["id"] for r in snap.search("Мир SwapMe в Roblox: освещение, карта", limit=5)] == [148]


def test_inflected_russian_query_finds_the_lesson_and_title_terms_count_double():
    """Harness proof 03.11: «+35 Лисьих Очков ... злоупотреблений» missed lesson 238 («фарм очков») by exact forms."""
    rows = [{"id": i, "subject": "lesson:ops", "predicate": f"заметка {i}", "statement": "игроки любят друзей и ивенты"}
            for i in range(30)]
    rows.append({"id": 238, "subject": "lesson:ops", "predicate": "Кулдаун награды в атрибуте = бесконечный фарм очков",
                 "statement": "статуя платила очки каждые 30 с; нужен суточный гейт на сервере"})
    snap = FactsSnapshot(rows)
    hits = snap.search("Статуя на площади: игрок получает +35 Лисьих Очков, защищённо от злоупотреблений", limit=5)
    assert hits[0]["id"] == 238
