"""FABLE5 perf: неизменный текст (тот же source_uri+контент) не переэмбеддится.

document_id = stable_id(source_uri, content_hash). Повторный ingest_text того же
memory.md обязан быть no-op по chunk+embed — иначе каждая задача платит за
переэмбеддинг одного и того же файла. Меняем контент → полный ре-индекс.
"""
from pathlib import Path

from bossman.context_engine import ContextStore, HashEmbedder, Ingestor


class CountingEmbedder(HashEmbedder):
    def __init__(self, dim: int = 64):
        super().__init__(dim)
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def test_identical_reingest_skips_embedding(tmp_path: Path):
    store = ContextStore(tmp_path / "ctx.db")
    embed = CountingEmbedder(64)
    ing = Ingestor(store, embed)
    text = "# Memory\nBossman keeps a canonical action cycle and argv-only exec."

    d1 = ing.ingest_text(text, source_uri="agents/coder/memory.md", project="coder")
    assert embed.calls == 1
    assert store.document_indexed(d1.document_id)

    # тот же контент → тот же document_id → embed НЕ вызывается второй раз
    d2 = ing.ingest_text(text, source_uri="agents/coder/memory.md", project="coder")
    assert d2.document_id == d1.document_id
    assert embed.calls == 1, "неизменный текст не должен переэмбеддиться"


def test_changed_content_triggers_full_reindex(tmp_path: Path):
    store = ContextStore(tmp_path / "ctx.db")
    embed = CountingEmbedder(64)
    ing = Ingestor(store, embed)

    ing.ingest_text("first version of the note", source_uri="agents/x/memory.md", project="x")
    assert embed.calls == 1
    ing.ingest_text("second, different version of the note", source_uri="agents/x/memory.md", project="x")
    assert embed.calls == 2, "изменённый контент обязан переиндексироваться"
