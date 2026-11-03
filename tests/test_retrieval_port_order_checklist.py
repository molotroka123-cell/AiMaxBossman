"""Harness proof 03.11 (owner table: «урок применяется на новой задаче — NOT PROVEN»).

1. The facts port's own order (stemmed, title-boosted) is the primary fact signal; plain BM25 over the ~20-row pool
   only breaks ties — before, a long generic lesson re-ranked the rule-bearing lesson out of the reserved slots.
2. BM25 meets inflected forms («Очков»/«Очки»).
3. Recalled lessons are also rendered as an engineering checklist, framed as guidance that grants nothing."""
from learning import retrieval


class _Port:
    def __init__(self, rows):
        self.rows = rows

    def search(self, query, *, limit):
        return self.rows[:limit]


def _facts(n_generic=6):
    rule = {"id": 238, "subject": "lesson:ops", "predicate": "Кулдаун награды = фарм очков",
            "statement": "статуя: суточный гейт на сервере, не os.clock"}
    generic = [{"id": 900 + i, "subject": "lesson:ops", "predicate": f"Roblox социальность {i}",
                "statement": "Roblox игроки статуя очки друзья ивенты флекс лидерборд статуя очки Roblox " * 3}
               for i in range(n_generic)]
    return [rule] + generic


def test_port_order_beats_bm25_rerank():
    r = retrieval.UnifiedRetriever(facts=_Port(_facts()))
    ctx = r.search("Roblox статуя очки", project_id="p", use_cache=False)
    refs = [c.ref for c in ctx.items]
    assert "fact:238" in refs[:retrieval.RESERVED_FACT_SLOTS], refs


def test_bm25_matches_inflections():
    s = retrieval.bm25_scores("получает +35 Лисьих Очков", ["фарм очков", "добавить друзей"])
    assert s[0] > 0 and s[1] == 0


def test_lessons_render_as_a_checklist_that_grants_nothing():
    r = retrieval.UnifiedRetriever(facts=_Port(_facts(0)))
    ctx = r.search("статуя очки", project_id="p", use_cache=False)
    assert "[ENGINEERING CHECKLIST" in ctx.text
    assert "grants no tool, permission or budget" in ctx.text
    assert "суточный гейт" in ctx.text.split("[ENGINEERING CHECKLIST", 1)[1]


def test_checklist_item_is_the_rule_not_the_incident_story():
    """A/B round 1 (03.11): the first 260 chars were the incident, the fix was cut, the model wrote the bug 9/9."""
    body = ("03.11.2026 аудит: EconomyService делал pcall(require, BoothService), а BoothService — Script. " * 3
            + "Исправление: BindableEvent ServerStorage.SwapMeRefreshBooth. "
            + "Правило: связь между Script-файлами только через BindableEvent/атрибуты.")
    rule = retrieval._actionable(body)
    assert rule.startswith("связь между Script-файлами только через BindableEvent")
    assert "SwapMeRefreshBooth" in rule
    assert retrieval._actionable("без маркера " * 100).startswith("без маркера")
