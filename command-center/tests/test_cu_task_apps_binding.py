"""Computer Use: task-scoped apps + exact HWND/PID binding (owner task 2026-10-10).

Regression for task 83 (installed build 6de18f8d, local Qwen): the launched Notepad
window was in front only *later* (the 0.3 s focus check gave up), the approval was
answered in another window that took the foreground, the follow-up focus_window had no
generation (misleading "observation invalid" message) and the owner rejected focusing
"Блокнот" by title because the owner's unrelated Notepad windows (same notepad.exe pid)
matched. Each case below has a negative control; the desktop is a fake with several
top-level windows, the engine/approvals/handler are real.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.features import tools_computer as tc
from bcc.tools import REGISTRY, ToolContext, decide_effect

from .helpers import make_stack
from .test_computer_use_tools import FakeShots, _consumed_approval

pytest.importorskip("bossman.computer_operator.models")
pytestmark = pytest.mark.timeout(120)

NOTEPAD_PID = 25204          # Win11 Notepad keeps ALL its windows in one process
OWNER_DOC = "OWNER PRIVATE DOCUMENT — must never be read or typed into"


class Win:
    def __init__(self, title, process, pid, doc="", exe="", tabs=("Без имени",), owner=None):
        self.title, self.process, self.pid, self.doc, self.exe = title, process, pid, doc, exe
        self.tabs, self.owner = list(tabs), owner


class MultiDesk:
    """Several top-level windows; only the OS lookups are faked, every decision is real."""

    def __init__(self):
        self.wins: dict[int, Win] = {
            100: Win("Terminal — agent session", "windowsterminal.exe", 900),
            200: Win("token — Блокнот", "notepad.exe", NOTEPAD_PID, doc=OWNER_DOC),
        }
        self.fg = 100
        self.executed: list[tuple] = []
        self.interrupt = None
        self.focus_requests: list[int] = []
        self.grant_focus = True          # does Windows honour SetForegroundWindow?
        self.password_focus = False

    # --- observation
    def _tree(self, h):
        w = self.wins[h]
        els = [{"name": "Текстовый редактор", "control_type": "Document", "value": w.doc,
                "left": 0, "top": 100, "right": 800, "bottom": 600, "x": 400, "y": 350}]
        els += [{"name": t, "control_type": "TabItem"} for t in w.tabs]
        return {"elements": els}

    async def snapshot(self):
        return await self.snapshot_window(self.fg)

    async def snapshot_window(self, h):
        if h not in self.wins:
            return {"title": "", "app": "", "handle": h, "error": "окна нет"}, None
        w = self.wins[h]
        return {"title": w.title, "app": w.process, "handle": h}, self._tree(h)

    async def foreground(self):
        return {"title": self.wins[self.fg].title, "handle": self.fg}

    # --- identity (real allowlist decision, faked OS lookups)
    def window_pid(self, h):
        return self.wins[h].pid if h in self.wins else 0

    def root_owner(self, h):
        w = self.wins.get(h)
        return w.owner if w is not None and w.owner else h

    def window_process_allowed(self, h, allowed=None):
        by_pid = {w.pid: w for w in self.wins.values()}
        return tc.window_allowlisted(h, allowed=allowed, pid_of=self.window_pid,
                                     name_of=lambda pid: by_pid[pid].process if pid in by_pid else "",
                                     exe_of=lambda pid: by_pid[pid].exe if pid in by_pid else "",
                                     hosted_matcher=lambda _h: False)

    def focused_is_password(self):
        return self.password_focus

    def set_interrupt(self, ev):
        self.interrupt = ev

    # --- effects
    async def execute(self, a, o):
        self.executed.append((a.kind.value, a.target, a.text, self.fg))
        if a.kind.value == "TYPE":
            self.wins[self.fg].doc += a.text or ""

    def top_windows(self):
        return [(h, w.title, w.pid) for h, w in self.wins.items()]

    def set_foreground(self, h):
        self.focus_requests.append(h)
        if self.grant_focus:
            self.fg = h


class FakeLauncher:
    """`launch notepad` opens a NEW window in the already running notepad.exe process."""

    def __init__(self, desk: MultiDesk):
        self.desk, self.calls = desk, []

    async def execute(self, a, o, *, allowed=None):
        from bossman.computer_operator.applist import canonical_app
        app = canonical_app(a.target, allowed)
        if app is None:
            raise RuntimeError("app is not allowlisted for launch")
        self.calls.append(app)
        proc = {"notepad": ("notepad.exe", NOTEPAD_PID), "charmap": ("charmap.exe", 777)}[app]
        h = 300 + len(self.calls)
        self.desk.wins[h] = Win("Без имени — Блокнот" if app == "notepad" else "Таблица символов",
                                proc[0], proc[1])
        self.desk.fg = h                 # a freshly started app normally comes to the front


@pytest.fixture
def md(env, monkeypatch):
    monkeypatch.setattr(tc, "availability", lambda: (True, ""))
    monkeypatch.setattr(tc, "SETTLE_S", 0)
    monkeypatch.setattr(tc, "FOCUS_WAIT_S", 0.2)
    monkeypatch.setattr(tc, "LAUNCH_FOCUS_WAIT_S", 0.2)
    monkeypatch.setattr(tc, "LAUNCH_WAIT_S", 0.5)
    desk = MultiDesk()
    st = tc.ComputerState()
    st.stop_path = env.settings.data_dir / "computer" / tc.STOP_FILE
    st.desktop, st.shots, st.launcher = desk, FakeShots(), FakeLauncher(desk)
    desk.set_interrupt(st.stop)
    env.svc._computer_state = st
    monkeypatch.setattr(tc, "_top_windows", desk.top_windows)
    monkeypatch.setattr(tc, "_set_foreground", desk.set_foreground)
    desk.st = st
    return desk


async def _task(env) -> int:
    return int((await make_stack(env.client))["task"]["id"])


async def _grant(env, tid, apps=("notepad",)):
    r = await env.client.put(f"/api/computer/tasks/{tid}/apps", json={"apps": list(apps)})
    assert r.status_code == 200, r.text
    return r.json()


def _ctx(env, tid, approval_id=None):
    return ToolContext(svc=env.svc, task={"id": tid}, run_id=1, agent={"permissions": {}},
                       approval_id=approval_id)


async def _act(env, tid, args, approve=True):
    aid = await _consumed_approval(env) if approve else None
    return await REGISTRY.get("computer.act").handler(args, _ctx(env, tid, aid))


async def _observe(env, tid, window=None):
    res = await REGISTRY.get("computer.observe").handler({} if window is None else {"window": window},
                                                         _ctx(env, tid))
    return res


async def _launch_notepad(env, md, tid) -> int:
    res = await _act(env, tid, {"action": "launch", "target": "notepad"})
    assert not res.error, res.content
    new = [h for h, r in md.st.launched.items() if r["task_id"] == tid]
    assert len(new) == 1
    return new[0]


# ------------------------------------------------------------------ owner grant (HTTP)

async def test_owner_grant_endpoint_validates_against_catalog(env, md):
    tid = await _task(env)
    assert (await env.client.get(f"/api/computer/tasks/{tid}/apps")).json()["granted"] is False
    for bad in (["cmd"], ["powershell"], ["msedge"], ["C:\\Windows\\System32\\cmd.exe"], [], "regedit",
                ["notepad", "explorer"]):
        r = await env.client.put(f"/api/computer/tasks/{tid}/apps", json={"apps": bad})
        assert r.status_code == 422, (bad, r.text)
    assert (await env.client.put(f"/api/computer/tasks/{tid + 999}/apps",
                                 json={"apps": ["notepad"]})).status_code == 404
    body = await _grant(env, tid, ["Блокнот", "charmap"])
    assert body["apps"] == ["notepad", "charmap"] and body["windows"] == "launched"
    got = (await env.client.get(f"/api/computer/tasks/{tid}/apps")).json()
    assert got["granted"] is True and got["apps"] == ["notepad", "charmap"]
    cat = (await env.client.get("/api/computer/catalog")).json()
    assert "charmap" in cat["apps"] and "cmd.exe" in cat["never"] and "windowsterminal.exe" in cat["never"]
    assert (await env.client.delete(f"/api/computer/tasks/{tid}/apps")).json()["revoked"] is True
    assert (await env.client.get(f"/api/computer/tasks/{tid}/apps")).json()["granted"] is False


def test_model_has_no_tool_that_grants_apps():
    assert not any("grant" in n or n.startswith("http") for n in REGISTRY.names())
    schema = json.dumps(REGISTRY.get("computer.act").input_schema)
    assert "apps" not in schema and "grant" not in schema


def test_catalog_never_contains_shells_browsers_or_bossman():
    from bossman.computer_operator import applist as al
    for name, spec in al.APP_CATALOG.items():
        for proc in (*spec.get("windows", ()), *spec.get("process", ())):
            assert not al.hard_denied_process(proc), (name, proc)
    for proc in ("cmd.exe", "WindowsTerminal.exe", "pwsh.exe", "msedge.exe", "consent.exe",
                 "CredentialUIBroker.exe", "bossman-desktop.exe", "bcc-desktop.exe", "explorer.exe"):
        assert al.hard_denied_process(proc), proc
    # even a hypothetical grant that named a shell process cannot make its window a target
    fake = {"evil": {"windows": ("cmd.exe",), "process": ("cmd.exe",)}}
    assert tc.window_allowlisted(5, allowed=fake, pid_of=lambda h: 1, name_of=lambda p: "cmd.exe")[0] is False


def test_packaged_app_window_must_come_from_its_package():
    paint = {"paint": {"windows": ("mspaint.exe",), "process": ("mspaint.exe",), "package": "microsoft.paint_"}}
    ok = lambda exe: tc.window_allowlisted(5, allowed=paint, pid_of=lambda h: 1,  # noqa: E731
                                           name_of=lambda p: "mspaint.exe", exe_of=lambda p: exe)[0]
    assert ok(r"C:\Program Files\WindowsApps\Microsoft.Paint_11.2605.81.0_x64__8wekyb3d8bbwe\PaintApp\mspaint.exe")
    assert not ok(r"C:\Users\asd\Downloads\mspaint.exe")
    assert not ok(r"C:\Program Files\WindowsApps\Evil.Paint_1_x64\mspaint.exe")
    assert not ok("")


# ------------------------------------------------------------------ launch within the grant

async def test_launch_only_granted_apps_engine_and_handler(env, md):
    tid = await _task(env)
    await _grant(env, tid, ["notepad"])
    spec = REGISTRY.get("computer.act")
    granted = {"permissions": {"computer.control": True}}
    # outside the catalog: engine DENY (never asked); inside the catalog: an owner question
    assert decide_effect(spec, {"action": "launch", "target": "msedge"}, granted)[0] == "deny"
    assert decide_effect(spec, {"action": "launch", "target": "charmap"}, granted)[0] == "ask"
    # catalog app NOT granted to this task: refused before the owner is asked …
    from bcc.tools import context_denial
    assert "не разрешено этой задаче" in (await context_denial(spec, {"action": "launch", "target": "charmap"},
                                                                _ctx(env, tid)) or "")
    assert await context_denial(spec, {"action": "launch", "target": "notepad"}, _ctx(env, tid)) is None
    # … and by the handler even if an approval exists
    res = await _act(env, tid, {"action": "launch", "target": "charmap"})
    assert res.error and "вне allowlist запуска этой задачи" in res.content
    assert md.st.launcher.calls == []
    # the default set (no grant) still cannot launch a catalog-only app
    other = tid + 1000                               # a task without any owner grant
    res = await _act(env, other, {"action": "launch", "target": "charmap"})
    assert res.error and md.st.launcher.calls == []
    # granted: launch works and binds the exact new window (hwnd + pid)
    await _grant(env, tid, ["notepad", "charmap"])
    res = await _act(env, tid, {"action": "launch", "target": "charmap"})
    assert not res.error, res.content
    (h, rec), = [(h, r) for h, r in md.st.launched.items() if r["task_id"] == tid]
    assert rec["app"] == "charmap" and rec["pid"] == 777
    assert f"hwnd={h}, pid=777" in res.content


# ------------------------------------------------------------------ exact HWND/PID binding

async def test_happy_path_type_into_the_launched_empty_window(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    obs = await _observe(env, tid)
    assert f"hwnd={h}, pid={NOTEPAD_PID}" in obs.content and OWNER_DOC not in obs.content
    g = obs.data["generation"]
    text = "BOSSMAN CU proof 2026-10-10 — мышь и клавиатура"
    res = await _act(env, tid, {"action": "type", "text": text, "generation": g, "window": h,
                                "pid": NOTEPAD_PID, "expect": {"contains_text": text}})
    assert not res.error and "ПРОВЕРЕНО" in res.content, res.content
    assert md.wins[h].doc == text and md.wins[200].doc == OWNER_DOC


async def test_task83_focus_moved_after_approval_is_refused_and_not_bypassed(env, md):
    """Approval answered in another window (e.g. the terminal) that took the foreground."""
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    g = (await _observe(env, tid)).data["generation"]
    md.fg = 100                                      # owner answered the approval in the terminal
    md.focus_requests.clear()
    res = await _act(env, tid, {"action": "type", "text": "x", "generation": g, "window": h,
                                "pid": NOTEPAD_PID})
    assert res.error and "окно сменилось" in res.content and "Фокус сервер сам" in res.content
    assert md.focus_requests == []                   # the server did NOT pull the window back
    assert [e for e in md.executed if e[0] == "TYPE"] == []
    assert md.wins[100].doc == "" and md.wins[h].doc == ""


async def test_binding_requires_exact_window_and_pid(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    g = (await _observe(env, tid)).data["generation"]
    base = {"action": "type", "text": "x", "generation": g}
    for args, why in (({**base}, "передайте window"),
                      ({**base, "window": h}, "передайте window"),
                      ({**base, "window": 200, "pid": NOTEPAD_PID}, "не окно наблюдения"),
                      ({**base, "window": h, "pid": NOTEPAD_PID + 1}, "не совпадает"),
                      ({**base, "window": str(h), "pid": NOTEPAD_PID}, "целые числа")):
        res = await _act(env, tid, args)
        assert res.error and why in res.content, (args, res.content)
    assert md.executed == []
    # missing generation is named as such (task 83 got "observation invalid … restart")
    res = await _act(env, tid, {"action": "focus_window", "window": h, "pid": NOTEPAD_PID})
    assert res.error and "generation обязателен" in res.content


async def test_changed_tab_or_content_after_approval_is_refused(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    g = (await _observe(env, tid)).data["generation"]
    md.wins[h].tabs.append("secrets.txt")             # another document appeared in that window
    res = await _act(env, tid, {"action": "type", "text": "x", "generation": g, "window": h,
                                "pid": NOTEPAD_PID})
    assert res.error and "изменилось с момента вопроса" in res.content
    g = (await _observe(env, tid)).data["generation"]
    md.wins[h].doc = "чужой текст"                     # content changed under the approval
    res = await _act(env, tid, {"action": "type", "text": "x", "generation": g, "window": h,
                                "pid": NOTEPAD_PID})
    assert res.error and "изменилось с момента вопроса" in res.content
    assert [e for e in md.executed if e[0] == "TYPE"] == []


async def test_owner_windows_of_the_same_process_are_never_read_or_typed(env, md):
    """Win11 Notepad: the owner's 'token' window shares notepad.exe and its pid."""
    tid = await _task(env)
    await _grant(env, tid)
    await _launch_notepad(env, md, tid)
    md.fg = 200                                      # owner's private Notepad window in front
    obs = await _observe(env, tid)
    assert OWNER_DOC not in obs.content and "скрыто" in obs.content
    g = obs.data["generation"]
    res = await _act(env, tid, {"action": "type", "text": "x", "generation": g, "window": 200,
                                "pid": NOTEPAD_PID})
    assert res.error and md.wins[200].doc == OWNER_DOC
    res = await _observe(env, tid, window=200)
    assert res.error and "не запускалось этой задачей" in res.content
    res = await _act(env, tid, {"action": "focus_window", "generation": g, "window": 200, "pid": NOTEPAD_PID})
    assert res.error and md.focus_requests == []
    res = await _act(env, tid, {"action": "focus_window", "generation": g, "target": "Блокнот"})
    assert res.error and "по заголовку недоступен" in res.content
    assert [e for e in md.executed if e[0] == "TYPE"] == []


async def test_focus_window_is_one_request_and_a_refusal_is_final(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    md.fg = 100
    obs = await _observe(env, tid, window=h)          # targeted: the task window is NOT in front
    assert "НЕ впереди" in obs.content and f"hwnd={h}" in obs.content
    g = obs.data["generation"]
    md.grant_focus = False                            # Windows foreground lock says no
    md.focus_requests.clear()
    res = await _act(env, tid, {"action": "focus_window", "generation": g, "window": h, "pid": NOTEPAD_PID})
    assert res.error and "не обходится" in res.content
    assert md.focus_requests == [h] and md.fg == 100  # exactly one plain request, no tricks
    md.grant_focus = True
    g = (await _observe(env, tid, window=h)).data["generation"]
    res = await _act(env, tid, {"action": "focus_window", "generation": g, "window": h, "pid": NOTEPAD_PID})
    assert not res.error and md.fg == h
    g = (await _observe(env, tid)).data["generation"]
    res = await _act(env, tid, {"action": "type", "text": "после фокуса", "generation": g, "window": h,
                                "pid": NOTEPAD_PID, "expect": {"contains_text": "после фокуса"}})
    assert not res.error and "ПРОВЕРЕНО" in res.content


async def test_launch_without_focus_is_a_fact_not_an_error(env, md, monkeypatch):
    """Task 83 step 1: the window appeared, focus came only later — launch itself succeeded."""
    tid = await _task(env)
    await _grant(env, tid)
    orig = md.st.launcher.execute

    async def launch_behind(a, o, *, allowed=None):
        await orig(a, o, allowed=allowed)
        md.fg = 100                                   # new window stays behind the terminal
    md.st.launcher.execute = launch_behind
    md.grant_focus = False
    res = await _act(env, tid, {"action": "launch", "target": "notepad"})
    assert not res.error, res.content
    assert "НЕ впереди" in res.content and len(md.focus_requests) == 1


# ------------------------------------------------------------------ secrets / irreversible / STOP

def test_secret_like_text_is_refused_by_the_engine():
    spec = REGISTRY.get("computer.act")
    granted = {"permissions": {"computer.control": True}}
    for text in ("sk-proj-" + "a" * 40, "ghp_" + "b" * 36, "AKIA" + "C" * 16,
                 "123456789:" + "A" * 35, "4111 1111 1111 1111",
                 "-----BEGIN OPENSSH PRIVATE KEY-----", "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefghijkl"):
        assert decide_effect(spec, {"action": "type", "text": text}, granted)[0] == "deny", text
    for text in ("BOSSMAN LOCAL UI PROOF 2026-10-10 — мышь и клавиатура", "id=20260928T093405Z-1",
                 "Телефон 8 800 555 35 35", "sk-short"):
        assert decide_effect(spec, {"action": "type", "text": text}, granted)[0] == "ask", text


def test_irreversible_external_actions_are_refused_with_or_without_approval():
    spec = REGISTRY.get("computer.act")
    granted = {"permissions": {"computer.control": True}}
    for sem in ("send", "external_upload", "deploy", "git_push", "merge", "release", "uninstall", "pay"):
        assert decide_effect(spec, {"action": "click", "target": "OK", "semantic": sem}, granted)[0] == "deny"
    assert decide_effect(spec, {"action": "click", "target": "Удалить", "semantic": "delete"}, granted)[0] == "ask"


async def test_secret_typing_refused_in_the_handler_even_with_approval(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    g = (await _observe(env, tid)).data["generation"]
    res = await _act(env, tid, {"action": "type", "text": "ghp_" + "x" * 36, "generation": g,
                                "window": h, "pid": NOTEPAD_PID})
    assert res.error and "секрет" in res.content and md.wins[h].doc == ""


async def test_stop_still_wins_in_task_scope(env, md):
    tid = await _task(env)
    await _grant(env, tid)
    h = await _launch_notepad(env, md, tid)
    g = (await _observe(env, tid)).data["generation"]
    await env.client.post("/api/computer/stop")
    res = await _act(env, tid, {"action": "type", "text": "после стопа", "generation": g, "window": h,
                                "pid": NOTEPAD_PID})
    assert res.error and "Стоп" in res.content and md.wins[h].doc == ""
    res = await _act(env, tid, {"action": "launch", "target": "notepad"})
    assert res.error and "Стоп" in res.content
    await env.client.post("/api/computer/resume")
    res = await _act(env, tid, {"action": "type", "text": "x", "generation": g, "window": h,
                                "pid": NOTEPAD_PID})
    assert res.error and "недействительно" in res.content


async def test_corrupted_grant_journal_fails_closed(env, md):
    tid = await _task(env)
    path = env.settings.data_dir / "computer" / tc.TASK_APPS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    res = await _act(env, tid, {"action": "launch", "target": "notepad"})
    assert res.error and "повреждён" in res.content
    assert md.st.launcher.calls == []
    assert (await env.client.put(f"/api/computer/tasks/{tid}/apps", json={"apps": ["notepad"]})).status_code == 409
    await asyncio.sleep(0)
