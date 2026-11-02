"""02.11.2026: 36 Roblox lessons (facts 149-184) were recorded but recall was 0/36 — across 22 runs all 98 context
items were vault notes. Notes score by the vault BM25 (+5 exact bonus) and get expanded to 700 tokens; facts score by
BM25 over a ~20-row pool. Different scales + the expansion budget meant a relevant lesson never reached the pack."""
import re

from learning.retrieval import DirectoryNotes, UnifiedRetriever

QUERY = "План мира SwapMe Roblox: старт игрока, карта, освещение, коллекция монет, удержание"


class Facts:
    def __init__(self, rows):
        self.rows = rows

    def search(self, query, *, limit):
        terms = {t for t in re.findall(r"\w+", query.lower()) if len(t) > 2}
        hits = [r for r in self.rows if any(t in (r["subject"] + " " + r["statement"]).lower() for t in terms)]
        return hits[:limit]

    def state_token(self):
        return str(len(self.rows))


def _vault(tmp_path, n=12):
    for i in range(n):
        (tmp_path / f"note{i}.md").write_text(
            f"# Roblox SwapMe карта {i}\n\n" + ("Roblox SwapMe карта освещение старт игрока удержание. " * 120),
            encoding="utf-8")
    return DirectoryNotes(tmp_path)


LESSONS = [
    {"id": 148, "subject": "Roblox environment lighting readability", "predicate": "lesson",
     "object": "", "statement": "Освещение будки и маршрутов проверять на телефоне и ПК (youtube 0fJjcLwXNy0@02:17).",
     "current": True},
    {"id": 146, "subject": "Roblox collection goals as retention hypothesis", "predicate": "lesson",
     "object": "", "statement": "Видимые достижимые цели коллекции монет как гипотеза удержания (youtube mmhOt0jZ4Qc@01:13).",
     "current": True},
]


def test_relevant_lessons_reach_the_context_even_when_notes_outscore_them(tmp_path):
    retriever = UnifiedRetriever(notes=_vault(tmp_path), facts=Facts(LESSONS))
    ctx = retriever.search(QUERY, project_id="swapme", use_cache=False)
    refs = [c.ref for c in ctx.items]
    assert "fact:148" in refs and "fact:146" in refs, refs
    assert "Roblox environment lighting readability" in ctx.text
    assert any(c.layer == "note" for c in ctx.items)            # notes still present, not displaced entirely


def test_irrelevant_facts_get_no_reserved_slot(tmp_path):
    unrelated = [{"id": 7, "subject": "Kitchen timer", "predicate": "note", "object": "",
                  "statement": "Pasta needs nine minutes", "current": True}]
    retriever = UnifiedRetriever(notes=_vault(tmp_path, 3), facts=Facts(unrelated))
    ctx = retriever.search(QUERY, project_id="swapme", use_cache=False)
    assert "fact:7" not in [c.ref for c in ctx.items]
