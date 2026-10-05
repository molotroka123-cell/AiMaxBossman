"""A local Rave agent without --allow must not crash: its editable set is the workspace top level (chat UX run B10).

`LocalConnector.run` built the sidecar request with `list(ctx.allow)` while `ctx.allow` was still None (the context computes
the default lazily inside run_process), so every `local:*` rave without --allow died with a TypeError."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.rave import connectors as cn
from bcc.rave.engine import AgentCtx
from bcc.rave.spec import AgentSpec


class FakeCtx:
    def __init__(self, workspace: Path, allow):
        self.workspace, self.allow, self.prompt = workspace, allow, "сделай"
        self.request = None

    async def checkpoint(self):
        return None

    async def begin_step(self, *a, **k):
        return None

    async def end_step(self, *a, **k):
        return None

    def exec_log(self, line):
        return None

    async def resolve_allow(self):
        if self.allow is None:
            self.allow = sorted(p.name for p in self.workspace.iterdir())
        return self.allow

    async def run_process(self, argv, *, stdin, timeout, env=None):
        self.request = json.loads(stdin.decode("utf-8"))
        out = json.dumps({"status": "completed", "summary": "ok", "steps": 1}).encode("utf-8")
        return SimpleNamespace(returncode=0, timed_out=False, stdout=out, stderr=b"")


def connector():
    return cn.LocalConnector(AgentSpec("local", "a"), endpoint="http://127.0.0.1:11434/v1", model="m")


async def test_default_allow_is_the_workspace_top_level_instead_of_a_type_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cn, "_core_path", lambda: str(tmp_path))
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    ctx = FakeCtx(tmp_path, None)
    outcome = await connector().run(ctx)
    assert outcome.answer == "ok"
    assert ctx.request["allowed_paths"] == ["README.md", "src"]


async def test_an_explicit_allow_list_is_passed_through_unchanged(tmp_path, monkeypatch):
    """Negative control: the owner's own list is never replaced by the default."""
    monkeypatch.setattr(cn, "_core_path", lambda: str(tmp_path))
    (tmp_path / "src").mkdir()
    ctx = FakeCtx(tmp_path, ["docs"])
    await connector().run(ctx)
    assert ctx.request["allowed_paths"] == ["docs"]


def test_the_real_context_exposes_the_same_lazy_default():
    assert hasattr(AgentCtx, "resolve_allow"), "run_process and the connectors share one place that computes the default"
    with pytest.raises(AttributeError):
        AgentCtx.no_such_method                                              # pragma: no cover - guards the hasattr above
