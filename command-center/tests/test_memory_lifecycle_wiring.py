"""The adapters that put the memory lifecycle over the REAL Command Center stores.

The lifecycle itself lives in the dependency-free ``bossman-shared`` distribution and is
proven in the root suite (``tests/test_memory_lifecycle.py``, including recall after a
genuine process restart). What is proven HERE is the wiring: that the canonical vault, its
derived SQLite index and the fact rows really satisfy the ports, and that a stale installed
copy of ``bossman-shared`` produces a named error instead of an obscure ImportError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.v2.memory import ObsidianVault
from bcc.v2.memory.lifecycle_wiring import (FactsSnapshot, LifecycleUnavailable, VaultNotes,
                                            available)
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


def _vault(tmp_path: Path) -> tuple[ObsidianVault, SQLiteMemoryBackend]:
    root = tmp_path / "vault"
    (root / "BOSSMAN Memory").mkdir(parents=True)
    vault = ObsidianVault(root=root)
    backend = SQLiteMemoryBackend(index_path=tmp_path / "index.sqlite3", vault_root=root)
    return vault, backend


def test_the_vault_port_reads_the_canonical_markdown_itself(tmp_path):
    vault, backend = _vault(tmp_path)
    (vault.root / "runbook.md").write_text(
        "# Runbook\n\nOn a stale derived index, reindex the renamed note.\n", encoding="utf-8")
    notes = VaultNotes(vault, backend)

    refs = dict(notes.iter_notes())
    assert "runbook.md" in refs and "stale derived index" in refs["runbook.md"]
    assert "Runbook" in notes.expand("runbook.md")


def test_the_vault_port_searches_through_the_existing_derived_index(tmp_path):
    vault, backend = _vault(tmp_path)
    (vault.root / "runbook.md").write_text(
        "# Runbook\n\nOn a stale derived index, reindex the renamed note.\n", encoding="utf-8")
    notes = VaultNotes(vault, backend)
    notes.reindex()                                   # what BOOT does

    hits = notes.search("stale derived index reindex", top_k=5)
    assert hits and hits[0]["ref"].endswith("runbook.md")
    assert hits[0]["score"] > 0 and hits[0]["text"]


def test_writing_a_note_goes_through_the_canonical_writer_and_stays_searchable(tmp_path):
    vault, backend = _vault(tmp_path)
    notes = VaultNotes(vault, backend)
    notes.reindex()
    assert notes.writable()

    ref = notes.write_note(title="Index decision", kind="decision", project="proj-alpha",
                           content="A stale index after a rename is rebuilt, never restored.",
                           tags=["decision"])
    assert ref.startswith("BOSSMAN Memory/")
    on_disk = (vault.root / ref)
    assert on_disk.is_file() and "never restored" in on_disk.read_text(encoding="utf-8")
    # written through ObsidianVault.write_memory, so its guarantees still hold
    assert on_disk.parent == vault.write_root
    assert [h["ref"] for h in notes.search("stale index rebuilt rename", top_k=5)]


def test_a_note_reference_cannot_escape_the_vault(tmp_path):
    vault, backend = _vault(tmp_path)
    with pytest.raises(PermissionError):
        VaultNotes(vault, backend).expand("../../secret.md")


def test_an_async_only_backend_says_so_instead_of_returning_nothing(tmp_path):
    """The exact pass reads the Markdown directly, so an unusable index must be loud."""
    vault, _ = _vault(tmp_path)

    class AsyncOnly:
        async def search(self, query, *, top_k=16):
            return []

    with pytest.raises(RuntimeError, match="no synchronous search"):
        VaultNotes(vault, AsyncOnly()).search("anything", top_k=3)


def test_the_facts_snapshot_matches_on_the_fact_fields_only():
    rows = [{"id": 1, "subject": "derived index", "predicate": "backend", "object": "sqlite",
             "statement": "the derived index backend is sqlite"},
            {"id": 2, "subject": "coffee", "predicate": "is", "object": "cold",
             "statement": "the coffee is cold"}]
    snap = FactsSnapshot(rows)
    assert [r["id"] for r in snap.search("derived index backend", limit=10)] == [1]
    assert [r["id"] for r in snap.search("", limit=10)] == [1, 2]
    assert snap.search("nothing matches this", limit=10) == []
    assert snap.state_token() != FactsSnapshot(rows[:1]).state_token()


@pytest.mark.skipif(available(), reason="the installed bossman-shared already has the lifecycle")
def test_a_stale_installed_shared_package_fails_with_a_named_error(tmp_path):
    from bcc.v2.memory import lifecycle_wiring
    with pytest.raises(LifecycleUnavailable, match="pip install -e"):
        lifecycle_wiring._learning_modules()


@pytest.mark.skipif(not available(),
                    reason="installed bossman-shared predates learning.lifecycle "
                           "(reinstall it from this checkout to run this)")
def test_the_wired_lifecycle_boots_over_the_real_vault(tmp_path):
    from learning.lessons import LessonBook
    from learning.lifecycle import MemoryLifecycle

    vault, backend = _vault(tmp_path)
    (vault.root / "runbook.md").write_text(
        "# Runbook\n\nOn a stale derived index, reindex the renamed note.\n", encoding="utf-8")
    life = MemoryLifecycle(state_dir=tmp_path / "state", notes=VaultNotes(vault, backend),
                           facts=FactsSnapshot([]), lessons=LessonBook(tmp_path / "learning"))
    health = life.boot()
    assert health.status in ("MEMORY_HEALTHY", "MEMORY_DEGRADED"), health.problems
    ctx = life.task_start(task_id="t", project_id="proj-alpha",
                          goal="stale derived index after a rename")
    assert [i.ref for i in ctx.memory.items]
