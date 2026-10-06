"""Recall order of durable memory (zone memory, 2026-10-06).

MemoryManager.retrieve used to re-sort the plugins' relevance-ranked results by
importance/confidence alone. The memory that matches the query could then land
behind unrelated "important" memories, and ContextCompiler clips the memory
section from the tail — so the one relevant memory was cut out of the prompt.
"""
from __future__ import annotations

from pathlib import Path

from bossman.context_engine import ContextStore, HashEmbedder, HybridRetriever, MemoryManager
from bossman.context_engine.compiler import ContextCompiler


def _mgr(tmp_path: Path):
    store = ContextStore(tmp_path / "context.db")
    return store, MemoryManager(store)


def test_relevant_memory_ranks_before_unrelated_important_one(tmp_path):
    store, mem = _mgr(tmp_path)
    port = mem.fact("Owner backend listens on port 8801 for deploy checks", project="p", importance=.3)
    theme = mem.preference("Owner prefers dark theme colours in every dashboard", project="p", importance=.95)
    mem.promote(port.memory_id)
    mem.promote(theme.memory_id)
    hits = mem.retrieve("which port does the backend listen on", project="p", limit=2)
    assert [m.memory_id for m in hits][0] == port.memory_id
    store.close()


def test_compiled_context_keeps_the_relevant_memory_under_budget(tmp_path):
    store, mem = _mgr(tmp_path)
    filler = " ".join(["unrelated-dashboard-palette-detail"] * 12)
    for i in range(12):
        rec = mem.preference(f"Preference {i}: {filler}", project="p", importance=.9)
        mem.promote(rec.memory_id)
    target = mem.fact("Release tag for the owner bundle is rc19-alpha", project="p", importance=.3)
    mem.promote(target.memory_id)

    compiler = ContextCompiler(HybridRetriever(store, HashEmbedder(64)), mem)
    ctx = compiler.compile(model="m", query="what is the release tag of the owner bundle",
                           project="p", model_window=4096, desired_output=512)
    memory_section = next(s for s in ctx.sections if s.name == "Relevant memory")
    assert target.memory_id in memory_section.text
    store.close()


def test_limit_still_bounds_the_result(tmp_path):
    store, mem = _mgr(tmp_path)
    for i in range(5):
        mem.promote(mem.fact(f"fact number {i} about storage", project="p").memory_id)
    assert len(mem.retrieve("storage", project="p", limit=3)) == 3
    store.close()
