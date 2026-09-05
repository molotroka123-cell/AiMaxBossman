"""EH-02 (TRUTH-003 §3–§8): наблюдатели пост-состояния terminal/github/memory/schedule/process.
Правило: ответ API / exit code 0 / вызов инструмента — не доказательство; VERIFIED только
по свежему чтению состояния; недоступный источник → UNVERIFIED, не PASS."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import sqlalchemy as sa

from bcc.db import schedules as schedules_t, utcnow
from bcc.v2.memory.facts import FactStore
from bcc.v2.tables import terminal_sessions as ts_t
from bcc.v2.verification import ExpectedState, parse_expected, payload_digest, verify, verify_all


async def _session(svc, sid: str, *, exit_code, status="finished", command="echo hi"):
    async with svc.db.session() as s:
        await s.execute(sa.insert(ts_t).values(id=sid, mode="sandbox", cwd="/tmp", command=command, status=status,
                                               exit_code=exit_code, started_at=utcnow(), finished_at=utcnow()))
        await s.commit()


def test_parse_expected_accepts_new_kinds_and_rejects_unknown():
    kinds = [e.kind for e in parse_expected([{"kind": k, "target": "x"} for k in
                                             ("terminal", "github", "memory", "schedule", "process", "cache", "model")])]
    assert kinds == ["terminal", "github", "memory", "schedule", "process"]


async def test_terminal_exit_code_alone_is_execution_not_effect(env, tmp_path):
    svc = env.svc
    await _session(svc, "t-ok", exit_code=0)
    await _session(svc, "t-bad", exit_code=1)
    r = await verify(ExpectedState("terminal", "t-ok", {"exit_code": 0}), svc=svc, task={}, roots=[tmp_path])
    assert r.status == "UNVERIFIED" and "исполнение" in r.reason                 # COMMAND_EXECUTED ≠ EFFECT
    r = await verify(ExpectedState("terminal", "t-bad", {"exit_code": 0}), svc=svc, task={}, roots=[tmp_path])
    assert r.status == "FAILED"
    target = tmp_path / "out.txt"
    r = await verify(ExpectedState("terminal", "t-ok", {"exit_code": 0, "path": str(target), "exists": True}),
                     svc=svc, task={}, roots=[tmp_path])
    assert r.status == "FAILED"                                                    # эффект объявлен, но его нет
    target.write_text("bossman-proof-1", encoding="utf-8")
    r = await verify(ExpectedState("terminal", "t-ok", {"exit_code": 0, "path": str(target), "contains": "bossman-proof-1"}),
                     svc=svc, task={}, roots=[tmp_path])
    assert r.status == "VERIFIED" and r.evidence[0].source == "terminal:session+file"
    r = await verify(ExpectedState("terminal", "missing", {"exit_code": 0}), svc=svc, task={}, roots=[tmp_path])
    assert r.status == "UNVERIFIED"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, text=True,
                                   stderr=subprocess.STDOUT).strip()


async def test_github_verifier_queries_remote_state_freshly(env, tmp_path):
    remote = tmp_path / "remote.git"; work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    work.mkdir(); _git(work, "init", "-q"); (work / "f.txt").write_text("1")
    _git(work, "add", "."); _git(work, "commit", "-q", "-m", "c1")
    local_sha = _git(work, "rev-parse", "HEAD")
    branch = _git(work, "rev-parse", "--abbrev-ref", "HEAD")
    # LOCAL_COMMIT != REMOTE_PUSH: коммит есть локально, но remote его не знает
    r = await verify(ExpectedState("github", f"{remote} refs/heads/{branch}", {"sha": local_sha}), svc=env.svc, task={})
    assert r.status == "FAILED"
    _git(work, "push", "-q", str(remote), f"HEAD:refs/heads/{branch}")
    r = await verify(ExpectedState("github", f"{remote} refs/heads/{branch}", {"sha": local_sha}), svc=env.svc, task={})
    assert r.status == "VERIFIED" and r.evidence[0].hash == local_sha
    r = await verify(ExpectedState("github", f"{remote} refs/heads/{branch}", {"sha": "0" * 40}), svc=env.svc, task={})
    assert r.status == "FAILED"
    r = await verify(ExpectedState("github", f"{remote} refs/heads/nope", {"exists": False}), svc=env.svc, task={})
    assert r.status == "VERIFIED"
    # remote недоступен → UNVERIFIED, не PASS
    r = await verify(ExpectedState("github", f"{tmp_path / 'absent.git'} refs/heads/{branch}", {"sha": local_sha}), svc=env.svc, task={})
    assert r.status == "UNVERIFIED"


async def test_memory_verifier_reads_back_independently(env):
    store = FactStore(env.svc)
    await store.add(subject="bossman", predicate="proof", statement="bossman proof nonce-7781", object="nonce-7781")
    ok = await verify(ExpectedState("memory", "bossman", {"predicate": "proof", "contains": "nonce-7781"}), svc=env.svc, task={})
    assert ok.status == "VERIFIED"
    bad = await verify(ExpectedState("memory", "bossman", {"predicate": "proof", "contains": "nonce-0000"}), svc=env.svc, task={})
    assert bad.status == "FAILED"
    missing = await verify(ExpectedState("memory", "nobody", {"predicate": "proof"}), svc=env.svc, task={})
    assert missing.status == "FAILED"
    await store.add(subject="bossman", predicate="proof", statement="bossman proof nonce-9999", object="nonce-9999", replace_current=True)
    stale = await verify(ExpectedState("memory", "bossman", {"predicate": "proof", "object": "nonce-7781", "current": True}), svc=env.svc, task={})
    assert stale.status == "FAILED"                                                  # перекрытый факт — не актуален


async def test_schedule_verifier_reads_row_not_api_response(env):
    svc = env.svc
    template = {"title": "t", "prompt": "p", "agent_id": None}
    async with svc.db.session() as s:
        res = await s.execute(sa.insert(schedules_t).values(name="daily", kind="daily", daily_time="09:00", enabled=True,
                                                             task_template=template))
        sid = int(res.inserted_primary_key[0]); await s.commit()
    exp = {"enabled": True, "kind": "daily", "daily_time": "09:00", "payload_digest": payload_digest(template)}
    assert (await verify(ExpectedState("schedule", str(sid), exp), svc=svc, task={})).status == "VERIFIED"
    assert (await verify(ExpectedState("schedule", str(sid), {"enabled": False}), svc=svc, task={})).status == "FAILED"
    assert (await verify(ExpectedState("schedule", str(sid), {"payload_digest": "deadbeef"}), svc=svc, task={})).status == "FAILED"
    assert (await verify(ExpectedState("schedule", str(sid + 100), {"enabled": True}), svc=svc, task={})).status == "FAILED"
    assert (await verify(ExpectedState("schedule", str(sid + 100), {"exists": False}), svc=svc, task={})).status == "VERIFIED"
    assert (await verify(ExpectedState("schedule", "abc", {"enabled": True}), svc=svc, task={})).status == "UNVERIFIED"


async def test_process_verifier_uses_live_pid(env):
    alive = await verify(ExpectedState("process", str(os.getpid()), {"running": True}), svc=env.svc, task={})
    assert alive.status == "VERIFIED"
    proc = subprocess.Popen(["python3", "-c", "pass"]); proc.wait()
    gone = await verify(ExpectedState("process", str(proc.pid), {"running": False}), svc=env.svc, task={})
    assert gone.status == "VERIFIED"
    assert (await verify(ExpectedState("process", "x", {"running": True}), svc=env.svc, task={})).status == "UNVERIFIED"


async def test_verify_all_aggregates_new_kinds_fail_closed(env, tmp_path):
    status, reason, results = await verify_all([ExpectedState("process", str(os.getpid()), {"running": True}),
                                                ExpectedState("terminal", "nope", {"exit_code": 0})], svc=env.svc, task={}, roots=[tmp_path])
    assert status == "UNVERIFIED" and len(results) == 2
