"""Agentic Rave account pool (lane rave-apps): the owner's OWN accounts, official CLIs only, each in its own
profile directory, OFF by default, opt-in through an approval, switched only between agent runs, every switch
visible, no secret stored. The CLIs are the stub in tests/rave_stub_cli.py: its login state and "usage limit"
live in the account's profile directory (`stub-login`, `stub-limit`), like a real CLI's login does."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from bcc.rave import connectors
from bcc.rave.pool import POOL_OPTIN_KIND, TERMS_NOTE, opt_in_preview

from .conftest import client_for, make_settings, start_app

pytest.importorskip("bossman.apprentice.proc_tree", reason="bossman-core not importable")

STUB = Path(__file__).with_name("rave_stub_cli.py")


async def wait_for(c, rid, pred, timeout=40.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        v = (await c.get(f"/api/rave/{rid}")).json()
        if pred(v):
            return v
        if loop.time() > end:
            raise AssertionError(f"timeout; last state: {json.dumps(v, ensure_ascii=False)[:1800]}")
        await asyncio.sleep(0.05)


def agent(v, name):
    return next(a for a in v["agents"] if a["name"] == name)


def settled(v):
    return v["status"] != "running" and not any(a.get("finalizing") for a in v["agents"])


def code_of(resp):
    body = resp.json()
    return (body.get("detail") or body.get("error"))["code"]


@pytest.fixture(autouse=True)
def _fresh_version_cache():
    connectors.reset_version_cache()
    yield
    connectors.reset_version_cache()


@pytest.fixture
async def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_RAVE_CLAUDE_CMD", json.dumps([sys.executable, str(STUB), "claude"]))
    monkeypatch.setenv("BOSSMAN_RAVE_CODEX_CMD", json.dumps([sys.executable, str(STUB), "codex"]))
    monkeypatch.setenv("BOSSMAN_RAVE_PROFILES_DIR", str(tmp_path / "profiles"))
    for var in ("STUB_LOGIN", "STUB_VERSION", "STUB_LIMIT", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    app, svc = await start_app(make_settings(tmp_path))
    (svc.rave.root / "optin.json").write_text(json.dumps({"claude": {"approved": True}, "codex": {"approved": True}}))
    async with client_for(app, svc) as c:
        yield c, svc
    await svc.stop()


async def add(c, tool, label, **extra):
    r = await c.post("/api/rave/pool/accounts", json={"tool": tool, "label": label, **extra})
    assert r.status_code == 200, r.text
    account = r.json()["account"]
    if account["profile_dir"]:
        (Path(account["profile_dir"]) / "stub-login").write_text("subscription")
    return account


async def enable(c, svc):
    r = await c.post("/api/rave/pool/enable", json={})
    assert r.status_code == 202 and r.json()["state"] == "WAIT_APPROVAL", r.text
    aid = r.json()["approval_id"]
    await c.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner:test"})
    ok = await c.post("/api/rave/pool/enable", json={"approval_id": aid})
    assert ok.status_code == 200 and ok.json()["pool"]["active"] is True, ok.text
    return aid


async def start(c, agents, prompt="build the thing"):
    r = await c.post("/api/rave", json={"prompt": prompt, "agents": agents})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def runs(account):
    log = Path(account["profile_dir"]) / "stub-runs.log"
    return len(log.read_text().splitlines()) if log.is_file() else 0


def journal(pool_view, event):
    return [j for j in pool_view["journal"] if j["event"] == event]


# ------------------------------------------------------------------ off by default


async def test_pool_is_off_by_default_and_changes_nothing(app):
    c, svc = app
    view = (await c.get("/api/rave/pool")).json()
    assert view["enabled"] is False and view["active"] is False and view["accounts"] == []
    assert "условиям использования" in view["terms_note"] and view["terms_note"] == TERMS_NOTE
    assert not (svc.rave.root / "pool.json").exists()               # nothing written just by looking
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and "account" not in cc           # the CLI's usual login, no pool involved
    assert cc["auth"] == "subscription (claude login)"
    assert (await c.get("/api/rave/connectors")).json()["pool"] == {"enabled": False, "active": False, "accounts": 0}


async def test_accounts_added_but_pool_not_enabled_are_never_used(app):
    c, _ = app
    a = await add(c, "claude", "Запасной")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and "account" not in cc
    assert runs(a) == 0                                              # its profile was not touched by the run


# ------------------------------------------------------------------ opt-in through approvals


async def test_enable_needs_an_owner_approval_bound_to_the_exact_accounts(app):
    c, svc = app
    assert code_of(await c.post("/api/rave/pool/enable", json={})) == "NO_ACCOUNTS"
    a1 = await add(c, "claude", "Основной")
    r = await c.post("/api/rave/pool/enable", json={})
    assert r.status_code == 202
    aid, preview = r.json()["approval_id"], r.json()["preview"]
    assert a1["id"] in preview and "Основной" in preview and TERMS_NOTE in preview
    row = next(x for x in (await c.get("/api/approvals", params={"status": "pending"})).json() if x["id"] == aid)
    assert row["kind"] == POOL_OPTIN_KIND == "rave_pool_optin"
    again = await c.post("/api/rave/pool/enable", json={})
    assert again.json()["approval_id"] == aid                        # the same pending request, not a duplicate
    assert code_of(await c.post("/api/rave/pool/enable", json={"approval_id": aid})) == "APPROVAL_INVALID"
    assert (await c.get("/api/rave/pool")).json()["enabled"] is False    # pending is not approved
    await c.post(f"/api/approvals/{aid}", json={"approve": True, "by": "owner:test"})
    ok = await c.post("/api/rave/pool/enable", json={"approval_id": aid})
    assert ok.status_code == 200 and ok.json()["state"] == "ENABLED"
    assert (await c.get("/api/rave/pool")).json()["active"] is True
    used = await c.post("/api/rave/pool/enable", json={"approval_id": aid})     # already covered: no second approval
    assert used.status_code == 200
    # an approval is for the list it showed: a new account needs a new one
    a2 = await add(c, "claude", "Запасной")
    view = (await c.get("/api/rave/pool")).json()
    assert view["waiting_approval"] == [a2["id"]] and view["enabled"] is True
    await c.post("/api/rave/pool/disable")
    new = await c.post("/api/rave/pool/enable", json={})
    assert new.status_code == 202 and new.json()["approval_id"] != aid and a2["id"] in new.json()["preview"]
    stale = await c.post("/api/rave/pool/enable", json={"approval_id": aid})   # the old approval was consumed
    assert code_of(stale) == "APPROVAL_INVALID"


async def test_a_rejected_approval_does_not_enable_the_pool(app):
    c, _ = app
    await add(c, "claude", "Основной")
    aid = (await c.post("/api/rave/pool/enable", json={})).json()["approval_id"]
    await c.post(f"/api/approvals/{aid}", json={"approve": False, "by": "owner:test"})
    assert code_of(await c.post("/api/rave/pool/enable", json={"approval_id": aid})) == "APPROVAL_INVALID"
    assert (await c.get("/api/rave/pool")).json()["enabled"] is False


def test_the_approval_text_is_deterministic_and_carries_the_terms_note():
    accounts = [{"id": "claude-2", "tool": "claude", "label": "B", "profile_dir": None},
                {"id": "claude-1", "tool": "claude", "label": "A", "profile_dir": "C:/p"}]
    assert opt_in_preview(accounts) == opt_in_preview(list(reversed(accounts)))
    assert TERMS_NOTE in opt_in_preview(accounts) and "обычный вход CLI" in opt_in_preview(accounts)


# ------------------------------------------------------------------ accounts


async def test_account_validation_and_no_secret_in_the_state_file(app):
    c, svc = app
    a = await add(c, "claude", "Основной")
    assert a["profile_dir"].endswith("claude-1") and Path(a["profile_dir"]).is_dir()
    assert (await c.post("/api/rave/pool/accounts", json={"tool": "gpt", "label": "x"})).status_code == 422
    assert (await c.post("/api/rave/pool/accounts", json={"tool": "claude", "label": " "})).status_code in (422,)
    rel = await c.post("/api/rave/pool/accounts", json={"tool": "claude", "label": "x", "profile_dir": "relative/dir"})
    assert rel.status_code == 422
    dup = await c.post("/api/rave/pool/accounts", json={"tool": "claude", "label": "x", "profile_dir": a["profile_dir"]})
    assert dup.status_code == 409 and code_of(dup) == "DUPLICATE"
    d1 = await c.post("/api/rave/pool/accounts", json={"tool": "codex", "label": "обычный", "use_default": True})
    assert d1.status_code == 200 and d1.json()["account"]["profile_dir"] is None
    d2 = await c.post("/api/rave/pool/accounts", json={"tool": "codex", "label": "ещё", "use_default": True})
    assert d2.status_code == 409
    state = (svc.rave.root / "pool.json").read_text(encoding="utf-8")
    low = state.lower()
    for forbidden in ("token", "secret", "password", "api_key", "owner@example.invalid", "email", "@"):
        assert forbidden not in low, forbidden
    (await c.post(f"/api/rave/pool/accounts/{a['id']}/check")).json()
    state = (svc.rave.root / "pool.json").read_text(encoding="utf-8").lower()
    assert "owner@example.invalid" not in state and "email" not in state and '"plan": "max"' in state
    removed = (await c.delete(f"/api/rave/pool/accounts/{a['id']}")).json()
    assert [x["id"] for x in removed["accounts"]] == ["codex-1"]
    assert Path(a["profile_dir"]).is_dir()                           # the profile (its login) is left alone
    assert (await c.delete("/api/rave/pool/accounts/claude-9")).status_code == 404


async def test_check_reads_each_accounts_own_login_state(app):
    c, _ = app
    ok = await add(c, "claude", "Вошёл")
    out = await add(c, "claude", "Не вошёл")
    (Path(out["profile_dir"]) / "stub-login").write_text("none")
    key = await add(c, "codex", "По ключу")
    (Path(key["profile_dir"]) / "stub-login").write_text("apikey")
    states = {}
    for a in (ok, out, key):
        states[a["id"]] = (await c.post(f"/api/rave/pool/accounts/{a['id']}/check")).json()["account"]
    assert states[ok["id"]]["login_state"] == "ready" and states[ok["id"]]["plan"] == "max"
    assert states[out["id"]]["login_state"] == "needs_login"
    assert states[key["id"]]["login_state"] == "not_subscription"
    view = (await c.get("/api/rave/pool")).json()
    by_id = {a["id"]: a for a in view["accounts"]}
    assert "CLAUDE_CONFIG_DIR" in by_id[out["id"]]["login_step"] and out["profile_dir"] in by_id[out["id"]]["login_step"]
    assert "CODEX_HOME" in by_id[key["id"]]["login_step"]


def test_profile_env_isolates_the_account_login(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "from-the-server-environment")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-in-env")            # ci-secret-scan: allow
    monkeypatch.setenv("CODEX_API_KEY", "key-in-env")                # ci-secret-scan: allow
    monkeypatch.setenv("BOSSMAN_SECRET_THING", "bossman-only")
    plain = connectors.profile_env("claude", None)
    assert plain.get("CLAUDE_CODE_OAUTH_TOKEN") == "from-the-server-environment"   # the default profile: as before
    assert "ANTHROPIC_API_KEY" not in plain and "BOSSMAN_SECRET_THING" not in plain
    prof = connectors.profile_env("claude", "C:/profiles/claude-2")
    assert prof["CLAUDE_CONFIG_DIR"] == "C:/profiles/claude-2"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in prof and "ANTHROPIC_API_KEY" not in prof   # the profile's login decides
    cx = connectors.profile_env("codex", "C:/profiles/codex-2")
    assert cx["CODEX_HOME"] == "C:/profiles/codex-2" and "CODEX_API_KEY" not in cx


# ------------------------------------------------------------------ switching between runs


async def test_agent_runs_on_the_first_ready_account_and_says_which(app):
    c, svc = app
    a1, a2 = await add(c, "claude", "Основной"), await add(c, "claude", "Запасной")
    await enable(c, svc)
    rid = await start(c, ["claude:cc"])
    v = await wait_for(c, rid, settled)
    cc = agent(v, "cc")
    assert cc["status"] == "done" and cc["account"] == a1["id"] and cc["account_label"] == "Основной"
    assert cc["auth"] == "subscription (claude login) · аккаунт Основной"
    assert runs(a1) == 1 and runs(a2) == 0
    events = (await c.get(f"/api/rave/{rid}/events")).json()["events"]
    assert any(e["kind"] == "pool_account" and e["account"] == a1["id"] for e in events)


async def test_limit_switches_to_the_next_account_between_runs_and_is_journaled(app):
    c, svc = app
    a1, a2 = await add(c, "claude", "Основной"), await add(c, "claude", "Запасной")
    await enable(c, svc)
    (Path(a1["profile_dir"]) / "stub-limit").write_text("1")          # A is out of usage
    rid = await start(c, ["claude:cc"])
    v = await wait_for(c, rid, settled)
    cc = agent(v, "cc")
    assert cc["status"] == "done", cc
    assert cc["account"] == a2["id"] and cc["answer"].startswith("STUB-CLAUDE-OK")
    assert runs(a1) == 1 and runs(a2) == 1                            # one whole run on A (refused), one on B
    view = (await c.get("/api/rave/pool")).json()
    (limited,) = journal(view, "limited")
    (switch,) = journal(view, "switch")
    assert limited["account"] == a1["id"] and limited["until"] > limited["at"]
    assert (switch["account"], switch["to"], switch["tool"]) == (a1["id"], a2["id"], "claude")
    assert switch["rave"] == rid and switch["agent"] == "cc" and "лимит" in switch["reason"]
    by_id = {a["id"]: a for a in view["accounts"]}
    assert by_id[a1["id"]]["state"] == "limited" and by_id[a2["id"]]["state"] == "ready"
    kinds = [e["kind"] for e in (await c.get(f"/api/rave/{rid}/events")).json()["events"]]
    assert "pool_limited" in kinds and "pool_switch" in kinds          # visible in the rave's own timeline
    # the next agent goes straight to B: A is marked limited, it is not tried (and not spent) again
    rid2 = await start(c, ["claude:c2"])
    assert agent(await wait_for(c, rid2, settled), "c2")["account"] == a2["id"]
    assert runs(a1) == 1 and runs(a2) == 2


async def test_all_accounts_limited_blocks_the_agent_and_resume_continues_later(app):
    c, svc = app
    a1, a2 = await add(c, "claude", "Основной"), await add(c, "claude", "Запасной")
    await enable(c, svc)
    for a in (a1, a2):
        (Path(a["profile_dir"]) / "stub-limit").write_text("1")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "blocked" and "нет готового аккаунта" in cc["error"]
    assert a1["id"] in cc["error"] and a2["id"] in cc["error"] and "bossman rave resume" in cc["error"]
    view = (await c.get("/api/rave/pool")).json()
    assert len(journal(view, "limited")) == 2 and len(journal(view, "switch")) == 1
    # the owner's limit is back (and he cleared the marks): resume runs the agent on A
    for a in (a1, a2):
        (Path(a["profile_dir"]) / "stub-limit").unlink()
        await c.post(f"/api/rave/pool/accounts/{a['id']}/clear-limit")
    await c.post(f"/api/rave/{rid}/resume", json={"agent": "cc"})
    v = await wait_for(c, rid, lambda x: agent(x, "cc")["status"] == "done", timeout=40)
    assert agent(v, "cc")["account"] == a1["id"]


async def test_an_account_that_needs_login_is_skipped_and_shown(app):
    c, svc = app
    a1, a2 = await add(c, "claude", "Основной"), await add(c, "claude", "Запасной")
    await enable(c, svc)
    (Path(a1["profile_dir"]) / "stub-login").write_text("none")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and cc["account"] == a2["id"] and runs(a1) == 0
    by_id = {a["id"]: a for a in (await c.get("/api/rave/pool")).json()["accounts"]}
    assert by_id[a1["id"]]["state"] == "needs_login"


async def test_unapproved_and_disabled_accounts_are_not_used(app):
    c, svc = app
    a1 = await add(c, "claude", "Основной")
    await enable(c, svc)
    a2 = await add(c, "claude", "Новый, без разрешения")
    (Path(a1["profile_dir"]) / "stub-limit").write_text("1")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "blocked" and runs(a2) == 0                # B exists but was never approved
    assert a2["id"] in cc["error"] and "ждёт разрешения владельца" in cc["error"]   # named, with the reason
    (Path(a1["profile_dir"]) / "stub-limit").unlink()
    await c.post(f"/api/rave/pool/accounts/{a1['id']}/clear-limit")
    off = (await c.post(f"/api/rave/pool/accounts/{a1['id']}/enabled", json={"enabled": False})).json()
    assert [a["enabled"] for a in off["accounts"]] == [False, True]
    rid2 = await start(c, ["claude:c2"])
    c2 = agent(await wait_for(c, rid2, settled), "c2")
    assert c2["status"] == "blocked" and "отключён владельцем" in c2["error"]    # never the default login behind his back
    assert "ждёт разрешения владельца" in c2["error"]
    await c.post(f"/api/rave/pool/accounts/{a1['id']}/enabled", json={"enabled": True})
    rid3 = await start(c, ["claude:c3"])
    assert agent(await wait_for(c, rid3, settled), "c3")["account"] == a1["id"]


async def test_switches_inside_one_agent_run_are_bounded(app):
    c, svc = app
    accounts = [await add(c, "claude", f"A{i}") for i in range(1, 6)]
    await enable(c, svc)
    for a in accounts:
        (Path(a["profile_dir"]) / "stub-limit").write_text("1")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled, timeout=60), "cc")
    assert cc["status"] == "blocked" and "предел переключений" in cc["error"]
    assert sum(runs(a) for a in accounts) == 4                        # MAX_SWITCHES (3) switches = 4 runs, then stop


async def test_pool_can_be_switched_off_and_on_again_without_a_new_approval(app):
    c, svc = app
    a1 = await add(c, "claude", "Основной")
    await enable(c, svc)
    off = (await c.post("/api/rave/pool/disable")).json()
    assert off["enabled"] is False and off["active"] is False
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and "account" not in cc and runs(a1) == 0   # off again: the usual login
    on = await c.post("/api/rave/pool/enable", json={})                # the old opt-in still covers every account
    assert on.status_code == 200 and on.json()["state"] == "ENABLED"


async def test_codex_accounts_switch_the_same_way(app):
    c, svc = app
    a1, a2 = await add(c, "codex", "Plus 1"), await add(c, "codex", "Plus 2")
    await enable(c, svc)
    (Path(a1["profile_dir"]) / "stub-limit").write_text("1")
    rid = await start(c, ["codex:cx"])
    cx = agent(await wait_for(c, rid, settled), "cx")
    assert cx["status"] == "done" and cx["account"] == a2["id"] and cx["answer"].startswith("STUB-CODEX-OK")
    assert runs(a1) == 1 and runs(a2) == 1
    (switch,) = journal((await c.get("/api/rave/pool")).json(), "switch")
    assert (switch["tool"], switch["account"], switch["to"]) == ("codex", a1["id"], a2["id"])


async def test_the_default_login_can_be_one_of_the_accounts(app):
    c, svc = app
    default = (await c.post("/api/rave/pool/accounts", json={"tool": "claude", "label": "Обычный вход",
                                                              "use_default": True})).json()["account"]
    other = await add(c, "claude", "Второй")
    await enable(c, svc)
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and cc["account"] == default["id"] and runs(other) == 0


async def test_a_limit_without_a_pool_stays_a_plain_failure(app, monkeypatch):
    """Negative control: pool off -> the old behaviour (the agent fails, no switching, nothing journaled)."""
    c, svc = app
    await add(c, "claude", "Основной")                                # registered, but the pool is not enabled
    monkeypatch.setenv("STUB_LIMIT", "1")
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "failed" and "LimitReached" in cc["error"] and "account" not in cc
    assert [j["event"] for j in (await c.get("/api/rave/pool")).json()["journal"]] == ["added"]


async def test_the_pool_only_governs_the_tools_it_has_accounts_for(app):
    c, svc = app
    a1 = await add(c, "codex", "Plus")
    await enable(c, svc)
    rid = await start(c, ["claude:cc", "codex:cx"])
    v = await wait_for(c, rid, settled)
    assert agent(v, "cc")["status"] == "done" and "account" not in agent(v, "cc")    # no claude account: as before
    assert agent(v, "cx")["account"] == a1["id"]


async def test_a_hand_edited_state_file_cannot_turn_the_pool_on_without_the_approval(app):
    c, svc = app
    a1 = await add(c, "claude", "Основной")
    path = svc.rave.root / "pool.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["enabled"] = True                                           # no opt-in recorded: nobody approved this
    path.write_text(json.dumps(data), encoding="utf-8")
    view = (await c.get("/api/rave/pool")).json()
    assert view["enabled"] is True and view["active"] is False
    rid = await start(c, ["claude:cc"])
    cc = agent(await wait_for(c, rid, settled), "cc")
    assert cc["status"] == "done" and "account" not in cc and runs(a1) == 0


# ------------------------------------------------------------------ CMD surface


def test_cli_pool_commands_use_the_same_endpoints_and_show_the_state(capsys):
    from bcc.rave import cli as rave_cli

    pool = {"enabled": True, "active": True, "terms_note": TERMS_NOTE, "journal": [
        {"at": 1.0, "event": "switch", "tool": "claude", "account": "claude-1", "to": "claude-2", "reason": "лимит исчерпан"}],
        "accounts": [{"id": "claude-1", "tool": "claude", "label": "Основной", "state": "limited", "limited_until": 4102444800,
                      "approved": True, "profile_dir": "C:/p/claude-1", "login_step": "x"},
                     {"id": "claude-2", "tool": "claude", "label": "Запасной", "state": "needs_login", "approved": False,
                      "profile_dir": None, "login_step": "войдите сами: claude auth login"}]}

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, path, **kw):
            self.calls.append(("GET", path))
            return pool

        def post(self, path, body=None, **kw):
            self.calls.append(("POST", path, body))
            if path.endswith("/enable") and not (body or {}).get("approval_id"):
                return {"state": "WAIT_APPROVAL", "approval_id": 7, "preview": "PREVIEW"}
            if path.endswith("/accounts"):
                return {"account": {"id": "claude-2"}, "pool": pool}
            if path.endswith("/check"):
                return {"account": pool["accounts"][1], "pool": pool}
            return {"state": "ENABLED", "pool": pool} if path.endswith("/enable") else pool

        def delete(self, path, **kw):
            self.calls.append(("DELETE", path))
            return pool

    client = Client()
    run = lambda *argv: rave_cli.run(client, rave_cli.build_parser().parse_args(list(argv)), rave_cli.Printer(False))  # noqa: E731
    assert run("pool") == 0
    out = capsys.readouterr().out
    assert "ВКЛЮЧЁН" in out and "claude-1" in out and "лимит" in out and "claude-1 → claude-2" in out
    assert "как войти: войдите сами" in out and "условиям использования" in out
    assert run("pool", "enable") == 0 and "bossman approve 7" in capsys.readouterr().out
    assert run("pool", "enable", "--approval-id", "7") == 0
    assert run("pool", "add", "claude", "Запасной", "--use-default") == 0
    assert run("pool", "check", "claude-2") == 0 and run("pool", "remove", "claude-2") == 0
    assert run("pool", "disable") == 0 and run("pool", "clear-limit", "claude-1") == 0
    assert run("pool", "add", "claude") == rave_cli.EXIT_USAGE
    assert run("pool", "bogus") == rave_cli.EXIT_USAGE
    paths = [c[1] for c in client.calls]
    assert "/api/rave/pool/accounts/claude-2/check" in paths and "/api/rave/pool/accounts/claude-2" in paths
    assert ("POST", "/api/rave/pool/enable", {"approval_id": 7}) in client.calls
    assert ("POST", "/api/rave/pool/accounts", {"tool": "claude", "label": "Запасной", "profile_dir": None,
                                                 "use_default": True}) in client.calls
