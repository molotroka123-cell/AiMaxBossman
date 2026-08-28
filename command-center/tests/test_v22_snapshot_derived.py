"""V2.2 §6 — снапшот знает про перестраиваемые производные хранилища.

Модульные тесты проверяют сам allowlist, интеграционные — что фича снапшота
действительно им пользуется: индекс памяти уезжает в артефакт, откатывается
вместе с БД, а не влезший по размеру честно помечается «перестроить».
"""
import json
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


# ------------------------------------------------------- интеграция с фичей снапшота

def _make_index(env, payload: bytes = b"memory-index-v1") -> Path:
    """Производное хранилище на месте, где его ищет allowlist."""
    mem = Path(env.svc.settings.data_dir) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    path = mem / "index-abc123.sqlite3"
    path.write_bytes(payload)
    return path


async def test_snapshot_copies_derived_index_and_counts_its_bytes(env):
    index = _make_index(env)
    made = (await env.client.post("/api/snapshots", json={"name": "с индексом"})).json()
    base = Path(made["path"])
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))

    entries = manifest["derived_stores"]
    assert [e["relative_path"] for e in entries] == ["memory/index-abc123.sqlite3"]
    entry = entries[0]
    assert entry["copied"] is True
    assert (base / entry["snapshot_file"]).read_bytes() == index.read_bytes()

    # размер производного файла входит в общий учёт артефакта, а не проходит мимо
    assert entry["snapshot_file"] in [f["name"] for f in manifest["files"]]
    assert made["size_bytes"] >= entry["size_bytes"]


async def test_restore_returns_derived_index_to_snapshot_state(env):
    index = _make_index(env, b"memory-index-v1")
    made = (await env.client.post("/api/snapshots", json={"name": "точка"})).json()
    sid = made["snapshot"]["id"]

    index.write_bytes(b"memory-index-v2-after-snapshot")

    preview = (await env.client.get(f"/api/snapshots/{sid}/restore-preview")).json()
    assert [x["relative_path"] for x in preview["derived_stores"]["copied"]] \
        == ["memory/index-abc123.sqlite3"]
    assert str(index) in preview["will_replace"]

    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    done = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                  json={"approval_id": approval_id})).json()

    assert index.read_bytes() == b"memory-index-v1"
    assert done["derived_rebuild_required"] == []
    assert done["derived_stores"][0]["restored"] is True

    # состояние до отката не потеряно: подстраховочная копия рядом с копией БД
    safety = Path(done["safety_copy"]).parent / "derived" / "memory" / "index-abc123.sqlite3"
    assert safety.read_bytes() == b"memory-index-v2-after-snapshot"


async def test_oversized_index_is_reported_as_rebuild_required(env, monkeypatch):
    """БД откатывается даже когда индекс не влез: он производный.

    Предел опускаем до реального размера БД плюс запас — так проверяется тот же
    арифметический путь «остаток предела», что и в бою, а не подменённая функция.
    """
    import bcc.features.snapshot as snap
    probe = (await env.client.post("/api/snapshots", json={"name": "замер"})).json()
    db_size = json.loads((Path(probe["path"]) / "manifest.json")
                         .read_text(encoding="utf-8"))["database"]["size_bytes"]
    limit = db_size + 4096
    monkeypatch.setattr(snap, "MAX_ARTIFACT_BYTES", limit)
    index = _make_index(env, b"y" * (limit + 1))

    resp = await env.client.post("/api/snapshots", json={"name": "без индекса"})
    assert resp.status_code == 200, resp.text
    made = resp.json()
    manifest = json.loads((Path(made["path"]) / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["derived_stores"][0]
    assert entry["copied"] is False and entry["rebuildable"] is True

    sid = made["snapshot"]["id"]
    preview = (await env.client.get(f"/api/snapshots/{sid}/restore-preview")).json()
    assert preview["derived_stores"]["omitted"][0]["relative_path"] == "memory/index-abc123.sqlite3"
    assert any("перестроить" in w for w in preview["warnings"])

    index.write_bytes(b"z" * 20)
    approval_id = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                         json={})).json()["error"]["approval_id"]
    await env.svc.approvals.decide(approval_id, True, "тест")
    done = (await env.client.post(f"/api/snapshots/{sid}/restore",
                                  json={"approval_id": approval_id})).json()

    assert done["ok"] is True                                   # БД откатилась
    assert done["derived_rebuild_required"] == ["memory/index-abc123.sqlite3"]
    assert done["derived_stores"][0]["reason"] == "not copied; rebuild required"
    assert index.read_bytes() == b"z" * 20                      # чужого не трогали


async def test_snapshot_never_copies_secrets_from_data_dir(env):
    """Allowlist, а не data_dir/** : secret.key рядом с индексом не уезжает."""
    _make_index(env)
    data_dir = Path(env.svc.settings.data_dir)
    (data_dir / "secret.key").write_bytes(b"fernet-key-canary")
    (data_dir / "memory").mkdir(parents=True, exist_ok=True)
    (data_dir / "memory" / "notes.md").write_text("личная заметка", encoding="utf-8")

    made = (await env.client.post("/api/snapshots", json={"name": "проверка allowlist"})).json()
    base = Path(made["path"])
    blob = b"".join(p.read_bytes() for p in base.rglob("*") if p.is_file())
    assert b"fernet-key-canary" not in blob
    assert "личная заметка".encode() not in blob
