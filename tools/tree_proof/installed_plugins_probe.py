"""Offline probe of the five 'reported' plugin leaves against the INSTALLED Bossman build.

Run only by tools/tree_proof/installed_rerun.py with PYTHONPATH = installed site-packages
(first) and PYTHONSAFEPATH=1.  Never touches the network (loopback literals are rejected by
the SSRF guard before any socket is opened) and never touches the owner's data dir.

    python installed_plugins_probe.py plugin-0
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SP = os.path.normcase(os.path.abspath(os.environ["IG_SP"]))
TMP = Path(tempfile.mkdtemp(prefix="installed_plugins_"))
os.environ["BCC_DATA_DIR"] = str(TMP / "data")

import bcc.features.plugins as P  # noqa: E402
from bcc.tools import REGISTRY, ToolContext, decide_effect, execute_tool  # noqa: E402

for _m in (P, sys.modules["bcc.tools"]):
    _f = os.path.normcase(os.path.abspath(_m.__file__))
    assert _f.startswith(SP), f"resolved_from_source: {_m.__name__} -> {_f}"
    print("IMPORTED", _m.__name__, _f)


def ctx() -> ToolContext:
    return ToolContext(svc=None, task={}, run_id=0, agent={}, workspace=str(TMP), call_id="probe")


async def call(name: str, args: dict):
    r = await execute_tool(REGISTRY.get(name), args, ctx())
    print(f"[{name}] error={r.error} one_line={r.one_line!r}")
    print("content:", str(r.content)[:300].replace("\n", "\\n"))
    return r


async def p_http_get():
    spec = REGISTRY.get("plugin:http.get")
    b = await call("plugin:http.get", {"url": "http://127.0.0.1:1/"})
    print("ssrf_blocked:", b.error, "| public GET leg NOT re-run (proved live in the original receipt)")
    return spec is not None and b.error and "blocked" in str(b.content).lower()


async def p_monitor_feed():
    spec = REGISTRY.get("plugin:monitor.feed")
    b = await call("plugin:monitor.feed", {"url": "http://127.0.0.1:1/feed.xml"})
    print("ssrf_blocked:", b.error, "| public RSS leg NOT re-run (proved live in the original receipt)")
    return spec is not None and b.error and "blocked" in str(b.content).lower()


async def p_sql_read():
    db = TMP / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table t(a int, b text)")
    con.executemany("insert into t values(?,?)", [(1, "x"), (2, "y")])
    con.commit()
    con.close()
    os.environ["SQL_PLUGIN_DSN"] = f"sqlite:///{db}"
    r = await call("plugin:sql.read", {"sql": "select a,b from t order by a"})
    w = await call("plugin:sql.read", {"sql": "delete from t"})
    n = sqlite3.connect(db).execute("select count(*) from t").fetchone()[0]
    print("rows_after_write_attempt:", n)
    return (not r.error) and (r.data or {}).get("rows") == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}] and w.error and n == 2


async def p_obsidian_read():
    v = TMP / "vault"
    v.mkdir(exist_ok=True)
    (v / "n.md").write_text("hello vault", "utf-8")
    os.environ["OBSIDIAN_VAULT"] = str(v)
    r = await call("plugin:obsidian.read", {"path": "n.md"})
    t = await call("plugin:obsidian.read", {"path": "../../etc/passwd"})
    return (not r.error) and r.content == "hello vault" and t.error


async def p_obsidian_write():
    v = TMP / "vault2"
    v.mkdir(exist_ok=True)
    os.environ["OBSIDIAN_VAULT"] = str(v)
    spec = REGISTRY.get("plugin:obsidian.write")
    effect, reason = decide_effect(spec, {}, {})
    print("default_effect:", spec.default_effect, "decide_effect:", effect, reason)
    w = await call("plugin:obsidian.write", {"path": "sub/w.md", "content": "written"})
    t = await call("plugin:obsidian.write", {"path": "../escape.md", "content": "x"})
    f = v / "sub" / "w.md"
    disk = f.read_text("utf-8") if f.exists() else None
    print("on_disk:", disk, "escape_exists:", (TMP / "escape.md").exists())
    return effect == "ask" and (not w.error) and disk == "written" and t.error and not (TMP / "escape.md").exists()


PLAN = {"plugin-0": p_http_get, "plugin-1": p_monitor_feed, "plugin-2": p_sql_read,
        "plugin-3": p_obsidian_read, "plugin-4": p_obsidian_write}


async def main(node: str) -> int:
    await P.setup(None)
    print(f"== {node} installed offline probe ==")
    try:
        ok = bool(await PLAN[node]())
    except Exception as exc:  # noqa: BLE001
        print("EXCEPTION", type(exc).__name__, exc)
        ok = False
    print("VERDICT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
