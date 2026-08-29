"""ЭТАП 2.222 — retrieval: dedup, restart persistence, sensitivity-aware фильтр."""
from bossman.context_engine import ContextStore, HashEmbedder, HybridRetriever, Ingestor


def _stack(tmp_path, db="context.db"):
    store = ContextStore(tmp_path / db)
    embed = HashEmbedder(128)
    return store, Ingestor(store, embed), HybridRetriever(store, embed)


def test_retrieval_dedup_identical_content(tmp_path):
    store, ing, retr = _stack(tmp_path)
    ing.ingest_text("Единый текст про hybrid retrieval и reranking.",
                    source_uri="a.md", source_type="markdown", project="p")
    ing.ingest_text("Единый текст про hybrid retrieval и reranking.",
                    source_uri="b.md", source_type="markdown", project="p")
    hits = retr.search("hybrid retrieval reranking", project="p")
    hashes = [h.chunk.content_hash for h in hits]
    assert len(hashes) == len(set(hashes)), "дубликаты по content_hash не схлопнуты"
    store.close()


def test_index_survives_restart(tmp_path):
    store, ing, retr = _stack(tmp_path)
    ing.ingest_text("Persist: BOSSMAN хранит индекс в SQLite/WAL.",
                    source_uri="p.md", source_type="markdown", project="p")
    store.close()
    store2 = ContextStore(tmp_path / "context.db")
    retr2 = HybridRetriever(store2, HashEmbedder(128))
    hits = retr2.search("SQLite WAL индекс", project="p")
    assert hits and hits[0].chunk.source_uri == "p.md"
    store2.close()


def test_sensitivity_aware_filtering(tmp_path):
    store, ing, retr = _stack(tmp_path)
    ing.ingest_text("Публичная заметка про токены и бюджет.",
                    source_uri="pub.md", source_type="markdown", project="p")
    ing.ingest_text("Секретная заметка про токены и бюджет и пароль.",
                    source_uri="secret.md", source_type="markdown", project="p",
                    sensitivity="secret")
    # без фильтра — секретный чанк доступен
    all_hits = retr.search("токены бюджет", project="p")
    assert any(h.chunk.source_uri == "secret.md" for h in all_hits)
    # агент без прав на секретное не получает секретный чанк, даже если релевантнее
    filtered = retr.search("токены бюджет", project="p", sensitivity_allow=("normal",))
    assert all(h.chunk.source_uri != "secret.md" for h in filtered)
    assert any(h.chunk.source_uri == "pub.md" for h in filtered)
    store.close()
