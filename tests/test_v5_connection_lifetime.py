"""The store owns DB handles, not cyclic GC or its callers."""
import gc
import sqlite3

import pytest

from bossman_shared.objective_store import ObjectiveStore


def assert_closed(connection):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_objective_connection_closes_after_commit(tmp_path):
    store = ObjectiveStore(tmp_path / "objectives.db")
    with store._connect() as connection:
        connection.execute("INSERT INTO v5_schema VALUES ('fixture', 'kept')")
    assert_closed(connection)
    with store._connect() as fresh:
        assert fresh.execute("SELECT value FROM v5_schema WHERE key='fixture'").fetchone()[0] == "kept"


def test_objective_connection_rolls_back_and_closes(tmp_path):
    store = ObjectiveStore(tmp_path / "objectives.db")
    with pytest.raises(RuntimeError, match="injected"):
        with store._connect() as connection:
            connection.execute("INSERT INTO v5_schema VALUES ('fixture', 'rolled back')")
            raise RuntimeError("injected")
    assert_closed(connection)
    with store._connect() as fresh:
        assert fresh.execute("SELECT value FROM v5_schema WHERE key='fixture'").fetchone() is None


def test_repeated_reads_do_not_delegate_cleanup_to_gc(tmp_path, monkeypatch):
    store = ObjectiveStore(tmp_path / "objectives.db")
    original = store._connect
    captured = []

    def connect():
        connection = original()
        captured.append(connection)  # hold refs so refcounts cannot hide a leak
        return connection

    monkeypatch.setattr(store, "_connect", connect)
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(100):
            assert store.list_objectives() == []
        assert len(captured) == 100
        for connection in captured:
            assert_closed(connection)
    finally:
        for connection in captured:
            connection.close()
        if enabled:
            gc.enable()
