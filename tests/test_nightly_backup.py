"""tools/nightly_backup.py on synthetic databases only (never the owner's live files)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import nightly_backup as nb  # noqa: E402
from bossman_shared.exchange_assistant.store import SCHEMA as EXCHANGE_SCHEMA  # noqa: E402

def _idle():
    return []


@pytest.fixture(autouse=True)
def _keep_test_process_priority(monkeypatch):
    """The real call would drop the whole pytest process into background IO mode."""
    monkeypatch.setattr(nb, "lower_io_priority", lambda: "test")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fake %LOCALAPPDATA% with every configured store; the real one is unreachable from here."""
    local = tmp_path / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    data_dir = local / "Bossman" / "CommandCenter"
    (data_dir / "exchange-assistant").mkdir(parents=True)
    (data_dir / "fx").mkdir()
    (local / "Bossman" / "telegram-companion").mkdir(parents=True)

    bcc = sqlite3.connect(data_dir / "bcc.db", isolation_level=None)
    bcc.execute("PRAGMA journal_mode=WAL")
    bcc.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, body TEXT)")
    bcc.execute('CREATE TABLE "odd ""name"" table"(x)')
    bcc.executemany("INSERT INTO tasks(body) VALUES(?)", [(f"t{i}",) for i in range(50)])
    bcc.close()

    ex = sqlite3.connect(data_dir / "exchange-assistant" / "assistant.db", isolation_level=None)
    ex.execute("PRAGMA journal_mode=WAL")
    ex.executescript(EXCHANGE_SCHEMA)
    ex.execute("INSERT INTO events(ts, kind, ref, detail) VALUES('2026-10-20','quote','q1','{}')")
    ex.close()

    fx = sqlite3.connect(data_dir / "fx" / "fx.db")
    fx.execute("CREATE TABLE fx_runs(id INTEGER PRIMARY KEY, day_local TEXT, outcome TEXT)")
    fx.execute("INSERT INTO fx_runs(day_local, outcome) VALUES('2026-10-19','done')")
    fx.commit()
    fx.close()

    comp = sqlite3.connect(local / "Bossman" / "telegram-companion" / "companion.sqlite3")
    comp.execute("CREATE TABLE inbox(id INTEGER PRIMARY KEY, who TEXT, body TEXT)")
    comp.execute("INSERT INTO inbox(who, body) VALUES('owner','sealed')")
    comp.commit()
    comp.close()
    # <data_dir>/telegram-companion/companion.sqlite3 deliberately absent: "missing" path.
    return {"local": local, "data_dir": data_dir, "dest": tmp_path / "Backups"}


def _run(env, *extra, probe=_idle):
    return nb.main(["--data-dir", str(env["data_dir"]), "--dest", str(env["dest"]), *extra], jobs_probe=probe)


def _only_run(dest: Path) -> Path:
    runs = nb.list_runs(dest)
    assert len(runs) == 1
    return runs[0]


# ---------------------------------------------------------------- happy path + manifest

def test_backup_writes_verified_copies_and_manifest(env, capsys):
    assert _run(env) == nb.EXIT_OK
    run = _only_run(env["dest"])
    manifest = json.loads((run / nb.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["status"] == nb.PASS
    by = {d["name"]: d for d in manifest["databases"]}
    assert by["bcc"]["tables"]["tasks"] == 50
    assert by["bcc"]["tables"]['odd "name" table'] == 0
    assert by["exchange-assistant"]["tables"]["events"] == 1
    assert by["exchange-assistant"]["append_only_triggers"] == ["events_no_update", "events_no_delete", "deals_no_delete"]
    for name in ("bcc", "exchange-assistant", "fx", "telegram-companion"):
        entry = by[name]
        copy = run / f"{name}.db"
        assert entry["status"] == nb.PASS and entry["integrity_check"] == "ok"
        assert entry["sha256"] == nb.sha256_file(copy) and entry["size"] == copy.stat().st_size
        assert "duration_s" in entry and Path(entry["source"]).is_file()
        # a self-contained file: no WAL left behind, the copy is in rollback-journal mode
        assert not Path(str(copy) + "-wal").exists()
        db = sqlite3.connect(nb._ro_uri(copy, immutable=True), uri=True)
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        db.close()
    assert manifest["missing"] == ["telegram-companion-home"]
    assert by["telegram-companion-home"]["status"] == nb.MISSING
    assert manifest["duration_s"] >= 0 and manifest["run"] == run.name


def test_wal_content_is_captured_not_just_the_main_file(env):
    """The live exchange DB keeps its schema in -wal (4 KiB main file); a file copy would be empty."""
    path = env["data_dir"] / "bcc.db"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executemany("INSERT INTO tasks(body) VALUES(?)", [("wal-only",)] * 25)
    assert Path(str(path) + "-wal").stat().st_size > 0      # rows live only in the WAL right now
    assert _run(env) == nb.EXIT_OK
    writer.close()
    manifest = nb.read_manifest(_only_run(env["dest"]))
    assert {d["name"]: d for d in manifest["databases"]}["bcc"]["tables"]["tasks"] == 75


# ---------------------------------------------------------------- concurrent writes

def test_backup_during_concurrent_writes_is_a_consistent_snapshot(env):
    path = env["data_dir"] / "bcc.db"
    stop = threading.Event()
    written = {"n": 0, "err": None}

    def writer():
        db = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            while not stop.is_set():
                db.execute("BEGIN IMMEDIATE")
                db.executemany("INSERT INTO tasks(body) VALUES(?)", [("x" * 200,)] * 20)
                db.execute("COMMIT")
                written["n"] += 20
        except Exception as exc:  # noqa: BLE001
            written["err"] = exc
        finally:
            db.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        deadline = time.time() + 5
        while written["n"] < 200 and time.time() < deadline:
            time.sleep(0.01)
        rc = _run(env)
    finally:
        stop.set()
        thread.join(10)
    assert written["err"] is None, written["err"]
    assert rc == nb.EXIT_OK
    run = _only_run(env["dest"])
    entry = {d["name"]: d for d in nb.read_manifest(run)["databases"]}["bcc"]
    snap = entry["tables"]["tasks"]
    assert 50 + 200 <= snap <= 50 + written["n"]
    db = sqlite3.connect(nb._ro_uri(run / "bcc.db", immutable=True), uri=True)
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == snap   # counts == the copied snapshot
    db.close()
    live = sqlite3.connect(path)
    assert live.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 50 + written["n"]  # writer never lost a row
    live.close()


def test_backup_never_writes_the_live_files(env):
    sources = [p for _s, p in nb.resolve_sources(env["data_dir"]) if p.is_file()]
    before = {p: nb.sha256_file(p) for p in sources}
    assert _run(env) == nb.EXIT_OK
    assert {p: nb.sha256_file(p) for p in sources} == before


# ---------------------------------------------------------------- failures -> exit 2

def _corrupt(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    page = 4096
    raw[page:page * 2] = b"\xde\xad\xbe\xef" * (page // 4)   # page 2 = a table b-tree root
    path.write_bytes(bytes(raw))


def test_corrupted_copy_is_detected(env, monkeypatch):
    original = nb.online_copy

    def corrupting(source, target):
        snap = original(source, target)
        if Path(target).name == "bcc.db":
            _corrupt(Path(target))
        return snap

    monkeypatch.setattr(nb, "online_copy", corrupting)
    assert _run(env) == nb.EXIT_FAIL
    manifest = nb.read_manifest(_only_run(env["dest"]))
    assert manifest["status"] == nb.FAIL
    bcc = {d["name"]: d for d in manifest["databases"]}["bcc"]
    assert bcc["status"] == nb.FAIL and bcc["errors"]
    others = [d for d in manifest["databases"] if d["name"] not in ("bcc", "telegram-companion-home")]
    assert all(d["status"] == nb.PASS for d in others)      # one bad DB does not hide the rest


def test_corruption_after_the_run_is_caught_by_verify_run(env):
    assert _run(env) == nb.EXIT_OK
    run = _only_run(env["dest"])
    assert nb.verify_run(run)["ok"]
    _corrupt(run / "fx.db")
    report = nb.verify_run(run)
    assert not report["ok"]
    fx = {r["name"]: r for r in report["results"]}["fx"]
    assert "sha256_mismatch" in fx["errors"]


def test_missing_append_only_triggers_fail_the_run(env):
    ex = sqlite3.connect(env["data_dir"] / "exchange-assistant" / "assistant.db", isolation_level=None)
    ex.execute("DROP TRIGGER events_no_delete")
    ex.close()
    assert _run(env) == nb.EXIT_FAIL
    entry = {d["name"]: d for d in nb.read_manifest(_only_run(env["dest"]))["databases"]}["exchange-assistant"]
    assert entry["status"] == nb.FAIL
    assert entry["errors"] == ["append_only_triggers_missing:events_no_delete"]


def test_row_count_mismatch_is_detected(env, tmp_path):
    db_path = tmp_path / "x.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE a(x)")
    db.execute("INSERT INTO a VALUES(1)")
    db.commit()
    db.close()
    assert nb.verify_copy(db_path, {"a": 1})["ok"]
    assert nb.verify_copy(db_path, {"a": 2})["errors"] == ["row_count_mismatch:a"]
    assert nb.verify_copy(db_path, {"a": 1, "b": 0})["errors"] == ["table_list_mismatch"]


# ---------------------------------------------------------------- missing sources

def test_missing_source_is_reported_not_fatal(env):
    (env["data_dir"] / "fx" / "fx.db").unlink()
    assert _run(env) == nb.EXIT_OK
    manifest = nb.read_manifest(_only_run(env["dest"]))
    assert manifest["status"] == nb.PASS
    assert set(manifest["missing"]) == {"fx", "telegram-companion-home"}


def test_no_source_at_all_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    empty = tmp_path / "Local" / "Bossman" / "CommandCenter"
    empty.mkdir(parents=True)
    rc = nb.main(["--data-dir", str(empty), "--dest", str(tmp_path / "B")], jobs_probe=_idle)
    assert rc == nb.EXIT_FAIL
    manifest = nb.read_manifest(_only_run(tmp_path / "B"))
    assert manifest["status"] == nb.FAIL and manifest["errors"] == ["no_source_database_found"]


# ---------------------------------------------------------------- retention

def _fake_run(dest: Path, name: str, status: str) -> Path:
    run = dest / name
    run.mkdir(parents=True)
    (run / nb.MANIFEST).write_text(json.dumps({"tool": nb.TOOL, "status": status}), encoding="utf-8")
    return run


def test_retention_never_touches_foreign_directories(tmp_path):
    dest = tmp_path / "B"
    foreign = dest / "20200101-0000"                 # run-shaped name, not ours (no manifest)
    foreign.mkdir(parents=True)
    other = dest / "20200101-0001"
    other.mkdir()
    (other / nb.MANIFEST).write_text(json.dumps({"tool": "something-else", "status": "PASS"}), encoding="utf-8")
    for day in range(1, 5):
        _fake_run(dest, f"202610{day:02d}-0300", nb.PASS)
    result = nb.apply_retention(dest, keep=1)
    assert result["deleted"] == ["20261001-0300", "20261002-0300", "20261003-0300"]
    assert foreign.is_dir() and other.is_dir()


def test_retention_keeps_last_14_runs(tmp_path):
    dest = tmp_path / "B"
    for day in range(1, 21):
        _fake_run(dest, f"202610{day:02d}-0300", nb.PASS)
    (dest / "not-a-run").mkdir()
    (dest / "notes.txt").write_text("keep me", encoding="utf-8")
    result = nb.apply_retention(dest, keep=14)
    assert result["deleted"] == [f"202610{d:02d}-0300" for d in range(1, 7)]
    assert [r.name for r in nb.list_runs(dest)] == [f"202610{d:02d}-0300" for d in range(7, 21)]
    assert (dest / "not-a-run").is_dir() and (dest / "notes.txt").is_file()


def test_retention_never_deletes_the_newest_pass(tmp_path):
    dest = tmp_path / "B"
    _fake_run(dest, "20261001-0300", nb.PASS)
    _fake_run(dest, "20261002-0300", nb.PASS)
    for day in range(3, 20):
        _fake_run(dest, f"202610{day:02d}-0300", nb.FAIL)
    _fake_run(dest, "20261019-0300-2", "RUNNING")  # crashed run: start manifest only = not PASS
    result = nb.apply_retention(dest, keep=14)
    assert result["protected_newest_pass"] == "20261002-0300"
    names = [r.name for r in nb.list_runs(dest)]
    assert "20261002-0300" in names and "20261001-0300" not in names
    assert len(names) == 15 and names[-1] == "20261019-0300-2"


def test_retention_runs_after_backup(env):
    for day in range(1, 16):
        _fake_run(env["dest"], f"202609{day:02d}-0300", nb.PASS)
    assert _run(env, "--keep", "3") == nb.EXIT_OK
    names = [r.name for r in nb.list_runs(env["dest"])]
    assert len(names) == 3 and nb.run_status(env["dest"] / names[-1]) == nb.PASS


def test_same_minute_runs_do_not_collide(env):
    assert _run(env) == nb.EXIT_OK
    assert _run(env) == nb.EXIT_OK
    names = sorted(r.name for r in nb.list_runs(env["dest"]))
    assert len(names) == 2 and len(set(names)) == 2


# ---------------------------------------------------------------- dry run

def test_dry_run_writes_nothing(env, capsys):
    assert _run(env, "--dry-run") == nb.EXIT_OK
    assert not env["dest"].exists()
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run" and out["would_refuse"] is False
    assert {s["name"]: s["exists"] for s in out["sources"]}["telegram-companion-home"] is False


# ---------------------------------------------------------------- restore drill

def test_restore_drill_restores_into_temp_and_verifies(env, capsys):
    assert _run(env) == nb.EXIT_OK
    capsys.readouterr()
    live = {p: nb.sha256_file(p) for _s, p in nb.resolve_sources(env["data_dir"]) if p.is_file()}
    assert _run(env, "--restore-drill") == nb.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == nb.PASS
    assert {r["name"] for r in report["results"]} == {"bcc", "exchange-assistant", "fx", "telegram-companion"}
    assert all(r["ok"] and r["integrity_check"] == "ok" for r in report["results"])
    workdir = Path(report["workdir"])
    assert not workdir.exists()                       # temp dir removed by default
    assert all(Path(r["restored_to"]).parent == workdir for r in report["results"])
    assert {p: nb.sha256_file(p) for p in live} == live  # live files untouched


def test_restore_drill_keep_dir_leaves_usable_files(env, capsys):
    assert _run(env) == nb.EXIT_OK
    capsys.readouterr()
    assert _run(env, "--restore-drill", "--keep-drill-dir") == nb.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    restored = Path(report["workdir"]) / "exchange-assistant.db"
    try:
        db = sqlite3.connect(restored)
        with pytest.raises(sqlite3.IntegrityError):  # append-only trigger survives the round trip
            db.execute("DELETE FROM events")
        db.close()
    finally:
        import shutil
        shutil.rmtree(report["workdir"], ignore_errors=True)


def test_restore_drill_uses_newest_pass_and_fails_on_tampered_backup(env, capsys):
    assert _run(env) == nb.EXIT_OK
    run = _only_run(env["dest"])
    _corrupt(run / "bcc.db")
    capsys.readouterr()
    assert _run(env, "--restore-drill") == nb.EXIT_FAIL
    report = json.loads(capsys.readouterr().out)
    bad = {r["name"]: r for r in report["results"]}["bcc"]
    assert not bad["ok"] and "sha256" in bad["errors"][0]


def test_restore_drill_without_any_pass_run_fails(tmp_path, capsys):
    dest = tmp_path / "B"
    _fake_run(dest, "20261020-0300", nb.FAIL)
    assert nb.main(["--dest", str(dest), "--data-dir", str(tmp_path / "dd"), "--restore-drill"], jobs_probe=_idle) == nb.EXIT_FAIL
    assert json.loads(capsys.readouterr().out)["errors"] == ["no_pass_run"]


# ---------------------------------------------------------------- owner priority: active direct-gen job

def test_refuses_while_a_direct_gen_job_is_active(env, capsys):
    probe = lambda: [{"job_id": "a", "status": "completed"}, {"job_id": "b", "status": "generating"}]  # noqa: E731
    assert _run(env, probe=probe) == nb.EXIT_BUSY
    assert not env["dest"].exists() or not nb.list_runs(env["dest"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "SKIPPED_BUSY" and out["direct_gen"]["active"] == ["b"]


def test_queued_and_unknown_states_count_as_active():
    assert nb.active_jobs(lambda: [{"id": "q", "status": "queued"}])["busy"]
    assert nb.active_jobs(lambda: [{"id": "w", "status": "weird"}])["busy"]
    assert not nb.active_jobs(lambda: [{"id": "c", "status": s} for s in ("completed", "failed", "cancelled")])["busy"]


def test_unreachable_backend_proceeds(env):
    def down():
        raise ConnectionError("URLError")
    assert _run(env, probe=down) == nb.EXIT_OK
    assert nb.run_status(_only_run(env["dest"])) == nb.PASS


def test_unreadable_job_list_refuses(env):
    def unauthorized():
        raise RuntimeError("HTTP 401")
    assert _run(env, probe=unauthorized) == nb.EXIT_BUSY


def test_http_probe_sends_owner_headers(tmp_path):
    import http.server

    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.update(path=self.path, token=self.headers.get("X-BCC-Token"), who=self.headers.get("X-Participant"))
            body = json.dumps({"jobs": [{"job_id": "z", "status": "loading"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        token = tmp_path / "token"
        token.write_text("tok-123\n", encoding="utf-8")
        probe = nb.http_jobs_probe(f"http://127.0.0.1:{server.server_port}", token)
        state = nb.active_jobs(probe)
    finally:
        server.shutdown()
        server.server_close()
    assert seen == {"path": "/api/direct-gen/jobs", "token": "tok-123", "who": "owner"}
    assert state["busy"] and state["active"] == ["z"]


def test_http_probe_unreachable_is_connection_error(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    probe = nb.http_jobs_probe(f"http://127.0.0.1:{port}", tmp_path / "missing-token", timeout=2)
    assert nb.active_jobs(probe) == {"busy": False, "reason": "backend_unreachable:URLError", "active": []}


# ---------------------------------------------------------------- misc safety

def test_lock_prevents_parallel_runs(env, capsys):
    env["dest"].mkdir(parents=True)
    (env["dest"] / nb.LOCK_NAME).write_text("{}", encoding="utf-8")
    assert _run(env) == nb.EXIT_BUSY
    assert json.loads(capsys.readouterr().out)["status"] == "SKIPPED_LOCKED"


def test_defaults_point_at_the_owner_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert nb.default_data_dir() == tmp_path / "Bossman" / "CommandCenter"
    assert nb.default_dest() == tmp_path / "Bossman" / "Backups"
    paths = {s.name: p for s, p in nb.resolve_sources(nb.default_data_dir())}
    assert paths["telegram-companion"] == tmp_path / "Bossman" / "telegram-companion" / "companion.sqlite3"
    assert paths["exchange-assistant"] == tmp_path / "Bossman" / "CommandCenter" / "exchange-assistant" / "assistant.db"
    assert paths["fx"] == tmp_path / "Bossman" / "CommandCenter" / "fx" / "fx.db"


def test_paths_agree_with_the_owning_modules(monkeypatch, tmp_path):
    from bossman_shared.exchange_assistant.rates import default_fx_db
    from bossman_shared.exchange_assistant.store import default_db
    env = {"LOCALAPPDATA": str(tmp_path)}
    paths = {s.name: p for s, p in nb.resolve_sources(tmp_path / "Bossman" / "CommandCenter")}
    assert paths["exchange-assistant"] == default_db(env)
    assert paths["fx"] == default_fx_db(env)


def test_io_priority_is_best_effort():
    import subprocess
    code = "import sys; sys.path.insert(0, sys.argv[1]); import nightly_backup as nb; print(nb.lower_io_priority())"
    out = subprocess.run([sys.executable, "-c", code, str(ROOT / "tools")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() in {"background", "nice10", "unavailable"}


def test_run_names_sort_chronologically(tmp_path):
    for name in ("20261020-0300-10", "20261020-0300-2", "20261020-0300", "20261019-2359"):
        _fake_run(tmp_path, name, nb.PASS)
    assert [r.name for r in nb.list_runs(tmp_path)] == ["20261019-2359", "20261020-0300", "20261020-0300-2", "20261020-0300-10"]
    assert nb.new_run_dir(tmp_path, dt.datetime(2026, 10, 20, 3, 0)).name == "20261020-0300-11"
