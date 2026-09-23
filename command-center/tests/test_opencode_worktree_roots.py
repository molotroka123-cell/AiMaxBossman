"""C2 — `git worktree add` не должен выполняться до проверки корней и имени.

Раньше `make_worktree` создавал полный checkout рядом с проектом
(`<parent>/<proj>-<name>`) и ветку `bossman/<name>`, а уже ПОТОМ
`approved_dir` отказывал 403: на диске вне одобренных корней оставался
мусорный checkout, в репозитории — ветка. Тест: при корнях = [проект]
отказ приходит, а новых каталогов вне корней и новых веток нет —
и через HTTP, и через инструмент агента.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.features.tools_opencode import _tool_start
from bcc.tools import ToolContext

from .test_v21_opencode import _git, fake, oc_rows, repo, set_roots  # noqa: F401


def _branches(project: Path) -> set[str]:
    out = _git(project, "branch", "--format=%(refname:short)").stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _snapshot(project: Path) -> tuple[set[str], set[str], str]:
    siblings = {p.name for p in project.parent.iterdir()}
    worktrees = _git(project, "worktree", "list", "--porcelain").stdout
    return siblings, _branches(project), worktrees


@pytest.mark.parametrize("name", ["ok", "sub/deep", "..", "a\\b", "-x"])
async def test_http_worktree_outside_roots_leaves_nothing(env, repo, name):
    await set_roots(env, [repo])
    before = _snapshot(repo)

    r = await env.client.post("/api/opencode/sessions",
                              json={"project_path": str(repo), "worktree": True,
                                    "worktree_name": name})
    assert r.status_code in (400, 403), r.text
    assert _snapshot(repo) == before
    assert not (repo.parent / f"{repo.name}-sub").exists()
    assert await oc_rows(env.svc) == []


async def test_tool_worktree_outside_roots_leaves_nothing(env, repo):
    await set_roots(env, [repo])
    before = _snapshot(repo)

    ctx = ToolContext(svc=env.svc, task={"id": 1}, run_id=7, agent={})
    result = await _tool_start({"project_path": str(repo), "worktree": True}, ctx)
    assert result.error
    assert "вне одобренных корней" in result.content
    assert _snapshot(repo) == before
    assert not (repo.parent / f"{repo.name}-run7").exists()


async def test_http_worktree_inside_roots_still_works(env, repo, fake, monkeypatch):
    """Корректное имя внутри корней — worktree создаётся, как и раньше."""
    monkeypatch.setenv("OPENCODE_URL", fake.url)
    await set_roots(env, [repo.parent])

    r = await env.client.post("/api/opencode/sessions",
                              json={"project_path": str(repo), "worktree": True,
                                    "worktree_name": "ok"})
    assert r.status_code == 200, r.text
    made = Path(r.json()["directory"])
    assert made == repo.parent / f"{repo.name}-ok" and (made / ".git").exists()
    assert "bossman/ok" in _branches(repo)
