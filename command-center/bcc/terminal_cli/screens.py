"""Single-column renderings for code, self-repair, automation and the lab.

Each view is built ONLY from what the backend (or the lab runner) reported:
phases that did not happen are shown as pending («○»), fields that are absent
are «—». The evolution loop API (/api/evolution/*) is being built by another
lane: `EvolutionAdapter` is the one place that knows its field names, so they
can be adjusted after the lanes merge; a 404 means «недоступно в этой сборке»
and a distinct exit code (10), never a fake status.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from rich.text import Text

from .api_client import BossmanError, Client
from .console import make_console, sanitize
from .human import View, human_duration, render_status_bar, render_title, status_cells
from .records import (EXIT_BLOCKED, EXIT_CONFLICT, EXIT_FAIL, EXIT_INTERRUPTED, EXIT_NOT_SUPPORTED,
                      EXIT_OK, EXIT_WAIT_APPROVAL, model_kind, record)
from .theme import glyphs

NOT_IN_BUILD = "недоступно в этой сборке"

# ----------------------------------------------------------------- coding path

#: sidecar tool -> phase of the Thinking-process line (only real tool calls)
CODING_PHASES = ("Inspect", "Patch", "Run tests", "Verify")
_TOOL_PHASE = {"list_dir": "Inspect", "read_file": "Inspect", "search": "Inspect",
               "edit_file": "Patch", "write_file": "Patch", "run_tests": "Run tests"}


def coding_phases(rec: dict) -> list[tuple[str, str]]:
    """[(phase, done|failed|pending)] from the record's real tool calls and
    Bossman's own verification. Nothing is marked done that did not happen."""
    calls = ((rec.get("sidecar") or {}).get("tool_calls") or [])
    seen: dict[str, str] = {}
    for c in calls:
        phase = _TOOL_PHASE.get(str(c.get("tool") or ""))
        if phase:
            seen[phase] = "done" if c.get("ok") else seen.get(phase, "failed")
    verification = rec.get("verification")
    if isinstance(verification, dict):
        seen["Verify"] = "done" if verification.get("passed") else "failed"
    return [(p, seen.get(p, "pending")) for p in CODING_PHASES]


def render_phases(phases: list[tuple[str, str]]) -> Text:
    g = glyphs()
    t = Text()
    for i, (name, state) in enumerate(phases):
        if i:
            t.append(f" {g.arrow} ", style="muted")
        mark, style = {"done": (g.done, "tool.ok"), "failed": (g.fail, "tool.err"),
                       "running": (g.running, "value.warn")}.get(state, (g.pending, "muted"))
        t.append(f"{mark} {name}", style=style)
    return t


def render_diff(diff: str, *, max_lines: int = 400) -> list[Text]:
    out = []
    lines = sanitize(diff).split("\n")
    for ln in lines[:max_lines]:
        style = ("diff.add" if ln.startswith("+") and not ln.startswith("+++") else
                 "diff.del" if ln.startswith("-") and not ln.startswith("---") else
                 "diff.hunk" if ln.startswith("@@") else "muted")
        out.append(Text(ln, style=style))
    if len(lines) > max_lines:
        out.append(Text(f"… +{len(lines) - max_lines} строк diff", style="muted"))
    return out


def run_coding_task(client: Client, out, *, instruction: str, allow: list[str], protect: list[str],
                    verify: list[str], repo: str, agent: str | None, timeout: int,
                    use_memory: bool) -> int:
    from .cli import UsageError
    if not allow:
        raise UsageError("нужен хотя бы один --allow <путь>: область правок задаётся явно")
    ready = client.get("/api/coding-tasks/readiness")
    if not ready.get("available"):
        raise BossmanError(f"coding path не готов: {ready.get('reason')}", kind="blocked")
    body: dict[str, Any] = {"instruction": instruction, "source_repo": repo, "allowed_paths": allow,
                            "protected_paths": protect, "verify_tests": verify,
                            "timeout_seconds": max(30, min(7200, timeout)), "use_memory": use_memory}
    if agent:
        from .ops import Catalog
        row = Catalog.load(client).find_agent(agent)
        if row is None:
            raise BossmanError(f"агент «{sanitize(agent)}» не найден", kind="not_found")
        body["agent_id"] = row["id"]
    created = client.post("/api/coding-tasks", body)
    ctid = created["id"]
    view = None if out.machine else View(make_console(), plain=False)
    if out.fmt == "stream-json":
        out.json(record("coding_task", subtype="submitted", coding_task_id=ctid, repo=repo))
    started = time.monotonic()
    rec: dict = {}
    try:
        while True:
            rec = client.get(f"/api/coding-tasks/{ctid}")
            if rec.get("status") in ("completed", "failed", "blocked"):
                break
            if view is not None:
                view.status_line(f"coding task {ctid}: {rec.get('status')} · "
                                 f"{human_duration((time.monotonic() - started) * 1000)} · Ctrl+C — отменить")
            time.sleep(1.0)
    except KeyboardInterrupt:
        client.post(f"/api/coding-tasks/{ctid}/cancel")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rec = client.get(f"/api/coding-tasks/{ctid}")
            if rec.get("status") in ("completed", "failed", "blocked"):
                break
            time.sleep(0.5)
        status = rec.get("status")
        res = record("coding_result", ok=True, coding_task_id=ctid, status=status,
                     outcome=rec.get("outcome"), note="Ctrl+C: отмена запрошена", exit_code=EXIT_INTERRUPTED)
        (out.json(res) if out.machine else out.say(f"coding task {ctid}: {status} ({rec.get('outcome')})"))
        return EXIT_INTERRUPTED
    return report_coding(out, view, rec)


#: refusal codes of POST /api/coding-tasks/{id}/apply (bcc.features.coding_tasks)
APPLY_REFUSALS = ("NOT_ELIGIBLE", "STALE_BASE", "DIRTY_TARGET", "PROTECTED_PATH", "APPLY_CHECK_FAILED",
                  "APPLY_FAILED", "AFTER_STATE_MISMATCH", "APPROVAL_INVALID", "RUNTIME_UNAVAILABLE")


def apply_coding_task(client: Client, out, task_id: str, approval_id: int | None) -> int:
    """`bossman code apply <id>`: ask for / use the owner's approval to bring a
    verified candidate into the canonical project. This command never approves:
    without an approved id it ends WAIT_APPROVAL (4) and names the approval."""
    body = {} if approval_id is None else {"approval_id": int(approval_id)}
    try:
        res = client.post(f"/api/coding-tasks/{task_id}/apply", body) or {}
    except BossmanError as exc:
        if exc.code == "ALREADY_APPLIED":
            state, exit_code = "CONFLICT", EXIT_CONFLICT
        elif exc.code in APPLY_REFUSALS:
            state, exit_code = "BLOCKED", EXIT_BLOCKED
        else:
            raise
        rec = record("coding_apply", ok=False, coding_task_id=task_id, task_state=state, code=exc.code,
                     error=sanitize(exc.message), exit_code=exit_code)
        (out.json(rec) if out.machine else out.say(f"coding apply {task_id}: {exc.code} — {sanitize(exc.message)}"))
        return exit_code
    if res.get("state") == "WAIT_APPROVAL":
        rec = record("coding_apply", ok=True, coding_task_id=task_id, task_state="WAIT_APPROVAL",
                     approval_id=res.get("approval_id"), preview=sanitize(res.get("preview")),
                     note="нужно решение владельца; затем повторите с --approval-id",
                     exit_code=EXIT_WAIT_APPROVAL)
        if out.machine:
            out.json(rec)
        else:
            out.say(sanitize(res.get("preview") or ""))
            out.say(f"ждёт одобрения владельца: разрешение #{res.get('approval_id')}; после одобрения — "
                    f"bossman code apply {task_id} --approval-id {res.get('approval_id')}")
        return EXIT_WAIT_APPROVAL
    ok = res.get("state") == "APPLIED"
    rec = record("coding_apply", ok=ok, coding_task_id=task_id, task_state="PASS" if ok else "FAIL",
                 applied_digest=res.get("applied_digest"), files=res.get("files"),
                 committed=res.get("committed"), exit_code=EXIT_OK if ok else EXIT_FAIL)
    if out.machine:
        out.json(rec)
    else:
        out.say(f"coding apply {task_id}: {'применено в рабочее дерево (без commit)' if ok else res.get('state')}"
                + (": " + ", ".join(sanitize(f) for f in (res.get("files") or {})) if ok else ""))
    return rec["exit_code"]


def coding_result_record(rec: dict) -> dict:
    status = rec.get("status")
    sidecar = rec.get("sidecar") or {}
    verification = rec.get("verification")
    exit_code = EXIT_OK if status == "completed" else EXIT_BLOCKED if status == "blocked" else EXIT_FAIL
    return record("coding_result", ok=True, coding_task_id=rec.get("id"), status=status,
                  task_state={"completed": "PASS", "blocked": "BLOCKED"}.get(status, "FAIL"),
                  error=sanitize(rec.get("error")) or None, outcome=rec.get("outcome"),
                  changed_files=[sanitize(f) for f in rec.get("changed_files") or []],
                  diff=sanitize(rec.get("diff") or "") or None,
                  verification=verification if isinstance(verification, dict) else None,
                  phases=[{"phase": p, "state": s} for p, s in coding_phases(rec)],
                  skills=(rec.get("skills") or {}).get("ids") or sidecar.get("skills_used") or None,
                  memory_used=sidecar.get("memory_used"),
                  model=sidecar.get("model"), model_kind=sidecar.get("model_kind")
                  or (model_kind(sidecar.get("model")) if sidecar.get("model") else None),
                  sandbox_removed=(rec.get("sandbox_cleanup") or {}).get("removed"),
                  exit_code=exit_code)


def report_coding(out, view: View | None, rec: dict) -> int:
    res = coding_result_record(rec)
    if out.machine:
        out.json(res)
        return res["exit_code"]
    g = glyphs()
    assert view is not None
    view.print(render_phases(coding_phases(rec)))
    ok = res["status"] == "completed"
    head = Text(f"{g.ok if ok else g.fail} coding task {res['coding_task_id']}: {res['status']}",
                style="ok" if ok else "error")
    if res.get("model"):
        head.append(f"  · {sanitize(res['model'])}", style="muted")
    if res.get("model_kind") == "MOCK_MODEL":
        head.append("  MOCK_MODEL", style="value.warn")
    view.print(head)
    if res.get("error"):
        view.print(Text("  " + res["error"], style="error"))
    if res.get("changed_files"):
        view.print(Text("  изменены: " + ", ".join(res["changed_files"]), style="text"))
    if res.get("skills"):
        view.print(Text(f"  {g.note} навыки: " + ", ".join(sanitize(s) for s in res["skills"]), style="note"))
    if rec.get("diff"):
        view.print(*render_diff(rec["diff"]))
    verification = res.get("verification")
    if verification:
        passed = verification.get("passed")
        view.print(Text(f"  проверка Bossman: {'PASS' if passed else 'FAIL'}"
                        f" (exit={verification.get('exit_code')})", style="ok" if passed else "error"))
    return res["exit_code"]


# ----------------------------------------------------------------- evolution (1.1 loop)


class EvolutionAdapter:
    """The ONLY place that knows /api/evolution/* field names."""

    BASE = "/api/evolution"
    PHASES = ("OBSERVE", "INSPECT", "REPRODUCE", "PATCH", "RUN_TESTS", "VERIFY", "SAVE_LESSON",
              "CHECKPOINT")

    def __init__(self, client: Client):
        self.client = client

    def call(self, action: str, body: dict | None = None) -> dict:
        try:
            if action in ("status", "report"):
                return self.client.get(f"{self.BASE}/{action}") or {}
            return self.client.post(f"{self.BASE}/{action}", body or {}) or {}
        except BossmanError as exc:
            if exc.kind in ("not_supported", "not_found") and exc.status in (404, 405):
                raise BossmanError(f"/evolution {action}: {NOT_IN_BUILD}", kind="not_supported") from None
            raise

    @classmethod
    def phases(cls, status: dict) -> list[tuple[str, str]]:
        """[(phase, done|running|pending|failed)] from whatever the status
        reports; unknown shape -> all pending (nothing invented)."""
        current = str(status.get("phase") or status.get("stage") or "").upper()
        done = {str(p).upper() for p in status.get("phases_done") or status.get("completed_phases") or []}
        failed = {str(p).upper() for p in status.get("phases_failed") or []}
        out = []
        for p in cls.PHASES:
            if p in failed:
                out.append((p, "failed"))
            elif p in done:
                out.append((p, "done"))
            elif p == current:
                out.append((p, "running"))
            else:
                out.append((p, "pending"))
        return out

    @staticmethod
    def terminal(status: dict) -> bool:
        state = str(status.get("state") or status.get("status") or "").lower()
        return state in ("completed", "failed", "stopped", "idle", "done", "blocked", "timeout")


def evolution_call(client: Client, out, action: str) -> int:
    data = EvolutionAdapter(client).call(action)
    if out.machine:
        out.json(record("evolution", ok=True, action=action, data=data, exit_code=EXIT_OK))
    else:
        out.say(json.dumps(data, ensure_ascii=False, indent=1, default=str)[:4000])
    return EXIT_OK


def run_repair(client: Client, out, *, model: str | None, max_seconds: float, plain: bool) -> int:
    """`bossman repair --self`: ONE bounded cycle of the 1.1 loop with the
    bossman_coding backend, followed to its verdict (Ctrl+C = stop)."""
    adapter = EvolutionAdapter(client)
    body: dict[str, Any] = {"backend": "bossman_coding", "cycles": 1}
    if model:
        body["model"] = model
    started_info = adapter.call("start", body)
    view = None if out.machine else View(make_console(plain=plain or None), plain=bool(plain))
    if out.fmt == "stream-json":
        out.json(record("evolution", subtype="started", data=started_info))
    if view is not None:
        width = view.width
        view.print(render_title(None, None, width, screen="self-repair", plain=view.plain))
        for line in render_status_bar(status_cells(mode="code", model=model or started_info.get("model"),
                                                   model_locality=None, model_kind=None, approvals="on-demand",
                                                   context=None, computer=None), width, plain=view.plain):
            view.print(line)
    t0 = time.monotonic()
    last_phases = None
    status: dict = {}
    try:
        while True:
            status = adapter.call("status")
            phases = adapter.phases(status)
            if phases != last_phases:
                last_phases = phases
                if out.fmt == "stream-json":
                    out.json(record("phase", phases=[{"phase": p, "state": s} for p, s in phases],
                                    raw_phase=status.get("phase") or status.get("stage")))
                elif view is not None:
                    view.print(render_phases(phases))
                    if status.get("current_step"):
                        view.print(Text(f"Current step: {sanitize(status['current_step'])}", style="muted"))
            if adapter.terminal(status):
                break
            if time.monotonic() - t0 > max_seconds:
                adapter.call("stop")
                status = {**status, "state": "timeout"}
                break
            if view is not None:
                view.status_line(f"цикл 1.1 · {human_duration((time.monotonic() - t0) * 1000)} · Ctrl+C — стоп")
            time.sleep(2.0)
    except KeyboardInterrupt:
        adapter.call("stop")
        status = {**status, "state": "stopped", "note": "Ctrl+C: stop requested"}
    report = {}
    try:
        report = adapter.call("report")
    except BossmanError:
        pass
    state = str(status.get("state") or status.get("status") or "").lower()
    verdict = str(report.get("verdict") or status.get("verdict") or "").upper() or None
    exit_code = EXIT_OK if verdict == "PASS" else EXIT_FAIL
    res = record("result", ok=True, task_state=verdict or state.upper() or "UNKNOWN", status=state,
                 report=report or None, duration_ms=int((time.monotonic() - t0) * 1000), exit_code=exit_code)
    if out.machine:
        out.json(res)
    elif view is not None:
        view.print(Text(f"итог цикла: {res['task_state']} ({state or '—'})",
                        style="ok" if verdict == "PASS" else "value.warn"))
    return exit_code


# ----------------------------------------------------------------- automation


COMPUTER_TAGS = {"computer.observe": "screen observed", "computer.screenshot": "screen observed",
                 "computer.focus": "window focused", "computer.act": "action executed",
                 "computer.click": "action executed", "computer.type": "action executed"}


def run_automation(client: Client, out, args) -> int:
    """A normal Bossman task for an agent that has computer-control tools; the
    view shows its real computer tool calls as an action queue. Approvals and
    STOP as always."""
    from .cli import run_headless
    from .ops import Catalog
    cat = Catalog.load(client)
    agent = None
    if args.agent:
        agent = cat.find_agent(args.agent)
    else:
        for a in cat.agents:
            tools = [str(t) for t in (a.get("tools") or []) if isinstance(t, str)]
            if a.get("enabled") and any(t.startswith("computer.") for t in tools):
                if args.local:
                    desc = cat.describe_agent(a).get("model") or {}
                    if desc.get("locality") != "local":
                        continue
                agent = a
                break
    if agent is None:
        raise BossmanError("нет включённого агента с инструментами управления компьютером"
                           + (" на локальной модели" if args.local else ""), kind="blocked",
                           hint="в «Агентах» выдайте агенту computer.* или укажите --agent")
    try:
        status = client.get("/api/computer/status")
    except BossmanError:
        status = {}
    if status and status.get("stopped"):
        raise BossmanError("управление компьютером остановлено (STOP)", kind="blocked",
                           hint="снимите STOP в вебе («Продолжить») или /computer resume")
    from .ops import new_request_id
    return run_headless(out, args, prompt=args.prompt, title="", agent=str(agent["id"]), model=None,
                        cwd=None, request_id=new_request_id(), detach=False,
                        max_seconds=args.max_seconds, approval_mode=args.approval_mode,
                        on_timeout="stop", verbose=False)


# ----------------------------------------------------------------- lab


def _lab_runner() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (Path(sys.executable).resolve().parent.parent / "app-support" / "self_improve_lab.py",
                      here.parents[3] / "tools" / "self_improve_lab.py"):
        if candidate.is_file():
            return candidate
    return None


def run_lab(client: Client, out, args) -> int:
    """`bossman evolve --lab`: the lab runner (tools/self_improve_lab.py,
    owned by another lane) in a child process tree; its JSON report is shown
    as is — a missing field is «—»."""
    runner = _lab_runner()
    if runner is None:
        raise BossmanError(f"лаборатория: {NOT_IN_BUILD} (нет self_improve_lab.py)", kind="not_supported")
    cmd = [sys.executable, str(runner), "compare", "--case", args.case,
           "--data-dir", str(client.target.data_dir), "--base-url", client.target.url, "--json"]
    if args.variants:
        cmd += ["--variants", args.variants]
    try:
        from bossman.apprentice.proc_tree import run_tree
        res = run_tree(cmd, timeout=None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, encoding="utf-8", errors="replace")
        stdout, code = res.stdout, res.returncode
    except ImportError:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        stdout, code = proc.stdout, proc.returncode
    try:
        report = json.loads((stdout or "").strip().splitlines()[-1]) if stdout else {}
    except (ValueError, IndexError):
        report = {"raw": sanitize(stdout)[-2000:]}
    rec = record("lab_result", ok=code == 0, exit_code=code, report=report)
    if out.machine:
        out.json(rec)
    else:
        out.say(json.dumps(report, ensure_ascii=False, indent=1, default=str)[:6000])
    return int(code or 0)
