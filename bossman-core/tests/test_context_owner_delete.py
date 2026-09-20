"""Owner-data deletion removes both durable document bytes and retrieval indexes."""
from bossman.context_engine.embeddings import HashEmbedder
from bossman.context_engine.ingest import Ingestor
from bossman.context_engine.store import ContextStore


def test_delete_document_removes_chunks_fts_and_survives_restart(tmp_path):
    path=tmp_path / "context.sqlite"
    marker="owner_delete_probe_7c91"
    store=ContextStore(path)
    try:
        doc=Ingestor(store, HashEmbedder()).ingest_text(
            f"{marker} private owner fixture", source_uri="private.md", project="A")
        assert store.lexical_search(marker, project="A")
        assert store.delete_document(doc.document_id, project="A") is True
        assert store.lexical_search(marker, project="A") == []
        assert store.get_chunk(next(iter([
            r[0] for r in store.db.execute(
                "SELECT chunk_id FROM chunks WHERE document_id=?", (doc.document_id,)).fetchall()
        ]), "")) is None
        assert store.db.execute("SELECT 1 FROM documents WHERE document_id=?",
                                (doc.document_id,)).fetchone() is None
    finally:
        store.close()
    reopened=ContextStore(path)
    try:
        assert reopened.lexical_search(marker, project="A") == []
        assert reopened.db.execute("SELECT 1 FROM documents WHERE project='A'").fetchone() is None
    finally:
        reopened.close()


def test_delete_is_project_scoped_and_cannot_erase_other_project(tmp_path):
    store=ContextStore(tmp_path / "context.sqlite")
    try:
        ingest=Ingestor(store, HashEmbedder())
        a=ingest.ingest_text("same bytes delete scope", source_uri="same.md", project="A")
        b=ingest.ingest_text("same bytes delete scope", source_uri="same.md", project="B")
        assert store.delete_document(a.document_id, project="B") is False
        assert store.lexical_search("delete scope", project="A")
        assert store.lexical_search("delete scope", project="B")
        assert store.delete_document(a.document_id, project="A") is True
        assert store.lexical_search("delete scope", project="A") == []
        assert store.lexical_search("delete scope", project="B")
        assert store.document_indexed(b.document_id)
    finally:
        store.close()
