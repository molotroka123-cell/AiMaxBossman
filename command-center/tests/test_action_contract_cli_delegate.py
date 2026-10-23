"""Live 23.10 (task 203): «передай задачу Claude через cli.delegate: создай файл docs/DELEGATE_PROOF.md …».

Gemma called cli.delegate, Claude Code created the file (rc 0, receipt), but action_contract classified the
prompt as TERMINAL_FILE_ACTION, did not count cli.delegate as its executor, attached terminal.*, fed back
«do it yourself» and the task failed with action_contract/no_verified_action. The file evidence derived
from the prompt was also resolved against the first terminal root instead of the delegate's repo.

The real cli.delegate handler runs here (repo/roots/STOP/ledger checks, receipt); only the external CLI
process (run_cli) is replaced by a fake that edits the repo like Claude Code would.
"""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc import db as dbm
from bcc.features import action_contract as ac
from bcc.features import tools_cli
from bcc.tools import to_api_name

from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

TARGET = "docs/DELEGATE_PROOF.md"
PROMPT = f"Передай задачу Claude через cli.delegate: создай файл {TARGET} с текстом ok."
DELEGATE = to_api_name("cli.delegate")


def _row(**kw) -> dict:
    base = {"tool": "cli.delegate", "status": "executed",
            "result_preview": "🛠 claude (auto) в repo: rc=0, 1.2 с\nготово"}
    base.update(kw)
    return base


def test_verified_delegate_call_needs_executed_and_rc0():
    assert ac._is_verified_delegate_call(_row())
    assert not ac._is_verified_delegate_call(_row(status="error"))
    assert not ac._is_verified_delegate_call(_row(result_preview="🛠 claude (auto) в repo: rc=1, 1 с"))
    assert not ac._is_verified_delegate_call(_row(result_preview="🛠 claude (auto) в repo: rc=None, 0 с"))
    assert not ac._is_verified_delegate_call(_row(result_preview="no rc reported"))
    assert not ac._is_verified_delegate_call(_row(tool="terminal.run"))


def test_delegate_tool_name_is_not_file_evidence():
    ev = ac._terminal_evidence(PROMPT)
    assert ev is not None and ev.target == TARGET


def test_delegation_never_counts_for_github():
    caps = {c.name: c for c in ac.CAPABILITIES}
    assert caps["TERMINAL_FILE_ACTION"].delegate_counts and caps["CODE_ACTION"].delegate_counts
    assert not caps["GITHUB_ACTION"].delegate_counts


async def _setup(env, tmp_path, monkeypatch, *, rc=0, create=True):
    roots = tmp_path / "roots"
    repo = roots / "candidate-wt"
    (repo / ".git").mkdir(parents=True)
    other = roots / "aaa-first-root"          # the verifier's roots[0] is NOT the delegate repo
    other.mkdir()
    async with env.svc.db.session() as s:
        await s.execute(sa.delete(dbm.settings_kv).where(dbm.settings_kv.c.key == "terminal.roots"))
        await s.execute(sa.insert(dbm.settings_kv).values(
            key="terminal.roots", value_enc=env.svc.vault.encrypt(json.dumps([str(other), str(roots)]))))
        await s.commit()
    calls = []

    def fake_run_cli(cli, prompt, cwd, **kw):
        calls.append((cli, str(cwd)))
        if create:
            (cwd / "docs").mkdir(exist_ok=True)
            (cwd / "docs" / "DELEGATE_PROOF.md").write_text("ok\n", encoding="utf-8")
        return {"rc": rc, "answer": "готово" if rc == 0 else "ошибка", "cost_usd": 0.01, "seconds": 1.0,
                "stdout_tail": "", "stderr_tail": "", "stopped": False}

    monkeypatch.setattr(tools_cli, "run_cli", fake_run_cli)
    return repo, other, calls


async def _delegate_task(env, repo, prompt=PROMPT):
    adapter = ToolAdapter([
        ("tool", DELEGATE, {"cli": "claude", "prompt": "создай файл docs/DELEGATE_PROOF.md с текстом ok",
                            "repo": str(repo)}),
        ("text", "Claude создал docs/DELEGATE_PROOF.md."),
    ])
    stack = await _stack_with_tools(env, ["cli.delegate"], adapter=adapter, prompt=prompt, max_steps=6)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}", json={"permissions": {"terminal.run": True}})
    return adapter, stack


async def _rows(env, task_id):
    async with env.svc.db.session() as s:
        return [dict(r._mapping) for r in (await s.execute(sa.select(dbm.tool_calls).where(
            dbm.tool_calls.c.task_id == task_id))).fetchall()]


async def _meta(env, task_id) -> dict:
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(dbm.tasks.c.meta).where(dbm.tasks.c.id == task_id))).first()
    return dict(row._mapping["meta"] or {})


async def test_successful_delegation_completes_the_file_action(env, tmp_path, monkeypatch):
    repo, other, calls = await _setup(env, tmp_path, monkeypatch)
    adapter, stack = await _delegate_task(env, repo)
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED + ("waiting_approval",))
    rows = await _rows(env, stack["task"]["id"])
    assert status == "completed", [(r["tool"], r["status"], r["result_preview"][:80]) for r in rows]
    assert calls == [("claude", str(repo.resolve()))]
    # no «do it yourself» retry, no terminal call
    assert [r["tool"] for r in rows] == ["cli.delegate"]
    assert adapter.calls == 2
    evidence = (await _meta(env, stack["task"]["id"]))["review"]["evidence"]
    assert evidence[0]["target"] == str((repo / TARGET).resolve())
    assert not (other / TARGET).exists()


async def test_failed_delegation_does_not_complete(env, tmp_path, monkeypatch):
    repo, _, _ = await _setup(env, tmp_path, monkeypatch, rc=1, create=False)
    _, stack = await _delegate_task(env, repo)
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED + ("waiting_approval",))
    assert status != "completed"


async def test_delegation_without_the_file_does_not_complete(env, tmp_path, monkeypatch):
    """rc 0 is not the evidence: the file must exist in the delegate's repo on a fresh read."""
    repo, _, _ = await _setup(env, tmp_path, monkeypatch, rc=0, create=False)
    _, stack = await _delegate_task(env, repo)
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED + ("waiting_approval",))
    assert status != "completed"
    assert not (repo / TARGET).exists()


async def test_delegation_does_not_satisfy_a_push_request(env, tmp_path, monkeypatch):
    repo, _, _ = await _setup(env, tmp_path, monkeypatch)
    _, stack = await _delegate_task(env, repo, prompt=PROMPT + " Потом закоммить и запушь.")
    assert "GITHUB_ACTION" in [c.name for c in ac.classify_all(PROMPT + " Потом закоммить и запушь.")]
    status = await _run_task(env, stack["task"]["id"], timeout=20, until=FINISHED + ("waiting_approval",))
    assert status != "completed"
