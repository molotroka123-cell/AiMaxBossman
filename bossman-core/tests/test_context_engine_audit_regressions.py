"""Adversarial durable context regressions; no model/provider dependencies."""
from dataclasses import replace

import pytest

from bossman.context_engine import ContextEngine, MemoryKind, MemoryStatus
from bossman.context_engine.embeddings import HashEmbedder
from bossman.context_engine.memory import MemoryManager
from bossman.context_engine.plugins import JsonMemoryPlugin


class CountingEmbedder(HashEmbedder):
    def __init__(self):
        super().__init__(32)
        self.calls = 0
    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def test_same_relative_source_is_indexed_independently_per_project(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    try:
        one = engine.index_text("distinctive launch config", source_uri="README.md", project="one")
        two = engine.index_text("distinctive launch config", source_uri="README.md", project="two")
        assert one.document_id != two.document_id
        assert {h.chunk.project for h in engine.retriever.search("launch", project="two")} == {"two"}
    finally:
        engine.close()


def test_identical_text_sensitivity_reclassification_updates_cached_chunks(tmp_path):
    embedder = CountingEmbedder()
    engine = ContextEngine(tmp_path / "ctx.db", embedder=embedder)
    try:
        engine.index_text("private medical marker", source_uri="note.txt", project="p")
        engine.index_text("private medical marker", source_uri="note.txt", project="p", sensitivity="clinical")
        assert embedder.calls == 1
        assert engine.retriever.search("medical marker", project="p", sensitivity_allow=("normal",)) == []
        assert engine.retriever.search("medical marker", project="p", sensitivity_allow=("clinical",))
    finally:
        engine.close()


def test_reindex_retires_stale_retrieval_but_preserves_raw_provenance(tmp_path):
    path = tmp_path / "ctx.db"
    engine = ContextEngine(path)
    old = engine.index_text("obsolete configuration alpha", source_uri="settings.md", project="p")
    old_chunk = engine.retriever.search("obsolete", project="p")[0].chunk.chunk_id
    engine.index_text("current configuration beta", source_uri="settings.md", project="p")
    engine.close()
    restored = ContextEngine(path)
    try:
        hits = restored.retriever.search("configuration obsolete", project="p")
        assert hits and all("obsolete" not in h.chunk.text for h in hits)
        assert restored.store.db.execute("SELECT text FROM documents WHERE document_id=?", (old.document_id,)).fetchone()[0] == "obsolete configuration alpha"
        assert restored.store.get_chunk(old_chunk) is not None
    finally:
        restored.close()


def test_query_relevance_survives_memory_plugin_merge(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    try:
        good = engine.memory.fact("quartz deployment rollback", project="p", importance=.1, confidence=.5)
        noise = engine.memory.fact("cooking soup ingredients", project="p", importance=1, confidence=1)
        engine.memory.promote(good.memory_id)
        engine.memory.promote(noise.memory_id)
        assert engine.memory.retrieve("quartz deployment rollback", project="p", limit=2)[0].memory_id == good.memory_id
    finally:
        engine.close()


def test_external_plugin_cannot_cross_project_or_restore_retired_memory(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    good = engine.memory.fact("quartz deployment", project="p")
    engine.memory.promote(good.memory_id)
    class UnscopedPlugin:
        name = "unscoped-test"
        def retrieve(self, query, project, limit):
            return [replace(good, memory_id="foreign", project="other", status=MemoryStatus.ACTIVE),
                    replace(good, memory_id="retired", status=MemoryStatus.SUPERSEDED),
                    replace(good, status=MemoryStatus.ACTIVE)]
    manager = MemoryManager(engine.store, [UnscopedPlugin()])
    try:
        assert [m.memory_id for m in manager.retrieve("quartz", project="p")] == [good.memory_id]
    finally:
        engine.close()


@pytest.mark.parametrize("change", ["text", "project", "kind"])
def test_verified_memory_id_cannot_accept_different_payload(tmp_path, change):
    engine = ContextEngine(tmp_path / "ctx.db")
    try:
        old = engine.memory.fact("Only local verified tools", project="p")
        engine.memory.promote(old.memory_id, verified=True)
        with pytest.raises(ValueError):
            engine.memory.candidate(MemoryKind.DECISION if change == "kind" else MemoryKind.FACT,
                "Unverified replacement text" if change == "text" else old.text,
                project="other" if change == "project" else "p", memory_id=old.memory_id)
        assert engine.store.memories("p")[0].text == "Only local verified tools"
    finally:
        engine.close()


def test_failed_index_publish_keeps_prior_source_and_no_partial_document(tmp_path, monkeypatch):
    engine = ContextEngine(tmp_path / "ctx.db")
    try:
        old = engine.index_text("stable quartz configuration", source_uri="config.txt", project="p")
        def fail(*args, **kwargs):
            raise OSError("interrupted indexing transaction")
        monkeypatch.setattr(engine.store, "replace_chunks", fail)
        with pytest.raises(OSError):
            engine.index_text("incomplete replacement", source_uri="config.txt", project="p")
        assert engine.store.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert engine.retriever.search("quartz", project="p")[0].chunk.document_id == old.document_id
    finally:
        engine.close()


def test_json_export_retired_status_cannot_become_active_context(tmp_path):
    import json
    path = tmp_path / "export.json"
    path.write_text(json.dumps([{"text": "quartz stale configuration", "project": "p", "status": "superseded"},
                                {"text": "quartz current configuration", "project": "p", "status": "active"}]))
    engine = ContextEngine(tmp_path / "ctx.db", memory_plugins=[JsonMemoryPlugin(path)])
    try:
        assert [m.text for m in engine.memory.retrieve("quartz", project="p")] == ["quartz current configuration"]
    finally:
        engine.close()


def test_unavailable_query_embedding_keeps_lexical_evidence(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    engine.index_text("quartz approval regression", source_uri="a.txt", project="p")
    def offline(texts):
        raise OSError("local embedder unavailable")
    engine.embedder.embed = offline
    try:
        assert engine.retriever.search("quartz", project="p")[0].chunk.source_uri == "a.txt"
    finally:
        engine.close()


def test_corrupt_vector_cannot_erase_valid_retrieval(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    engine.index_text("quartz approval regression", source_uri="a.txt", project="p")
    bad = engine.index_text("unrelated noise", source_uri="b.txt", project="p")
    engine.store.db.execute("UPDATE chunks SET vector='[broken' WHERE document_id=?", (bad.document_id,))
    engine.store.db.commit()
    try:
        assert engine.retriever.search("quartz", project="p")[0].chunk.source_uri == "a.txt"
    finally:
        engine.close()


def test_empty_project_is_an_isolated_namespace_even_with_small_limit(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    try:
        engine.index_text("quartz deployment", source_uri="global.txt")
        engine.index_text("quartz deployment", source_uri="private.txt", project="other")
        global_mem = engine.memory.fact("quartz deployment", importance=.1)
        foreign_mem = engine.memory.fact("quartz deployment", project="other", importance=1)
        engine.memory.promote(global_mem.memory_id)
        engine.memory.promote(foreign_mem.memory_id)
        assert [m.memory_id for m in engine.memory.retrieve("quartz", limit=1)] == [global_mem.memory_id]
        assert {h.chunk.source_uri for h in engine.retriever.search("quartz", candidate_limit=1)} == {"global.txt"}
    finally:
        engine.close()


def test_stale_plugin_copy_cannot_revive_superseded_durable_memory(tmp_path):
    engine = ContextEngine(tmp_path / "ctx.db")
    old = engine.memory.fact("quartz old deployment", project="p")
    new = engine.memory.fact("quartz current deployment", project="p")
    engine.memory.promote(old.memory_id)
    engine.memory.promote(new.memory_id)
    engine.memory.supersede(old.memory_id, new.memory_id)
    class StalePlugin:
        name = "stale-export"
        def retrieve(self, *args):
            return [replace(old, status=MemoryStatus.ACTIVE)]
    try:
        assert MemoryManager(engine.store, [StalePlugin()]).retrieve("quartz", project="p") == []
    finally:
        engine.close()


@pytest.mark.parametrize("bad_vectors", [[], [[float("nan")]*32], [[1.0]], [["invalid"]*32]])
def test_invalid_embedding_response_cannot_publish_partial_source(tmp_path, bad_vectors):
    engine = ContextEngine(tmp_path / "ctx.db", embedder=CountingEmbedder())
    old = engine.index_text("quartz current state", source_uri="state.txt", project="p")
    engine.embedder.embed = lambda texts: bad_vectors
    try:
        with pytest.raises(ValueError):
            engine.index_text("new unpublished state", source_uri="state.txt", project="p")
        assert engine.store.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert engine.store.lexical_search("quartz", project="p")[0][0].document_id == old.document_id
    finally:
        engine.close()


def test_cosine_normalizes_unscaled_valid_vectors_and_rejects_nan():
    from bossman.context_engine.embeddings import cosine
    assert cosine([2.0, 0.0], [1.0, 1.0]) == pytest.approx(2**-.5)
    assert cosine([float("nan"), 0], [1, 0]) == 0


def test_legacy_store_reopen_selects_latest_source_without_deleting_history(tmp_path):
    path = tmp_path / "ctx.db"
    engine = ContextEngine(path)
    old = engine.index_text("quartz obsolete state", source_uri="state.txt", project="p")
    latest = engine.index_text("quartz current state", source_uri="state.txt", project="p")
    engine.store.db.execute("DROP TABLE current_documents")  # Pre-upgrade schema had no current-version index.
    engine.store.db.commit()
    engine.close()
    upgraded = ContextEngine(path)
    try:
        assert {h.chunk.document_id for h in upgraded.retriever.search("quartz", project="p")} == {latest.document_id}
        assert upgraded.store.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
        assert upgraded.store.document_indexed(old.document_id)
    finally:
        upgraded.close()
