"""Read-only memory plugins next to the store (zone memory, 2026-10-06).

MarkdownMemoryPlugin/JsonMemoryPlugin are read-only bridges meant to sit next
to StoreMemoryPlugin (ContextEngine(memory_plugins=[...])). Before the fix:
  * MemoryManager.candidate() called write_candidate on every plugin, so the
    read-only one raised — and when it was listed first, the store write never
    happened (the memory was lost);
  * MemoryManager.retrieve() let one broken source (corrupt JSON export) raise,
    which ContextEngine.inject_into_builder turned into an EMPTY injection:
    the store's own memories and all evidence vanished from the prompt.
"""
from __future__ import annotations

from pathlib import Path

from bossman.context_engine import (
    ContextEngine,
    ContextStore,
    JsonMemoryPlugin,
    MarkdownMemoryPlugin,
    MemoryKind,
    MemoryManager,
    MemoryStatus,
    StoreMemoryPlugin,
)


class _Builder:
    def __init__(self) -> None:
        self.retrieved: list[str] = []

    def set_retrieved(self, chunks: list[str]) -> None:
        self.retrieved = list(chunks)


def test_candidate_is_persisted_when_a_readonly_plugin_is_listed_first(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    store = ContextStore(tmp_path / "context.db")
    mem = MemoryManager(store, plugins=[MarkdownMemoryPlugin(tmp_path / "notes"),
                                        StoreMemoryPlugin(store)])
    rec = mem.fact("Owner backend listens on port 8801", project="p")
    stored = store.memories("p", (MemoryStatus.CANDIDATE,))
    assert [m.memory_id for m in stored] == [rec.memory_id]
    store.close()


def test_broken_readonly_source_does_not_erase_store_recall(tmp_path: Path):
    broken = tmp_path / "export.json"
    broken.write_text("{not json", encoding="utf-8")
    engine = ContextEngine(tmp_path / "context.db")
    engine.memory.plugins.append(JsonMemoryPlugin(broken))
    rec = engine.memory.constraint("Never deploy without release owner approval", project="p")
    engine.memory.promote(rec.memory_id)

    builder = _Builder()
    blocks = engine.inject_into_builder(builder, "deploy approval", project="p")
    assert any(rec.memory_id in b for b in blocks)
    engine.close()


def test_readonly_plugin_memories_still_merge_into_recall(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "ops.md").write_text("Backups rotate every night at 03:00\n", encoding="utf-8")
    store = ContextStore(tmp_path / "context.db")
    mem = MemoryManager(store, plugins=[StoreMemoryPlugin(store), MarkdownMemoryPlugin(notes)])
    rec = mem.candidate(MemoryKind.FACT, "Backups are verified weekly", project="p")
    mem.promote(rec.memory_id)
    texts = [m.text.strip() for m in mem.retrieve("when do backups rotate", project="p")]
    assert "Backups rotate every night at 03:00" in texts
    assert "Backups are verified weekly" in texts
    store.close()


def test_candidate_without_any_writable_plugin_fails_closed(tmp_path: Path):
    import pytest

    store = ContextStore(tmp_path / "context.db")
    mem = MemoryManager(store, plugins=[MarkdownMemoryPlugin(tmp_path)])
    with pytest.raises(RuntimeError, match="no writable memory plugin"):
        mem.fact("this must not vanish silently", project="p")
    store.close()
