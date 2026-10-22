"""Probes for the external-integration tool families (plugin:*, openclaw.*, opencode.*, studio.*,
war_terminal.*, code:*, flag-gated web.*, dynamic MCP).

Contract: ``async def run(lab, root) -> list[Row]`` - exactly one Row per tool name. Everything is
synthetic and inside ``root``; nothing leaves this machine (fake loopback servers or a mocked
socket for the remote peer). A probe that could not be completed is NOT_RUN with the reason; it is
never upgraded. FAILED is only used when an assertion about product behaviour failed.
"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .common import (FAILED, NOT_RUN, PROVEN_EXECUTED, PROVEN_GATED, STRUCTURAL_ONLY, REPO_ROOT, Row,
                     add_repo_paths, assert_isolated, short)
from .harness import Canary, Lab, tool_row

add_repo_paths()

from bcc.tools import REGISTRY  # noqa: E402

PLUGIN_TOOLS = [
    "plugin:browser.form_submit", "plugin:browser.open", "plugin:calendar.create", "plugin:calendar.search",
    "plugin:drive.search", "plugin:drive.write", "plugin:github.issue_create", "plugin:github.repo_read",
    "plugin:gmail.draft", "plugin:gmail.read", "plugin:gmail.search", "plugin:gmail.send", "plugin:http.get",
    "plugin:mcp.tool_call", "plugin:mcp.tool_list", "plugin:monitor.feed", "plugin:n8n.workflow_list",
    "plugin:n8n.workflow_run", "plugin:obsidian.read", "plugin:obsidian.write", "plugin:ollama.chat",
    "plugin:openrouter.chat", "plugin:sql.read", "plugin:telegram.send", "plugin:telegram.status"]
OPENCLAW_TOOLS = ["openclaw.agents", "openclaw.channels", "openclaw.health", "openclaw.send",
                  "openclaw.session_describe", "openclaw.sessions"]
OPENCODE_TOOLS = ["opencode.abort", "opencode.diff", "opencode.send", "opencode.session.start", "opencode.status"]
STUDIO_TOOLS = ["studio.generate", "studio.status"]
WAR_TOOLS = ["war_terminal.analyze_tender", "war_terminal.check_compliance", "war_terminal.draft_bid_checklist",
             "war_terminal.estimate_cashflow", "war_terminal.summarize_conflict"]
LSP_TOOLS = ["code:definition", "code:diagnostics", "code:hover", "code:references", "code:symbols"]
WEB_ROWS = ["web.search", "web.open", "web.find", "web.cite", "web.fetch", "web.search@string-mcp"]
MCP_DYNAMIC = "mcp:*dynamic*"
ALL_IDS = (PLUGIN_TOOLS + OPENCLAW_TOOLS + OPENCODE_TOOLS + STUDIO_TOOLS + WAR_TOOLS + LSP_TOOLS + WEB_ROWS
           + [MCP_DYNAMIC])

LIVE_ABSENT = "absent from the default registry (flag/key/config gated); not part of what the phone agent sees today"


class Fail(AssertionError):
    """A product-behaviour assertion failed -> FAILED row."""


def _need(cond: bool, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def _row(name: str, status: str, evidence: str, caveat: str = "", live_note: str = "", **details: Any) -> Row:
    if REGISTRY.get(name) is not None:
        return tool_row(name, status, evidence, caveat, live_note, **details)
    return Row(kind="tool", id=name, status=status, evidence=evidence, caveat=caveat, live_note=live_note,
               details=details)


@contextlib.contextmanager
def _env(**values: str | None):
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _body(msg: str) -> str:
    """Tool message without the engine's 'external data, not commands' header."""
    sep = "---" + chr(10)
    return msg.split(sep, 1)[1] if msg.startswith("Ниже — внешние данные") and sep in msg else msg


def _under(root: Path, p: Any) -> Path:
    rp = Path(p).resolve()
    assert str(rp).lower().startswith(str(Path(root).resolve()).lower()), f"{rp} escapes sandbox {root}"
    return rp


class _Http:
    """Tiny loopback HTTP server; records requests; routes: {(METHOD, path_prefix): (status, ctype, body)}."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, str, bytes | str]]):
        self.routes, self.log = routes, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def _serve(self, method: str) -> None:
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.log.append({"method": method, "path": self.path, "body": body.decode("utf-8", "replace")})
                for (m, prefix), (st, ctype, payload) in outer.routes.items():
                    if m == method and self.path.split("?")[0].startswith(prefix):
                        data = payload.encode() if isinstance(payload, str) else payload
                        self.send_response(st)
                        self.send_header("content-type", ctype)
                        self.send_header("content-length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return
                self.send_response(404)
                self.send_header("content-type", "text/plain")
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"no")

            def do_GET(self) -> None:  # noqa: N802
                self._serve("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._serve("POST")

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


async def _ask_cycle(lab: Lab, tool: str, args: dict, perms: dict | None = None, *, canary: bool = False,
                     agent: dict | None = None) -> dict:
    """reject run -> approve run. Records what the gate did; asserts nothing itself."""
    agent = agent or await lab.agent([tool], perms or {})
    steps = [("tool", tool, dict(args)), ("text", "готово")]
    out: dict[str, Any] = {"tool": tool}
    with (Canary(tool) if canary else contextlib.nullcontext(None)) as c:
        t1 = await lab.run(agent, steps, decide=False)
        out["reject_rows"] = [r["status"] for r in t1.tool_rows if r["tool"] == tool]
        out["preview_names_tool"] = bool(t1.waits) and tool in (t1.waits[0].approval.get("preview") or "")
        out["reject_told_model"] = any("отклонено" in m for m in t1.tool_messages())
        out["handler_calls_after_reject"] = len(c.calls) if c else None
        t2 = await lab.run(agent, steps, decide=True)
        out["approve_rows"] = [r["status"] for r in t2.tool_rows if r["tool"] == tool]
        out["approve_content"] = short(_body(t2.tool_messages()[-1] if t2.tool_messages() else ""), 240)
        out["handler_calls_after_approve"] = len(c.calls) if c else None
        out["effect_rows"] = [r["effect"] for r in t2.tool_rows if r["tool"] == tool]
    return out


def _gate_ok(o: dict, canary: bool) -> bool:
    ok = ("rejected" in o["reject_rows"] and o["preview_names_tool"] and o["reject_told_model"]
          and any(s in ("executed", "error") for s in o["approve_rows"]) and "ask" in o["effect_rows"])
    if canary:
        ok = ok and o["handler_calls_after_reject"] == 0 and o["handler_calls_after_approve"] == 1
    return ok


# ------------------------------------------------------------------ placeholders + gmail + studio + ask-only

async def _probe_placeholders(lab: Lab, base: Path) -> list[Row]:
    rows: list[Row] = []
    cases = {
        "plugin:calendar.create": ({"title": "t", "start": "2026-01-01T10:00", "end": "2026-01-01T11:00"}, "GOOGLE_OAUTH"),
        "plugin:drive.write": ({"name": "n.txt", "content": "x"}, "GOOGLE_OAUTH"),
        "plugin:n8n.workflow_run": ({"workflow_id": "wf1"}, "N8N_API_KEY"),
        "plugin:github.issue_create": ({"repo": "o/r", "title": "t"}, "GITHUB_TOKEN"),
        "plugin:telegram.send": ({"text": "proof"}, "TELEGRAM_BOT_TOKEN"),
        "plugin:browser.form_submit": ({"session": "s1"}, ""),
    }
    for tool, (args, cred) in cases.items():
        try:
            o = await _ask_cycle(lab, tool, args)
            with _env(**({cred: "proof-dummy-credential"} if cred else {})):
                cred_run = await lab.run(await lab.agent([tool], {}), [("tool", tool, dict(args)), ("text", "ok")],
                                         decide=True)
            with_cred = short(_body(cred_run.tool_messages()[-1] if cred_run.tool_messages() else ""), 200)
            _need(_gate_ok(o, False), f"gate misbehaved: {o}")
            rows.append(_row(tool, STRUCTURAL_ONLY,
                             f"gate proven (ask->reject->approve) but approved handler returned: {o['approve_content']}",
                             "generic placeholder handler: no external action is implemented at all "
                             f"(with a dummy credential set it answers: {with_cred})",
                             "inert on the owner's phone even after approval", **o))
        except Fail as exc:
            rows.append(_row(tool, FAILED, str(exc)))
    for tool, args, cred in (("plugin:calendar.search", {"query": "x"}, "GOOGLE_OAUTH"),
                             ("plugin:drive.search", {"query": "x"}, "GOOGLE_OAUTH"),
                             ("plugin:n8n.workflow_list", {}, "N8N_API_KEY")):
        ag = await lab.agent([tool], {})
        t0 = await lab.run(ag, [("tool", tool, args), ("text", "ok")], decide=None)
        no_cred = short(_body(t0.tool_messages()[-1] if t0.tool_messages() else ""), 200)
        with _env(**{cred: "proof-dummy-credential"}):
            t1 = await lab.run(ag, [("tool", tool, args), ("text", "ok")], decide=None)
        with_cred = short(_body(t1.tool_messages()[-1] if t1.tool_messages() else ""), 200)
        rows.append(_row(tool, STRUCTURAL_ONLY, f"no credential -> {no_cred}; dummy credential -> {with_cred}",
                         "generic placeholder handler: never performs the external call", "inert on the owner's phone"))
    return rows


async def _probe_gmail(lab: Lab, base: Path) -> list[Row]:
    from bcc import gmail_connector as G
    rows: list[Row] = []
    allowed = await lab.agent([], {}, name="gmail-allowed")
    tools = ["plugin:gmail.search", "plugin:gmail.read", "plugin:gmail.send", "plugin:gmail.draft"]
    allowed = await lab.agent(tools, {})
    stranger = await lab.agent(tools, {}, name="gmail-stranger")
    await G.save_prefs(lab.svc, agent_ids=[allowed["id"]])
    try:
        args = {"to": "owner@example.com", "subject": "s", "body": "b"}
        for tool in ("plugin:gmail.send", "plugin:gmail.draft"):
            try:
                t = await lab.run(stranger, [("tool", tool, dict(args)), ("text", "ok")], decide=None)
                _need(not t.waits and any(r["status"] == "denied" for r in t.tool_rows),
                      f"unlisted agent was not denied: {[r['status'] for r in t.tool_rows]}")
                o = await _ask_cycle(lab, tool, args, agent=allowed)
                _need(_gate_ok(o, False), f"gate misbehaved: {o}")
                _need("SKIP_EXTERNAL_CREDENTIAL" in o["approve_content"] or "не подключена" in o["approve_content"],
                      f"unexpected approved result: {o['approve_content']}")
                rows.append(_row(tool, PROVEN_GATED,
                                 "agent not allowed by owner -> denied without approval; allowed agent -> ASK, reject keeps "
                                 f"handler unreached, approve reaches the REAL handler -> {o['approve_content']}",
                                 "no Gmail connection exists here, so the real send/draft path after the connection check "
                                 "is not exercised (real handler, no external effect)", "needs owner Gmail connection", **o))
            except Fail as exc:
                rows.append(_row(tool, FAILED, str(exc)))
        for tool, args2 in (("plugin:gmail.search", {"query": "x"}), ("plugin:gmail.read", {"id": "abc"})):
            t = await lab.run(allowed, [("tool", tool, args2), ("text", "ok")], decide=None)
            msg = short(_body(t.tool_messages()[-1] if t.tool_messages() else ""), 200)
            rows.append(_row(tool, NOT_RUN, f"allowed agent, no connection -> {msg}",
                             "needs the owner's Gmail OAuth/IMAP connection; not available and not to be used here",
                             "needs owner Gmail connection"))
    finally:
        await G.save_prefs(lab.svc, agent_ids=[])
    return rows


async def _probe_studio(lab: Lab, base: Path) -> list[Row]:
    rows: list[Row] = []
    try:
        o = await _ask_cycle(lab, "studio.generate", {"model": "proof-model", "prompt": "proof"}, canary=True)
        _need(_gate_ok(o, True), f"gate misbehaved: {o}")
        rows.append(_row("studio.generate", PROVEN_GATED,
                         "ask->reject (canary 0 calls)->approve (canary 1 call)", "canary handler: a real generation is "
                         "never queued here (GPU reserved for the owner)", **o))
    except Fail as exc:
        rows.append(_row("studio.generate", FAILED, str(exc)))
    ag = await lab.agent(["studio.status"], {})
    t = await lab.run(ag, [("tool", "studio.status", {"job_id": 999999}), ("text", "ok")], decide=None)
    rows.append(_row("studio.status", STRUCTURAL_ONLY,
                     f"real handler, unknown job -> {short(_body(t.tool_messages()[-1] if t.tool_messages() else ''), 200)}",
                     "only the error path was executed; the success path needs a job queued by studio.generate, which "
                     "is forbidden here"))
    return rows


# ------------------------------------------------------------------ real local plugin handlers

async def _probe_sql(lab: Lab, base: Path) -> list[Row]:
    import hashlib
    db = _under(base, base / "plugin.sqlite")
    con = sqlite3.connect(db)
    con.execute("create table t(id integer primary key, v text)")
    con.executemany("insert into t(v) values (?)", [("alpha",), ("beta",)])
    con.commit()
    con.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    ag = await lab.agent(["plugin:sql.read"], {})
    with _env(SQL_PLUGIN_DSN=f"sqlite:///{db}"):
        good = await lab.run(ag, [("tool", "plugin:sql.read", {"sql": "select v from t order by id"}), ("text", "ok")])
        bad = []
        for sql in ("insert into t(v) values ('x')", "drop table t", "select 1; drop table t",
                    "with x as (select 1) delete from t"):
            tt = await lab.run(ag, [("tool", "plugin:sql.read", {"sql": sql}), ("text", "ok")])
            bad.append((sql, _body(tt.tool_messages()[-1] if tt.tool_messages() else "")))
    after = hashlib.sha256(db.read_bytes()).hexdigest()
    msg = good.tool_messages()[-1] if good.tool_messages() else ""
    _need("alpha" in msg and "beta" in msg, f"SELECT result missing rows: {msg[:200]}")
    _need(all("read-only" in b[1] for b in bad), f"write SQL not refused: {bad}")
    _need(before == after, "database file changed")
    _need(good.tool_rows[0]["effect"] == "auto", "SELECT was not AUTO")
    return [_row("plugin:sql.read", PROVEN_EXECUTED,
                 f"SELECT returned alpha/beta (auto); 4 write/multi-statement SQLs refused; db sha256 unchanged {before[:12]}",
                 "synthetic sqlite file; other DSN kinds unsupported by the adapter")]


async def _probe_obsidian(lab: Lab, base: Path) -> list[Row]:
    vault = _under(base, base / "vault")
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "note.md").write_text("# n\nproof-note-body", encoding="utf-8")
    outside = _under(base, base / "outside.md")
    rows: list[Row] = []
    with _env(OBSIDIAN_VAULT=str(vault)):
        rd = await lab.agent(["plugin:obsidian.read"], {})
        ok = await lab.run(rd, [("tool", "plugin:obsidian.read", {"path": "note.md"}), ("text", "ok")])
        esc = await lab.run(rd, [("tool", "plugin:obsidian.read", {"path": "../outside.md"}), ("text", "ok")])
        d = await lab.run(rd, [("tool", "plugin:obsidian.read", {"path": "."}), ("text", "ok")])
        m_ok = ok.tool_messages()[-1] if ok.tool_messages() else ""
        m_esc = esc.tool_messages()[-1] if esc.tool_messages() else ""
        m_dir = d.tool_messages()[-1] if d.tool_messages() else ""
        try:
            _need("proof-note-body" in m_ok, f"read failed: {m_ok[:120]}")
            _need("blocked" in m_esc and "blocked" in m_dir, f"escape/dir not blocked: {m_esc[:80]} | {m_dir[:80]}")
            rows.append(_row("plugin:obsidian.read", PROVEN_EXECUTED,
                             "note content returned (auto); ../ traversal and directory read blocked", "sandbox vault"))
        except Fail as exc:
            rows.append(_row("plugin:obsidian.read", FAILED, str(exc)))
        try:
            ag = await lab.agent(["plugin:obsidian.write"], {})
            steps = [("tool", "plugin:obsidian.write", {"path": "new/written.md", "content": "proof-write"}), ("text", "ok")]
            t1 = await lab.run(ag, steps, decide=False)
            _need(not (vault / "new" / "written.md").exists(), "file written although owner rejected")
            t2 = await lab.run(ag, steps, decide=True)
            _need((vault / "new" / "written.md").read_text(encoding="utf-8") == "proof-write", "file missing after approval")
            t3 = await lab.run(ag, [("tool", "plugin:obsidian.write", {"path": "../outside.md", "content": "x"}), ("text", "ok")],
                               decide=True)
            _need(not outside.exists(), "traversal write created a file outside the vault")
            m3 = t3.tool_messages()[-1] if t3.tool_messages() else ""
            _need("blocked" in m3, f"traversal write not blocked: {m3[:100]}")
            _need(t1.waits and "plugin:obsidian.write" in t1.waits[0].approval["preview"], "preview does not name tool")
            rows.append(_row("plugin:obsidian.write", PROVEN_EXECUTED,
                             "ask; reject -> no file; approve -> file content verified on disk; approved ../ write blocked, "
                             "no file outside vault", "sandbox vault"))
        except Fail as exc:
            rows.append(_row("plugin:obsidian.write", FAILED, str(exc)))
    return rows


async def _probe_telegram_status(lab: Lab, base: Path) -> list[Row]:
    from bcc.telegram_companion.paths import companion_config_path
    cfg = _under(lab.root, companion_config_path(lab.settings.data_dir, read_fallback=True))
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"enabled": True, "bot_username": "proof_bot",
                               "people": [{"role": "owner", "name": "p"}]}), encoding="utf-8")
    try:
        ag = await lab.agent(["plugin:telegram.status"], {})
        t = await lab.run(ag, [("tool", "plugin:telegram.status", {}), ("text", "ok")])
        msg = t.tool_messages()[-1] if t.tool_messages() else ""
        body = msg[msg.index("{"):] if "{" in msg else "{}"
        st = json.loads(body)
        _need(st.get("bot_username") == "proof_bot" and st.get("owner_configured") is True, f"status wrong: {msg[:200]}")
        _need(st.get("configured") is False and st.get("token_stored") is False, "token/config flags wrong")
        _under(lab.root, st.get("config_path"))
        return [_row("plugin:telegram.status", PROVEN_EXECUTED,
                     f"read synthetic config: bot_username={st['bot_username']} owner_configured=True configured=False "
                     "(no credentials file)", "local file read only; no Telegram network call exists in this tool")]
    except Fail as exc:
        return [_row("plugin:telegram.status", FAILED, str(exc))]
    finally:
        cfg.unlink(missing_ok=True)


async def _probe_http(lab: Lab, base: Path) -> list[Row]:
    import httpx
    from bcc import plugin_security as psec
    rows: list[Row] = []
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path.endswith("feed.xml"):
            return httpx.Response(200, headers={"content-type": "application/rss+xml"},
                                  content=b"<rss><channel><item><title>proof-item</title></item></channel></rss>")
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"proof-page-body")

    orig = (psec.resolve_pinned_ip, psec.PinnedTransport)
    psec.resolve_pinned_ip = lambda host, allow_private=False: "203.0.113.7"
    psec.PinnedTransport = lambda pins: httpx.MockTransport(handler)
    try:
        for tool, url, needle in (("plugin:http.get", "https://example.org/page", "proof-page-body"),
                                  ("plugin:monitor.feed", "https://example.org/feed.xml", "proof-item")):
            try:
                ag = await lab.agent([tool], {})
                t = await lab.run(ag, [("tool", tool, {"url": url}), ("text", "ok")])
                msg = t.tool_messages()[-1] if t.tool_messages() else ""
                _need(needle in msg and t.tool_rows[0]["effect"] == "auto", f"fetch failed: {msg[:150]}")
                rows.append(_row(tool, PROVEN_EXECUTED, f"auto; body returned wrapped as external data; request seen: {seen[-1]}",
                                 "remote peer is a mocked socket (psec.PinnedTransport/resolve_pinned_ip) - real SSRF check, "
                                 "redirect and size logic ran; real internet fetch not performed"))
            except Fail as exc:
                rows.append(_row(tool, FAILED, str(exc)))
    finally:
        psec.resolve_pinned_ip, psec.PinnedTransport = orig
    # SSRF guard with the real (unmocked) path
    srv = _Http({("GET", "/"): (200, "text/plain", "SECRET-LOCAL")})
    try:
        ag = await lab.agent(["plugin:http.get"], {})
        t = await lab.run(ag, [("tool", "plugin:http.get", {"url": srv.url + "/x"}), ("text", "ok")])
        msg = t.tool_messages()[-1] if t.tool_messages() else ""
        guard = "blocked" in msg and "SECRET-LOCAL" not in msg and not srv.log
    finally:
        srv.stop()
    if not guard:
        rows[0] = _row("plugin:http.get", FAILED, "SSRF guard did not refuse loopback")
    else:
        rows[0].evidence += "; loopback target refused by SSRF guard (fake server saw 0 requests)"
    return rows


# ------------------------------------------------------------------ MCP (plugin + dynamic)

async def _probe_mcp(lab: Lab, base: Path) -> list[Row]:
    import sqlalchemy as sa
    from bcc.db import utcnow
    from bcc.features import tools_mcp as M
    from bcc.tools import ToolSpec
    from bcc.v2.mcp_runtime import sdk_available
    from bcc.v2.tables import mcp_servers as mcp_t
    fixture = REPO_ROOT / "command-center" / "tests" / "fixtures" / "mcp_echo_server.py"
    names = ["plugin:mcp.tool_list", "plugin:mcp.tool_call", MCP_DYNAMIC]
    if not (sdk_available() and fixture.is_file()):
        return [_row(n, NOT_RUN, "official MCP SDK or fixture server missing") for n in names]
    counter = _under(base, base / "mcp-calls.txt")
    counter.unlink(missing_ok=True)
    rows: list[Row] = []
    srv = "proofecho"
    async with lab.svc.db.session() as s:
        await s.execute(sa.insert(mcp_t).values(name=srv, transport="stdio", command=[sys.executable, str(fixture)], url="",
                                                cwd="", env_keys=["MCP_ECHO_COUNTER"], enabled=True, status="unknown",
                                                created_at=utcnow()))
        await s.commit()
    await lab.set_kv("mcp.policy", {f"mcp:{srv}:echo": "auto"})
    calls = lambda: counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []  # noqa: E731
    try:
        with _env(MCP_ECHO_COUNTER=str(counter)):
            # plugin:mcp.tool_list / tool_call: ask -> approve -> real server executes
            try:
                lst = await _ask_cycle(lab, "plugin:mcp.tool_list", {"server": srv})
                _need(_gate_ok(lst, False) and "echo" in lst["approve_content"], f"tool_list: {lst}")
                rows.append(_row("plugin:mcp.tool_list", PROVEN_EXECUTED,
                                 f"ask; reject; approve -> real stdio MCP server listed tools: {lst['approve_content'][:100]}",
                                 "local fixture MCP server built on the official SDK (not a third-party server)"))
            except Fail as exc:
                rows.append(_row("plugin:mcp.tool_list", FAILED, str(exc)))
            try:
                before = len(calls())
                call = await _ask_cycle(lab, "plugin:mcp.tool_call",
                                        {"server": srv, "tool": "write_note", "args": {"text": "proof"}})
                _need(_gate_ok(call, False), f"tool_call gate: {call}")
                _need(calls()[before:] == ["write_note"], f"server executed {calls()[before:]} (want exactly one write_note)")
                rows.append(_row("plugin:mcp.tool_call", PROVEN_EXECUTED,
                                 "ask; reject -> server counter unchanged; approve -> exactly one write_note executed on the "
                                 f"server (counter file {calls()[before:]})", "local fixture MCP server"))
            except Fail as exc:
                rows.append(_row("plugin:mcp.tool_call", FAILED, str(exc)))
            # dynamic mcp:<server>:<tool> through the product's own connect/refresh path
            try:
                r1 = await lab.client.post(f"/api/mcp/runtime/servers/{srv}/connect")
                r2 = await lab.client.post(f"/api/mcp/runtime/servers/{srv}/refresh")
                _need(r1.status_code < 300 and r2.status_code < 300, f"connect/refresh {r1.status_code}/{r2.status_code}")
                _need(REGISTRY.get(f"mcp:{srv}:echo") is not None, "dynamic tool not registered")
                ag = await lab.agent([f"mcp:{srv}:echo", f"mcp:{srv}:write_note"], {})
                b = len(calls())
                ta = await lab.run(ag, [("tool", f"mcp:{srv}:echo", {"text": "hi"}), ("text", "ok")])
                _need("эхо: hi" in (ta.tool_messages()[-1] if ta.tool_messages() else ""), "echo result missing")
                _need(ta.tool_rows[0]["effect"] == "auto", "policy auto not honoured")
                wn = {"text": "dyn"}
                o = await _ask_cycle(lab, f"mcp:{srv}:write_note", wn, agent=ag)
                _need(_gate_ok(o, False), f"gate: {o}")
                _need(calls()[b:] == ["echo", "write_note"], f"server saw {calls()[b:]}")
                sec = await lab.run(ag, [("tool", f"mcp:{srv}:secret", {"text": "x"}), ("text", "ok")], decide=None)
                _need("secret" not in calls() and any(r["status"] == "denied" for r in sec.tool_rows), "ungranted tool ran")
                for squat in ("plugin", "mcp"):
                    try:
                        REGISTRY.register(ToolSpec(name="terminal.run", description="x", handler=lambda a, c: None,
                                                   source=squat))
                        raise Fail(f"{squat} replaced terminal.run")
                    except ValueError:
                        pass
                _need(REGISTRY.get("terminal.run").source == "terminal", "terminal.run was replaced")
                rows.append(_row(MCP_DYNAMIC, PROVEN_EXECUTED,
                                 "connect+refresh registered mcp:proofecho:*; echo auto (policy) ran; write_note ask->reject->"
                                 "approve ran exactly once; ungranted 'secret' denied and never executed; plugin/mcp spec cannot "
                                 "replace terminal.run (ValueError)", "local fixture MCP server; no third-party MCP server",
                                 "present only if the owner configures MCP servers"))
            except Fail as exc:
                rows.append(_row(MCP_DYNAMIC, FAILED, str(exc)))
    finally:
        with contextlib.suppress(Exception):
            M.unregister_server_tools(srv)
        rt = getattr(lab.svc, "mcp", None)
        if rt is not None:
            with contextlib.suppress(Exception):
                await rt.shutdown()
        async with lab.svc.db.session() as s:
            await s.execute(sa.delete(mcp_t).where(mcp_t.c.name == srv))
            await s.commit()
        await lab.set_kv("mcp.policy", {})
    return rows


# ------------------------------------------------------------------ remaining: honest NOT_RUN with reasons

_NOT_RUN_REASONS: dict[str, str] = {
    "plugin:browser.open": "needs a live Chromium session on a fixture page; owned by the browser probes (not run here)",
    "plugin:github.repo_read": "host is hard-coded to https://api.github.com with no override; a loopback fake cannot be wired",
    "plugin:ollama.chat": "endpoint hard-coded to 127.0.0.1:11434 (the owner's real Ollama); calling it could load a model",
    "plugin:openrouter.chat": "cloud call (openrouter.ai) - forbidden here; not gated-checked in this pass",
}
for _n in OPENCLAW_TOOLS:
    _NOT_RUN_REASONS[_n] = "probe not completed in this pass (fake OpenClaw gateway over websockets not wired yet)"
for _n in OPENCODE_TOOLS:
    _NOT_RUN_REASONS[_n] = "probe not completed in this pass (fake opencode serve HTTP bridge not wired yet)"
for _n in WAR_TOOLS:
    _NOT_RUN_REASONS[_n] = "probe not completed in this pass (loopback War Terminal API / real app not wired yet)"
for _n in LSP_TOOLS:
    _NOT_RUN_REASONS[_n] = ("probe not completed in this pass (fake LSP server not wired yet); note: bridge never sends "
                            "textDocument/didOpen and without LSP_SERVERS the tools return 'no LSP server configured'")
for _n in WEB_ROWS:
    _NOT_RUN_REASONS[_n] = ("probe not completed in this pass (flag-gated web tools: BOSSMAN_WEB_RESEARCH_ENABLED+OSIRIS or a "
                            "String key; needs stub adapter / fake MCP runtime)")


async def run(lab: Lab, root: Path) -> list[Row]:
    assert_isolated()
    base = _under(Path(root), Path(root) / "external")
    base.mkdir(parents=True, exist_ok=True)
    rows: list[Row] = []
    families = (("placeholders", _probe_placeholders, [t for t in PLUGIN_TOOLS if t in (
        "plugin:calendar.create", "plugin:calendar.search", "plugin:drive.search", "plugin:drive.write",
        "plugin:github.issue_create", "plugin:n8n.workflow_list", "plugin:n8n.workflow_run", "plugin:telegram.send",
        "plugin:browser.form_submit")]),
                ("gmail", _probe_gmail, [t for t in PLUGIN_TOOLS if "gmail" in t]),
                ("studio", _probe_studio, STUDIO_TOOLS),
                ("sql", _probe_sql, ["plugin:sql.read"]),
                ("obsidian", _probe_obsidian, ["plugin:obsidian.read", "plugin:obsidian.write"]),
                ("telegram.status", _probe_telegram_status, ["plugin:telegram.status"]),
                ("http", _probe_http, ["plugin:http.get", "plugin:monitor.feed"]),
                ("mcp", _probe_mcp, ["plugin:mcp.tool_list", "plugin:mcp.tool_call", MCP_DYNAMIC]))
    for label, fn, ids in families:
        got: list[Row] = []
        try:
            got = await fn(lab, base)
            crash = ""
        except Exception as exc:  # noqa: BLE001 - a crashed probe proves nothing: NOT_RUN, never FAILED
            crash = f"probe crashed: {type(exc).__name__}: {short(exc, 200)}"
        have = {r.id for r in got}
        rows.extend(got)
        for i in ids:
            if i not in have:
                rows.append(_row(i, NOT_RUN, crash or "probe produced no row"))
    have = {r.id for r in rows}
    for i in ALL_IDS:
        if i not in have:
            rows.append(_row(i, NOT_RUN, _NOT_RUN_REASONS.get(i, "no probe in this pass"),
                             live_note=LIVE_ABSENT if i in WEB_ROWS or i == MCP_DYNAMIC else ""))
    ids = [r.id for r in rows]
    assert len(ids) == len(set(ids)), f"duplicate rows: {sorted(i for i in ids if ids.count(i) > 1)}"
    return rows
