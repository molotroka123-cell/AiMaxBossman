"""Proof (owner 31.10): «проверь что глм 5,3 флеш как и другие реально может создавать файлы на компьютере в папках итд
и писать код если я скажу».

    python tools/prove_project_files.py                       # part A: facts from the grants of every enabled agent
    python tools/prove_project_files.py --write-proof --scripted          # part B, engine path, no LLM
    python tools/prove_project_files.py --write-proof --mirror-agent 48 --add-tools code.write \\
        --base-url http://127.0.0.1:11434/v1 --model qwen3-coder:30b     # part B with a REAL local model
    [--out docs/audits/2026-10-31-project-files]

Part A — facts from the grants, read-only: the owner's Bossman DB (BCC_DATA_DIR or %LOCALAPPDATA%\\Bossman\\CommandCenter)
is opened `mode=ro`; ONLY agents/models/providers config is read (ids, names, tool names, permission flags, base URLs —
never chats, tasks, memory or keys). Per agent: where the model runs (loopback / cloud), chat-only flag, the tools the
model really receives, which of them touch files, whether a file write asks the owner, and whether the agent gets the
project folder (features/chat_projects). The verdict is computed by the SAME functions the engine uses
(bcc.tools.chat_only / _agent_tool_names, bcc.permissions.agent_allowed, provider_governance.is_cloud_offload).

Part B — a real small file write in a TEMP project: a throw-away Bossman in this process (temp data dir, temp projects
root — nothing of the owner's is touched), one agent (by default mirroring the tools + permissions of the chosen real
LOCAL agent, plus --add-tools), a chat in a project, one message «создай файл …», the real engine and the real tools.
`--scripted`: a scripted model that just calls code.write (proves the engine/tool/grant path, no LLM judgement).
`--base-url/--model`: a real local LLM decides. PASS = a file with the random code is inside the project folder.
Exit 0 only when every requested part passed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import string
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for rel in ("command-center", "bossman-core", ""):
    p = str((REPO_ROOT / rel).resolve())
    if p not in sys.path:
        sys.path.insert(0, p)

FILE_WRITE_TOOLS = ("code.write", "code.patch", "terminal.run")


def _data_dir() -> Path:
    return Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter")


def _json(raw):
    if raw is None or isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- part A

def grants(db: Path, only: set[int] | None = None) -> list[dict]:
    from bcc.features.chat_projects import FILE_TOOLS, _is_loopback
    from bcc.permissions import agent_allowed
    from bcc.provider_governance import is_cloud_offload
    from bcc.tools import _agent_tool_names, chat_only

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        models = {r["id"]: dict(r) for r in con.execute("select id, name, alias, kind, provider_id from models")}
        provs = {r["id"]: dict(r) for r in con.execute("select id, name, base_url from providers")}
        agents = [dict(r) for r in con.execute(
            "select id, name, enabled, tools, permissions, model_id, fallback_model_id from agents order by id")]
    finally:
        con.close()
    out = []
    for a in agents:
        if only and a["id"] not in only:
            continue
        if not only and not a["enabled"]:
            continue
        agent = {"tools": _json(a["tools"]) or [], "permissions": _json(a["permissions"]) or {}}
        model = models.get(a["model_id"]) or {}
        prov = provs.get(model.get("provider_id")) or {}
        local = bool(model) and _is_loopback(prov.get("base_url") or "") and not is_cloud_offload(prov, model)
        only_chat = chat_only(agent)
        received = [] if only_chat else _agent_tool_names(agent)
        file_tools = sorted(set(received) & set(FILE_WRITE_TOOLS))
        write_rules = {}
        for t in file_tools:
            if t in ("code.write", "code.patch"):
                write_rules[t] = ("AUTO inside allowed roots (agent holds permission terminal.run)"
                                  if agent_allowed(agent, "terminal.run") else "ASK the owner (no terminal.run permission)")
            else:
                write_rules[t] = ("host mode: ASK every command; sandbox: AUTO only for the read/build list, "
                                  "writes/redirects ASK")
        project_folder = local and not only_chat and bool(set(received) & FILE_TOOLS)
        if only_chat:
            verdict = "CANNOT_CREATE_FILES: chat-only (cloud_pc_access is not true) — the model receives NO tools"
        elif not file_tools:
            verdict = "CANNOT_CREATE_FILES: no file-writing tool granted (code.write / code.patch / terminal.run)"
        elif not local:
            verdict = ("CLOUD_WITH_PC_TOOLS: can run its granted tools inside the owner-configured roots, but gets NO "
                       "project folder (project grant is for local models only)")
        elif set(file_tools) & {"code.write", "code.patch"}:
            verdict = ("CAN_CREATE_FILES_IN_PROJECT: local model with code.write/code.patch; files land in the project "
                       f"folder (code.write: {write_rules.get('code.write') or write_rules.get('code.patch')})")
        else:
            verdict = ("TERMINAL_ONLY: local model, project folder is its cwd, but it can create files only through "
                       "terminal.run commands — every host command asks the owner; grant code.write for direct writes")
        out.append({"id": a["id"], "name": a["name"], "enabled": bool(a["enabled"]),
                    "model": model.get("name"), "model_kind": model.get("kind"), "provider": prov.get("name"),
                    "model_on_this_pc": local, "chat_only": only_chat, "tools_received": received,
                    "file_tools": file_tools, "write_rules": write_rules, "gets_project_folder": project_folder,
                    "permissions_flags": {k: v for k, v in agent["permissions"].items()
                                          if isinstance(v, bool)}, "verdict": verdict})
    return out


# ---------------------------------------------------------------- part B

def _code() -> str:
    return "PRJ" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))


async def write_proof(*, tools: list[str], permissions: dict, base_url: str | None, model_name: str | None,
                      scripted: bool, timeout_s: float) -> dict:
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings

    tmp = Path(tempfile.mkdtemp(prefix="bossman-project-proof-"))
    os.environ["BCC_PROJECTS_ROOT"] = str(tmp / "Projects")
    data = tmp / "data"
    code = _code()
    fake = None
    if scripted:
        fake = await _ScriptedCoder(code).start()
        base_url, model_name = fake.url, "scripted-coder"
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=tmp / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=True,
                     engine_options={"poll_interval": 0.05, "recover_every": 5.0, "retry_base_delay": 0.05})
    svc = app.state.svc
    await svc.start()
    t0 = time.monotonic()
    result: dict = {"temp_dir": str(tmp), "code": code, "model": model_name, "base_url": base_url,
                    "scripted": scripted, "tools": tools, "permissions": permissions}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proof",
                                     headers={HEADER: svc.auth.token}, timeout=60) as c:
            prov = (await c.post("/api/providers", json={"name": "local", "kind": "openai_compat",
                                                          "base_url": base_url})).json()
            mod = (await c.post("/api/models", json={"provider_id": prov["id"], "name": model_name,
                                                     "alias": "proof-model", "context_window": 32768})).json()
            agent = (await c.post("/api/agents", json={"name": "Proof coder", "model_id": mod["id"], "max_steps": 6,
                                                       "max_tokens": 1024, "tools": tools,
                                                       "permissions": permissions})).json()
            proj = (await c.post("/api/projects", json={"name": "PROOF project files"})).json()
            thread = (await c.post("/api/chat/threads", json={"title": "PROOF project files",
                                                              "project": proj["slug"]})).json()
            text = (f"Создай в папке проекта файл hello.py с одной строкой: print('{code}'). "
                    f"Используй инструмент code.write с путём hello.py. Потом коротко ответь «готово».")
            sent = await c.post(f"/api/chat/threads/{thread['id']}/send",
                                json={"text": text, "agent_id": agent["id"], "client_request_id": "proof-" + code})
            sent.raise_for_status()
            task_id = sent.json()["task"]["id"]
            final = {}
            while time.monotonic() - t0 < timeout_s:
                final = (await c.get(f"/api/tasks/{task_id}")).json()["task"]
                if final["status"] in {"completed", "failed", "stopped", "blocked", "waiting_approval"}:
                    break
                await asyncio.sleep(1.0)
            folder = Path(proj["path"])
            files = sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*") if p.is_file())
            hello = folder / "hello.py"
            body = hello.read_text(encoding="utf-8", errors="replace") if hello.is_file() else ""
            import sqlalchemy as sa
            from bcc.db import approvals as ap_t, tool_calls as tc
            async with svc.db.session() as s:
                calls = [dict(r) for r in (await s.execute(
                    sa.select(tc.c.tool, tc.c.status, tc.c.effect).where(tc.c.task_id == task_id)
                    .order_by(tc.c.id))).mappings().all()]
                asks = [{"kind": r["kind"], "status": r["status"], "preview": str(r["preview"] or "")[:300]}
                        for r in (await s.execute(sa.select(ap_t).where(ap_t.c.task_id == task_id))).mappings().all()]
            result["approvals"] = asks
            result.update(project_folder=str(folder), task_id=task_id, status=final.get("status"),
                          workspace_path=final.get("workspace_path"), files_in_project=files,
                          hello_py=body[:300], code_in_file=code in body,
                          outside_root=[str(p) for p in Path(os.environ["BCC_PROJECTS_ROOT"]).glob("*.py")],
                          seconds=round(time.monotonic() - t0, 1),
                          tool_calls=calls[:10])
            result["pass"] = bool(result["code_in_file"]) and result["status"] == "completed"
    finally:
        await svc.stop()
        if fake is not None:
            await fake.stop()
    return result


class _ScriptedCoder:
    """OpenAI-compatible loopback server: first answer = a code.write tool call, second = «готово»."""

    def __init__(self, code: str):
        self.code, self.n, self.server, self.url = code, 0, None, ""

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1"
        return self

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            length = next((int(x.split(b":")[1]) for x in head.split(b"\r\n")
                           if x.lower().startswith(b"content-length:")), 0)
            body = json.loads(await reader.readexactly(length) or b"{}")
            if body.get("stream"):
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                return
            self.n += 1
            if self.n == 1:
                args = {"path": "hello.py", "content": f"print('{self.code}')\n"}
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                    "name": "code_write", "arguments": json.dumps(args)}}]}
            else:
                msg = {"role": "assistant", "content": "готово"}
            data = json.dumps({"model": "scripted-coder", "choices": [{"message": msg, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % len(data) + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", type=int, action="append", help="only these agent ids (repeatable)")
    ap.add_argument("--write-proof", action="store_true")
    ap.add_argument("--scripted", action="store_true")
    ap.add_argument("--mirror-agent", type=int, help="copy tools + permissions of this real agent (part B)")
    ap.add_argument("--add-tools", nargs="*", default=[])
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--out", help="directory for receipt.json")
    args = ap.parse_args()

    receipt: dict = {"tool": "tools/prove_project_files.py", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    ok = True
    db = _data_dir() / "bcc.db"
    if db.is_file():
        rows = grants(db, set(args.agent) if args.agent else None)
        receipt["grants"] = {"db": str(db), "agents": rows}
        for r in rows:
            print(f"[{r['id']:>3}] {r['name']}\n      model={r['model']} on_this_pc={r['model_on_this_pc']} "
                  f"chat_only={r['chat_only']} tools={r['tools_received'] or '—'}\n      -> {r['verdict']}")
    else:
        receipt["grants"] = {"db": str(db), "error": "no Bossman DB here (set BCC_DATA_DIR)"}
        print(f"part A skipped: {db} not found")

    if args.write_proof:
        tools, perms = ["code.write"], {"terminal.run": True}
        if args.mirror_agent is not None and db.is_file():
            src = grants(db, {args.mirror_agent})
            if not src:
                print(f"agent {args.mirror_agent} not found")
                return 2
            if not src[0]["model_on_this_pc"]:
                print(f"agent {args.mirror_agent} is not a local model: part B writes only for local models")
                return 2
            tools = list(src[0]["tools_received"])
            perms = {k: v for k, v in src[0]["permissions_flags"].items()}
        tools = sorted(set(tools) | set(args.add_tools or []))
        if not args.scripted and not (args.base_url and args.model):
            print("part B needs --scripted or --base-url + --model (a LOCAL model)")
            return 2
        res = asyncio.run(write_proof(tools=tools, permissions=perms, base_url=args.base_url, model_name=args.model,
                                      scripted=args.scripted, timeout_s=args.timeout))
        receipt["write_proof"] = res
        ok = ok and res.get("pass", False)
        print(json.dumps({k: res.get(k) for k in ("status", "workspace_path", "files_in_project", "hello_py",
                                                  "code_in_file", "seconds", "tool_calls", "approvals", "pass")},
                         ensure_ascii=False, indent=1))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"receipt: {out / 'receipt.json'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
