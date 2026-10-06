"""authored_by_lane memapps: behaviour of bossman.context_engine.memory (MemoryManager). Real SQLite store, no mocks."""
from __future__ import annotations

import pytest

from bossman.context_engine.memory import MemoryManager, StoreMemoryPlugin
from bossman.context_engine.models import MemoryKind, MemoryStatus
from bossman.context_engine.store import ContextStore


@pytest.fixture()
def mm(tmp_path):
    store = ContextStore(tmp_path / "ctx.sqlite")
    try:
        yield MemoryManager(store)
    finally:
        store.close()


def test_candidate_is_not_active_and_carries_provenance(mm):
    m = mm.fact("Deploy target is Windows 11", project="p", source_refs=["doc://a"], verification="pytest 3 passed")
    assert m.status is MemoryStatus.CANDIDATE
    prov = m.metadata["provenance"]
    assert prov["source"] == ["doc://a"] and prov["verification"] == "pytest 3 passed"
    assert len(prov["content_hash"]) == 64
    # candidates are NOT retrievable as active memory until promoted
    assert mm.retrieve("Windows deploy", project="p") == []


def test_promote_makes_memory_retrievable_and_verified_raises_confidence(mm):
    m = mm.fact("owner prefers short Russian reports", project="p", confidence=.5)
    p = mm.promote(m.memory_id, verified=True)
    assert p.status is MemoryStatus.ACTIVE and p.confidence >= .9 and p.last_verified_at
    hits = mm.retrieve("short Russian reports", project="p")
    assert [h.memory_id for h in hits] == [m.memory_id]
    assert mm.retrieve("short Russian reports", project="other") == []  # project isolation


def test_polarity_conflict_marks_new_memory_disputed(mm):
    old = mm.fact("autonomous publishing is enabled for the site", project="p")
    mm.promote(old.memory_id)
    new = mm.fact("autonomous publishing is not enabled for the site", project="p")
    assert new.status is MemoryStatus.DISPUTED
    assert old.memory_id in new.contradicted_by


def test_decision_ids_are_sequential_and_supersede_hides_old(mm):
    d1 = mm.decision("use sqlite for context", project="p")
    d2 = mm.decision("use sqlite with fts5 for context", project="p")
    assert (d1.memory_id, d2.memory_id) == ("DEC-0001", "DEC-0002")
    mm.promote(d1.memory_id)
    mm.promote(d2.memory_id)
    mm.supersede(d1.memory_id, d2.memory_id)
    ids = {m.memory_id for m in mm.retrieve("sqlite context", project="p")}
    assert ids == {"DEC-0002"}  # superseded record is kept in store but not served


def test_failure_memory_is_retrievable_and_repromote_keeps_status(mm):
    f = mm.failure("pytest hangs on Windows", cause="fork", fix="use spawn", verification="12 passed", project="p")
    assert f.memory_id == "FAIL-0001" and f.kind is MemoryKind.FAILURE
    mm.promote(f.memory_id)
    assert [x.memory_id for x in mm.retrieve_failures("pytest hangs", project="p")] == ["FAIL-0001"]
    other = mm.fact("note about unrelated thing", project="p")
    mm.promote(other.memory_id)
    assert all(x.kind is MemoryKind.FAILURE for x in mm.retrieve_failures("pytest hangs unrelated", project="p"))
    # re-distilling the same fact must not demote an ACTIVE record back to CANDIDATE
    same = mm.fact("note about unrelated thing", project="p")
    assert same.status is MemoryStatus.ACTIVE


def test_no_writable_plugin_raises_instead_of_losing_data(tmp_path):
    class RO:
        name = "ro"
        read_only = True

        def retrieve(self, q, p, n):
            return []

        def write_candidate(self, r):
            raise RuntimeError("ro")

    store = ContextStore(tmp_path / "c.sqlite")
    try:
        with pytest.raises(RuntimeError):
            MemoryManager(store, [RO()]).fact("x y z")
    finally:
        store.close()


def test_store_plugin_is_default():
    assert StoreMemoryPlugin.name == "local-store"
