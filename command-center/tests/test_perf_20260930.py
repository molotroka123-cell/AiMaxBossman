"""perf lane 2026-09-30: measured start-up / request-path fixes. Every test is red on the code before the fix.

Numbers behind each fix live in docs/audits/PERF_20260930.md (method, raw values, verdicts).
"""
from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import time
from datetime import datetime
from types import SimpleNamespace

import sqlalchemy as sa

from bcc.db import Database, V2_NEW_COLUMNS, task_runs as runs_t, tasks as tasks_t

from .conftest import client_for, make_settings, start_app


def _count_statements(engine, needle: str):
    seen: list[str] = []

    @sa.event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _on(conn, cursor, statement, parameters, context, executemany):
        if needle in statement:
            seen.append(statement)

    return seen


async def _max_loop_gap(coro, tick: float = 0.01) -> float:
    """Longest time the event loop could not run a 10 ms sleeper while `coro` executed."""
    gaps = [0.0]
    stop = asyncio.Event()

    async def sleeper():
        while not stop.is_set():
            t = time.perf_counter()
            await asyncio.sleep(tick)
            gaps[0] = max(gaps[0], time.perf_counter() - t - tick)

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0.05)
    await coro
    stop.set()
    await task
    return gaps[0]


# ---------------------------------------------------------------- db: migration + indexes

def test_second_start_issues_no_alter_for_columns_that_already_exist(tmp_path):
    async def scenario():
        db = Database(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
        await db.create_all()
        alters = _count_statements(db.engine, "ALTER TABLE")
        await db.create_all()
        await db.close()
        return alters

    assert asyncio.run(scenario()) == []          # old code: one failing ALTER per V2 column (26+)


def test_migration_still_adds_a_column_a_legacy_database_lacks(tmp_path):
    """Negative control: the fast path must not skip columns that are genuinely missing."""
    import sqlite3
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title VARCHAR(300), prompt TEXT NOT NULL, agent_id INTEGER,"
                " status VARCHAR(24), priority INTEGER, max_retries INTEGER, schedule_id INTEGER,"
                " created_at DATETIME, updated_at DATETIME)")
    con.commit()
    con.close()

    async def scenario():
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        await db.close()

    asyncio.run(scenario())
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    con.close()
    assert {c for t, c, _ in V2_NEW_COLUMNS if t == "tasks"} <= cols


def test_hot_lookup_indexes_exist_on_new_and_on_legacy_databases(tmp_path):
    import sqlite3
    path = tmp_path / "idx.db"

    async def create():
        db = Database(f"sqlite+aiosqlite:///{path}")
        await db.create_all()
        await db.close()

    def index_names():
        con = sqlite3.connect(path)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        con.close()
        return names

    asyncio.run(create())
    wanted = {"ix_task_runs_task_id", "ix_task_runs_status", "ix_tool_calls_task_id", "ix_approvals_task_id"}
    assert wanted <= index_names()
    con = sqlite3.connect(path)                 # a database created before these indexes existed
    for name in wanted:
        con.execute(f"DROP INDEX {name}")
    con.commit()
    con.close()
    assert not wanted & index_names()
    asyncio.run(create())
    assert wanted <= index_names()


# ---------------------------------------------------------------- api: /api/tasks N+1

async def test_task_list_reads_last_runs_in_a_constant_number_of_queries(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        now = datetime(2026, 9, 30, 12, 0, 0)
        expect: dict[int, int | None] = {}
        async with svc.db.session() as s:
            for i in range(1, 13):
                await s.execute(sa.insert(tasks_t).values(id=i, title=f"t{i}", prompt="p", status="completed",
                                                          created_at=now, updated_at=now))
                last = None
                for attempt in range(i % 4):              # 0..3 runs; task with 0 runs has no last_run
                    res = await s.execute(sa.insert(runs_t).values(task_id=i, attempt=attempt, status="completed",
                                                                   result=f"r{i}.{attempt}"))
                    last = int(res.inserted_primary_key[0])
                expect[i] = last
            await s.commit()
        selects = _count_statements(svc.db.engine, "SELECT")
        async with client_for(app, svc) as client:
            r = await client.get("/api/tasks?limit=100")
        assert r.status_code == 200
        rows = {t["id"]: t for t in r.json()}
        assert len(rows) == 12
        for tid, run_id in expect.items():
            got = rows[tid]["last_run"]
            assert (got["id"] if got else None) == run_id
        assert len(selects) <= 4, f"{len(selects)} SELECTs for 12 tasks (was 1 + N)"   # old code: 13
    finally:
        await svc.stop()


# ---------------------------------------------------------------- event loop must not block

async def test_owner_run_status_runs_its_subprocess_off_the_event_loop(monkeypatch):
    from bcc.features import v15_owner_run as mod
    monkeypatch.setattr(mod, "_call", lambda svc, command, body=None: (time.sleep(0.3), {"status": "idle"})[1])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=SimpleNamespace(owner_input=None))))
    gap = await _max_loop_gap(mod.status(request))
    assert gap < 0.12, f"event loop stalled {gap * 1000:.0f} ms (old code: ~300 ms)"


async def test_skill_catalog_scan_runs_off_the_event_loop(monkeypatch):
    from bcc.features import skills as mod

    class SlowCatalog:
        def entries(self, revoked):
            time.sleep(0.3)
            return []

    async def no_revocations(svc):
        return {}

    monkeypatch.setattr(mod, "_catalog", lambda svc: SlowCatalog())
    monkeypatch.setattr(mod, "catalog_revocations", no_revocations)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(svc=SimpleNamespace())))
    gap = await _max_loop_gap(mod.list_catalog(request))
    assert gap < 0.12, f"event loop stalled {gap * 1000:.0f} ms (old code: ~300 ms)"


# ---------------------------------------------------------------- closed loopback port fails fast

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_portcheck_knows_a_closed_port_from_a_listening_one(monkeypatch):
    from bcc import portcheck
    portcheck._cache = None
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    listening = srv.getsockname()[1]
    try:
        assert await portcheck.loopback_port_closed(listening) is False
        assert await portcheck.loopback_port_closed(_free_port()) is True
    finally:
        srv.close()
    assert await portcheck.loopback_port_closed(_free_port(), host="192.168.1.5") is False   # not loopback: unknown
    portcheck._cache = None
    monkeypatch.setattr(portcheck, "_listening", lambda: None)                              # cannot read sockets
    assert await portcheck.loopback_port_closed(_free_port()) is False                      # unknown, not "closed"
    portcheck._cache = None


async def test_apps_probe_of_a_closed_port_does_not_open_a_connection():
    from bcc.features import apps
    from bcc import portcheck
    portcheck._cache = None

    class NoNetwork:
        bcc_network = True                      # what _probe_client() marks a real network client with

        async def get(self, *a, **k):
            raise AssertionError("closed port must be answered without a network round trip")

    t = time.perf_counter()
    out = await apps._probe({"id": "x", "port": _free_port()}, NoNetwork())
    assert out["status"] == "STOPPED" and out["reachable"] is False
    assert "не отвечает" in out["detail"]
    assert time.perf_counter() - t < 0.5


async def test_apps_probe_keeps_using_a_substitute_transport(monkeypatch):
    """Negative control: a mocked client (tests, embedders) is still asked, even for a port nobody listens on."""
    import httpx
    from bcc.features import apps
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await apps._probe({"port": _free_port(), "health_path": "/health"}, client)
    assert asked and out["reachable"] is True


# ---------------------------------------------------------------- start-up weight

def test_importing_the_autonomy_feature_does_not_load_the_remote_client_stack():
    code = ("import sys, bcc.features.v15_autonomy as m; "
            "print('bossman_v3.autonomy_kernel' in sys.modules, 'redis' in sys.modules, 'playwright' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.split() == ["False", "False", "False"], out.stdout + out.stderr      # old code: True True True


def test_build_identity_reuses_the_boot_resolution_instead_of_spawning_git_again(monkeypatch):
    import importlib
    from bcc import build_identity, run_provenance
    calls = []

    def fake_sha(repo=None):
        calls.append(1)
        return "a" * 40

    monkeypatch.setattr(run_provenance, "repository_sha", fake_sha)
    try:
        importlib.reload(build_identity)
        boot = len(calls)
        build_identity.source_identity()
        assert boot == 1 and len(calls) == 1       # old code: the first call resolved (git) a second time
    finally:
        monkeypatch.undo()
        importlib.reload(build_identity)
