"""§15 — measure the convergence targets instead of asserting them.

Every number the final report quotes is produced here, by running the thing and
counting what happened. The alternative — writing the target into the report
because the code that should produce it was merged — is exactly the kind of
claim this whole run exists to stop making.

Three benchmarks, each mirroring a pathology the acceptance corpus recorded:

  doc_edit          the T3 shape: a documentation correction. The corpus spent
                    60 owner confirmations and 1 295 189 tokens on it. Measured
                    here: approvals asked, tokens, and whether it completed.
  review_deadlock   the T1/T2/T3 shape: a task parked in `waiting_approval`
                    with nothing pending. Measured here: how many such tasks
                    remain deadlocked after the sweep. Target 0.
  recovery          the T1 shape: an expired provider key. Measured here:
                    whether the run escalates to the owner or burns its retry
                    budget re-sending a rejected credential.

What these runs ARE, so the numbers are not read as something else: synthetic
contract runs. The model adapter is scripted, its token counts are constants
(`_Scripted(tokens=(400, 60))` per call), and the terminal is a fake that
performs the one write the script itself constructs. `tokens_total` therefore
measures the SHAPE of the loop — how many calls it took — not model usage, and
it is not an A/B against the corpus's 1 295 189: that figure is kept as
historical provenance only (`corpus_baseline`, `comparable_ab: false`).

Success is `completed` plus a verified real result on disk. A run that ended
`failed` is not a pass with a good number attached; it is a failed run. A run
that was correctly BLOCKED (the expired-key recovery) is a safety outcome and
is labelled as one — it is not "task success".

Run: python -m scripts.convergence_metrics [--out metrics.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _env():
    """A real server on a temporary database, as the tests use."""
    import httpx
    from bcc.api import create_app
    from bcc.auth import HEADER
    from bcc.config import Settings

    workdir = Path(tempfile.mkdtemp(prefix="bcc-metrics-"))
    settings = Settings(data_dir=workdir / "data",
                        database_url=f"sqlite+aiosqlite:///{workdir / 'data' / 'm.db'}",
                        ui_dir=workdir / "no-ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    svc = app.state.svc
    await svc.start()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                               base_url="http://metrics", headers={HEADER: svc.auth.token})
    return app, svc, client, workdir


class _Scripted:
    """A model that performs a documentation edit: read, write, confirm.

    Deliberately the smallest honest shape of the task — the corpus's T3 did
    the same work and spent 63 steps on it."""

    def __init__(self, commands: list[str], tokens=(400, 60)):
        self.commands = list(commands)
        self.calls = 0
        self.tokens = tokens

    async def chat(self, model, messages, **kw):
        from bcc.providers import ChatResult, ToolCall
        self.calls += 1
        if self.calls <= len(self.commands):
            return ChatResult(text="", tokens_in=self.tokens[0], tokens_out=self.tokens[1],
                              finish="tool_calls", model=model,
                              tool_calls=[ToolCall(id=f"c{self.calls}", name="terminal_run",
                                                   arguments={"command": self.commands[self.calls - 1],
                                                              "mode": "sandbox"},
                                                   raw_arguments="{}")])
        return ChatResult(text="документация исправлена", tokens_in=self.tokens[0],
                          tokens_out=self.tokens[1], model=model)

    async def health(self):
        from bcc.providers import Health
        return Health(status="ok", latency_ms=1)

    async def list_models(self):
        return ["scripted"]


NOTES = Path("docs") / "NOTES.md"
NOTES_FIXED = "fixed\n"


def fake_terminal(workdir: Path, command: str):
    """A terminal stand-in that produces the document it claims to.

    The audit's objection to the previous stand-in was exact: it answered
    `exit_code=0` to every command and wrote nothing, so a "completed" doc
    edit had no document behind it. This one performs the single write the
    script constructs and reads it back honestly — `cat` of a missing file
    is a missing file, not a success."""
    from bcc.tools import ToolResult
    target = workdir / NOTES
    if "open('docs/NOTES.md','w').write(" in command:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(NOTES_FIXED, encoding="utf-8")
        return ToolResult(content="exit_code=0", one_line="terminal: wrote docs/NOTES.md")
    if command.strip().startswith("cat docs/NOTES.md"):
        if not target.exists():
            return ToolResult(content="cat: docs/NOTES.md: No such file or directory\nexit_code=1",
                              one_line="terminal: no such file", error=True)
        return ToolResult(content=target.read_text(encoding="utf-8") + "exit_code=0",
                          one_line="terminal: cat")
    return ToolResult(content=f"unsupported in synthetic run: {command[:60]}\nexit_code=127",
                      one_line="terminal: unsupported", error=True)


def result_verified(workdir: Path) -> bool:
    """The real result, observed on disk — not inferred from a status."""
    target = workdir / NOTES
    return target.exists() and target.read_text(encoding="utf-8") == NOTES_FIXED


def doc_edit_verdict(*, final_status: str, approvals_pass: bool, tokens_total: int,
                     verified: bool) -> dict[str, Any]:
    """The pass criterion, kept pure so it can be tested without the harness.

    `completed` is the task's status and nothing else. `success` additionally
    requires the verified result. `failed` is never a pass."""
    completed = final_status == "completed"
    success = completed and verified
    tokens_pass = tokens_total < 100_000
    return {"completed": completed, "result_verified": verified, "success": success,
            "tokens_pass": tokens_pass,
            "pass": bool(approvals_pass and tokens_pass and success)}


async def _doc_edit(svc, client, *, commands: list[str], label: str,
                    workdir: Path) -> dict[str, Any]:
    """The T3 benchmark: how much does a documentation correction cost now?

    Run in two shapes on purpose, because the honest answer differs and picking
    the flattering one would be the reporting failure this run exists to stop:

      read_modify_verify  cat -> write -> cat. TWO owner decisions, and the
                          second one is correct: a read lease must not cover a
                          write, so the write is its own question. The master
                          target is "0-1 approvals unless policy requires more
                          for a specific real effect", and a write IS such an
                          effect.
      single_write        one write command. ONE decision.
    """
    import sqlalchemy as sa
    from bcc import mission_budget as mb
    from bcc.db import (approval_leases as leases_t, approvals as approvals_t,
                        task_runs as runs_t)
    from bcc.tools import REGISTRY, ToolSpec

    async def handler(args, ctx):
        return fake_terminal(workdir, str(args.get("command", "")))

    REGISTRY.register(ToolSpec(name="terminal.run", description="terminal",
                               handler=handler, input_schema={"command": {"type": "string"}},
                               permission="terminal.run", default_effect="ask"))
    adapter = _Scripted(list(commands))
    svc.registry.adapter_factory = lambda m, p: adapter

    provider = (await client.post("/api/providers", json={
        "name": f"scripted-{label}", "kind": "openai_compat",
        "base_url": "http://127.0.0.1:9/v1", "api_key": "not-a-real-key"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "scripted",
        "alias": f"scripted-{label}"})).json()
    agent = (await client.post("/api/agents", json={
        "name": f"docs-{label}", "model_id": model["id"], "max_steps": 8})).json()
    await client.patch(f"/api/agents/{agent['id']}", json={"tools": ["terminal.run"]})
    task = (await client.post("/api/tasks", json={
        "title": "исправить документацию", "prompt": "поправь docs/NOTES.md",
        "agent_id": agent["id"], "run_now": True})).json()["task"]

    approvals_asked = 0
    for _ in range(12):
        run = await svc.engine.claim()
        if run is None:
            break
        await svc.engine.execute(run)
        pending = (await client.get("/api/approvals")).json()
        if not pending:
            continue
        approvals_asked += 1
        # The owner answers ONCE, with a scope: this is the mechanism, and
        # measuring it without using it would measure the old behaviour.
        await client.post(f"/api/approvals/{pending[0]['id']}",
                          json={"approve": True, "by": "owner",
                                "lease": {"max_uses": 20, "ttl_seconds": 600}})
        # Workers are not running in this harness, so the decision is applied
        # explicitly. Without it the task stays parked and the benchmark would
        # report "1 approval" for work that never finished — a number that
        # looks like the target and means nothing.
        await svc.engine._resume_decided_approvals()

    status = (await client.get(f"/api/tasks/{task['id']}")).json()["task"]["status"]
    metrics = await mb.run_metrics(svc, task["id"])
    async with svc.db.session() as s:
        rows = [dict(r._mapping) for r in (await s.execute(sa.select(approvals_t).where(
            approvals_t.c.task_id == task["id"]))).fetchall()]
        leases = [dict(r._mapping) for r in (await s.execute(sa.select(leases_t).where(
            leases_t.c.task_id == task["id"]))).fetchall()]
    total_approvals = len(rows)
    granted = [str(lease.get("effect_class") or "unknown") for lease in leases]
    distinct = sorted(set(granted))
    # The master's target is "0-1 owner approvals, UNLESS existing policy
    # requires more for a specific real effect" (§7, §16). Truncating that to
    # `<= 1` would mark the run failed for obeying its own rule: a read lease
    # must not cover a write, so a read-then-write edit is two questions by
    # policy, not by waste. What must NOT happen — and what the corpus's 60
    # confirmations were — is the same effect asked for again and again, so
    # the criterion is one question per distinct real effect, and every
    # question is listed below with the effect it bought.
    same_effect_asked_twice = len(granted) > len(distinct)
    unexplained = total_approvals - len(leases)
    approvals_pass = (total_approvals <= 1
                      or (not same_effect_asked_twice and unexplained <= 0))
    verdict = doc_edit_verdict(final_status=status, approvals_pass=approvals_pass,
                               tokens_total=metrics["tokens_total"],
                               verified=result_verified(workdir))
    return {"benchmark": f"doc_edit/{label}",
            "kind": "synthetic_contract",
            # `completed` is the task's status; `success` also needs the
            # document on disk. A run that ended `failed` is a failed run.
            "completed": verdict["completed"],
            "result_verified": verdict["result_verified"],
            "success": verdict["success"],
            # Historical provenance, not a baseline this run is compared to:
            # the tokens here are scripted constants, the corpus's were real.
            "corpus_baseline": {"approvals": 60, "tokens": 1_295_189, "cost_usd": 4.043307,
                                "verdict": "PARTIAL (deadlocked)"},
            "comparable_ab": False,
            "tokens_note": ("scripted constants of the fake adapter (400 in / 60 out per "
                            "call), counted per model call: a loop-shape metric, not "
                            "measured model usage"),
            "approvals_asked": total_approvals,
            # Each question explained, so "2" is a fact with a reason rather
            # than a number to argue about.
            "approvals_explained": [
                {"effect_class": lease.get("effect_class"), "used": lease.get("used"),
                 "of": lease.get("max_uses")} for lease in leases],
            "tokens_total": metrics["tokens_total"],
            "model_cost_usd": metrics["model_cost_usd"],
            "final_status": status,
            "distinct_effect_classes": distinct,
            "same_effect_asked_twice": same_effect_asked_twice,
            "approvals_without_a_recorded_effect": max(0, unexplained),
            "target_approvals": "0-1, or one per distinct real effect (master \u00a77)",
            "target_tokens": "< 100000",
            "approvals_pass": approvals_pass,
            "tokens_pass": verdict["tokens_pass"],
            "pass": verdict["pass"]}


async def _deadlock_rate(svc, client) -> dict[str, Any]:
    """ReviewDeadlockRate: parked with nothing pending, four times in the corpus."""
    import sqlalchemy as sa
    from bcc import review_escalation as resc
    from bcc.db import approvals as approvals_t, tasks as tasks_t, utcnow
    from datetime import timedelta

    provider = (await client.post("/api/providers", json={
        "name": "p2", "kind": "openai_compat", "base_url": "http://127.0.0.1:9/v1",
        "api_key": "not-a-real-key"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "m2", "alias": "m2"})).json()
    agent = (await client.post("/api/agents", json={
        "name": "a2", "model_id": model["id"]})).json()

    made = []
    for index in range(4):                      # the corpus's own count
        task = (await client.post("/api/tasks", json={
            "title": f"deadlock-{index}", "prompt": "x", "agent_id": agent["id"]})).json()["task"]
        async with svc.db.session() as s:
            await s.execute(sa.update(tasks_t).where(tasks_t.c.id == task["id"]).values(
                status="waiting_approval", updated_at=utcnow() - timedelta(minutes=30)))
            await s.execute(sa.delete(approvals_t).where(approvals_t.c.task_id == task["id"]))
            await s.commit()
        made.append(task["id"])

    before = await resc.audit(svc)
    acted = await resc.reconcile(svc)
    after = await resc.audit(svc)
    return {"benchmark": "review_deadlock",
            "kind": "synthetic_contract",
            "corpus_baseline": {"deadlocks": 4, "escape": "/stop only"},
            "reproduced": before["deadlocked"],
            "deadlocked_after_sweep": after["deadlocked"],
            "actions": [a["action"] for a in acted],
            "review_deadlock_rate": after["deadlocked"] / max(1, len(made)),
            "pass": after["deadlocked"] == 0}


async def _recovery(svc, client) -> dict[str, Any]:
    """The T1 benchmark: an expired key must reach the owner, not the retry loop."""
    import sqlalchemy as sa
    from bcc.db import task_runs as runs_t
    from bcc.providers import ProviderError

    class Expired:
        calls = 0

        async def chat(self, model, messages, **kw):
            Expired.calls += 1
            raise ProviderError("HTTP 401 OpenRouter: API key expired", kind="http")

        async def health(self):
            from bcc.providers import Health
            return Health(status="ok")

        async def list_models(self):
            return ["x"]

    svc.registry.adapter_factory = lambda m, p: Expired()
    provider = (await client.post("/api/providers", json={
        "name": "p3", "kind": "openai_compat", "base_url": "http://127.0.0.1:9/v1",
        "api_key": "not-a-real-key"})).json()
    model = (await client.post("/api/models", json={
        "provider_id": provider["id"], "name": "m3", "alias": "m3"})).json()
    agent = (await client.post("/api/agents", json={
        "name": "a3", "model_id": model["id"]})).json()
    task = (await client.post("/api/tasks", json={
        "title": "expired key", "prompt": "x", "agent_id": agent["id"],
        "run_now": True, "max_retries": 5})).json()["task"]

    for _ in range(8):
        run = await svc.engine.claim()
        if run is None:
            break
        await svc.engine.execute(run)

    async with svc.db.session() as s:
        row = (await s.execute(sa.select(runs_t.c.status, runs_t.c.attempt, runs_t.c.error)
                               .where(runs_t.c.task_id == task["id"]))).first()
    error = str(row._mapping["error"] or "")
    blocked_correctly = row._mapping["status"] == "failed" and "владелец" in error
    return {"benchmark": "recovery_on_expired_key",
            # A correctly BLOCKED task is a safety outcome. It is counted as
            # such — never as task success: the task did not do its work,
            # and that was the right result.
            "kind": "safety_outcome",
            "success": False,
            "safety_outcome_pass": blocked_correctly,
            "corpus_baseline": {"behaviour": "retries exhausted before key replacement",
                                "owner_action": "manual key replacement"},
            "provider_calls": Expired.calls,
            "retry_budget": 5,
            "attempts_used": int(row._mapping["attempt"] or 0),
            "final_status": row._mapping["status"],
            "escalated_to_owner": "владелец" in error,
            "pass": blocked_correctly}


async def _run(out: Path) -> int:
    app, svc, client, workdir = await _env()
    started = time.time()
    try:
        write = "python - <<'PY'\nopen('docs/NOTES.md','w').write('fixed\\n')\nPY"
        results = [await _doc_edit(svc, client, label="read_modify_verify",
                                   commands=["cat docs/NOTES.md", write, "cat docs/NOTES.md"],
                                   workdir=Path(workdir)),
                   await _doc_edit(svc, client, label="single_write", commands=[write],
                                   workdir=Path(workdir)),
                   await _deadlock_rate(svc, client),
                   await _recovery(svc, client)]
    finally:
        await client.aclose()
        await svc.stop()
    report = {
        "run": "convergence_metrics",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_seconds": round(time.time() - started, 2),
        "note": ("каждое число получено прогоном, а не записано в отчёт "
                 "по факту слияния кода"),
        "evidence_class": "synthetic_contract",
        "evidence_note": ("scripted adapter, constant token counts, fake terminal that "
                          "performs one constructed write: contract checks of the loop, "
                          "not measured model usage and not an A/B against the corpus"),
        "benchmarks": results,
        "verdict_basis": {
            "task_success": [r["benchmark"] for r in results if r.get("success") is True],
            "safety_outcomes": [r["benchmark"] for r in results if r.get("kind") == "safety_outcome"],
        },
        "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.convergence_metrics")
    parser.add_argument("--out", default="docs/testing/convergence-metrics-20260908.json")
    return asyncio.run(_run(Path(parser.parse_args(argv).out)))


if __name__ == "__main__":
    raise SystemExit(main())
