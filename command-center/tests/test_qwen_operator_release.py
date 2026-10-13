"""Package D (stage 2.2 close): after the owner's button the operator may merge a verified lab commit into main,
publish the lab branch, and run an approved command on the computer. Offline: fakes + a local bare "origin".

What must NOT change: no force, no other branch, no other remote, nothing without the button, never a protected
file, never keys / vault material (see test_qwen_operator.py for the fixtures)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features import qwen_operator as qo
from bcc.features.qwen_operator import OperatorScope

from .test_qwen_operator import PLAN, Fakes, git, repo, stack  # noqa: F401  (fixtures)

BRANCH = "lab/op-0123456789ab"


# ---------------------------------------------------------------- the only two pass-through shapes
@pytest.mark.parametrize("argv,kind", [
    (["git", "push", "origin", BRANCH], "lab_push"),
    (["git", "push", "origin", f"{BRANCH}:{BRANCH}"], "lab_push"),
    (["git", "push", "origin", f"{BRANCH}:main"], "release_main"),
    (["git", "push", "origin", "main"], None),
    (["git", "push", "origin", f"{BRANCH}:develop"], None),
    (["git", "push", "origin", f"+{BRANCH}:main"], None),
    (["git", "push", "--force", "origin", f"{BRANCH}:main"], None),
    (["git", "push", "upstream", f"{BRANCH}:main"], None),
    (["git", "push", "origin", "lab/op-xyz:main"], None),
    (["git", "push", "origin", f":{BRANCH}"], None),
    (["git", "push"], None),
])
def test_only_lab_branch_push_and_release_shapes_pass(argv, kind):
    assert qo.lab_push_kind(argv) == kind


def test_scope_asks_for_release_and_lab_push_and_keeps_everything_else_blocked(tmp_path):
    scope = OperatorScope(tmp_path / "lab", data_dir=tmp_path / "data")
    v = scope.check_command(["git", "push", "origin", f"{BRANCH}:main"])
    assert (v.kind, v.category, v.executable_after_approval) == ("ask", "release_main", True)
    v = scope.check_command(["git", "push", "origin", BRANCH])
    assert (v.kind, v.category, v.executable_after_approval) == ("ask", "lab_push", True)
    for argv in (["git", "push", "origin", "main"], ["git", "push", "--force", "origin", BRANCH],
                 ["git", "merge", BRANCH], ["git", "push", "origin", f"+{BRANCH}:main"]):
        v = scope.check_command(argv)
        assert v.kind == "ask" and v.category in ("promotion", "force_push") and not v.executable_after_approval, argv


# ---------------------------------------------------------------- release: end to end with a local origin
def _origin(repo: Path, tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "origin", "main")
    git(repo, "fetch", "origin")
    return bare


async def _approve_first(svc, *, yes=True):
    for _ in range(300):
        pend = await svc.approvals.list("pending")
        if pend:
            await svc.approvals.decide(pend[0]["id"], yes, "owner")
            return pend[0]
        await asyncio.sleep(0.1)
    raise AssertionError("no pending decision appeared")


def _saved(svc, rid):
    return json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json").read_text("utf-8"))


async def test_owner_button_merges_verified_commit_into_main(stack, repo, tmp_path):  # noqa: F811
    svc, _c, _a = stack
    bare = _origin(repo, tmp_path)
    f = Fakes(repo, PLAN)
    rec = await qo.start_task(svc, "улучши докстринг", source_repo=str(repo), decision_wait_s=60,
                              local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    row = await _approve_first(svc)
    assert row["kind"] == qo.DECISION_KIND and "release_main" in row["preview"]
    await qo._JOBS[rec["id"]]
    saved = _saved(svc, rec["id"])
    d = [x for x in saved["decisions"] if x["category"] == "release_main"][0]
    assert d["status"] == "APPROVED_EXECUTED", d
    assert "слито в main" in d["note"] and "APPROVE" in d["note"]
    assert git(bare, "rev-parse", "main") == saved["commit_sha"]            # main moved to the verified commit
    assert git(repo, "rev-parse", "main") != saved["commit_sha"]            # the local lab never touched local main


async def test_denied_release_pushes_nothing(stack, repo, tmp_path):  # noqa: F811
    svc, _c, _a = stack
    bare = _origin(repo, tmp_path)
    before = git(bare, "rev-parse", "main")
    f = Fakes(repo, PLAN)
    rec = await qo.start_task(svc, "улучши докстринг", source_repo=str(repo), decision_wait_s=60,
                              local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    await _approve_first(svc, yes=False)
    await qo._JOBS[rec["id"]]
    saved = _saved(svc, rec["id"])
    assert [x["status"] for x in saved["decisions"]] == ["DENIED_BY_OWNER"]
    assert git(bare, "rev-parse", "main") == before


def _run_for_release(tmp_path, *, verdict="APPROVE", head="c1", commit="c1", names=()):
    scope = OperatorScope(tmp_path / "lab", data_dir=tmp_path / "data")
    (tmp_path / "lab").mkdir(exist_ok=True)
    rec = {"lab": {"branch": BRANCH}, "commit_sha": commit, "commands": [], "steps": [],
           "verifier": {"verdict": verdict, "independence": "different_family_free_cloud"}}
    run = qo.Run(None, rec, {}, scope)
    out = {"rev-parse": head, "merge-base": "b0", "diff": "\n".join(names)}

    def fake_git(repo, *args, **kw):
        return SimpleNamespace(stdout=out[args[0]].encode(), returncode=0, stderr=b"")
    return run, rec, fake_git


async def test_release_refused_for_protected_files_reject_or_moved_head(tmp_path, monkeypatch):
    argv = ["git", "push", "origin", f"{BRANCH}:main"]
    ran = []
    monkeypatch.setattr(qo, "run_on_host", lambda *a, **k: ran.append(a) or {"exit_code": 0, "stderr": "", "error": ""})
    for kw, word in (({"names": ["command-center/bcc/approvals.py"]}, "защищённые"),
                     ({"verdict": "REJECT"}, "REJECT"), ({"head": "other"}, "HEAD")):
        run, rec, fake_git = _run_for_release(tmp_path, **kw)
        monkeypatch.setattr(qo, "_git", fake_git)
        d = {"argv": argv, "approval_id": 1, "status": "PENDING"}
        await run.apply_lab_push(d, "release_main")
        assert d["status"] == "APPROVED_REFUSED" and word in d["note"], (kw, d)
    assert ran == []                                                       # nothing was pushed in any case


async def test_other_tasks_branch_is_refused(tmp_path, monkeypatch):
    run, rec, fake_git = _run_for_release(tmp_path)
    monkeypatch.setattr(qo, "_git", fake_git)
    d = {"argv": ["git", "push", "origin", "lab/op-ffffffffffff:main"], "approval_id": 1}
    await run.apply_lab_push(d, "release_main")
    assert d["status"] == "APPROVED_REFUSED" and "не принадлежит" in d["note"]


# ---------------------------------------------------------------- the computer itself, only after the button
async def test_host_command_is_parked_then_runs_only_after_owner_approval(stack, repo, tmp_path):  # noqa: F811
    svc, _c, _a = stack
    host = tmp_path / "host_dir"
    host.mkdir()
    plan = {**PLAN, "files": [], "read_only": False, "extra_commands": [
        {"argv": [sys.executable, "-c", "open('proof.txt','w').write('ok')"], "why": "создать файл на ПК",
         "recommendation": "ок", "concerns": "-", "host_cwd": str(host)}]}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "положи файл proof.txt в папку на ПК", source_repo=str(repo),
                              decision_wait_s=60, local_chat=f.local_chat, free_cloud=f.free_cloud, coder=f.coder)
    for _ in range(200):                                                  # parked, nothing ran yet
        if _saved(svc, rec["id"])["decisions"]:
            break
        await asyncio.sleep(0.1)
    assert not (host / "proof.txt").exists()
    d = _saved(svc, rec["id"])["decisions"][0]
    assert d["category"] == "host_command" and d["executable"] is True
    await _approve_first(svc)
    await qo._JOBS[rec["id"]]
    saved = _saved(svc, rec["id"])
    assert (host / "proof.txt").read_text() == "ok"
    assert [x["status"] for x in saved["decisions"] if x["category"] == "host_command"] == ["APPROVED_EXECUTED"]


async def test_host_command_touching_keys_is_never_executable(tmp_path):
    (tmp_path / "lab").mkdir()
    (tmp_path / "vault").mkdir()
    scope = OperatorScope(tmp_path / "lab", data_dir=tmp_path / "data")
    parked = {}

    class R(qo.Run):
        async def park(self, **kw):
            parked.update(kw)
            return {"approval_id": 1, **kw}
    run = R(None, {"commands": [], "steps": [], "decisions": []}, {}, scope)
    out = await run.gate_command(["cmd", "/c", "type", "x"], why="w", host_cwd=str(tmp_path / "vault"))
    assert parked["category"] == "credentials" and parked["executable"] is False and out["ran"] is False


async def test_relative_or_missing_host_dir_is_refused(tmp_path):
    (tmp_path / "lab").mkdir()
    scope = OperatorScope(tmp_path / "lab", data_dir=tmp_path / "data")
    run = qo.Run(None, {"commands": [], "steps": [], "decisions": []}, {}, scope)
    for where in ("relative/dir", str(tmp_path / "does_not_exist")):
        out = await run.gate_command([sys.executable, "-c", "pass"], why="w", host_cwd=where)
        assert out["ran"] is False and "host_cwd" in out["refused"]
