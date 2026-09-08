"""Live PostgreSQL guard proof; unavailable server is explicitly OWNER_REQUIRED.

Set BCC_TEST_POSTGRES_URL to an asyncpg test DSN, then run this test. Only a new
random schema is changed, then dropped. No existing tasks or agents are touched.
"""
import json
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from bcc.db import Database


async def test_postgres_provenance_guard_with_real_server():
    url = os.environ.get("BCC_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("OWNER_REQUIRED: set BCC_TEST_POSTGRES_URL to run real PostgreSQL provenance guard proof")
    schema = "bcc_provenance_" + uuid.uuid4().hex
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
            await conn.execute(sa.text("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, status TEXT, provenance JSON)"))
            await conn.execute(sa.text("INSERT INTO task_runs VALUES (1, 'running', NULL), (2, 'completed', NULL)"))
        await database._install_provenance_guard()
        await database._install_provenance_guard()
        original = json.dumps({"schema_version": 1, "agent_id": 7})
        async with database.engine.begin() as conn:
            await conn.execute(sa.text("UPDATE task_runs SET provenance=CAST(:record AS JSON) WHERE id=1"), {"record": original})
            await conn.execute(sa.text("UPDATE task_runs SET provenance=provenance, status='completed' WHERE id=1"))
        for run_id, record, error in ((1, '{"agent_id":8}', 'immutable'),
                                      (1, None, 'immutable'),
                                      (2, original, 'backfill')):
            with pytest.raises(sa.exc.DBAPIError, match=error):
                async with database.engine.begin() as conn:
                    await conn.execute(sa.text("UPDATE task_runs SET provenance=CAST(:record AS JSON) WHERE id=:id"),
                                       {"record": record, "id": run_id})
        async with database.engine.connect() as conn:
            rows = (await conn.execute(sa.text("SELECT id, provenance FROM task_runs ORDER BY id"))).all()
        assert rows[0][1] == json.loads(original)
        assert rows[1][1] is None
    finally:
        await database.close()
        if created:
            async with admin.begin() as conn:
                await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
