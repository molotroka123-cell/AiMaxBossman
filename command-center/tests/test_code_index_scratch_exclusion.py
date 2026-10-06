"""Audit 2026-10-05 #3: a code root that is an ANCESTOR of <data_dir>/scratch must not index other agents' scratch files."""
from __future__ import annotations

import json
from pathlib import Path

from bcc.features import tools_code
from bcc.v2.code_index import CodeIndex, index_async, search_async

FOREIGN = "zq_neighbour_private_draft_marker"
REAL = "zq_project_public_marker"


def _scratch_hits(hits: list[dict]) -> list[dict]:
    return [h for h in hits if "scratch" in h["source"] or FOREIGN in h["content"]]


def _tree(data_dir: Path) -> None:
    foreign = data_dir / "scratch" / "mission-m1" / "agent-other"
    foreign.mkdir(parents=True)
    (foreign / "notes.py").write_text(f"def draft():\n    return '{FOREIGN}'\n", encoding="utf-8")
    proj = data_dir / "proj"
    proj.mkdir()
    (proj / "real.py").write_text(f"def real():\n    return '{REAL}'\n", encoding="utf-8")


async def test_ancestor_root_does_not_index_a_neighbours_scratch(env):
    data_dir = Path(env.svc.settings.data_dir)
    _tree(data_dir)
    handle = await tools_code.get_handle(env.svc, data_dir)          # the root is an ancestor of scratch/
    await index_async(handle.index, force=True)
    assert not _scratch_hits(await search_async(handle.index, FOREIGN)), "a neighbour's scratch draft became searchable"
    assert any(h["source"].endswith("proj/real.py") for h in await search_async(handle.index, REAL)), \
        "legit project files must still be indexed"                     # negative control


async def test_an_agents_own_scratch_root_is_still_indexed(env):
    data_dir = Path(env.svc.settings.data_dir)
    _tree(data_dir)
    own = data_dir / "scratch" / "mission-m1" / "agent-other"
    handle = await tools_code.get_handle(env.svc, own)
    await index_async(handle.index, force=True)
    assert any(h["source"].endswith("notes.py") for h in await search_async(handle.index, FOREIGN)), \
        "the scratch root itself (own alias) must keep working"


def test_an_index_saved_by_an_older_build_drops_scratch_entries_on_load(tmp_path):
    root = tmp_path
    (root / "scratch" / "m" / "a").mkdir(parents=True)
    (root / "proj").mkdir()
    (root / "scratch" / "m" / "a" / "n.py").write_text(f"X = '{FOREIGN}'\n", encoding="utf-8")
    (root / "proj" / "r.py").write_text(f"Y = '{REAL}'\n", encoding="utf-8")
    old = CodeIndex(roots=[root], index_path=tmp_path / "idx.json")          # older build: no exclusion
    old.index_sync(force=True)
    assert any("scratch" in rel for rel in old.files), "precondition: the old build did index scratch"
    fresh = CodeIndex(roots=[root], index_path=tmp_path / "idx.json", exclude_dirs=(root / "scratch",))
    fresh.load()
    assert not any("scratch" in rel for rel in fresh.files) and not _scratch_hits(fresh.search_sync(FOREIGN))
    assert any(h["source"].endswith("r.py") for h in fresh.search_sync(REAL))
