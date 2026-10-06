"""Evidence probe for the 'Плагины и коннекторы' leaves (plugin-0 .. plugin-22).

Re-runnable:  python tools/tree_proof/plugins_probe.py [--only plugin-3,plugin-4]
Writes docs/architecture/bossman-tree-20261005/evidence/plugins.json and
evidence/out/<node_id>.txt.  Uses a temp dir for every file/db it touches; never
touches the owner's data dir.  Does NOT call outward-facing leaves: for those it only
proves the approval gate (policy effect == ask) and says so.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CC = ROOT / "command-center"
sys.path.insert(0, str(CC))
EVID = ROOT / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
OUT = EVID / "out"

TMP = Path(tempfile.mkdtemp(prefix="plugins_probe_"))
os.environ["BCC_DATA_DIR"] = str(TMP / "data")          # never the owner's dir

import bcc.features.plugins as P                         # noqa: E402
from bcc.tools import REGISTRY, ToolContext, decide_effect, execute_tool  # noqa: E402

NODES = {f"plugin-{i}": c for i, c in enumerate(P.MANIFEST)}
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|ghp_[A-Za-z0-9]{10,}|Bearer\s+[A-Za-z0-9._\-]{10,})")


def scrub(s: str) -> str:
    for ref in {c.credential_ref for c in P.MANIFEST if c.credential_ref}:
        v = os.environ.get(ref)
        if v and len(v) > 5:
            s = s.replace(v, "[REDACTED]")
    return SECRET_RE.sub("[REDACTED]", s)


def ctx() -> ToolContext:
    return ToolContext(svc=None, task={}, run_id=0, agent={}, workspace=str(TMP), call_id="probe")


async def call(name: str, args: dict):
    spec = REGISTRY.get(name)
    r = await execute_tool(spec, args, ctx())
    print(f"[{name}] error={r.error} one_line={r.one_line!r}")
    print("content:", scrub(str(r.content))[:600].replace("\n", "\\n"))
    return r


def gate(name: str) -> bool:
    spec = REGISTRY.get(name)
    effect, reason = decide_effect(spec, {}, {})
    print(f"[{name}] default_effect={spec.default_effect} decide_effect(agent without grants)={effect!r} reason={reason}")
    print("NOTE: the outward effect was NOT exercised; only the approval gate was checked.")
    return effect == "ask" and spec.default_effect == "ask"


# ---------------------------------------------------------------- probes (return pass:bool)

async def p_http_get():
    r = await call("plugin:http.get", {"url": "https://example.com/"})
    b = await call("plugin:http.get", {"url": "http://127.0.0.1:1/"})
    print("ssrf_blocked:", b.error)
    return (not r.error) and "Example Domain" in r.content and b.error


async def p_monitor_feed():
    r = await call("plugin:monitor.feed", {"url": "https://www.python.org/dev/peps/peps.rss"})
    low = r.content.lower()
    return (not r.error) and ("<rss" in low or "<feed" in low or "<?xml" in low)


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
    ok_gate = gate("plugin:obsidian.write")
    w = await call("plugin:obsidian.write", {"path": "sub/w.md", "content": "written"})
    t = await call("plugin:obsidian.write", {"path": "../escape.md", "content": "x"})
    f = v / "sub" / "w.md"
    disk = f.read_text("utf-8") if f.exists() else None
    print("on_disk:", disk, "escape_exists:", (TMP / "escape.md").exists())
    return ok_gate and (not w.error) and disk == "written" and t.error and not (TMP / "escape.md").exists()


async def stub_call(name: str):
    cap = next(c for c in P.MANIFEST if c.tool_name == name)
    r = await call(name, {k: "x" for k in cap.required})
    performed = (r.data or {}).get("performed", None)
    print("performed:", performed, "-> a handler that returns NOT_TESTED_LIVE/SKIP is not a pass")
    return (not r.error) and performed is not False


async def p_gate(name: str):
    return gate(name)


READS = {"plugin-5": "plugin:mcp.tool_list", "plugin-7": "plugin:ollama.chat", "plugin-9": "plugin:github.repo_read",
         "plugin-11": "plugin:gmail.search", "plugin-13": "plugin:calendar.search", "plugin-15": "plugin:drive.search",
         "plugin-17": "plugin:telegram.status", "plugin-19": "plugin:n8n.workflow_list", "plugin-21": "plugin:browser.open"}
GATES = {"plugin-6": "plugin:mcp.tool_call", "plugin-10": "plugin:github.issue_create", "plugin-12": "plugin:gmail.send",
         "plugin-14": "plugin:calendar.create", "plugin-16": "plugin:drive.write", "plugin-18": "plugin:telegram.send",
         "plugin-20": "plugin:n8n.workflow_run", "plugin-22": "plugin:browser.form_submit"}

# (probe name, kind, fn)
PLAN = {
    "plugin-0": [("live_get_public_and_ssrf_block", "live_call", p_http_get)],
    "plugin-1": [("live_feed_public_rss", "live_call", p_monitor_feed)],
    "plugin-2": [("sqlite_ro_select_and_write_denied", "live_call", p_sql_read)],
    "plugin-3": [("temp_vault_read_and_traversal_block", "live_call", p_obsidian_read)],
    "plugin-4": [("temp_vault_write_gate_and_confine", "live_call", p_obsidian_write)],
    "plugin-8": [("handler_live_call", "live_call", lambda: stub_call("plugin:openrouter.chat")),
                 ("approval_gate_refuses", "import", lambda: p_gate("plugin:openrouter.chat"))],
}
for _n, _t in READS.items():
    PLAN[_n] = [("handler_live_call", "live_call", (lambda t=_t: stub_call(t)))]
for _n, _t in GATES.items():
    PLAN[_n] = [("approval_gate_refuses", "import", (lambda t=_t: p_gate(t)))]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def run_node(node: str, sha: str, cmd: str):
    buf_all = io.StringIO()
    results = []
    for probe, kind, fn in PLAN[node]:
        t0 = now()
        buf = io.StringIO()
        ok = False
        with redirect_stdout(buf):
            print(f"== {node} {NODES[node].tool_name} probe={probe} ==")
            try:
                ok = bool(await fn())
            except Exception as exc:  # noqa: BLE001
                print("EXCEPTION", type(exc).__name__, scrub(str(exc)))
                ok = False
            print("VERDICT", "PASS" if ok else "FAIL")
        text = scrub(buf.getvalue())
        buf_all.write(text + "\n")
        results.append(dict(node_id=node, sha=sha, probe=probe, command=cmd, exit_code=0 if ok else 1,
                            started_at=t0, finished_at=now(), output_tail=text[-1500:],
                            verdict="PASS" if ok else "FAIL", model=None, kind=kind))
    data = buf_all.getvalue().encode("utf-8")
    (OUT / f"{node}.txt").write_bytes(data)
    h = hashlib.sha256(data).hexdigest()
    for r in results:
        r["output_sha256"] = h
    return results


async def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    OUT.mkdir(parents=True, exist_ok=True)
    await P.setup(None)
    receipts = []
    for node in NODES:
        if only and node not in only:
            continue
        receipts += await run_node(node, sha, f"python tools/tree_proof/plugins_probe.py --only {node}")
    path = EVID / "plugins.json"
    if only and path.exists():
        old = [r for r in json.loads(path.read_text("utf-8")) if r["node_id"] not in only]
        receipts = old + receipts
    path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), "utf-8")
    for r in receipts:
        print(r["node_id"], NODES[r["node_id"]].tool_name, r["probe"], r["verdict"])


if __name__ == "__main__":
    asyncio.run(main())
