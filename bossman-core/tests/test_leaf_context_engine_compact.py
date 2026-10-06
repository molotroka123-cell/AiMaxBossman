"""authored_by_lane memapps: bossman.context_engine.compact (CompactSkill) - anchors survive compaction."""
from __future__ import annotations

from bossman.context_engine.compact import CompactSkill, Message, extract_anchors


def test_extract_anchors_finds_versions_paths_units_and_test_status():
    a = extract_anchors("ветка feat/x, файл bcc/app.py, v1.9.2, 16 GB RAM, 12 passed, sha deadbeef1")
    for want in ("bcc/app.py", "v1.9.2", "16 GB", "12 passed", "feat/x", "deadbeef1"):
        assert want in a, (want, a)


def test_compaction_keeps_anchors_constraints_and_recent_verbatim():
    msgs = [Message("user", "Goal: ship release v2.1.0. You must never delete the owner database.")]
    msgs += [Message("assistant", f"filler step {i} about nothing in particular") for i in range(30)]
    msgs.append(Message("assistant", "Tests: 41 passed in bcc/features/healing.py, branch green/leaves."))
    msgs.append(Message("user", "What is next?"))
    res = CompactSkill().compact(msgs, target_tokens=4000, keep_recent=2)
    assert res.quality_checks["anchors_preserved"] and res.quality_checks["recent_preserved"]
    assert "v2.1.0" in res.text and "41 passed" in res.text and "bcc/features/healing.py" in res.text
    assert "never delete the owner database" in res.text
    assert res.preserved_recent_messages == 2 and not res.overflow


def test_tiny_budget_raises_overflow_instead_of_dropping_anchors():
    msgs = [Message("user", "Pin version v9.8.7 and file core/x.py. Decision: we will keep it." * 3)]
    res = CompactSkill().compact(msgs, target_tokens=5, keep_recent=1)
    assert res.overflow is True
    assert "v9.8.7" in res.text and "core/x.py" in res.text


def test_empty_input_is_safe():
    r = CompactSkill().compact([])
    assert r.text == "" and r.input_tokens == 0
