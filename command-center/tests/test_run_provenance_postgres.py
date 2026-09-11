"""Real PostgreSQL contracts, in a fresh isolated schema for every test.

BCC_REQUIRE_POSTGRES=1 makes missing configuration an error. CI always sets it.
Connection/service failures are never converted to skips. Outside this mandatory
CI gate, a developer without a DSN sees an explicit unavailable-service skip.
"""
import json
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from bcc.db import Database


def _postgres_url():
    url = os.environ.get("BCC_TEST_POSTGRES_URL")
    if not url:
        if os.environ.get("BCC_REQUIRE_POSTGRES") == "1":
            pytest.fail("POSTGRES_EVIDENCE_REQUIRED: BCC_TEST_POSTGRES_URL is missing", pytrace=False)
        pytest.skip("POSTGRES_LIVE_REQUIRED: set BCC_TEST_POSTGRES_URL or use postgres-contracts CI")
    try:
        driver = sa.engine.make_url(url).drivername
    except (ValueError, sa.exc.ArgumentError):
        pytest.fail("PostgreSQL test URL is malformed", pytrace=False)
    if driver != "postgresql+asyncpg":
        pytest.fail("PostgreSQL proof requires the postgresql+asyncpg driver", pytrace=False)
    return url


@pytest.fixture
async def postgres_db():
    url = _postgres_url()
    schema = "bcc_contracts_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    database = Database(url)
    await database.engine.dispose()
    database.engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    created = False
    try:
        async with admin.begin() as conn:
            await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            created = True
        async with database.engine.begin() as conn:
            # Same relevant column types/nullability as task_runs. No existing
            # application table or owner task is read, rewritten or dropped.
            await conn.execute(sa.text("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, status TEXT, provenance JSON, result TEXT)"))
            await conn.execute(sa.text("CREATE TABLE rollback_control (id INTEGER PRIMARY KEY)"))
        for _ in range(2):
            await database._install_terminal_run_guard()
            await database._install_provenance_guard()
        yield database
    finally:
        await database.close()
        try:
            if created:
                async with admin.begin() as conn:
                    await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            await admin.dispose()


async def _read_run(database, run_id):
    async with database.engine.connect() as conn:
        row = (await conn.execute(sa.text("SELECT * FROM task_runs WHERE id=:id"), {"id": run_id})).one()
        return dict(row._mapping)


async def _deny_and_prove_rollback(database, *, run_id, values, mutation, message):
    before = await _read_run(database, run_id)
    with pytest.raises(sa.exc.DBAPIError, match=message):
        async with database.engine.begin() as conn:
            # A second independent table proves transaction rollback, not merely
            # that the attempted row retains its original value.
            await conn.execute(sa.text("INSERT INTO rollback_control VALUES (1)"))
            await conn.execute(sa.text(mutation), {"id": run_id, **values})
    assert await _read_run(database, run_id) == before
    async with database.engine.connect() as conn:
        assert (await conn.execute(sa.text("SELECT count(*) FROM rollback_control"))).scalar_one() == 0


async def test_postgres_provenance_guard_with_real_server(postgres_db):
    database = postgres_db
    original = json.dumps({"schema_version": 1, "agent_id": 7})
    async with database.engine.begin() as conn:
        await conn.execute(sa.text("INSERT INTO task_runs (id,status) VALUES (1,'running'), (2,'completed')"))
        await conn.execute(sa.text("UPDATE task_runs SET provenance=CAST(:record AS JSON) WHERE id=1"), {"record": original})
        await conn.execute(sa.text("UPDATE task_runs SET provenance=provenance, status='completed' WHERE id=1"))
    assert (await _read_run(database, 1))["provenance"] == json.loads(original)
    for run_id, record, error in ((1, '{"agent_id":8}', 'immutable'),
                                  (1, None, 'immutable'),
                                  (1, 'null', 'immutable'),
                                  (2, original, 'backfill')):
        await _deny_and_prove_rollback(database, run_id=run_id, values={"record": record},
                                      mutation="UPDATE task_runs SET provenance=CAST(:record AS JSON) WHERE id=:id",
                                      message=error)
    assert (await _read_run(database, 2))["provenance"] is None


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_postgres_terminal_status_is_immutable_and_rolls_back(postgres_db, terminal):
    database = postgres_db
    async with database.engine.begin() as conn:
        await conn.execute(sa.text("INSERT INTO task_runs (id,status,result) VALUES (1,:status,'original')"),
                           {"status": terminal})
    for attempted in ("queued", "leased", "running", "stopped", None,
                      "failed" if terminal == "completed" else "completed"):
        await _deny_and_prove_rollback(database, run_id=1, values={"status": attempted},
                                      mutation="UPDATE task_runs SET status=:status, result='forged' WHERE id=:id",
                                      message="terminal run status is immutable")


async def test_postgres_normal_transitions_retry_and_receipt_writes_commit(postgres_db):
    database = postgres_db
    async with database.engine.begin() as conn:
        await conn.execute(sa.text("INSERT INTO task_runs (id,status) VALUES (1,'queued')"))
    for status in ("leased", "running"):
        async with database.engine.begin() as conn:
            await conn.execute(sa.text("UPDATE task_runs SET status=:status WHERE id=1"), {"status": status})
        assert (await _read_run(database, 1))["status"] == status
    async with database.engine.begin() as conn:
        await conn.execute(sa.text("UPDATE task_runs SET provenance=CAST(:record AS JSON) WHERE id=1"),
                           {"record": json.dumps({"schema_version": 1, "agent_id": 3})})
        await conn.execute(sa.text("UPDATE task_runs SET status='completed', result='verified result' WHERE id=1"))
    before = await _read_run(database, 1)
    async with database.engine.begin() as conn:
        await conn.execute(sa.text("UPDATE task_runs SET status='completed', provenance=provenance, result='verified receipt' WHERE id=1"))
        # Retry is a new run; immutability must not prevent its creation.
        await conn.execute(sa.text("INSERT INTO task_runs (id,status) VALUES (2,'queued')"))
    after = await _read_run(database, 1)
    assert after["status"] == "completed" and after["result"] == "verified receipt"
    assert after["provenance"] == before["provenance"]
    assert (await _read_run(database, 2))["status"] == "queued"


def test_required_postgres_evidence_cannot_skip_missing_configuration(monkeypatch):
    monkeypatch.delenv("BCC_TEST_POSTGRES_URL", raising=False)
    monkeypatch.setenv("BCC_REQUIRE_POSTGRES", "1")
    with pytest.raises(pytest.fail.Exception, match="POSTGRES_EVIDENCE_REQUIRED"):
        _postgres_url()


def test_required_postgres_evidence_cannot_substitute_sqlite(monkeypatch):
    monkeypatch.setenv("BCC_TEST_POSTGRES_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("BCC_REQUIRE_POSTGRES", "1")
    with pytest.raises(pytest.fail.Exception, match="postgresql\\+asyncpg"):
        _postgres_url()
