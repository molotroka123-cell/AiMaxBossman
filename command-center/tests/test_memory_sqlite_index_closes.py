"""The vault search index closes every SQLite connection it opens.

``with sqlite3.connect(...) as conn`` commits or rolls back but never closes, so the
index file stayed open until garbage collection; on Windows the owner's data
directory could not be removed (owner scenario OS-99).
"""
import sqlite3

import pytest

from bcc.v2.memory import sqlite_index
from bcc.v2.memory.sqlite_index import SQLiteMemoryBackend


def _is_open(connection) -> bool:
    try:
        connection.total_changes
    except sqlite3.ProgrammingError:
        return False
    return True


@pytest.fixture
def opened(monkeypatch):
    connections = []
    real = sqlite3.connect

    def tracking(*args, **kwargs):
        connection = real(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite_index.sqlite3, "connect", tracking)
    return connections


def test_index_search_expand_remove_and_stats_close_their_connections(tmp_path, opened):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "a.md").write_text("# A\n\nдедлайн в пятницу\n", encoding="utf-8")
    backend = SQLiteMemoryBackend(index_path=tmp_path / "index.sqlite", vault_root=vault)
    assert backend.index_sync([vault / "notes"])["added"] == 1
    hits = backend.search_sync("дедлайн")
    assert hits
    backend.stats_sync()
    assert backend.remove_source_sync("notes/a.md") is True
    assert opened, "the index opened no connection at all"
    assert [c for c in opened if _is_open(c)] == [], "a connection outlived its call"
