"""tools/owner_one_bossman.py: backup/restore proofs, locks, launch refusals, repoint, agent fixes."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("owner_one_bossman", ROOT / "tools" / "owner_one_bossman.py")
ob = importlib.util.module_from_spec(spec)
sys.modules["owner_one_bossman"] = ob
spec.loader.exec_module(ob)


def _hold_lock(path: Path):
    """Hold byte 0 of ``path`` like bcc/backend_lock.py and the Telegram pollers do."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+b")
    if f.seek(0, 2) == 0:
        f.write(b"0"); f.flush()
    f.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def _live_wal_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE t(x)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    con.commit()                 # rows live only in the -wal file (no checkpoint)
    return con


def _data_root(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    src = tmp_path / "data"
    (src / "pit-v1.7").mkdir(parents=True)
    (src / "token").write_text("secret-token", encoding="utf-8")
    (src / "pit-v1.7" / "config.json").write_text('{"core_url": "http://127.0.0.1:8800"}', encoding="utf-8")
    (src / "voice").mkdir()
    (src / "voice" / "a.bin").write_bytes(os.urandom(4096))
    con = _live_wal_db(src / "bcc.db")
    return src, con


def test_backup_of_a_live_wal_database_keeps_committed_rows_and_skips_locks(tmp_path):
    src, con = _data_root(tmp_path)
    held = _hold_lock(src / "backend.lock")
    try:
        assert (src / "bcc.db-wal").stat().st_size > 0
        meta = ob.backup(src, tmp_path / "bk")
    finally:
        held.close(); con.close()
    bk = tmp_path / "bk"
    assert "bcc.db" in meta["sqlite_via_backup_api"]
    skipped = {s["path"] for s in meta["skipped"]}
    assert {"backend.lock", "bcc.db-wal", "bcc.db-shm"} <= skipped
    assert not (bk / "backend.lock").exists() and not (bk / "bcc.db-wal").exists()
    manifest = ob.read_manifest(bk)
    assert set(manifest) == {"token", "pit-v1.7/config.json", "voice/a.bin", "bcc.db"}
    assert ob.verify(bk)["ok"]
    # standalone snapshot: the WAL rows are inside the single backup file
    copy = tmp_path / "probe.db"
    copy.write_bytes((bk / "bcc.db").read_bytes())
    assert sqlite3.connect(str(copy)).execute("select count(*) from t").fetchone()[0] == 50


def test_verify_detects_a_changed_and_a_missing_file(tmp_path):
    src, con = _data_root(tmp_path); con.close()
    ob.backup(src, tmp_path / "bk")
    (tmp_path / "bk" / "token").write_text("changed", encoding="utf-8")
    (tmp_path / "bk" / "voice" / "a.bin").unlink()
    res = ob.verify(tmp_path / "bk")
    assert not res["ok"] and res["differ"] == ["token"] and res["missing"] == ["voice/a.bin"]


def test_restore_into_an_empty_folder_proves_hashes_and_integrity(tmp_path):
    src, con = _data_root(tmp_path); con.close()
    meta = ob.backup(src, tmp_path / "bk")
    res = ob.restore(tmp_path / "bk", tmp_path / "restore")
    assert res["ok"] and res["files"] == meta["files"]
    assert res["manifest_sha256"] == meta["manifest_sha256"]
    assert res["integrity"] == {"bcc.db": "ok"}
    assert (tmp_path / "restore" / "token").read_text(encoding="utf-8") == "secret-token"
    with pytest.raises(SystemExit):
        ob.restore(tmp_path / "bk", tmp_path / "restore")        # not empty: refused


def test_backup_refuses_a_destination_inside_the_source_or_not_empty(tmp_path):
    src, con = _data_root(tmp_path); con.close()
    with pytest.raises(SystemExit):
        ob.backup(src, src / "inner")
    (tmp_path / "full").mkdir(); (tmp_path / "full" / "x").write_text("x")
    with pytest.raises(SystemExit):
        ob.backup(src, tmp_path / "full")


def test_lock_held_sees_another_holder_and_not_a_free_file(tmp_path):
    p = tmp_path / "x.lock"
    assert not ob.lock_held(p)                    # missing
    p.write_bytes(b"0")
    assert not ob.lock_held(p)                    # present, free
    f = _hold_lock(p)
    try:
        assert ob.lock_held(p)
    finally:
        f.close()
    assert not ob.lock_held(p)


def test_launch_backend_attaches_instead_of_starting_a_second_one(tmp_path, monkeypatch):
    data = tmp_path / "data"; data.mkdir()
    f = _hold_lock(data / "backend.lock")
    (data / "backend.json").write_text(json.dumps({"pid": 4242, "port": 8801, "build_sha": "84f5e0ac" * 5}))
    spawned = []
    monkeypatch.setattr(ob, "_spawn_hidden", lambda *a, **k: spawned.append(a) or 1)
    out = io.StringIO()
    try:
        assert ob.launch("backend", tmp_path / "home", data, 8801, None, out=out) == 0
    finally:
        f.close()
    assert spawned == [] and "ALREADY RUNNING" in out.getvalue()


def test_launch_companion_does_not_start_a_second_poller_for_the_same_token(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "telegram-companion"; cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("{}")
    (cfg_dir / "companion.env").write_text("TG_COMPANION_BOT_TOKEN=123:abc\n")
    locks = tmp_path / "pollers"
    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(locks))
    f = _hold_lock(locks / f"{ob.token_fingerprint('123:abc')}.lock")
    spawned = []
    monkeypatch.setattr(ob, "_spawn_hidden", lambda *a, **k: spawned.append(a) or 1)
    out = io.StringIO()
    try:
        rc = ob.launch("companion", tmp_path / "home", tmp_path / "data", 8801, cfg_dir / "config.json", out=out)
    finally:
        f.close()
    assert rc == 0 and spawned == [] and "ALREADY POLLED" in out.getvalue()


def test_launch_jeff_refuses_when_the_pit_poller_lock_is_held(tmp_path, monkeypatch):
    data = tmp_path / "data"
    f = _hold_lock(data / "pit-v1.7" / "poller.lock")
    spawned = []
    monkeypatch.setattr(ob, "_spawn_hidden", lambda *a, **k: spawned.append(a) or 1)
    out = io.StringIO()
    try:
        assert ob.launch("jeff", tmp_path / "home", data, 8801, None, out=out) == 0
    finally:
        f.close()
    assert spawned == []


def test_supervisor_placeholder_starts_nothing_until_delivered(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(ob, "_spawn_hidden", lambda *a, **k: spawned.append(a) or 1)
    out = io.StringIO()
    assert ob.launch("supervisor", tmp_path / "home", tmp_path / "data", 8801, None, out=out) == 0
    assert spawned == [] and "not delivered" in out.getvalue()


def test_owner_voice_choice_is_read_from_the_voice_script(tmp_path):
    voice = tmp_path / "voice"; (voice / "piper").mkdir(parents=True)
    exe, model = voice / "piper" / "piper.exe", voice / "ru_RU-dmitri-medium.onnx"
    exe.write_bytes(b"x"); model.write_bytes(b"x")
    (voice / "Start-Jeff-Voice.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$env:BOSSMAN_PIT_TTS_EXECUTABLE = '{exe}'\n"
        f"$env:BOSSMAN_PIT_TTS_MODEL_PATH = '{model}'\n"
        "$env:PATH = 'C:\\x;' + $env:PATH\n", encoding="utf-8")
    env = ob.jeff_voice_env(tmp_path)
    assert env == {"BOSSMAN_PIT_TTS_EXECUTABLE": str(exe), "BOSSMAN_PIT_TTS_MODEL_PATH": str(model)}


def test_child_env_points_at_one_data_root_and_drops_port_overrides(tmp_path):
    env = ob.child_env(tmp_path / "home", tmp_path / "data",
                       {"PATH": "C:\\bin", "BCC_PORT": "8800", "PYTHONPATH": "C:\\wt"})
    assert env["BCC_DATA_DIR"] == env["BOSSMAN_DATA_DIR"] == str(tmp_path / "data")
    assert "BCC_PORT" not in env and "PYTHONPATH" not in env
    assert env["PATH"].startswith(str(tmp_path / "home" / "runtime" / "Scripts"))
    argv = ob.command_for("backend", tmp_path / "home", tmp_path / "data", 8801, None)
    assert argv[1:] == ["-I", "-X", "utf8", "-m", "bcc", "--host", "127.0.0.1", "--port", "8801"]


def test_repoint_keeps_the_old_config_and_is_idempotent(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"core_url": "http://127.0.0.1:8800", "people": [1]}), encoding="utf-8")
    ident = os.stat(cfg).st_ino
    res = ob.repoint(cfg, 8801, "S")
    assert res["changed"] and res["before"] == "http://127.0.0.1:8800"
    assert os.stat(cfg).st_ino == ident          # same file: owner/ACL untouched
    assert json.loads(cfg.read_text(encoding="utf-8")) == {"core_url": "http://127.0.0.1:8801", "people": [1]}
    assert json.loads(Path(res["backup"]).read_text(encoding="utf-8"))["core_url"].endswith(":8800")
    assert ob.repoint(cfg, 8801, "T")["changed"] is False


def test_apply_agents_patches_only_differences_and_checks_models(tmp_path):
    (tmp_path / "token").write_text("t")
    agents = {1: {"id": 1, "name": "Пилот", "model_id": 1, "fallback_model_id": 2},
              4: {"id": 4, "name": "NEX", "model_id": 6, "fallback_model_id": None}}
    calls = []

    def api(port, token, method, path, body):
        calls.append((method, path, body))
        assert token == "t"
        if path == "/api/agents":
            return [dict(a) for a in agents.values()]
        if path == "/api/models":
            return [{"id": i} for i in (1, 2, 6, 9)]
        agents[int(path.rsplit("/", 1)[1])].update(body)
        return {}
    res = ob.apply_agents(8801, tmp_path, [{"id": 1, "model_id": 9, "fallback_model_id": 6},
                                           {"id": 4, "model_id": 6}], tmp_path / "ev", api=api)
    assert res["ok"]
    assert [c for c in calls if c[0] == "PATCH"] == [("PATCH", "/api/agents/1", {"model_id": 9, "fallback_model_id": 6})]
    assert json.loads((tmp_path / "ev" / "agents-before.json").read_text(encoding="utf-8"))[0]["model_id"] == 1
    with pytest.raises(SystemExit):
        ob.apply_agents(8801, tmp_path, [{"id": 1, "model_id": 99}], tmp_path / "ev", api=api)


def test_jeff_is_stopped_through_its_own_stop_flag_before_any_signal(tmp_path, monkeypatch):
    (tmp_path / "pit-v1.7").mkdir()
    signals = []
    monkeypatch.setattr(ob, "_wait_exit", lambda pid, seconds: True)
    monkeypatch.setattr(ob, "ctrl_c", lambda pid, python=None, event=0: signals.append(pid) or 0)
    res = ob.stop_process({"pid": 7, "kind": "jeff"}, tmp_path)
    assert res["stopped"] == "graceful" and res["how"] == ["stop.flag"] and signals == []
    assert (tmp_path / "pit-v1.7" / "stop.flag").is_file()


def test_a_backend_that_ignores_ctrl_c_is_not_killed_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "_wait_exit", lambda pid, seconds: False)
    monkeypatch.setattr(ob, "ctrl_c", lambda pid, python=None, event=0: 0)
    killed = []
    monkeypatch.setattr(ob.subprocess, "run", lambda *a, **k: killed.append(a))
    assert ob.stop_process({"pid": 7, "kind": "backend"}, tmp_path)["stopped"] == "NO"
    assert killed == []
