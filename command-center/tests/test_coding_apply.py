"""Owner run 2026-09-23, P1: a VERIFIED coding candidate had no controlled way
into the canonical project. The coding path (IsolatedWorktree → OpenHandsClient
→ independent evidence → Bossman's own verify_tests) ended with a patch shown
as evidence; the owner then copied it by hand, and a diff with CRLF context
failed `git apply` on a repository with core.autocrlf=true.

`POST /api/coding-tasks/{id}/apply` (and `bossman code apply <id>`) closes that:
  * only completed + verified tasks with a recorded evidence digest qualify;
  * an explicit, single-use owner approval bound to task id + evidence digest
    (the existing approvals queue, F-015 consume) is required every time;
  * immediately before applying: HEAD == base commit, touched paths clean,
    protected paths untouched, `git apply --check` on the exact reviewed bytes;
  * the result is compared with the candidate's after-state; on any failure the
    canonical tree is restored; nothing is committed, pushed or merged.

The sidecar is the scripted one from test_coding_tasks (same contract as the
real sidecar); sandbox, evidence, verification and git are the real ones.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bcc.features import coding_tasks as ct

from .test_coding_tasks import _allow, _wait_terminal, sidecar

pytest.importorskip("bossman.apprentice.local_sidecar", reason="bossman-core runtime not installed")

TEST_CALC = ("import unittest\n"
             "from calc import add\n\n\n"
             "class T(unittest.TestCase):\n"
             "    def test_add(self):\n"
             "        self.assertEqual(add(2, 3), 5)\n")
FIX = ("pathlib.Path('calc.py').write_bytes("
       "pathlib.Path('calc.py').read_bytes().replace(b'a - b', b'a + b'))")


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], check=check,
                          capture_output=True, text=True).stdout


def _crlf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode()


@pytest.fixture(autouse=True)
def _fresh_handshake():
    ct._handshake_cache.clear()
    yield
    ct._handshake_cache.clear()


def make_repo(root: Path, eol: str = "lf") -> Path:
    """eol: lf — plain repo; crlf_autocrlf — core.autocrlf=true, LF in the index,
    CRLF in the working tree (Windows default); crlf_index — CRLF committed into
    the index, then core.autocrlf=true (an old Windows repo)."""
    src = root / f"owner-repo-{eol}"
    src.mkdir()
    git(src, "init", "-q")
    git(src, "config", "user.email", "o@o")
    git(src, "config", "user.name", "owner")
    git(src, "config", "core.autocrlf", "true" if eol == "crlf_autocrlf" else "false")
    calc = "def add(a, b):\n    return a - b\n"
    enc = (lambda t: t.encode()) if eol == "lf" else _crlf
    (src / "calc.py").write_bytes(enc(calc))
    (src / "test_calc.py").write_bytes(enc(TEST_CALC))
    (src / "SECRETS.md").write_bytes(enc("owner only\n"))
    git(src, "add", "-A")
    git(src, "commit", "-qm", "init")
    if eol == "crlf_index":
        git(src, "config", "core.autocrlf", "true")
    return src


async def complete_task(env, repo, monkeypatch, tmp_path, *, verify=True, body=FIX,
                        protected=("SECRETS.md",)):
    monkeypatch.setenv(ct.COMMAND_ENV, sidecar(body))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile; monkeypatch.setattr(tempfile, "tempdir", None)   # restored on teardown
    await _allow(env, repo.parent)
    res = await env.client.post("/api/coding-tasks", json={
        "instruction": "add() is wrong; fix it", "source_repo": str(repo),
        "allowed_paths": ["calc.py"], "protected_paths": list(protected),
        "verify_tests": ["test_calc.py"] if verify else [], "timeout_seconds": 120, "use_memory": False})
    assert res.status_code == 200, res.text
    rec = await _wait_terminal(env, res.json()["id"], timeout=120)
    return rec


async def request_apply(env, task_id, approval_id=None):
    body = {} if approval_id is None else {"approval_id": approval_id}
    return await env.client.post(f"/api/coding-tasks/{task_id}/apply", json=body)


async def approve(env, approval_id):
    res = await env.client.post(f"/api/approvals/{approval_id}", json={"approve": True, "by": "owner"})
    assert res.status_code == 200 and res.json()["status"] == "approved", res.text


async def approved_apply(env, task_id):
    wait = await request_apply(env, task_id)
    assert wait.status_code == 202, wait.text
    assert wait.json()["state"] == "WAIT_APPROVAL"
    aid = wait.json()["approval_id"]
    await approve(env, aid)
    return aid, await request_apply(env, task_id, aid)


def tree_state(repo: Path) -> dict:
    return {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} | {
        "HEAD": git(repo, "rev-parse", "HEAD"), "status": git(repo, "status", "--porcelain")}


def code(res) -> str:
    return res.json()["error"]["code"]


# ------------------------------------------------------------------ happy path


async def test_a_verified_candidate_is_applied_only_after_a_single_use_owner_approval(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    assert rec["status"] == "completed" and rec["verification"]["passed"] is True, rec.get("error")
    # the verified candidate carries what the apply is bound to
    assert rec["evidence_digest"] == "sha256:" + hashlib.sha256(rec["diff"].encode("utf-8")).hexdigest()
    assert rec["base_commit"] == git(repo, "rev-parse", "HEAD").strip()
    head = rec["base_commit"]

    wait = await request_apply(env, rec["id"])
    assert wait.status_code == 202 and wait.json()["state"] == "WAIT_APPROVAL", wait.text
    aid = wait.json()["approval_id"]
    preview = wait.json()["preview"]
    assert rec["id"] in preview and rec["evidence_digest"] in preview and head in preview
    # nothing happened yet; asking again reuses the pending approval (no spam)
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"
    again = await request_apply(env, rec["id"])
    assert again.status_code == 202 and again.json()["approval_id"] == aid
    # a pending (not yet approved) approval does not apply
    pending = await request_apply(env, rec["id"], aid)
    assert pending.status_code == 403 and code(pending) == "APPROVAL_INVALID"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"

    await approve(env, aid)
    done = await request_apply(env, rec["id"], aid)
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["state"] == "APPLIED" and out["committed"] is False
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    # a working-tree modification only: not committed, not staged, HEAD unchanged
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert git(repo, "status", "--porcelain").strip() == "M calc.py"
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
    stored = (await env.client.get(f"/api/coding-tasks/{rec['id']}")).json()
    assert stored["applied_at"] and stored["applied_digest"] == out["applied_digest"]
    assert stored["apply"]["approval_id"] == aid and stored["apply"]["files"]["calc.py"]["match"] == "exact"

    # no double apply: refused, even with a fresh approval; the approval is spent
    twice = await request_apply(env, rec["id"], aid)
    assert twice.status_code == 409 and code(twice) == "ALREADY_APPLIED"
    assert (await request_apply(env, rec["id"])).status_code == 409
    rows = (await env.client.get("/api/approvals", params={"status": "all"})).json()
    assert next(r for r in rows if r["id"] == aid)["status"] == "consumed"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


# ------------------------------------------------------------------ CRLF


@pytest.mark.parametrize("eol", ["crlf_autocrlf", "crlf_index", "lf"])
@pytest.mark.parametrize("global_autocrlf", ["true", "false"])
async def test_crlf_candidates_apply_on_an_autocrlf_repository(env, monkeypatch, tmp_path, eol, global_autocrlf):
    """REPRO: the reviewed diff has CRLF context. Carried as text it became
    CR CR LF on Windows and `git apply` refused it; and the sandbox clone took
    its EOL rules from the global config (Git for Windows: autocrlf=true), so an
    LF project got a CRLF-context diff that did not apply to it at all. The exact
    reviewed bytes must apply, keep the project's EOL, change only one line."""
    gcfg = tmp_path / "global.gitconfig"
    gcfg.write_text(f"[core]\n\tautocrlf = {global_autocrlf}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = make_repo(tmp_path, eol)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    assert rec["status"] == "completed", rec.get("error")
    # the sandbox follows the PROJECT's EOL rules, whatever the global config says
    assert ("\r\n" in rec["diff"]) == (eol != "lf")
    _aid, done = await approved_apply(env, rec["id"])
    assert done.status_code == 200, done.text
    fixed = "def add(a, b):\n    return a + b\n"
    assert (repo / "calc.py").read_bytes() == (fixed.encode() if eol == "lf" else _crlf(fixed))
    stat = git(repo, "diff", "--numstat").split()
    assert stat[:3] == ["1", "1", "calc.py"], stat       # one line, no whole-file EOL churn
    assert git(repo, "status", "--porcelain").strip() == "M calc.py"


# ------------------------------------------------------------------ refusals


async def test_an_unverified_task_is_not_eligible(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path, verify=False)
    assert rec["status"] == "completed"
    res = await request_apply(env, rec["id"])
    assert res.status_code == 409 and code(res) == "NOT_ELIGIBLE"
    assert res.json()["error"]["reason_code"] == "NOT_VERIFIED"
    assert (await env.client.get("/api/approvals")).json() == []     # no approval minted


async def test_failed_and_digestless_tasks_are_not_eligible(env):
    base = {"instruction": "x", "boot_id": ct.BOOT_ID, "created_at": 1.0, "diff": "d", "source_repo": "/x",
            "verify_tests": ["t.py"], "verification": {"ran": True, "passed": True}}
    ct._write(env.svc, {**base, "id": "aaaaaaaaaaa1", "status": "failed"})
    ct._write(env.svc, {**base, "id": "aaaaaaaaaaa2", "status": "completed"})            # no digest
    ct._write(env.svc, {**base, "id": "aaaaaaaaaaa3", "status": "completed", "base_commit": "b" * 40,
                        "evidence_digest": "sha256:" + "0" * 64, "candidate_files": {}})  # digest ≠ diff
    ct._write(env.svc, {**base, "id": "aaaaaaaaaaa4", "status": "running"})
    for tid, reason in (("aaaaaaaaaaa1", "NOT_COMPLETED"), ("aaaaaaaaaaa2", "NO_EVIDENCE_DIGEST"),
                        ("aaaaaaaaaaa3", "EVIDENCE_DIGEST_MISMATCH"), ("aaaaaaaaaaa4", "NOT_COMPLETED")):
        res = await request_apply(env, tid)
        assert res.status_code == 409 and code(res) == "NOT_ELIGIBLE", (tid, res.text)
        assert res.json()["error"]["reason_code"] == reason, (tid, res.text)
    assert (await env.client.get("/api/approvals")).json() == []


async def test_stale_base_and_dirty_target_are_refused_and_the_tree_is_untouched(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    assert rec["status"] == "completed"
    # dirty: the owner edited a touched file locally
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a  # mine\n")
    before = tree_state(repo)
    res = await request_apply(env, rec["id"])
    assert res.status_code == 409 and code(res) == "DIRTY_TARGET" and "calc.py" in res.json()["error"]["paths"]
    assert tree_state(repo) == before
    git(repo, "checkout", "--", "calc.py")
    # an untracked unrelated file is not "dirty target"
    (repo / "notes.txt").write_text("scratch\n")
    # stale: the canonical project moved on after the task started
    (repo / "README.md").write_text("new\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "moved on")
    before = tree_state(repo)
    res = await request_apply(env, rec["id"])
    assert res.status_code == 409 and code(res) == "STALE_BASE"
    assert tree_state(repo) == before
    assert (await env.client.get("/api/approvals")).json() == []


async def test_a_base_change_after_approval_is_still_caught_at_apply_time(env, monkeypatch, tmp_path):
    """Preconditions are re-checked immediately before applying, not only when
    the approval is requested: the tree changes AFTER the request's first check
    and the approval's consumption (the narrowest window there is)."""
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    wait = await request_apply(env, rec["id"])
    aid = wait.json()["approval_id"]
    await approve(env, aid)
    real_consume = env.svc.approvals.consume

    async def consume_then_owner_edits(*a, **kw):
        ok = await real_consume(*a, **kw)
        (repo / "calc.py").write_text("changed meanwhile\n")
        return ok
    monkeypatch.setattr(env.svc.approvals, "consume", consume_then_owner_edits)
    res = await request_apply(env, rec["id"], aid)
    assert res.status_code == 409 and code(res) == "DIRTY_TARGET", res.text
    assert (repo / "calc.py").read_text() == "changed meanwhile\n"
    stored = (await env.client.get(f"/api/coding-tasks/{rec['id']}")).json()
    assert not stored.get("applied_at")


async def test_protected_paths_and_tampered_evidence_are_refused(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    path = ct._path(env.svc, rec["id"])
    original = json.loads(path.read_text(encoding="utf-8"))
    # the record now names calc.py protected (e.g. policy tightened after the run)
    ct._write(env.svc, {**original, "protected_paths": ["SECRETS.md", "calc.py"]})
    res = await request_apply(env, rec["id"])
    assert res.status_code == 409 and code(res) == "PROTECTED_PATH"
    # the diff was edited after review: digest no longer matches → not eligible
    ct._write(env.svc, {**original, "diff": original["diff"].replace("a + b", "a * b")})
    res = await request_apply(env, rec["id"])
    assert res.status_code == 409 and res.json()["error"]["reason_code"] == "EVIDENCE_DIGEST_MISMATCH"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"


async def test_an_approval_for_another_task_or_a_spent_one_is_refused(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    # an approved row of another kind/preview (e.g. a terminal command) is not authority here
    other = await env.svc.approvals.create("terminal", "[sandbox] ls\ncwd: /")
    await approve(env, other["id"])
    res = await request_apply(env, rec["id"], other["id"])
    assert res.status_code == 403 and code(res) == "APPROVAL_INVALID"
    # an approval for the same task but a different evidence digest is not authority either
    forged = await env.svc.approvals.create(
        "coding_apply", ct.apply_preview({**rec, "evidence_digest": "sha256:" + "f" * 64}))
    await approve(env, forged["id"])
    res = await request_apply(env, rec["id"], forged["id"])
    assert res.status_code == 403 and code(res) == "APPROVAL_INVALID"
    # a rejected approval stays rejected
    wait = await request_apply(env, rec["id"])
    aid = wait.json()["approval_id"]
    await env.client.post(f"/api/approvals/{aid}", json={"approve": False})
    res = await request_apply(env, rec["id"], aid)
    assert res.status_code == 403 and code(res) == "APPROVAL_INVALID"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"
    # the approval flag in the body is not an approval
    res = await env.client.post(f"/api/coding-tasks/{rec['id']}/apply", json={"approved": True})
    assert res.status_code == 202 and (repo / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"


async def test_an_after_state_mismatch_rolls_the_canonical_tree_back(env, monkeypatch, tmp_path):
    """Atomic from the owner's view: the applied bytes must equal the verified
    candidate's after-state; otherwise the canonical tree is restored."""
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    path = ct._path(env.svc, rec["id"])
    original = json.loads(path.read_text(encoding="utf-8"))
    files = {k: {**v, "sha256": "0" * 64, "sha256_eol": "0" * 64} for k, v in original["candidate_files"].items()}
    ct._write(env.svc, {**original, "candidate_files": files})
    before = tree_state(repo)
    aid, res = await approved_apply(env, rec["id"])
    assert res.status_code == 409 and code(res) == "AFTER_STATE_MISMATCH", res.text
    assert tree_state(repo) == before
    stored = (await env.client.get(f"/api/coding-tasks/{rec['id']}")).json()
    assert not stored.get("applied_at")


async def test_concurrent_applies_apply_once(env, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    rec = await complete_task(env, repo, monkeypatch, tmp_path)
    wait = await request_apply(env, rec["id"])
    aid = wait.json()["approval_id"]
    await approve(env, aid)
    results = await asyncio.gather(*(request_apply(env, rec["id"], aid) for _ in range(4)))
    assert sorted(r.status_code for r in results).count(200) == 1, [r.text for r in results]
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"


# ------------------------------------------------------------------ CLI


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, body=None, **_kw):
        self.calls.append((path, body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_cli_code_apply_exit_codes():
    pytest.importorskip("rich")
    from bcc.terminal_cli import cli, screens
    from bcc.terminal_cli.api_client import BossmanError
    args = cli.build_parser().parse_args(["code", "apply", "abcdef123456", "--approval-id", "7", "--json"])
    assert args.instruction == "apply" and args.task_id == "abcdef123456" and args.approval_id == 7
    out = cli.Out("json")
    fake = _FakeClient([{"state": "WAIT_APPROVAL", "approval_id": 7, "preview": "p"}])
    assert screens.apply_coding_task(fake, out, "abcdef123456", None) == 4
    assert fake.calls == [("/api/coding-tasks/abcdef123456/apply", {})]
    fake = _FakeClient([{"state": "APPLIED", "applied_digest": "sha256:x", "files": {}}])
    assert screens.apply_coding_task(fake, out, "abcdef123456", 7) == 0
    assert fake.calls == [("/api/coding-tasks/abcdef123456/apply", {"approval_id": 7})]
    fake = _FakeClient([BossmanError("stale", status=409, code="STALE_BASE", kind="conflict")])
    assert screens.apply_coding_task(fake, out, "abcdef123456", 7) == 5
    fake = _FakeClient([BossmanError("twice", status=409, code="ALREADY_APPLIED", kind="conflict")])
    assert screens.apply_coding_task(fake, out, "abcdef123456", 7) == 11
    # the CLI has no approve switch on this path: only --approval-id of an owner-approved row
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["code", "apply", "abcdef123456", "--yes"])


def test_no_approve_window_cannot_approve_through_any_terminal_path(tmp_path, monkeypatch):
    """BOSSMAN_TERMINAL_NO_APPROVE covered only `bossman approve`; chat /approve
    and the follower's ask-mode posted the decision directly. The guard now sits
    in the one client all of them use — a coding apply approval included."""
    from bcc.terminal_cli.api_client import BossmanError, Client, Target
    (tmp_path / "token").write_text("t", encoding="utf-8")
    client = Client(Target(url="http://127.0.0.1:9", data_dir=tmp_path))
    monkeypatch.setenv("BOSSMAN_TERMINAL_NO_APPROVE", "1")
    with pytest.raises(BossmanError) as exc:
        client.post("/api/approvals/5", {"approve": True, "by": "owner:terminal"})
    assert exc.value.kind == "blocked"
    client.close()
