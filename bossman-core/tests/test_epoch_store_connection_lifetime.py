"""V3 Fleet/Organization handles must not leak into long-horizon V4 missions."""
import sqlite3

import pytest

from bossman_v3.fleet.store import FleetStore
from bossman_v3.organization.store import OrganizationStore


@pytest.mark.parametrize("store_type,method", [(FleetStore, "connect"), (OrganizationStore, "_connect")])
def test_store_closes_owned_connection_on_success(tmp_path, store_type, method):
    store = store_type(tmp_path / "fixture.db")
    with getattr(store, method)() as con:
        assert con.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        con.execute("SELECT 1")


@pytest.mark.parametrize("store_type,method", [(FleetStore, "connect"), (OrganizationStore, "_connect")])
def test_store_closes_owned_connection_on_exception(tmp_path, store_type, method):
    store = store_type(tmp_path / "fixture.db")
    with pytest.raises(RuntimeError, match="injected"):
        with getattr(store, method)() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("CREATE TABLE rollback_fixture(value TEXT)")
            raise RuntimeError("injected")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        con.execute("SELECT 1")
    with getattr(store, method)() as fresh:
        assert fresh.execute("SELECT name FROM sqlite_master WHERE name='rollback_fixture'").fetchone() is None
