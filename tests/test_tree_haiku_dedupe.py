"""Duplicate leaves: the judge proposes, only a deterministic name/source match lets a retire receipt be written."""
from __future__ import annotations

from tools.tree_proof import haiku_dedupe as dd


def leaf(nid, label, path, label_en=None, status="recorded"):
    n = {"id": nid, "label": label, "status": status, "sources": [{"path": path}]}
    if label_en:
        n["label_en"] = label_en
    return n


def test_same_original_name_or_same_source_is_a_match():
    a = leaf("skill-24", "Разработка через тесты", ".agents/skills/test-driven-development/SKILL.md", "test-driven-development")
    b = leaf("skill-44", "TDD-навык", "command-center/bcc/skills_catalog/x/test-driven-development/SKILL.md", "Test-Driven-Development")
    assert "same original name" in dd.deterministic_match(b, a)
    c = leaf("cap-23", "Кэширование", "command-center/bcc/features/cache_intel.py", status="code")
    d = leaf("mod-cache_intel", "Кэш-аналитика", "command-center/bcc/features/cache_intel.py", "cache_intel", status="reported")
    assert dd.deterministic_match(c, d) == "same source module command-center/bcc/features/cache_intel.py"


def test_related_but_different_leaves_and_shared_docs_never_match():
    a = leaf("oss-1", "Аудит безопасности · security-audit-skill", "BOSSMAN_V1_1_CLAUDE_IMPLEMENTATION.md", "cloudflare/security-audit-skill")
    b = leaf("oss-2", "Ревью кода · open-code-review", "BOSSMAN_V1_1_CLAUDE_IMPLEMENTATION.md", "alibaba/open-code-review")
    assert dd.deterministic_match(a, b) is None                   # one catalogue document lists many different repos
    assert dd.deterministic_match(a, a) is None
    page = leaf("pv-ui", "Страница Poker Vision", "command-center/ui/pages/poker_vision.js", status="reported")
    panel = leaf("pv-source-panel", "Панель источника", "command-center/ui/pages/poker_vision.js", status="code")
    assert dd.deterministic_match(panel, page) is None            # 10.10: a page sub-feature is not a duplicate of the page


def test_retire_keeps_the_older_record_in_the_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "EVID", tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "skill-23.txt").write_text("old skills-lane record", encoding="utf-8")
    gone = leaf("skill-23", "Отладка", "a/SKILL.md", "systematic-debugging")
    kept = leaf("skill-43", "Отладка (каталог)", "b/SKILL.md", "systematic-debugging")
    rcs = dd.retire([], gone, kept, "same original name", "same skill copied twice", "Навыки", "abc123")
    assert (tmp_path / "out" / "archive" / "skill-23.before-dedupe.txt").read_text(encoding="utf-8") == "old skills-lane record"
    assert "deterministic: skill-23 vs skill-43" in (tmp_path / "out" / "skill-23.txt").read_text(encoding="utf-8")
    assert rcs[0]["verdict"] == "RETIRE" and rcs[0]["kind"] == "audit" and len(rcs[0]["reason"]) >= 20


def test_same_name_pairs_only_among_retirable_leaves():
    a = leaf("skill-23", "Отладка", ".agents/skills/systematic-debugging/SKILL.md", "systematic-debugging")
    b = leaf("skill-43", "Отладка (каталог)", "cat/systematic-debugging/SKILL.md", "systematic-debugging")
    c = leaf("mod-x", "X", "x.py", "systematic-debugging", status="reported")
    assert [(p["id"], q["id"]) for p, q in dd.same_name_pairs([a, b, c])] == [("skill-23", "skill-43")]
