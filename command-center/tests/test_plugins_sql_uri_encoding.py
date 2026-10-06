"""plugin:sql.read — путь к SQLite-файлу должен попадать в URI закодированным.

Дефект (zone plugins, 2026-10-06): `_run_sqlite_read` склеивал
`file:<путь>?mode=ro` без percent-encoding. Если в пути к базе встречается
`#`, SQLite считает остаток фрагментом URI: путь обрывается, а вместе с ним
теряется `?mode=ro` — драйверная гарантия read-only пропадает, и открывается
(и создаётся) ДРУГОЙ файл. `%` в имени каталога декодируется, и база не
открывается вовсе. Оба случая — обычные имена папок на Windows.
"""
from __future__ import annotations

import sqlite3

import pytest

import bcc.features.plugins as P
from bcc.tools import REGISTRY


@pytest.fixture(autouse=True)
async def registered():
    await P.setup(None)
    yield


def _ctx():
    return type("C", (), {"svc": None, "task": {}, "run_id": 1, "agent": {},
                          "workspace": "", "call_id": "c", "step": 0})()


def _make_db(folder):
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "data.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(id int, name text)")
    con.execute("INSERT INTO t VALUES (1,'a'),(2,'b')")
    con.commit()
    con.close()
    return db


@pytest.mark.parametrize("folder", ["notes #1", "pct%41dir", "space dir"])
async def test_sql_read_opens_the_configured_file(tmp_path, monkeypatch, folder):
    db = _make_db(tmp_path / folder)
    before = sorted(p.name for p in tmp_path.iterdir())
    monkeypatch.setenv("SQL_PLUGIN_DSN", f"sqlite:///{db}")
    res = await REGISTRY.get("plugin:sql.read").handler(
        {"sql": "SELECT name FROM t ORDER BY id"}, _ctx())
    assert not res.error, res.content
    assert res.data["rows"] == [{"name": "a"}, {"name": "b"}]
    # ни одного постороннего файла рядом (обрезанный путь создавал бы новую базу)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_readonly_backstop_survives_hash_in_path(tmp_path):
    """Даже если SQL-гейт пропустит запись, соединение обязано быть mode=ro."""
    db = _make_db(tmp_path / "vault #2")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        P._run_sqlite_read(str(db), "INSERT INTO t VALUES (3,'c')", (), 10)
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM t").fetchone()[0]
    con.close()
    assert n == 2
