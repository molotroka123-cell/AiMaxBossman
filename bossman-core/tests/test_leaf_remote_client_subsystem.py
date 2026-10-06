"""authored_by_lane (opsplug): bossman.remote_client.subsystem - Postgres-backed device store, honest degradation."""
import pytest

from bossman import db as db_mod
from bossman import errors
from bossman.remote_client import subsystem
from bossman.remote_client.auth import SCOPE_CHAT
from bossman.remote_client.service import DeviceService, get_service, reset_service, set_service
from bossman.remote_client.store import DDL, InMemoryDeviceStore, PostgresDeviceStore


class _Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *a):
        self.log.append(sql)


class _Acquire:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return _Conn(self.log)

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        return _Acquire(self.log)


@pytest.fixture(autouse=True)
def _restore_service():
    yield
    reset_service()


async def test_with_a_database_the_schema_is_created_and_the_postgres_store_becomes_active(monkeypatch):
    pool = _Pool()

    async def fake_pool():
        return pool

    monkeypatch.setattr(db_mod, "pool", fake_pool)
    sub = subsystem.build_subsystem()
    await sub.validate()
    assert pool.log == [DDL]                                    # CREATE TABLE IF NOT EXISTS ran
    assert isinstance(get_service().store, PostgresDeviceStore)
    assert sub._degraded is False


async def test_without_a_database_it_degrades_loudly_but_keeps_a_working_in_memory_service(monkeypatch):
    async def boom():
        raise OSError("no postgres here")

    monkeypatch.setattr(db_mod, "pool", boom)
    sub = subsystem.build_subsystem()
    with pytest.raises(errors.BossmanError, match="degraded"):
        await sub.validate()
    assert sub._degraded is True
    svc = get_service()
    assert isinstance(svc.store, InMemoryDeviceStore)
    did, raw = await svc.enroll("phone", {SCOPE_CHAT})
    assert (await svc.authenticate(f"Bearer {raw}")).device_id == did


async def test_degradation_message_never_carries_the_connection_error_text(monkeypatch):
    async def boom():
        raise OSError("password authentication failed for user bossman password=hunter2hunter2")

    monkeypatch.setattr(db_mod, "pool", boom)
    with pytest.raises(errors.BossmanError) as caught:
        await subsystem.build_subsystem().validate()
    assert "hunter2" not in str(caught.value)


async def test_start_stop_are_idempotent_no_ops():
    sub = subsystem.build_subsystem()
    await sub.start()
    await sub.stop()
    await sub.stop()
    assert sub.name == "remote_client" and sub.critical is False
