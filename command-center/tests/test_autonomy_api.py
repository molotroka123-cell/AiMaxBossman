"""/api/autonomy/*: goals with evidence, status, release panel (Apply / Reject / Revise / Confirm)."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from bcc.autonomy.service import AutonomyService, ReleaseRefused, release_commands
from bcc.autonomy.types import Budget, Goal, Review

SHA, BASE = "1" * 40, "2" * 40
DIFF = "a" * 64
GID = "JEFF-0042"


def pinned(ok=True):
    return lambda: SimpleNamespace(ok=ok, reason="pinned" if ok else "constitution is not pinned",
                                   as_dict=lambda: {"status": "OK" if ok else "BLOCKED", "ok": ok})


def goal():
    return Goal(goal_id=GID, problem="Jeff leaks the model name", desired_result="Jeff answers as Bossman",
                constraints=(), acceptance_tests=("identity_redteam.leaks == 0",),
                budget=Budget(max_minutes=600, max_agent_turns=10), risk_tier="prompts_models",
                target_metric="identity_redteam.leaks", protected_metrics=("task_success",))


def to_user_approval(auto: AutonomyService):
    s = auto.goals
    s.create(goal())
    s.transition(GID, "PLANNED", {})
    s.transition(GID, "BUILDING", {})
    s.set_candidate(GID, SHA, DIFF, author="claude", branch="auto/jeff-0042", base_sha=BASE,
                    target_branch="release/bossman-owner")
    s.transition(GID, "TESTING", {})
    s.record_tests(GID, SHA, DIFF, True)
    s.transition(GID, "CLAUDE_REVIEW", {})
    s.record_review(Review(GID, "codex", SHA, DIFF, "APPROVE", "ok"))
    s.record_review(Review(GID, "claude", SHA, DIFF, "APPROVE", "ok"))
    s.transition(GID, "CODEX_REVIEW", {})
    s.transition(GID, "STAGING", {})
    s.record_staging(GID, {"sha": SHA, "passed": True, "checks": {}})
    s.transition(GID, "USER_APPROVAL", {})


@pytest.fixture
async def api(env):
    env.svc.autonomy = AutonomyService(env.settings.data_dir, constitution_status=pinned())
    return env


async def test_requires_the_owner_token(api):
    anon = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test")
    async with anon:
        assert (await anon.get("/api/autonomy/goals")).status_code == 401


async def test_empty_state(api):
    c = api.client
    assert (await c.get("/api/autonomy/goals")).json() == {"items": []}
    st = (await c.get("/api/autonomy/status")).json()
    assert st["loop"] == "READY" and st["goals_total"] == 0 and st["journal"]["ok"] and st["lease"] is None
    assert st["level"] == "L2"
    assert (await c.get("/api/autonomy/goals/JEFF-9999")).status_code == 404
    assert (await c.get("/api/autonomy/goals/..%2Fetc")).status_code == 404
    assert (await c.get("/api/autonomy/journal/verify")).json()["ok"] is True


async def test_blocked_constitution_is_visible_and_disables_apply(env):
    env.svc.autonomy = auto = AutonomyService(env.settings.data_dir, constitution_status=pinned(False))
    to_user_approval(auto)
    st = (await env.client.get("/api/autonomy/status")).json()
    assert st["loop"] == "BLOCKED" and "not pinned" in st["reason"]
    view = (await env.client.get(f"/api/autonomy/goals/{GID}")).json()
    assert view["actions"]["apply"] == {"allowed": False, "reason": "constitution is not pinned / changed"}
    r = await env.client.post(f"/api/autonomy/goals/{GID}/apply", json={"sha": SHA, "diff_sha256": DIFF})
    assert r.status_code == 409


async def test_goal_detail_has_evidence_and_disabled_actions_before_the_gate(api):
    auto = api.svc.autonomy
    auto.goals.create(goal())
    view = (await api.client.get(f"/api/autonomy/goals/{GID}")).json()
    assert view["state"] == "PROPOSED" and view["evidence"][0]["kind"] == "goal.created"
    assert all(not a["allowed"] and a["reason"] for a in view["actions"].values())
    r = await api.client.post(f"/api/autonomy/goals/{GID}/apply", json={"sha": SHA, "diff_sha256": DIFF})
    assert r.status_code == 409 and "USER_APPROVAL" in r.json()["error"]["message"]


async def test_apply_records_decision_returns_commands_and_does_not_release(api):
    to_user_approval(api.svc.autonomy)
    c = api.client
    view = (await c.get(f"/api/autonomy/goals/{GID}")).json()
    assert view["actions"]["apply"]["allowed"] and not view["actions"]["confirm"]["allowed"]
    assert (await c.post(f"/api/autonomy/goals/{GID}/apply",
                         json={"sha": "3" * 40, "diff_sha256": DIFF})).status_code == 409
    assert (await c.post(f"/api/autonomy/goals/{GID}/apply", json={"sha": "short", "diff_sha256": DIFF})
            ).status_code == 422
    r = (await c.post(f"/api/autonomy/goals/{GID}/apply", json={"sha": SHA, "diff_sha256": DIFF})).json()
    assert r["goal"]["state"] == "USER_APPROVAL"                           # Apply is not a release
    assert f"git merge --ff-only {SHA}" in r["commands"]["release"]
    assert f"git revert --no-edit {BASE}..{SHA}" in r["commands"]["rollback"]
    view = (await c.get(f"/api/autonomy/goals/{GID}")).json()
    assert view["commands"]["expect_head"] == SHA
    assert view["actions"]["confirm"]["allowed"] and not view["actions"]["apply"]["allowed"]
    kinds = [e["kind"] for e in view["evidence"]]
    assert "goal.user_decision" in kinds and "release.commands" in kinds
    bad = await c.post(f"/api/autonomy/goals/{GID}/confirm", json={"sha": SHA, "diff_sha256": "b" * 64})
    assert bad.status_code == 409
    r = (await c.post(f"/api/autonomy/goals/{GID}/confirm", json={"sha": SHA, "diff_sha256": DIFF})).json()
    assert r["goal"]["state"] == "DEPLOYED"


async def test_reject_and_revise(api):
    to_user_approval(api.svc.autonomy)
    r = (await api.client.post(f"/api/autonomy/goals/{GID}/revise", json={"note": "shorter prompt"})).json()
    assert r["goal"]["state"] == "BUILDING" and r["goal"]["approvals"] == []
    assert (await api.client.post(f"/api/autonomy/goals/{GID}/reject", json={})).status_code == 409


async def test_reject_blocks(api):
    to_user_approval(api.svc.autonomy)
    r = (await api.client.post(f"/api/autonomy/goals/{GID}/reject", json={"note": "no"})).json()
    assert r["goal"]["state"] == "BLOCKED" and r["goal"]["blocked_reason"] == "rejected by the user"


async def test_journal_endpoint_filters(api):
    api.svc.autonomy.goals.create(goal())
    items = (await api.client.get(f"/api/autonomy/journal?goal_id={GID}&limit=5")).json()["items"]
    assert items and all(e["payload"]["goal_id"] == GID for e in items)


def test_release_commands_refuse_unsafe_refs():
    with pytest.raises(ReleaseRefused):
        release_commands({"sha": SHA, "target_branch": "main; rm -rf /"})
    with pytest.raises(ReleaseRefused):
        release_commands({"sha": SHA, "branch": "--upload-pack=x"})
    cmds = release_commands({"sha": SHA})
    assert cmds["target_branch"] == "release/bossman-owner" and f"git revert --no-edit {SHA}" in cmds["rollback"]


def test_unknown_level_file_falls_back_to_l0(tmp_path):
    auto = AutonomyService(tmp_path, constitution_status=pinned())
    (auto.root / "level.json").write_text('{"level": "L9"}')
    assert auto.level() == "L0"


def test_ui_page_is_registered_lazily_and_matches_its_manifest():
    import re
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "ui" / "pages"
    index = (ui / "index.js").read_text("utf-8")
    page = (ui / "autonomy.js").read_text("utf-8")
    entry = re.search(r"lazyPage\(\{ id: 'autonomy'[^}]*\},\s*\(\) => import\('\./autonomy\.js'\)", index)
    assert entry, "autonomy must be registered in ui/pages/index.js"
    for key, value in re.findall(r"(\w+): '([^']+)'", entry.group(0).split("},")[0]):
        assert f"{key}: '{value}'" in page, (key, value)
    assert "console.log" not in page and "innerHTML" not in page
    assert "disabled: !allowed" in page and "title:" in page
    assert page.endswith("export default AutonomyPage;\n")
