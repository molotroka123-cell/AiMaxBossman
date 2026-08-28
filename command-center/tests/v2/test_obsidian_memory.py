from pathlib import Path

from bcc.v2.memory.obsidian import ObsidianVault
from bcc.v2.memory.context_pack import build_context_pack
from bcc.v2.memory.memsearch_bridge import MemoryHit

def test_obsidian_write_restricted(tmp_path: Path):
    vault = tmp_path/"vault"
    vault.mkdir()
    ob = ObsidianVault(vault)
    p = ob.write_memory(title="Decision", content="Use Redis", kind="decision")
    assert p.parent == (vault/"BOSSMAN Memory")
    assert "Use Redis" in p.read_text()

def test_iter_markdown_excludes_obsidian(tmp_path: Path):
    vault = tmp_path/"vault"
    (vault/".obsidian").mkdir(parents=True)
    (vault/".obsidian"/"x.md").write_text("secret config")
    (vault/"notes").mkdir()
    (vault/"notes"/"a.md").write_text("# A")
    ob = ObsidianVault(vault)
    found = list(ob.iter_markdown())
    assert len(found) == 1
    assert found[0].name == "a.md"

def test_context_budget_and_dedup():
    hits = [
        MemoryHit("same content"*100, "a.md", score=1),
        MemoryHit("same content"*100, "a2.md", score=.9),
        MemoryHit("different"*100, "b.md", score=.8),
    ]
    pack = build_context_pack("q", hits, max_tokens=1000, max_items=5, per_item_tokens=300)
    assert len(pack.items) <= 2
    assert pack.estimated_tokens <= 1000
