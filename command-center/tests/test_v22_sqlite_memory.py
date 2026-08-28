from pathlib import Path
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


async def test_sqlite_memory_search_expand(tmp_path: Path):
    vault = tmp_path / "vault"; vault.mkdir()
    note = vault / "architecture.md"
    note.write_text("# Router\n\nBOSSMAN routes verified tool-capable models.\n\n# Memory\n\nObsidian is source of truth.", encoding="utf-8")
    backend = SQLiteMemoryBackend(index_path=tmp_path / "memory.sqlite3", vault_root=vault)
    result = await backend.index([vault])
    assert result["files"] == 1 and result["chunks"] >= 2
    hits = await backend.search("verified tool capable models", top_k=5)
    assert hits and hits[0].source == "architecture.md"
    expanded = await backend.expand(hits[0].chunk_hash)
    assert "BOSSMAN routes" in expanded["content"]


async def test_index_one_preserves_other_note(tmp_path: Path):
    vault = tmp_path / "vault"; vault.mkdir()
    a = vault / "a.md"; b = vault / "b.md"
    a.write_text("# A\n\nalpha token", encoding="utf-8")
    b.write_text("# B\n\nbeta unique token", encoding="utf-8")
    backend = SQLiteMemoryBackend(index_path=tmp_path / "memory.sqlite3", vault_root=vault)
    await backend.index([vault])
    a.write_text("# A\n\nalpha changed token", encoding="utf-8")
    result = await backend.index_one(a)
    assert result["updated"] == 1
    assert (await backend.search("changed token"))[0].source == "a.md"
    assert (await backend.search("beta unique token"))[0].source == "b.md"


async def test_deleted_note_removed_on_full_scan(tmp_path: Path):
    vault = tmp_path / "vault"; vault.mkdir()
    note = vault / "gone.md"; note.write_text("# Gone\n\ntemporary fact", encoding="utf-8")
    backend = SQLiteMemoryBackend(index_path=tmp_path / "memory.sqlite3", vault_root=vault)
    await backend.index([vault]); note.unlink()
    result = await backend.index([vault])
    assert result["removed"] == 1
    assert not await backend.search("temporary fact")
