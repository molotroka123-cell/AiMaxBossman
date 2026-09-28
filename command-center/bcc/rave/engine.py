"""Rave supervisor: durable records, one asyncio runner per agent, pause /
resume / STOP, crash recovery, conflict detection and owner-approved apply.

State lives in `<data>/rave/<rave_id>/rave.json` (atomic tmp+replace) plus an
append-only `events.jsonl`. The live part (runner tasks, process trees) exists
only in this process; after a restart every unfinished agent comes back
`paused` (reason `recovered_after_restart`) and is never re-run silently.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import workspace as wsx
from .connectors import Blocked, Connector, ProcResult, build, child_env
from .spec import RAVE_ID, AgentSpec, SpecError, parse_agents

BOOT_ID = secrets.token_hex(8)
TERMINAL = ("done", "failed", "stopped", "blocked", "interrupted")
LIVE = ("queued", "running", "pausing", "paused", "stopping")
APPLY_KIND = "rave_apply"
OPTIN_KIND = "rave_connector_optin"
TEST_TIMEOUT = 300
MAX_PROMPT = 8000
OPTIN_PREVIEW = {
    "claude": ("Agentic Rave: разрешить агентам запускать официальный Claude Code CLI (`claude -p`) под "
               "ВАШИМ входом и подпиской Claude, в изолированной копии проекта, только правки файлов "
               "(без Bash и веба). Личное использование; расход лимитов вашей подписки."),
    "codex": ("Agentic Rave: разрешить агентам запускать официальный Codex CLI (`codex exec`, sandbox "
              "workspace-write) под ВАШИМ входом ChatGPT (подписка), в изолированной копии проекта. Личное "
              "использование; расход лимитов вашего плана."),
}


class RaveError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status, self.detail = status, {"code": code, "message": message, **extra}


class _Stopped(Exception):
    pass


def now() -> float:
    return round(time.time(), 3)


# ------------------------------------------------------------------ process control


def _tree(pid: int) -> list:
    import psutil
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.Error:
        return []


def _suspend(pid: int, on: bool) -> int:
    import psutil
    n = 0
    for p in _tree(pid):
        with contextlib.suppress(psutil.Error):
            (p.suspend if on else p.resume)()
            n += 1
    return n


@dataclass
class Runner:
    rid: str
    name: str
    task: asyncio.Task | None = None
    gate: asyncio.Event = field(default_factory=asyncio.Event)   # set = may run
    stop: bool = False
    tree: Any = None                                           # bossman ProcessTree
    suspended: bool = False
    rerun: bool = False

    def __post_init__(self) -> None:
        self.gate.set()


# ------------------------------------------------------------------ service


class RaveService:
    def __init__(self, svc: Any):
        self.svc = svc
        self.root = Path(svc.settings.data_dir) / "rave"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runners: dict[tuple[str, str], Runner] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.closing = False

    # ---- storage

    def _dir(self, rid: str) -> Path:
        if not RAVE_ID.match(rid or ""):
            raise RaveError(404, "NOT_FOUND", "рейв не найден")
        return self.root / rid

    def load(self, rid: str) -> dict:
        path = self._dir(rid) / "rave.json"
        if not path.is_file():
            raise RaveError(404, "NOT_FOUND", f"рейв {rid} не найден")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, rec: dict) -> None:
        d = self._dir(rec["id"])
        d.mkdir(parents=True, exist_ok=True)
        rec["updated_at"] = now()
        tmp = d / "rave.json.tmp"
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / "rave.json")

    def lock(self, rid: str) -> asyncio.Lock:
        return self.locks.setdefault(rid, asyncio.Lock())

    async def mutate(self, rid: str, name: str | None, **fields: Any) -> dict:
        async with self.lock(rid):
            rec = self.load(rid)
            if name is not None:
                agent = self._agent(rec, name)
                agent.update(fields)
            else:
                rec.update(fields)
            self.save(rec)
            return rec

    @staticmethod
    def _agent(rec: dict, name: str) -> dict:
        for a in rec["agents"]:
            if a["name"] == name:
                return a
        raise RaveError(404, "NOT_FOUND", f"в рейве {rec['id']} нет агента {name}")

    async def event(self, rid: str, kind: str, agent: str | None = None, **data: Any) -> None:
        line = {"ts": now(), "kind": kind, "agent": agent, **data}
        with contextlib.suppress(OSError):
            with open(self._dir(rid) / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        with contextlib.suppress(Exception):
            await self.svc.bus.emit("rave." + kind, rave_id=rid, agent=agent,
                                    **{k: v for k, v in data.items() if k not in ("kind",)})

    def events(self, rid: str, after: int = 0) -> dict:
        path = self._dir(rid) / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        items = []
        for i, raw in enumerate(lines[after:], start=after + 1):
            with contextlib.suppress(ValueError):
                items.append({"seq": i, **json.loads(raw)})
        return {"events": items, "cursor": len(lines)}

    # ---- views

    @staticmethod
    def derive(rec: dict) -> str:
        st = [a["status"] for a in rec["agents"]]
        if any(a.get("finalizing") for a in rec["agents"]):
            return "running"
        if any(s in ("running", "queued", "pausing", "stopping") for s in st):
            return "running"
        if any(s == "paused" for s in st):
            return "paused"
        if all(s == "done" for s in st):
            return "done"
        if all(s == "stopped" for s in st):
            return "stopped"
        return "partial"

    def view(self, rec: dict) -> dict:
        out = dict(rec)
        out["status"] = self.derive(rec)
        for a in out["agents"]:
            r = self.runners.get((rec["id"], a["name"]))
            a["live"] = bool(r and r.task and not r.task.done())
        return out

    def list(self) -> list[dict]:
        items = []
        for d in sorted(self.root.glob("rv-*"), key=lambda p: p.stat().st_mtime, reverse=True):
            with contextlib.suppress(OSError, ValueError, RaveError):
                rec = self.load(d.name)
                items.append({"id": rec["id"], "prompt": rec["prompt"][:120], "status": self.derive(rec),
                              "created_at": rec["created_at"], "agents": {a["name"]: a["status"] for a in rec["agents"]},
                              "conflicts": len(rec.get("conflicts") or [])})
        return items

    # ---- create

    async def create(self, *, prompt: str, agents: list[str], repo: str | None, allow: list[str],
                     test: str | None) -> dict:
        prompt = (prompt or "").strip()
        if not prompt:
            raise RaveError(422, "USAGE", "пустой prompt")
        if len(prompt) > MAX_PROMPT:
            raise RaveError(422, "USAGE", f"prompt длиннее {MAX_PROMPT} символов")
        try:
            specs = parse_agents(agents)
            connectors = [build(s) for s in specs]
        except SpecError as exc:
            raise RaveError(422, "USAGE", str(exc)) from None
        if test:
            try:
                _split(test)
            except ValueError as exc:
                raise RaveError(422, "USAGE", f"--test не разбирается: {exc}") from None
        rid = "rv-" + secrets.token_hex(4)
        d = self._dir(rid)
        d.mkdir(parents=True)
        if repo:
            project = await self._confined_repo(repo)
            try:
                base = await asyncio.to_thread(wsx.head_commit, project)
            except wsx.WorkspaceError as exc:
                raise RaveError(400, "BAD_REPO", f"нет коммита HEAD: {exc}") from None
            scratch = False
        else:
            project = d / "base"
            base = await asyncio.to_thread(wsx.create_scratch_repo, project, prompt)
            scratch = True
        rec = {"id": rid, "prompt": prompt, "repo": str(project), "scratch": scratch, "base_commit": base,
               "allow": [a.replace("\\", "/").strip("/") for a in allow if a.strip()], "test_cmd": test or None,
               "created_at": now(), "boot_id": BOOT_ID, "conflicts": [], "applied": {},
               "agents": [self._agent_record(rid, d, s, c) for s, c in zip(specs, connectors)]}
        self.save(rec)
        await self.event(rid, "created", prompt=prompt[:200], agents=[s.raw for s in specs], repo=str(project),
                         base=base)
        for s in specs:
            self._start(rid, s.name)
        return self.view(rec)

    @staticmethod
    def _agent_record(rid: str, d: Path, spec: AgentSpec, conn: Connector) -> dict:
        desc = conn.describe()
        return {"name": spec.name, "connector": spec.connector, "spec": spec.to_dict(),
                "provider": desc.get("provider"), "model": desc.get("model"), "auth": desc.get("auth"),
                "status": "queued",
                "step": 0, "steps_total": None, "step_label": None, "journal": {},
                "answer": None, "error": None, "workspace": str(d / "agents" / spec.name / "ws"),
                "branch": f"rave/{rid}/{spec.name}", "fingerprint": None, "result_commit": None,
                "changed_files": [], "diff_stat": "", "tests": None, "started_at": None,
                "finished_at": None, "pause_reason": None, "stop_requested": False, "meta": {}}

    async def _confined_repo(self, raw: str) -> Path:
        from ..features.tools_code import _within, allowed_roots
        try:
            p = Path(raw).expanduser().resolve(strict=True)
        except (OSError, ValueError):
            raise RaveError(400, "BAD_REPO", f"путь недоступен: {raw}") from None
        roots = await allowed_roots(self.svc)
        if not any(_within(p, [r]) for r in roots):
            raise RaveError(403, "OUTSIDE_ROOTS", "репозиторий вне разрешённых корней Bossman",
                            hint="добавьте корень в настройки code/terminal roots")
        if not wsx.is_git_repo(p):
            raise RaveError(400, "BAD_REPO", "это не git-репозиторий")
        return p

    # ---- runners

    def _start(self, rid: str, name: str, *, rerun: bool = False) -> Runner:
        runner = Runner(rid, name, rerun=rerun)
        self.runners[(rid, name)] = runner
        runner.task = asyncio.create_task(self._run_agent(runner), name=f"rave-{rid}-{name}")
        return runner

    async def _run_agent(self, runner: Runner) -> None:
        rid, name = runner.rid, runner.name
        rec = self.load(rid)
        agent = self._agent(rec, name)
        spec = AgentSpec(agent["spec"]["connector"], name, agent["spec"].get("model"),
                         dict(agent["spec"].get("params") or {}))
        conn = build(spec)
        ctx = AgentCtx(self, runner, rec, agent)
        final: dict[str, Any] = {}
        try:
            await self.mutate(rid, name, status="running", started_at=agent.get("started_at") or now(),
                              pause_reason=None, error=None)
            await self.event(rid, "agent_started", name, provider=agent["provider"], model=agent["model"])
            await self._ensure_workspace(rid, name, rec)
            # interrupted non-idempotent step from an earlier boot: owner decides
            inflight = [int(k) for k, v in (agent.get("journal") or {}).items() if v == "started"]
            if inflight and not conn.idempotent and not runner.rerun:
                final = {"status": "interrupted",
                         "error": (f"шаг {inflight[0]} был в работе при перезапуске Bossman; исход НЕИЗВЕСТЕН. "
                                   f"Рабочая копия сохранена. Повторить шаг поверх неё — решение владельца: "
                                   f"bossman rave resume {rid} --agent {name}")}
                return
            if conn.optin:
                await self._require_optin(conn.optin)
            await conn.preflight(ctx)
            outcome = await conn.run(ctx)
            final = {"status": "done", "answer": outcome.answer[:20000], "meta": outcome.meta}
        except Blocked as exc:
            final = {"status": "blocked", "error": exc.reason, "meta": {k: v for k, v in exc.extra.items()}}
        except (_Stopped, asyncio.CancelledError) as exc:
            if not runner.stop:                   # backend shutdown: leave durable state for recovery
                if isinstance(exc, asyncio.CancelledError):
                    raise
            final = {"status": "stopped", "error": "остановлено владельцем (STOP)"}
        except Exception as exc:  # noqa: BLE001 — one agent's crash never touches the others
            final = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:1500]}
        finally:
            if final:
                await self._finish(runner, final)

    async def _ensure_workspace(self, rid: str, name: str, rec: dict) -> None:
        agent = self._agent(rec, name)
        ws = Path(agent["workspace"])
        if ws.is_dir() and (ws / ".git").exists():
            return
        fp = await asyncio.to_thread(wsx.create_workspace, Path(rec["repo"]), rec["base_commit"], ws,
                                     agent["branch"])
        await self.mutate(rid, name, fingerprint=fp)
        agent["fingerprint"] = fp
        await self.event(rid, "workspace_ready", name, workspace=str(ws), branch=agent["branch"])

    async def _finish(self, runner: Runner, final: dict) -> None:
        rid, name = runner.rid, runner.name
        runner.tree = None
        rec = self.load(rid)
        agent = self._agent(rec, name)
        ws = Path(agent["workspace"])
        snap: dict[str, Any] = {}
        if ws.is_dir() and (ws / ".git").exists():
            try:
                snap = await asyncio.to_thread(wsx.snapshot, ws, f"rave {rid}: agent {name} ({final['status']})",
                                               agent.get("fingerprint"))
            except Exception as exc:  # noqa: BLE001
                snap = {"error": f"{type(exc).__name__}: {exc}"}
            if snap.get("tampered"):
                dest = self._dir(rid) / "agents" / name / "files-copy"
                n = await asyncio.to_thread(wsx.copy_files, ws, dest)
                final.setdefault("error", "")
                final["error"] = ((final.get("error") or "") + f" · агент изменил .git своей копии — git в ней не "
                                  f"запускается; файлы ({n}) скопированы в {dest}").strip(" ·")
                if final["status"] == "done":
                    final["status"] = "blocked"
        fields: dict[str, Any] = {**final, "finished_at": now()}
        if snap.get("commit"):
            try:
                items = await asyncio.to_thread(wsx.changed, ws, rec["base_commit"], snap["commit"])
                d = await asyncio.to_thread(wsx.diff, ws, rec["base_commit"], snap["commit"], max_bytes=1)
            except Exception:  # noqa: BLE001
                items, d = [], {"stat": ""}
            fields.update(result_commit=snap["commit"], changed_files=items, diff_stat=d["stat"])
        # the owner's test command, in the finished workspace only
        if final["status"] == "done" and rec.get("test_cmd") and snap.get("commit") and not runner.stop:
            await self.mutate(rid, name, step_label="tests")
            fields["tests"] = await self._run_tests(runner, ws, rec["test_cmd"])
            if runner.stop:
                fields["status"], fields["error"] = "stopped", "остановлено владельцем (STOP) во время тестов"
        # `finalizing` keeps the rave "running" until the conflict check below has
        # seen this agent's result: a watcher never reads a final table without it.
        await self.mutate(rid, name, **fields, finalizing=True)
        await self.event(rid, "agent_" + fields["status"], name, error=fields.get("error"),
                         files=len(fields.get("changed_files") or []),
                         tests=(fields.get("tests") or {}).get("passed"))
        try:
            await self.refresh_conflicts(rid)
        finally:
            await self.mutate(rid, name, finalizing=False)

    async def _run_tests(self, runner: Runner, ws: Path, cmd: str) -> dict:
        argv = _split(cmd)
        env = child_env()
        res = await AgentCtx.spawn(self, runner, argv, cwd=ws, stdin=b"", timeout=TEST_TIMEOUT, env=env)
        text = (res.stdout + res.stderr).decode("utf-8", "replace")
        return {"ran": True, "command": cmd, "exit_code": res.returncode, "timed_out": res.timed_out,
                "passed": (not res.timed_out) and res.returncode == 0, "output_tail": text[-3000:]}

    async def refresh_conflicts(self, rid: str) -> list[dict]:
        # serialized per rave: two agents ending together must not let the older
        # computation (fewer finished agents) overwrite the newer one
        async with self.locks.setdefault(rid + ":conflicts", asyncio.Lock()):
            return await self._refresh_conflicts(rid)

    async def _refresh_conflicts(self, rid: str) -> list[dict]:
        rec = self.load(rid)
        results = [wsx.AgentResult(a["name"], Path(a["workspace"]), a["result_commit"])
                   for a in rec["agents"] if a.get("result_commit") and a["result_commit"] != rec["base_commit"]]
        found: list[dict] = []
        if len(results) >= 2:
            try:
                found = await asyncio.to_thread(wsx.detect_conflicts, rec["base_commit"], results,
                                                self._dir(rid) / "conflicts")
            except Exception as exc:  # noqa: BLE001
                found = [{"error": f"{type(exc).__name__}: {exc}"}]
        old = {(c.get("file"), tuple(c.get("agents") or [])) for c in rec.get("conflicts") or []}
        await self.mutate(rid, None, conflicts=found)
        for c in found:
            if (c.get("file"), tuple(c.get("agents") or [])) not in old:
                await self.event(rid, "conflict", None, file=c.get("file"), agents=c.get("agents"),
                                 auto_mergeable=c.get("auto_mergeable"))
        return found

    # ---- opt-in (a normal Bossman approval)

    async def _require_optin(self, key: str) -> None:
        store = self.root / "optin.json"
        data = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
        if data.get(key, {}).get("approved"):
            return
        preview = OPTIN_PREVIEW[key]
        approvals = self.svc.approvals
        rows = await approvals.list("approved,pending")
        mine = [a for a in rows if a.get("kind") == OPTIN_KIND and a.get("preview") == preview]
        approved = [a for a in mine if a.get("status") == "approved"]
        if approved and await approvals.consume(approved[0]["id"], kind=OPTIN_KIND, preview=preview):
            data[key] = {"approved": True, "approval_id": approved[0]["id"], "at": now()}
            store.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            return
        pending = [a for a in mine if a.get("status") == "pending"]
        appr = pending[0] if pending else await approvals.create(OPTIN_KIND, preview)
        raise Blocked(f"нужно разрешение владельца на коннектор {key}: bossman approve {appr.get('id')}, "
                      f"затем bossman rave resume <id> --agent <имя>", approval_id=appr.get("id"))

    def optin_state(self) -> dict:
        store = self.root / "optin.json"
        return json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}

    # ---- controls

    def _targets(self, rec: dict, agent: str | None) -> list[dict]:
        return [self._agent(rec, agent)] if agent else list(rec["agents"])

    async def pause(self, rid: str, agent: str | None = None) -> dict:
        rec = self.load(rid)
        changed = []
        for a in self._targets(rec, agent):
            r = self.runners.get((rid, a["name"]))
            if a["status"] not in ("running", "queued") or not r or not r.task or r.task.done():
                continue
            r.gate.clear()
            status = "pausing"
            if r.tree is not None and not r.suspended:
                n = await asyncio.to_thread(_suspend, r.tree.pid, True)
                r.suspended = True
                status = "paused"
                await self.event(rid, "suspended", a["name"], processes=n)
            await self.mutate(rid, a["name"], status=status, pause_reason="owner")
            await self.event(rid, "agent_" + status, a["name"])
            changed.append(a["name"])
        return {"ok": True, "changed": changed, "rave": self.view(self.load(rid))}

    async def resume(self, rid: str, agent: str | None = None) -> dict:
        rec = self.load(rid)
        changed = []
        for a in self._targets(rec, agent):
            key = (rid, a["name"])
            r = self.runners.get(key)
            live = bool(r and r.task and not r.task.done())
            if live and a["status"] in ("paused", "pausing"):
                if r.suspended and r.tree is not None:
                    await asyncio.to_thread(_suspend, r.tree.pid, False)
                    r.suspended = False
                r.gate.set()
                await self.mutate(rid, a["name"], status="running", pause_reason=None)
                await self.event(rid, "agent_resumed", a["name"])
                changed.append(a["name"])
            elif not live and a["status"] == "paused":
                # recovered after a restart: continue from the journal (done steps are skipped)
                self._start(rid, a["name"])
                await self.event(rid, "agent_resumed", a["name"], recovered=True)
                changed.append(a["name"])
            elif not live and a["status"] in ("interrupted", "blocked") and agent:
                # an explicit per-agent owner decision: re-run on top of the kept workspace
                self._start(rid, a["name"], rerun=a["status"] == "interrupted")
                await self.event(rid, "agent_rerun", a["name"], previous=a["status"])
                changed.append(a["name"])
        return {"ok": True, "changed": changed, "rave": self.view(self.load(rid))}

    async def stop(self, rid: str, agent: str | None = None) -> dict:
        rec = self.load(rid)
        changed = []
        for a in self._targets(rec, agent):
            if a["status"] in TERMINAL:
                continue
            key = (rid, a["name"])
            r = self.runners.get(key)
            await self.mutate(rid, a["name"], stop_requested=True, status="stopping")
            if r and r.task and not r.task.done():
                r.stop = True
                r.gate.set()
                if r.tree is not None:
                    if r.suspended:
                        await asyncio.to_thread(_suspend, r.tree.pid, False)
                    await asyncio.to_thread(r.tree.kill)
                r.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(asyncio.shield(r.task), timeout=30)
            else:
                # no live runner (recovered/paused/queued in a dead boot): stop is final right away
                runner = Runner(rid, a["name"], stop=True)
                await self._finish(runner, {"status": "stopped", "error": "остановлено владельцем (STOP)"})
            changed.append(a["name"])
        await self.event(rid, "stop", agent, agents=changed)
        return {"ok": True, "changed": changed, "rave": self.view(self.load(rid))}

    async def stop_all(self) -> dict:
        stopped = {}
        for item in self.list():
            if item["status"] in ("running", "paused"):
                res = await self.stop(item["id"])
                stopped[item["id"]] = res["changed"]
        return {"ok": True, "stopped": stopped}

    # ---- recovery / shutdown

    async def recover(self) -> dict:
        recovered = {}
        for d in self.root.glob("rv-*"):
            try:
                rec = self.load(d.name)
            except (RaveError, OSError, ValueError):
                continue
            if rec.get("boot_id") == BOOT_ID:
                continue
            names = []
            refresh = any(a.pop("finalizing", False) for a in rec["agents"])
            for a in rec["agents"]:
                if a["status"] in LIVE:
                    if a.get("stop_requested") or a["status"] == "stopping":
                        a.update(status="stopped", error="остановлено владельцем (STOP) до перезапуска",
                                 finished_at=a.get("finished_at") or now())
                    else:
                        a.update(status="paused", pause_reason="recovered_after_restart")
                    names.append(a["name"])
            rec["boot_id"] = BOOT_ID
            self.save(rec)
            if refresh:
                with contextlib.suppress(Exception):
                    await self.refresh_conflicts(rec["id"])
            if names:
                recovered[rec["id"]] = names
                await self.event(rec["id"], "recovered", None, agents=names, boot=BOOT_ID)
        return recovered

    async def shutdown(self) -> None:
        """Backend stopping: kill child trees, cancel runners, keep durable state as is."""
        self.closing = True
        for r in list(self.runners.values()):
            if r.task and not r.task.done():
                if r.tree is not None:
                    with contextlib.suppress(Exception):
                        if r.suspended:
                            _suspend(r.tree.pid, False)
                        r.tree.kill()
                r.task.cancel()

    # ---- diff / apply

    async def diff(self, rid: str, name: str) -> dict:
        rec = self.load(rid)
        a = self._agent(rec, name)
        ws = Path(a["workspace"])
        if not (ws / ".git").exists():
            return {"agent": name, "status": a["status"], "stat": "", "patch": "", "files": [],
                    "note": "рабочая копия ещё не создана"}
        head = a.get("result_commit")
        if not head:
            # live agent: show the working tree against base without committing
            if wsx.tampered(ws, a.get("fingerprint")):
                raise RaveError(409, "TAMPERED", "агент изменил .git своей копии: git в ней не запускается")
            proc = await asyncio.to_thread(wsx.git, ws, "diff", "--no-ext-diff", rec["base_commit"], check=False)
            stat = await asyncio.to_thread(wsx.git, ws, "diff", "--no-ext-diff", "--stat", rec["base_commit"],
                                           check=False)
            return {"agent": name, "status": a["status"], "live": True, "stat": wsx.out(stat),
                    "patch": proc.stdout.decode("utf-8", "replace")[:400_000], "files": [],
                    "note": "агент ещё работает: незакоммиченное состояние (новые файлы не показаны до конца)"}
        d = await asyncio.to_thread(wsx.diff, ws, rec["base_commit"], head)
        return {"agent": name, "status": a["status"], "base": rec["base_commit"], "head": head, **d}

    @staticmethod
    def apply_preview(rec: dict, a: dict) -> str:
        files = ", ".join(sorted(c["path"] for c in a.get("changed_files") or []))
        return (f"rave apply: рейв {rec['id']} · агент {a['name']} → проект (рабочее дерево, без commit/push)\n"
                f"repo: {rec['repo']}\nbase: {rec['base_commit']}\nresult: {a.get('result_commit')}\nfiles: {files}")

    async def apply(self, rid: str, name: str, approval_id: int | None) -> tuple[int, dict]:
        rec = self.load(rid)
        a = self._agent(rec, name)
        if a["status"] not in ("done", "stopped", "failed") or not a.get("result_commit"):
            raise RaveError(409, "NOT_ELIGIBLE", f"у агента {name} нет законченного результата (status={a['status']})")
        if not a.get("changed_files"):
            raise RaveError(409, "NOT_ELIGIBLE", f"агент {name} ничего не изменил")
        if rec["applied"].get(name):
            raise RaveError(409, "ALREADY_APPLIED", f"результат {name} уже применён", **rec["applied"][name])
        preview = self.apply_preview(rec, a)
        approvals = self.svc.approvals
        # preflight BEFORE asking for / spending an approval: a result that can no
        # longer apply cleanly is refused with nothing written and nothing consumed
        check = await asyncio.to_thread(wsx.apply_to_project, Path(rec["repo"]), Path(a["workspace"]),
                                        rec["base_commit"], a["result_commit"], dry_run=True)
        if check["conflicts"]:
            await self.event(rid, "apply_conflict", name, files=check["conflicts"])
            raise RaveError(409, "CONFLICT", "файлы проекта уже не совпадают с базой рейва (например, применён "
                                             "другой агент): ничего не записано, обе версии сохранены",
                            files=check["conflicts"])
        if approval_id is None:
            pending = [x for x in await approvals.list("pending")
                       if x.get("kind") == APPLY_KIND and x.get("preview") == preview]
            appr = pending[0] if pending else await approvals.create(APPLY_KIND, preview)
            await self.event(rid, "apply_requested", name, approval_id=appr.get("id"))
            return 202, {"state": "WAIT_APPROVAL", "approval_id": appr.get("id"), "preview": preview}
        if not await approvals.consume(approval_id, kind=APPLY_KIND, preview=preview):
            raise RaveError(403, "APPROVAL_INVALID", "разрешение не одобрено, уже использовано или выдано на "
                                                     "другое действие; запросите новое")
        async with self.lock(rid):
            res = await asyncio.to_thread(wsx.apply_to_project, Path(rec["repo"]), Path(a["workspace"]),
                                          rec["base_commit"], a["result_commit"])
        if not res["applied"]:
            await self.event(rid, "apply_conflict", name, files=res["conflicts"])
            raise RaveError(409, "CONFLICT", "файлы проекта уже не совпадают с базой рейва (например, применён "
                                             "другой агент): ничего не записано, обе версии сохранены",
                            files=res["conflicts"], approval_id=approval_id)
        entry = {"at": now(), "approval_id": approval_id, "files": res["files"], "commit": a["result_commit"]}
        rec = await self.mutate(rid, None, applied={**self.load(rid)["applied"], name: entry})
        await self.event(rid, "applied", name, files=res["files"], approval_id=approval_id)
        return 200, {"state": "APPLIED", "agent": name, **entry}


def _split(cmd: str) -> list[str]:
    parts = shlex.split(cmd, posix=os.name != "nt")
    if os.name == "nt":
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    if not parts:
        raise ValueError("пустая команда")
    return parts


# ------------------------------------------------------------------ agent context


class AgentCtx:
    """What a connector sees: its workspace, the prompt, the step journal, and
    pause/STOP-aware process spawning. No other authority."""

    def __init__(self, service: RaveService, runner: Runner, rec: dict, agent: dict):
        self.service, self.runner = service, runner
        self.rid, self.name = rec["id"], agent["name"]
        self.prompt = rec["prompt"]
        self.workspace = Path(agent["workspace"])
        self.agent_dir = self.workspace.parent
        self.journal: dict[str, str] = dict(agent.get("journal") or {})
        self.allow = list(rec.get("allow") or [])
        if not self.allow:
            self.allow = None  # computed lazily from the base tree

    async def checkpoint(self) -> None:
        r = self.runner
        if r.stop:
            raise _Stopped()
        if not r.gate.is_set():
            await self.service.mutate(self.rid, self.name, status="paused")
            await self.service.event(self.rid, "agent_paused", self.name, at_step=True)
            await r.gate.wait()
            if r.stop:
                raise _Stopped()
            await self.service.mutate(self.rid, self.name, status="running", pause_reason=None)

    def journal_state(self, step: int) -> str | None:
        return self.journal.get(str(step))

    async def begin_step(self, step: int, total: int, label: str) -> None:
        self.journal[str(step)] = "started"
        await self.service.mutate(self.rid, self.name, journal=dict(self.journal), step=step,
                                  steps_total=total, step_label=label)
        await self.service.event(self.rid, "step_started", self.name, step=step, total=total, label=label)

    async def end_step(self, step: int, note: str = "") -> None:
        self.journal[str(step)] = "done"
        await self.service.mutate(self.rid, self.name, journal=dict(self.journal))
        await self.service.event(self.rid, "step_done", self.name, step=step, note=note or None)

    def exec_log(self, line: str) -> None:
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        with open(self.agent_dir / "exec.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} boot={BOOT_ID} {line}\n")

    async def run_process(self, argv: list[str], *, stdin: bytes, timeout: float,
                          env: dict[str, str] | None = None) -> ProcResult:
        if self.allow is None:
            self.allow = await asyncio.to_thread(_top_level, self.workspace)
        return await self.spawn(self.service, self.runner, argv, cwd=self.workspace, stdin=stdin,
                                timeout=timeout, env=env)

    @staticmethod
    async def spawn(service: RaveService, runner: Runner, argv: list[str], *, cwd: Path, stdin: bytes,
                    timeout: float, env: dict[str, str] | None) -> ProcResult:
        """Child process in a Job object (whole tree killed on STOP/timeout); time
        spent suspended by pause does not count against `timeout`."""
        from bossman.apprentice.proc_tree import ProcessTree  # bossman-core
        if runner.stop:
            raise _Stopped()
        tree = ProcessTree(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        runner.tree = tree
        if not runner.gate.is_set():             # paused while starting: suspend at once
            await asyncio.to_thread(_suspend, tree.pid, True)
            runner.suspended = True

        def wait() -> ProcResult:
            active = 0.0
            first = True
            chunks_out: list[bytes] = []
            chunks_err: list[bytes] = []
            try:
                while True:
                    try:
                        o, e = tree.proc.communicate(input=stdin if first else None, timeout=1.0)
                        chunks_out.append(o or b"")
                        chunks_err.append(e or b"")
                        return ProcResult(tree.proc.returncode, b"".join(chunks_out), b"".join(chunks_err), False)
                    except subprocess.TimeoutExpired:
                        first = False
                        if not runner.suspended:
                            active += 1.0
                        if active > timeout:
                            tree.kill()
                            o, e = tree.proc.communicate()
                            return ProcResult(None, o or b"", e or b"", True)
            finally:
                tree.close()

        try:
            return await asyncio.to_thread(wait)
        finally:
            runner.tree = None
            runner.suspended = False


def _top_level(ws: Path) -> list[str]:
    """Default write scope of the local sidecar: every top-level entry of the
    project (minus .git) plus a `rave/` folder for new files."""
    names = sorted({p.name for p in ws.iterdir() if p.name != ".git"} | {"rave"})
    return names
