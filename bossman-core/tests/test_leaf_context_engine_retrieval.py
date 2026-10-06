"""authored_by_lane memapps: bossman.context_engine.retrieval (HybridRetriever) over a real store with HashEmbedder."""
from __future__ import annotations

import dataclasses

from bossman.context_engine.embeddings import HashEmbedder
from bossman.context_engine.ingest import Ingestor
from bossman.context_engine.models import Chunk, RetrievalHit
from bossman.context_engine.retrieval import HybridRetriever, LexicalReranker
from bossman.context_engine.store import ContextStore


def _setup(tmp_path):
    store = ContextStore(tmp_path / "c.sqlite")
    ing = Ingestor(store, HashEmbedder())
    return store, ing, HybridRetriever(store, HashEmbedder())


def test_relevant_document_ranks_first_and_hits_have_scores(tmp_path):
    store, ing, r = _setup(tmp_path)
    try:
        ing.ingest_text("The quartz scheduler restarts nightly at 03:00 on the owner machine", source_uri="a.md", project="p")
        ing.ingest_text("Bananas are yellow and grow in tropical climates", source_uri="b.md", project="p")
        hits = r.search("nightly scheduler restart", project="p")
        assert hits and "scheduler" in hits[0].chunk.text
        assert hits[0].final_score >= hits[-1].final_score
        assert hits[0].reasons
    finally:
        store.close()


def test_sensitive_chunks_are_withheld_without_permission(tmp_path):
    store, ing, r = _setup(tmp_path)
    try:
        ing.ingest_text("wallet recovery phrase lives in the vault", source_uri="s.md", project="p", sensitivity="secret")
        ing.ingest_text("wallet UI colour is blue", source_uri="n.md", project="p")
        allowed = r.search("wallet", project="p", sensitivity_allow=("normal",))
        assert allowed and all((h.chunk.sensitivity or "normal") == "normal" for h in allowed)
        assert not any("recovery" in h.chunk.text for h in allowed)
        everything = r.search("wallet", project="p")
        assert any("recovery" in h.chunk.text for h in everything)
    finally:
        store.close()


def test_project_scope_and_duplicate_content_dedup(tmp_path):
    store, ing, r = _setup(tmp_path)
    try:
        ing.ingest_text("same body about pipelines", source_uri="one.md", project="A")
        ing.ingest_text("same body about pipelines", source_uri="two.md", project="A")
        ing.ingest_text("pipelines of project B", source_uri="b.md", project="B")
        texts = [h.chunk.text for h in r.search("pipelines", project="A")]
        assert len(texts) == len(set(texts))
        assert not any("project B" in t for t in texts)
    finally:
        store.close()


def test_lexical_reranker_prefers_query_overlap():
    fields = {f.name for f in dataclasses.fields(Chunk)}

    def mk(i, text):
        kw = {"chunk_id": i, "document_id": "d", "text": text, "heading": "", "ordinal": 0}
        return RetrievalHit(chunk=Chunk(**{k: v for k, v in kw.items() if k in fields}))

    hits = LexicalReranker().rerank("alpha beta", [mk("1", "gamma delta"), mk("2", "alpha beta gamma")])
    assert hits[0].chunk.chunk_id == "2" and hits[0].rerank_score > hits[1].rerank_score
