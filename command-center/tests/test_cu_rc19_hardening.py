"""rc19 Computer Use hardening (workstream B) — each case failed on 762e96d2.

Proven defects closed here (see docs/security/COMPUTER_USE_THREAT_MODEL.md):
  D1 one-shot approval: agent grant / rule / lease drove desktop mutations with no
     owner decision (engine AUTO) — now an ASK floor + a handler-side claim;
  D2 no window allowlist: input and focus_window reached any window (PowerShell,
     cmd, Terminal) and Win-key hotkeys opened the shell;
  D3 payment / credential actions were approvable and password fields undetected;
  D4 the approved call was bound to "generation == current" and <=45 s, so any
     observation during the owner's decision (the Telegram approve button itself
     observes) made every approved action refuse, while `launch` had no expiry;
  D5 the handler trusted ctx.approval_id blindly (no row / status / TTL / replay);
  D6 `expect.file_contains` read files the action never wrote (content oracle).

Desktop is the FakeDesktop from test_computer_use_tools (real mouse untouched);
the engine, approvals table and handler are the real ones (CONTRACT/MOCK desktop).
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
import sqlalchemy as sa

from bcc import approval_scope as scope
from bcc.db import approvals as approvals_t, utcnow
from bcc.features import tools_computer as tc
from bcc.tools import REGISTRY, ToolContext, decide_effect

from .test_computer_use_tools import FakeDesktop, FakeShots, _consumed_approval
from .test_redteam_rc_20260921 import _another_task, _tool_rows
from .test_v21_tool_loop import FINISHED, ToolAdapter, _run_task, _stack_with_tools

pytest.importorskip("bossman.computer_operator.models")
pytestmark = pytest.mark.timeout(120)


@pytest.fixture
def desk(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    st = tc.ComputerState()
    st.stop_path = env.settings.data_dir / "computer" / tc.STOP_FILE
    st.desktop, st.shots, st.launcher = FakeDesktop(), FakeShots(), None
    st.desktop.set_interrupt(st.stop)
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    return st


def _ctx(env, approval_id=None, task_id=1):
    return ToolContext(svc=env.svc, task={"id": task_id}, run_id=1, agent={"permissions": {}},
                       approval_id=approval_id)


async def _handler(env, args, approval_id=None, task_id=1):
    return await REGISTRY.get("computer.act").handler(args, _ctx(env, approval_id, task_id))


GRANTED = {"permissions": {"computer.control": True}}


# ------------------------------------------------------------------ D1 one-shot approval

def test_every_mutation_is_an_owner_question_even_for_a_granted_agent(env):
    spec = REGISTRY.get("computer.act")
    for args in ({"action": "type", "text": "hello"}, {"action": "hotkey", "keys": ["ctrl", "s"]},
                 {"action": "click", "target": "Файл"}, {"action": "launch", "target": "notepad"},
                 {"action": "focus_window", "target": "Блокнот"}, {"action": "scroll", "clicks": 2}):
        assert decide_effect(spec, args, GRANTED)[0] == "ask", args
        # an owner rule cannot lower the hook floor either
        rules = [{"tool": "computer.act", "effect": "auto"}]
        assert decide_effect(spec, args, GRANTED, rules)[0] == "ask", args
    assert decide_effect(spec, {"action": "wait", "seconds": 1}, GRANTED)[0] == "auto"


async def test_handler_refuses_a_mutation_without_an_approval_row(env, desk):
    await tc.observe(env.svc)
    res = await _handler(env, {"action": "type", "text": "x", "generation": desk.generation})
    assert res.error and "отдельного одобрения" in res.content
    assert desk.desktop.executed == []
    # a passive wait needs none
    res = await _handler(env, {"action": "wait", "seconds": 0})
    assert not res.error, res.content


async def test_computer_act_is_not_leasable(env):
    sc = scope.Scope(tool="computer.act", effect_class=scope.WRITE, task_id=1, agent_id=1)
    assert "аренда для него не выдаётся" in scope.lease_offer(sc)
    with pytest.raises(PermissionError):
        await scope.grant(env.svc, approval={}, scope=sc, max_uses=5, ttl_seconds=60)
    other = scope.Scope(tool="terminal.run", effect_class=scope.READ, task_id=1, agent_id=1)
    assert "Можно ответить один раз на всю область" in scope.lease_offer(other)


# ------------------------------------------------------------------ D5 approval row checks

async def test_approval_must_be_real_consumed_fresh_and_unused(env, desk):
    await tc.observe(env.svc)
    args = {"action": "type", "text": "one", "generation": desk.generation}
    # no such row
    res = await _handler(env, args, approval_id=987654)
    assert res.error and "не найдено" in res.content
    # approved but never accepted for execution by the engine
    row = await env.svc.approvals.create("tool", "x")
    await env.svc.approvals.decide(row["id"], True, "owner")
    res = await _handler(env, args, approval_id=row["id"])
    assert res.error and "не принято движком" in res.content
    # rejected
    rej = await env.svc.approvals.create("tool", "x")
    await env.svc.approvals.decide(rej["id"], False, "owner")
    res = await _handler(env, args, approval_id=rej["id"])
    assert res.error and "не принято движком" in res.content
    # a different kind of approval (e.g. a studio budget) is not a tool approval
    other = await env.svc.approvals.create("studio_budget", "x")
    await env.svc.approvals.decide(other["id"], True, "owner")
    await env.svc.approvals.accept_for_execution(other["id"])
    res = await _handler(env, args, approval_id=other["id"])
    assert res.error and "другого вида" in res.content
    assert desk.desktop.executed == []
    # the real thing executes exactly once …
    aid = await _consumed_approval(env)
    res = await _handler(env, args, approval_id=aid)
    assert not res.error, res.content
    assert [e[2] for e in desk.desktop.executed if e[0] == "TYPE"] == ["one"]
    # … and a replay of the used approval is refused, also after a backend restart
    await tc.observe(env.svc)
    res = await _handler(env, {**args, "generation": desk.generation}, approval_id=aid)
    assert res.error and "уже использовано" in res.content
    env.svc._computer_state = None                      # "restart": memory gone, disk stays
    st2 = tc._owner_state(env.svc)
    assert aid in st2.used_approvals
    st2.desktop, st2.shots = desk.desktop, FakeShots()
    await tc.observe(env.svc)
    res = await _handler(env, {**args, "generation": st2.generation}, approval_id=aid)
    assert res.error and "уже использовано" in res.content
    assert [e[2] for e in desk.desktop.executed if e[0] == "TYPE"] == ["one"]


async def test_approval_of_another_task_is_not_transferable(env, desk):
    await tc.observe(env.svc)
    from .helpers import make_stack
    tid = int((await make_stack(env.client))["task"]["id"])
    row = await env.svc.approvals.create("tool", "x", task_id=tid)
    await env.svc.approvals.decide(row["id"], True, "owner")
    await env.svc.approvals.accept_for_execution(row["id"])
    res = await _handler(env, {"action": "type", "text": "x", "generation": desk.generation},
                         approval_id=row["id"], task_id=tid + 1000)
    assert res.error and "другой задаче" in res.content
    assert desk.desktop.executed == []


async def test_expired_approval_never_executes(env, desk, monkeypatch):
    await tc.observe(env.svc)
    aid = await _consumed_approval(env)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(approvals_t).where(approvals_t.c.id == aid).values(
            created_at=utcnow() - timedelta(seconds=tc.APPROVAL_TTL_S + 5)))
        await s.commit()
    res = await _handler(env, {"action": "launch", "target": "notepad"}, approval_id=aid)
    assert res.error and "истекло" in res.content
    assert desk.desktop.executed == []
    # the env knob only shortens the TTL, never lengthens it
    monkeypatch.setenv(tc.APPROVAL_TTL_ENV, "1000000")
    assert tc._approval_ttl_s() == tc.APPROVAL_TTL_S
    monkeypatch.setenv(tc.APPROVAL_TTL_ENV, "20")
    assert tc._approval_ttl_s() == 20
    monkeypatch.setenv(tc.APPROVAL_TTL_ENV, "0")
    assert tc._approval_ttl_s() == tc.MIN_APPROVAL_TTL_S
    monkeypatch.setenv(tc.APPROVAL_TTL_ENV, "nan")
    assert tc._approval_ttl_s() == tc.APPROVAL_TTL_S


# ------------------------------------------------------------------ D4 approval rebinding

async def test_approved_action_survives_an_observation_during_the_decision(env, desk):
    """The Telegram approve button calls /api/computer/observe before deciding; the
    approved action (bound to the generation the owner was asked about) must still run."""
    g = (await tc.observe(env.svc))["generation"]
    await env.client.post("/api/computer/observe")          # what the Пульт button does
    assert desk.generation != g
    res = await _handler(env, {"action": "type", "text": "после одобрения", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert not res.error, res.content
    assert desk.desktop.doc.endswith("после одобрения")


async def test_approved_action_refused_after_resume_restart_or_window_change(env, desk):
    g = (await tc.observe(env.svc))["generation"]
    await env.client.post("/api/computer/stop")
    await env.client.post("/api/computer/resume")
    res = await _handler(env, {"action": "type", "text": "x", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert res.error and "недействительно" in res.content
    # window changed between the question and the effect
    g = (await tc.observe(env.svc))["generation"]
    desk.desktop.fg_handle = 77
    orig = desk.desktop.snapshot

    async def other_window():
        fg, tree = await orig()
        return {**fg, "handle": 77, "title": "Другой документ — Блокнот"}, tree

    desk.desktop.snapshot = other_window
    res = await _handler(env, {"action": "type", "text": "x", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert res.error and "окно сменилось" in res.content
    desk.desktop.snapshot, desk.desktop.fg_handle = orig, 1
    # restart: history is memory-only, the approved generation is gone
    g = (await tc.observe(env.svc))["generation"]
    env.svc._computer_state = None
    st2 = tc._owner_state(env.svc)
    st2.desktop, st2.shots = desk.desktop, FakeShots()
    res = await _handler(env, {"action": "type", "text": "x", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert res.error and "недействительно" in res.content
    assert desk.desktop.executed == []


# ------------------------------------------------------------------ D2 allowlist / shell

async def test_input_into_a_shell_window_is_refused(env, desk):
    for proc, title in (("powershell.exe", "Windows PowerShell"), ("cmd.exe", "Командная строка"),
                        ("windowsterminal.exe", "Terminal"), ("msedge.exe", "New tab - Microsoft Edge"),
                        ("telegram.exe", "Telegram")):
        desk.desktop.process, desk.desktop.title, desk.desktop.app = proc, title, proc
        g = (await tc.observe(env.svc))["generation"]
        for args in ({"action": "type", "text": "Remove-Item -Recurse C:\\data"},
                     {"action": "hotkey", "keys": ["enter"]}, {"action": "click", "target": "Файл"}):
            res = await _handler(env, {**args, "generation": g}, approval_id=await _consumed_approval(env))
            assert res.error and "вне allowlist" in res.content, (proc, args, res.content)
    assert desk.desktop.executed == []


async def test_focus_window_only_reaches_allowlisted_windows(env, desk, monkeypatch):
    monkeypatch.setattr(tc, "_top_windows", lambda: [(501, "Windows PowerShell", 9)])
    desk.desktop.process = "powershell.exe"
    g = (await tc.observe(env.svc))["generation"]
    res = await _handler(env, {"action": "focus_window", "target": "PowerShell", "generation": g},
                         approval_id=await _consumed_approval(env))
    assert res.error and "не трогаются" in res.content


def test_shell_hotkeys_and_launch_outside_allowlist_are_denied_by_the_engine(env):
    spec = REGISTRY.get("computer.act")
    for keys in (["win", "r"], ["winleft", "x"], ["ctrl", "esc"], ["ctrl", "shift", "esc"], "win+r"):
        assert decide_effect(spec, {"action": "hotkey", "keys": keys}, GRANTED)[0] == "deny", keys
    for target in ("cmd", "powershell", "cmd.exe /c del *", "C:\\Windows\\System32\\cmd.exe", "wt"):
        assert decide_effect(spec, {"action": "launch", "target": target}, GRANTED)[0] == "deny", target
    assert decide_effect(spec, {"action": "hotkey", "keys": ["ctrl", "s"]}, GRANTED)[0] == "ask"
    assert decide_effect(spec, {"action": "launch", "target": "notepad"}, GRANTED)[0] == "ask"


async def test_shell_hotkey_refused_in_the_handler_even_with_approval(env, desk):
    await tc.observe(env.svc)
    res = await _handler(env, {"action": "hotkey", "keys": ["win", "r"], "generation": desk.generation},
                         approval_id=await _consumed_approval(env))
    assert res.error and "оболочку Windows" in res.content
    with pytest.raises(tc.ActRefused, match="оболочку Windows"):
        await tc.act(env.svc, {"action": "hotkey", "keys": ["ctrl", "shift", "esc"],
                               "generation": desk.generation})
    assert desk.desktop.executed == []


def test_allowlist_decision_uses_the_process_not_the_title():
    ok = lambda name, hosted=False: tc.window_allowlisted(  # noqa: E731
        5, pid_of=lambda h: 1, name_of=lambda p: name, hosted_matcher=lambda h: hosted)
    assert ok("notepad.exe") == (True, "notepad.exe")
    assert ok("Notepad.exe")[0] is True
    assert ok("powershell.exe")[0] is False
    assert ok("applicationframehost.exe", hosted=False)[0] is False
    assert ok("applicationframehost.exe", hosted=True) == (True, "calculator")
    assert tc.window_allowlisted(0)[0] is False
    assert tc.window_allowlisted(5, pid_of=lambda h: 1 / 0)[0] is False


# ------------------------------------------------------------------ D3 payment / credentials

async def test_payment_is_refused_even_with_an_owner_approval(env, desk):
    desk.desktop.extra = [{"name": "Оплатить", "control_type": "Button",
                           "left": 300, "top": 0, "right": 360, "bottom": 30, "x": 330, "y": 15}]
    await tc.observe(env.svc)
    spec = REGISTRY.get("computer.act")
    for args in ({"action": "click", "target": "Оплатить", "semantic": "pay"},
                 {"action": "click", "target": "Оплатить"},
                 {"action": "click", "target": "Файл", "semantic": "transfer"}):
        assert decide_effect(spec, args, GRANTED)[0] == "deny", args
        res = await _handler(env, {**args, "generation": desk.generation},
                             approval_id=await _consumed_approval(env))
        assert res.error and "платежи и учётные данные" in res.content, args
    # consequence visible only from the foreground window: refused at the effect boundary
    desk.desktop.title = "Checkout — оплата заказа — Блокнот"
    await tc.observe(env.svc)
    with pytest.raises(tc.ActRefused, match="платежи и учётные данные"):
        await tc.act(env.svc, {"action": "click", "target": "Файл", "generation": desk.generation},
                     approved_kind="pay", approval_ref=1)
    assert desk.desktop.executed == []


async def test_credential_entry_is_refused(env, desk):
    desk.desktop.extra = [{"name": "Password", "control_type": "Edit", "is_password": True,
                           "value": "(секретное поле — значение скрыто)",
                           "left": 0, "top": 40, "right": 200, "bottom": 60, "x": 100, "y": 50},
                          {"name": "Логин", "control_type": "Edit", "value": "",
                           "left": 0, "top": 70, "right": 200, "bottom": 90, "x": 100, "y": 80}]
    await tc.observe(env.svc)
    spec = REGISTRY.get("computer.act")
    assert decide_effect(spec, {"action": "type", "target": "Password", "text": "hunter2"},
                         GRANTED)[0] == "deny"
    assert decide_effect(spec, {"action": "type", "text": "x", "semantic": "secret_entry"},
                         GRANTED)[0] == "deny"
    res = await _handler(env, {"action": "type", "target": "Password", "text": "hunter2",
                               "generation": desk.generation}, approval_id=await _consumed_approval(env))
    assert res.error and "учётные данные" in res.content
    # the only field of the window is a password box
    desk.desktop.extra = []
    await tc.observe(env.svc)
    desk.last["elements"][0]["is_password"] = True
    with pytest.raises(tc.ActRefused, match="секретное"):
        await tc.act(env.svc, {"action": "type", "text": "x", "generation": desk.generation})
    # the OS says the focused field is a password box although its name is harmless
    await tc.observe(env.svc)
    desk.desktop.password_focus = True
    with pytest.raises(tc.ActRefused, match="секретное"):
        await tc.act(env.svc, {"action": "type", "text": "x", "generation": desk.generation})
    # focus type unknown -> fail closed
    desk.desktop.password_focus = None
    await tc.observe(env.svc)
    with pytest.raises(tc.ActRefused, match="не удалось проверить"):
        await tc.act(env.svc, {"action": "type", "text": "x", "generation": desk.generation})
    assert [e for e in desk.desktop.executed if e[0] == "TYPE"] == []


# ------------------------------------------------------------------ D6 verifier

def test_file_content_is_not_read_unless_the_action_wrote_it(tmp_path):
    secret = tmp_path / "id_rsa"
    secret.write_text("-----BEGIN KEY-----", encoding="utf-8")
    obs = {"window": {"title": "x"}, "elements": []}
    ok, notes = tc.verify(obs, {"file_exists": str(secret), "file_contains": "BEGIN"},
                          started_at=time.time() + 100)
    assert ok is False
    assert not any("есть «BEGIN»" in n or "НЕТ «BEGIN»" in n for n in notes)
    assert any("не проверялось" in n for n in notes)


def test_file_sha256_postcondition(tmp_path):
    import hashlib
    f = tmp_path / "form.txt"
    body = "Имя: Тест\nГород: Москва\n".encode("utf-8")
    f.write_bytes(body)
    obs = {"window": {"title": "form.txt — Блокнот"}, "elements": []}
    started = time.time() - 5
    good = hashlib.sha256(body).hexdigest()
    assert tc.verify(obs, {"file_exists": str(f), "file_sha256": good}, started_at=started)[0] is True
    assert tc.verify(obs, {"file_exists": str(f), "file_sha256": good.upper()}, started_at=started)[0] is True
    assert tc.verify(obs, {"file_exists": str(f), "file_sha256": "0" * 64}, started_at=started)[0] is False
    assert tc.verify(obs, {"file_exists": str(f), "file_sha256": "abc"}, started_at=started)[0] is False
    assert tc.verify(obs, {"file_sha256": good}, started_at=started)[0] is False


# ------------------------------------------------------------------ engine end-to-end

async def _cu_stack(env, desk, script):
    adapter = ToolAdapter(script)
    stack = await _stack_with_tools(env, ["computer.act", "computer.observe"], adapter=adapter)
    await env.client.patch(f"/api/agents/{stack['agent']['id']}",
                           json={"permissions": {"computer.control": True, "computer.observe": True}})
    return stack, adapter


async def _pending_approval(env, task_id):
    rows = [a for a in (await env.client.get("/api/approvals")).json() if a.get("task_id") == task_id]
    assert rows, "no pending approval"
    return rows[0]["id"]


async def test_engine_approve_executes_once_deny_never_replay_refused(env, desk):
    g = (await tc.observe(env.svc))["generation"]
    call = {"action": "type", "text": "одобрено", "generation": g,
            "expect": {"contains_text": "одобрено"}}
    stack, adapter = await _cu_stack(env, desk, [("tool", "computer_act", call), ("text", "готово")])
    tid = stack["task"]["id"]
    assert await _run_task(env, tid) == "waiting_approval"
    assert desk.desktop.executed == []                      # granted agent, still parked
    aid = await _pending_approval(env, tid)
    preview = next(a for a in (await env.client.get("/api/approvals")).json() if a["id"] == aid)["preview"]
    assert "аренда для него не выдаётся" in preview
    await env.client.post("/api/approvals/" + str(aid), json={"approve": True, "by": "owner"})
    assert await _run_task(env, tid, until=FINISHED) == "completed"
    typed = [e for e in desk.desktop.executed if e[0] == "TYPE"]
    assert [e[2] for e in typed] == ["одобрено"]
    # replaying the decision does not re-run anything
    again = (await env.client.post("/api/approvals/" + str(aid), json={"approve": True, "by": "owner"})).json()
    assert again["status"] == "consumed"
    await _run_task(env, tid, until=FINISHED)
    assert len([e for e in desk.desktop.executed if e[0] == "TYPE"]) == 1

    # deny -> never executes
    g2 = (await tc.observe(env.svc))["generation"]
    task2 = await _another_task(env, stack, ToolAdapter([
        ("tool", "computer_act", {"action": "type", "text": "запрещено", "generation": g2}),
        ("text", "ок")]))
    assert await _run_task(env, task2) == "waiting_approval"
    aid2 = await _pending_approval(env, task2)
    await env.client.post("/api/approvals/" + str(aid2), json={"approve": False, "by": "owner"})
    assert await _run_task(env, task2, until=FINISHED) in FINISHED
    assert "запрещено" not in desk.desktop.doc
    rows = await _tool_rows(env.svc, task_id=task2)
    assert rows[-1]["status"] == "rejected"


async def test_engine_lease_cannot_drive_the_desktop(env, desk):
    g = (await tc.observe(env.svc))["generation"]
    stack, _ = await _cu_stack(env, desk, [
        ("tool", "computer_act", {"action": "type", "text": "по аренде", "generation": g}),
        ("text", "ок")])
    tid = stack["task"]["id"]
    assert await _run_task(env, tid) == "waiting_approval"
    aid = await _pending_approval(env, tid)
    # production answers 500 (generic handler); the in-process client re-raises it
    with pytest.raises(PermissionError):
        await env.client.post("/api/approvals/" + str(aid),
                              json={"approve": True, "by": "owner",
                                    "lease": {"max_uses": 10, "ttl_seconds": 600}})
    # the refused lease rolled the decision back: the question is still pending
    row = next(a for a in (await env.client.get("/api/approvals?status=all")).json() if a["id"] == aid)
    assert row["status"] == "pending"
    assert desk.desktop.executed == []
