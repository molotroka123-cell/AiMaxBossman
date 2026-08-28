from pathlib import Path
from bcc.v2.derived_stores import copy_into_snapshot, discover, restore_from_snapshot, safety_copy_current


async def test_allowlist_copy_and_restore(tmp_path: Path):
    data = tmp_path / "data"; (data / "memory").mkdir(parents=True); (data / "code-index").mkdir()
    mem = data / "memory" / "index-test.sqlite3"; mem.write_bytes(b"memory-v1")
    code = data / "code-index" / "abc.json"; code.write_text('{"code":1}', encoding="utf-8")
    (data / "secret.key").write_text("do-not-copy", encoding="utf-8")
    found = discover(data)
    assert mem.resolve() in found and code.resolve() in found
    assert (data / "secret.key").resolve() not in found
    snap = tmp_path / "snap"; snap.mkdir()
    entries = await copy_into_snapshot(data_dir=data, snapshot_base=snap,
                                       per_file_limit=1024*1024, total_limit=4*1024*1024)
    assert len(entries) == 2 and all(x["copied"] for x in entries)
    mem.write_bytes(b"memory-v2")
    safety = tmp_path / "safety"; safety.mkdir()
    await safety_copy_current(data_dir=data, safety_dir=safety, entries=entries)
    result = await restore_from_snapshot(data_dir=data, snapshot_base=snap, entries=entries)
    assert all(x["restored"] for x in result)
    assert mem.read_bytes() == b"memory-v1"


async def test_large_store_is_rebuildable_omission(tmp_path: Path):
    data = tmp_path / "data"; (data / "memory").mkdir(parents=True)
    (data / "memory" / "index-huge.sqlite3").write_bytes(b"x" * 5000)
    snap = tmp_path / "snap"; snap.mkdir()
    entries = await copy_into_snapshot(data_dir=data, snapshot_base=snap,
                                       per_file_limit=1000, total_limit=1000)
    assert entries[0]["copied"] is False and entries[0]["rebuildable"] is True
