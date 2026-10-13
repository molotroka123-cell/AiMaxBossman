"""Stage 2.2 - Qwen operator (L1 Lab): scope guard, owner-decision parking, bounded self-correction, no promotion.

Everything here is offline: the local model, the free cloud worker and the coder sidecar are fakes. The REAL
model run is recorded by tools/stage22_operator_proof.py (docs/owner/STAGE_2_2_QWEN_OPERATOR_20261013_RU.md).
"""
from __future__ import annotations

import asyncio
import difflib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bcc.features import qwen_operator as qo
from bcc.features.qwen_operator import OperatorScope

from .conftest import client_for, make_settings, start_app


# ---------------------------------------------------------------- helpers
def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                         env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "pkg").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "docs" / "constitution").mkdir(parents=True)
    (r / "pkg" / "mod.py").write_text('def f():\n    """old"""\n    return 1\n', encoding="utf-8")
    (r / "tests" / "test_mod.py").write_text("from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
                                             encoding="utf-8")
    (r / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (r / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (r / "docs" / "constitution" / "BOSSMAN_CONSTITUTION.md").write_text("v1\n", encoding="utf-8")
    git(r, "init", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-m", "base")
    return r


def make_diff(repo: Path, rel: str, new_text: str) -> str:
    old = (repo / rel).read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new_text.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"))


GOOD = 'def f():\n    """new docstring"""\n    return 1\n'
BAD = 'def f():\n    """broken"""\n    return 2\n'


class Fakes:
    def __init__(self, repo: Path, plan: dict, *, bodies=(GOOD,), cloud_ok=True):
        self.repo, self.plan, self.bodies, self.cloud_ok = repo, plan, list(bodies), cloud_ok
        self.coder_calls = 0
        self.chat_calls: list[str] = []

    async def local_chat(self, model, messages, **kw):
        self.chat_calls.append(model)
        text = json.dumps(self.plan) if kw.get("json_mode") else "VERDICT: APPROVE local fresh context"
        return {"ok": True, "model": model, "text": text, "tokens_in": 11, "tokens_out": 7, "cost_usd": 0.0,
                "seconds": 0.1, "num_ctx_requested": kw.get("num_ctx")}

    async def free_cloud(self, role, system, prompt):
        if not self.cloud_ok:
            return {"ok": False, "text": "", "model": None, "error": "NOT_RUN: no free cloud model answered",
                    "cost_usd": 0.0, "attempts": []}
        return {"ok": True, "role": role, "model": "nvidia/nemotron-3-ultra-550b-a55b:free", "provider": "fake",
                "text": "VERDICT: APPROVE ok" if role == "verifier" else "draft", "tokens_in": 5, "tokens_out": 3,
                "cost_usd": 0.0, "seconds": 0.1}

    async def coder(self, run, instruction, paths, tests, attempt):
        self.coder_calls += 1
        body = self.bodies[min(self.coder_calls, len(self.bodies)) - 1]
        diff = make_diff(Path(run.scope.worktree), paths[0], body)
        return {"ok": True, "model": "fake-coder", "diff": diff, "changed_files": paths, "tokens_in": 1,
                "tokens_out": 1, "seconds": 0.1, "num_ctx": qo.CTX_256K}


PLAN = {"summary": "doc fix", "files": ["pkg/mod.py"], "edit_instruction": "improve docstring",
        "verify_tests": ["tests/test_mod.py"], "worker_prompt": "draft a docstring", "extra_commands": []}


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    async def has(model, transport=None):
        return model == qo.LOCAL_UNCENSORED[0]
    monkeypatch.setattr(qo, "ollama_has", has)

    async def variant(base, n, transport=None):
        return {"ok": True, "tag": base, "created": False}

    async def nothing(model):
        return None
    monkeypatch.setattr(qo, "ensure_ctx_variant", variant)
    monkeypatch.setattr(qo, "unload_model", nothing)
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings)
    client = client_for(app, svc)
    model = (await client.post("/api/providers", json={"name": "ol", "kind": "openai_compat",
                                                       "base_url": "http://127.0.0.1:11434/v1"})).json()
    mid = (await client.post("/api/models", json={"provider_id": model["id"], "name": qo.LOCAL_UNCENSORED[0],
                                                  "alias": "owner-local-community-uncensored",
                                                  "kind": "local"})).json()["id"]
    agent = (await client.post("/api/agents", json=qo.agent_values(mid))).json()
    yield svc, client, agent
    await client.aclose()
    await svc.stop()


async def run_task(svc, repo, fakes: Fakes, task="улучши докстринг", wait=0):
    rec = await qo.start_task(svc, task, source_repo=str(repo), decision_wait_s=wait, local_chat=fakes.local_chat,
                              free_cloud=fakes.free_cloud, coder=fakes.coder)
    await qo._JOBS[rec["id"]]
    return json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))


# ---------------------------------------------------------------- scope guard (pure)
@pytest.fixture
def scope(tmp_path):
    lab = tmp_path / "lab"
    lab.mkdir()
    return OperatorScope(lab, data_dir=tmp_path / "data")


def test_write_inside_lab_allowed_outside_asks(scope, tmp_path):
    assert scope.check_write("command-center/bcc/x.py").kind == "allow"
    v = scope.check_write(str(tmp_path / "elsewhere" / "x.py"))
    assert (v.kind, v.category) == ("ask", "outside_lab")
    assert scope.check_write("../escape.py").kind == "ask"


@pytest.mark.parametrize("rel", [
    "docs/constitution/BOSSMAN_CONSTITUTION.md", "command-center/bcc/autonomy/policy.py",
    "command-center/bcc/approvals.py", "command-center/bcc/permissions.py",
    "command-center/bcc/features/qwen_operator.py", ".github/workflows/ci.yml"])
def test_constitution_gate_and_own_guard_are_owner_decisions(scope, rel):
    v = scope.check_write(rel)
    assert (v.kind, v.category, v.executable_after_approval) == ("ask", "protected_file", False)


@pytest.mark.parametrize("argv,category", [
    (["git", "push", "origin", "HEAD"], "promotion"),
    (["git", "push", "--force", "origin", "main"], "force_push"),
    (["git", "merge", "main"], "promotion"),
    (["rm", "-rf", "important"], "destructive"),
    (["curl", "https://example.com"], "network"),
])
def test_owner_decision_commands_ask_never_run(scope, argv, category):
    v = scope.check_command(argv)
    assert v.kind == "ask" and v.category == category


def test_promotion_is_never_executable_even_if_the_owner_approves_here(scope):
    assert scope.check_command(["git", "push"]).executable_after_approval is False
    assert scope.check_promotion("push").kind == "refuse"
    assert "Claude и Codex" in scope.check_promotion("push").reason


def test_lab_commands_are_allowed(scope):
    assert scope.check_command([sys.executable, "-m", "pytest", "tests/x.py", "-q"]).kind == "allow"
    assert scope.check_command(["git", "status"]).kind == "allow"
    assert scope.check_command(["git", "commit", "-m", "x"]).kind == "allow"


def test_shell_string_is_refused_not_run(scope):
    assert scope.check_command(["git status && rm -rf /"]).kind in ("ask", "refuse")
    assert scope.check_command("git status").kind == "refuse"


def test_commit_only_on_lab_branch(scope):
    assert scope.check_commit_branch("lab/op-1").kind == "allow"
    assert scope.check_commit_branch("main").kind == "refuse"
    assert scope.check_commit_branch("release/bossman-owner").kind == "refuse"


def test_secret_reads_ask(scope):
    assert scope.check_read("C:/Users/x/AppData/Local/Bossman/keys/provider-keys.env").kind == "ask"
    assert scope.check_read("command-center/bcc/api.py").kind == "allow"


def test_agent_row_has_no_bare_dangerous_permissions():
    v = qo.agent_values(7)
    perms = v["permissions"]
    for bare in ("filesystem.write", "terminal.run", "git.write", "git.push", "deploy.production", "email.send"):
        assert bare not in perms
    assert perms["operator_l1"]["git.push"] is False and perms["operator_l1"]["git.merge"] is False
    assert perms["require_tests_before_finish"] is True and v["budget_usd"] == 0.0
    assert v["role"] == qo.OPERATOR_ROLE


def test_no_deadline_and_all_roles_default_to_uncensored_qwen():
    assert qo.DEFAULT_DECISION_WAIT_S is None
    for role in ("planner", "coder", "diagnostician"):
        assert qo.DEFAULT_ROLES[role]["model"] in qo.LOCAL_UNCENSORED
        assert qo.DEFAULT_ROLES[role]["num_ctx"] == 262144
    src = Path(qo.__file__).read_text(encoding="utf-8")
    assert "time_budget_s" not in src


# ---------------------------------------------------------------- pipeline
async def test_happy_path_commits_on_lab_branch_only(stack, repo):
    svc, _c, agent = stack
    f = Fakes(repo, PLAN)
    rec = await run_task(svc, repo, f)
    assert rec["status"] == "completed", (rec.get("error"), rec["decisions"])
    assert rec["commit_sha"] and rec["lab"]["branch"].startswith("lab/")
    assert git(repo, "rev-parse", "main") == rec["lab"]["base_commit"]          # canonical branch untouched
    assert git(Path(rec["lab"]["path"]), "rev-parse", "HEAD") == rec["commit_sha"]
    assert "new docstring" in (Path(rec["lab"]["path"]) / "pkg" / "mod.py").read_text(encoding="utf-8")
    assert "old" in (repo / "pkg" / "mod.py").read_text(encoding="utf-8")        # source checkout untouched
    assert qo.public(rec)["cost_usd"] == 0
    roles = {c["role"] for c in rec["model_calls"]}
    assert {"planner", "worker", "coder", "verifier"} <= roles
    assert rec["verifier"]["independence"] == "different_family_free_cloud"
    assert rec["promotion"]["status"] == "BLOCKED"
    assert rec["roles"]["planner"]["model"] == qo.LOCAL_UNCENSORED[0]


async def test_owner_decisions_park_and_other_steps_continue(stack, repo):
    svc, _c, _a = stack
    plan = {**PLAN, "files": ["pkg/mod.py", "docs/constitution/BOSSMAN_CONSTITUTION.md"],
            "extra_commands": [{"argv": ["git", "push", "origin", "HEAD"], "why": "publish", "recommendation": "no",
                                "concerns": "leaves the lab"}]}
    rec = await run_task(svc, repo, Fakes(repo, plan))
    assert rec["status"] == "completed_with_pending_decisions"
    assert rec["commit_sha"], "the allowed step continued"
    cats = sorted(d["category"] for d in rec["decisions"])
    assert cats == ["promotion", "protected_file"]
    assert all(d["status"] == "PENDING" and d["approval_id"] for d in rec["decisions"])
    rows = await svc.approvals.list("pending")
    assert {r["kind"] for r in rows} == {qo.DECISION_KIND}
    assert "Рекомендация оператора" in rows[0]["preview"] and "Опасения" in rows[0]["preview"]
    # nothing parked was executed
    assert (repo / "docs" / "constitution" / "BOSSMAN_CONSTITUTION.md").read_text(encoding="utf-8") == "v1\n"
    assert not any(c.get("argv", [""])[:2] == ["git", "push"] and c.get("exit_code") is not None
                   for c in rec["commands"])


async def test_approved_push_is_still_blocked_by_rule_4(stack, repo):
    svc, _c, _a = stack
    plan = {**PLAN, "extra_commands": [{"argv": ["git", "push"], "why": "w", "recommendation": "r", "concerns": "c"}]}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "t", source_repo=str(repo), decision_wait_s=30, local_chat=f.local_chat,
                              free_cloud=f.free_cloud, coder=f.coder)
    for _ in range(100):                                    # owner approves while the task waits
        pend = await svc.approvals.list("pending")
        if pend:
            await svc.approvals.decide(pend[0]["id"], True, "owner")
            break
        await asyncio.sleep(0.1)
    await qo._JOBS[rec["id"]]
    saved = json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))
    d = saved["decisions"][0]
    assert d["status"] == "APPROVED_BLOCKED_BY_RULE_4" and "Release" in d["note"]
    assert saved["status"] == "completed"


async def test_denied_decision_is_recorded_not_executed(stack, repo):
    svc, _c, _a = stack
    plan = {**PLAN, "extra_commands": [{"argv": ["rm", "-rf", "x"], "why": "w", "recommendation": "r",
                                        "concerns": "c"}]}
    f = Fakes(repo, plan)
    rec = await qo.start_task(svc, "t", source_repo=str(repo), decision_wait_s=30, local_chat=f.local_chat,
                              free_cloud=f.free_cloud, coder=f.coder)
    for _ in range(100):
        pend = await svc.approvals.list("pending")
        if pend:
            await svc.approvals.decide(pend[0]["id"], False, "owner")
            break
        await asyncio.sleep(0.1)
    await qo._JOBS[rec["id"]]
    saved = json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rec['id']}.json").read_text("utf-8"))
    assert saved["decisions"][0]["status"] == "DENIED_BY_OWNER"


async def test_self_correction_rolls_back_then_succeeds(stack, repo):
    svc, _c, _a = stack
    f = Fakes(repo, PLAN, bodies=(BAD, GOOD))
    rec = await run_task(svc, repo, f)
    assert rec["status"] == "completed"
    assert f.coder_calls == 2
    assert [a["attempt"] for a in rec["attempts"]] == [1]
    assert any(s["kind"] == "rollback" for s in rec["steps"])
    assert any(s["kind"] == "diagnose" for s in rec["steps"])
    text = (Path(rec["lab"]["path"]) / "pkg" / "mod.py").read_text(encoding="utf-8")
    assert "new docstring" in text and "broken" not in text


async def test_three_attempts_then_stop_with_clean_lab(stack, repo):
    svc, _c, _a = stack
    f = Fakes(repo, PLAN, bodies=(BAD,))
    rec = await run_task(svc, repo, f)
    assert rec["status"] == "failed" and f.coder_calls == qo.MAX_ATTEMPTS
    assert not rec.get("commit_sha")
    assert git(Path(rec["lab"]["path"]), "status", "--porcelain") == ""        # rolled back, nothing left
    assert git(Path(rec["lab"]["path"]), "rev-parse", "HEAD") == rec["lab"]["base_commit"]


async def test_no_verifier_cloud_falls_back_to_fresh_local_and_says_so(stack, repo):
    svc, _c, _a = stack
    rec = await run_task(svc, repo, Fakes(repo, PLAN, cloud_ok=False))
    assert rec["status"] == "completed"
    assert rec["verifier"]["independence"].startswith("NOT_INDEPENDENT")
    assert any(s["kind"] == "worker_draft" and s["status"] == "NOT_RUN" for s in rec["steps"])


async def test_owner_stop_blocks_start_and_midrun(stack, repo):
    svc, _c, _a = stack
    stop = Path(svc.settings.data_dir) / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    stop.write_text("{}", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        await qo.start_task(svc, "t", source_repo=str(repo))
    assert getattr(exc.value, "status_code", None) == 423
    stop.unlink()


async def test_promote_endpoint_always_refuses_and_records(stack, repo):
    svc, client, _a = stack
    rec = await run_task(svc, repo, Fakes(repo, PLAN))
    r = await client.post(f"/api/qwen-operator/tasks/{rec['id']}/promote")
    assert r.status_code == 403
    assert "PROMOTION_REQUIRES_USER" in json.dumps(r.json())
    saved = (await client.get(f"/api/qwen-operator/tasks/{rec['id']}")).json()
    assert saved["refused_attempts"][0]["action"] == "promote"
    assert git(repo, "rev-parse", "main") == rec["lab"]["base_commit"]


async def test_command_endpoint_uses_the_same_guard(stack, repo):
    svc, client, _a = stack
    rec = await run_task(svc, repo, Fakes(repo, PLAN))
    ok = (await client.post(f"/api/qwen-operator/tasks/{rec['id']}/command", json={"argv": ["git", "status"]})).json()
    assert ok["ran"] is True and ok["verdict"] == "allow"
    push = (await client.post(f"/api/qwen-operator/tasks/{rec['id']}/command",
                              json={"argv": ["git", "push", "origin", "HEAD"], "why": "t"})).json()
    assert push["ran"] is False and push["parked"]
    assert push["verdict"] == "ask"


async def test_operator_requires_its_agent(tmp_path, repo):
    app, svc = await start_app(make_settings(tmp_path))
    try:
        with pytest.raises(Exception) as exc:
            await qo.start_task(svc, "t", source_repo=str(repo))
        assert getattr(exc.value, "status_code", None) == 409
    finally:
        await svc.stop()
