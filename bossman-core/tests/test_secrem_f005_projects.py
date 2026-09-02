"""SECREM F-005 — projects runner: builtin/cmd идут через ту же дисциплину, что runner._call_tool.

REPRO (Fable 5.1): `REGISTRY[spec["builtin"]].handler(t.params)` напрямую (без
allowlist/approval) и `create_subprocess_shell(spec["cmd"].format(**quoted))` —
модельный план подставлял параметры в шаблон оболочки.
"""
from __future__ import annotations

import pytest

from bossman.projects import runner as pr
from bossman.toolkit import ToolResult


# ------------------------------------------------------------ cmd → argv без оболочки

def test_repro_param_injection_stays_single_argv_element():
    argv, via_shell = pr.build_cmd_argv("ffmpeg -i {input} {out}",
                                        {"input": "a.mp4; rm -rf /", "out": "$(id).mp4"}, {})
    assert via_shell is False
    assert argv == ["ffmpeg", "-i", "a.mp4; rm -rf /", "$(id).mp4"]


def test_unknown_placeholder_and_extra_params_refused():
    with pytest.raises(pr.ProjectToolDenied, match="неизвестные плейсхолдеры"):
        pr.build_cmd_argv("tool {evil}", {"evil": "x"}, {})
    with pytest.raises(pr.ProjectToolDenied, match="не из шаблона"):
        pr.build_cmd_argv("tool {out}", {"out": "o", "cmd": "rm"}, {})
    with pytest.raises(pr.ProjectToolDenied, match="нет значений"):
        pr.build_cmd_argv("tool {out} {input}", {}, {"out": "o"})
    argv, _ = pr.build_cmd_argv("tool {out} {input}", {"seconds": 5}, {"out": "o", "input": "o"})
    assert argv == ["tool", "o", "o"]                 # seconds — метаданные, не в argv


def test_shell_template_only_in_sh_c_form_with_quoted_values():
    argv, via_shell = pr.build_cmd_argv("sh -c 'echo {text} | piper --out {out}'",
                                        {"text": "hi; rm -rf /", "out": "x.wav"}, {})
    assert via_shell is True and argv[:2] == ["sh", "-c"]
    assert "'hi; rm -rf /'" in argv[2] and "x.wav" in argv[2]
    with pytest.raises(pr.ProjectToolDenied, match="shell-шаблон"):
        pr.build_cmd_argv("bash -c 'echo {text}' extra", {"text": "a"}, {})


# ------------------------------------------------------------ builtin allowlist + approval

class _Tool:
    def __init__(self, name, confirm_default=False, mandatory=None):
        self.name = name
        self.confirm_default = confirm_default
        self.mandatory_confirm = mandatory
        self.calls = []

    async def handler(self, params, ctx):
        self.calls.append(params)
        return ToolResult("done")


async def test_undeclared_builtin_is_refused_without_execution(monkeypatch):
    tool = _Tool("media.probe")
    monkeypatch.setattr(pr, "REGISTRY", {"media.probe": tool})
    monkeypatch.setattr(pr, "load_registry", lambda: {"tools": {}})
    with pytest.raises(pr.ProjectToolDenied, match="не объявлен"):
        await pr._run_builtin("slug", "media.probe", {"path": "x"})
    assert tool.calls == []
    # объявлен в registry, но не зарегистрирован в toolkit — тоже отказ
    monkeypatch.setattr(pr, "load_registry",
                        lambda: {"tools": {"probe": {"kind": "builtin", "builtin": "ghost"}}})
    with pytest.raises(pr.ProjectToolDenied, match="не зарегистрирован"):
        await pr._run_builtin("slug", "ghost", {})


async def test_confirm_default_builtin_asks_owner_and_rejection_pauses(monkeypatch):
    tool = _Tool("media.probe", confirm_default=True)
    monkeypatch.setattr(pr, "REGISTRY", {"media.probe": tool})
    monkeypatch.setattr(pr, "load_registry",
                        lambda: {"tools": {"probe": {"kind": "builtin", "builtin": "media.probe"}}})
    monkeypatch.setattr(pr, "_ctx", lambda slug: object())
    created = []

    async def fake_create(kind, preview, **kw):
        created.append((kind, preview, kw))
        return 77

    async def fake_wait(aid):
        return {"status": "rejected"}
    monkeypatch.setattr(pr.approvals, "create", fake_create)
    monkeypatch.setattr(pr.approvals, "wait", fake_wait)
    with pytest.raises(pr.ProjectPaused):
        await pr._run_builtin("slug", "media.probe", {"path": "x"}, task_id="t1")
    assert tool.calls == [] and created and created[0][0] == "action"
    assert "media.probe" in created[0][1]

    async def ok_wait(aid):
        return {"status": "approved", "decided_by": "owner"}
    monkeypatch.setattr(pr.approvals, "wait", ok_wait)
    res = await pr._run_builtin("slug", "media.probe", {"path": "x"}, task_id="t1")
    assert res.content == "done" and tool.calls == [{"path": "x"}]


async def test_no_confirm_builtin_runs_without_approval_but_only_if_declared(monkeypatch):
    tool = _Tool("fs.list")
    monkeypatch.setattr(pr, "REGISTRY", {"fs.list": tool})
    monkeypatch.setattr(pr, "load_registry",
                        lambda: {"tools": {"ls": {"kind": "builtin", "builtin": "fs.list"}}})
    monkeypatch.setattr(pr, "_ctx", lambda slug: object())
    called = []

    async def boom(*a, **kw):
        called.append(1)
        raise AssertionError("approval не нужен")
    monkeypatch.setattr(pr.approvals, "create", boom)
    res = await pr._run_builtin("slug", "fs.list", {})
    assert res.content == "done" and called == []
