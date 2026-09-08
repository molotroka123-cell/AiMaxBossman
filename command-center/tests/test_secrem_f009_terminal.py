"""SECREM F-009 (HIGH) — terminal.run: sandbox больше не «run anywhere».

REPRO (Fable 5.1): mode="sandbox" брал effective_roots=[cwd] — модель называла
ЛЮБОЙ каталог хоста корнем, он монтировался RW в контейнер (`-v cwd:/work`)
на эффекте auto, без approval. Теперь cwd для всех режимов ограничен корнями
владельца + личной scratch-областью; резолв (symlink/../) — ДО авторизации;
одобренный (нормализованный) путь == исполненный путь; сессия принадлежит
задаче. Docker-рантайм на этом хосте недоступен → контейнерная часть
NOT_TESTED_ON_THIS_HOST (см. test_docker_runtime_proof_marker).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from bcc.features import tools_terminal as tt
from bcc.tools import ToolContext
from bcc.v2.terminal_control import TerminalManager, TerminalPolicy

from .helpers import make_stack


def _ctx(env, task_id=1, workspace=None, agent=None):
    return ToolContext(svc=env.svc, task={"id": task_id, "meta": {}}, run_id=1,
                       agent=agent or {}, workspace=workspace)


def _live(mgr):
    return list(mgr.sessions.values())


@pytest.mark.parametrize("mode", ["sandbox", "project_host", "system_admin"])
async def test_repro_cwd_outside_roots_refused_in_every_mode(env, tmp_path, mode):
    """REPRO F-009: cwd вне корней → отказ до запуска (никакого процесса/контейнера)."""
    outside = tmp_path.parent / "secrem_outside"
    outside.mkdir(exist_ok=True)
    try:
        res = await tt._tool_run({"command": "echo pwned", "cwd": str(outside), "mode": mode},
                                 _ctx(env))
        assert res.error is True and "вне разрешённых корней" in res.content, (mode, res.content)
        # ни одной сессии не запущено
        assert not _live(tt._mgr(env.svc))
    finally:
        shutil.rmtree(outside, ignore_errors=True)


async def test_variant_dotdot_and_symlink_escape_resolved_before_authz(env, tmp_path):
    root = env.settings.data_dir
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path.parent / "secrem_target"
    outside.mkdir(exist_ok=True)
    try:
        # ../ из корня
        res = await tt._tool_run({"command": "echo x", "cwd": str(root / ".." / ".." / outside.name),
                                  "mode": "project_host"}, _ctx(env))
        assert res.error is True and "вне разрешённых корней" in res.content
        # symlink внутри корня → наружу
        link = root / "escape"
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(outside, link, target_is_directory=True)
        res = await tt._tool_run({"command": "echo x", "cwd": str(link), "mode": "project_host"},
                                 _ctx(env))
        assert res.error is True and "вне разрешённых корней" in res.content
        # нормализация для approval показывает РЕЗОЛВЛЕННЫЙ путь (цель symlink)
        norm = tt.normalize_run_args({"command": "echo x", "cwd": str(link)})
        assert norm["cwd"] == str(outside.resolve()) and norm["mode"] == "sandbox"
    finally:
        shutil.rmtree(outside, ignore_errors=True)


async def test_inside_root_project_host_runs_and_session_owned_by_task(env):
    # AP-001: the un-configured default root is the scratch subdirectory, not
    # settings.data_dir itself (data_dir holds bcc.db and the UI auth token —
    # it must never be an implicit allowed root; see tools_terminal._roots()).
    # Within scratch, a caller may only use its OWN owner directory (cwd="scratch"
    # resolves to it), matching V2.2 §9's per-agent isolation.
    stack = await make_stack(env.client)          # реальные task/agent — FK terminal_sessions
    tid = stack["task"]["id"]
    res = await tt._tool_run({"command": "echo secrem-ok", "cwd": "scratch",
                              "mode": "project_host", "timeout": 20},
                             _ctx(env, task_id=tid, agent=stack["agent"]))
    assert res.error is False, res.content
    assert "secrem-ok" in res.content
    sess = _live(tt._mgr(env.svc))
    assert sess and all(s.owner == str(tid) for s in sess)
    # чужая задача не может читать/писать/убивать сессию
    sid = sess[-1].id
    other = _ctx(env, task_id=tid + 1000)
    for fn, args in ((tt._tool_status, {"session_id": sid}),
                     (tt._tool_stdin, {"session_id": sid, "text": "x"}),
                     (tt._tool_kill, {"session_id": sid})):
        r = await fn(args, other)
        assert r.error is True, (fn.__name__, r.content)


async def test_manager_start_enforces_roots_before_policy(tmp_path):
    """Уровень менеджера: даже прямой вызов start() с cwd вне allowed_roots — PermissionError,
    процесс не создаётся."""
    mgr = TerminalManager()
    pol = TerminalPolicy(allowed_roots=[tmp_path / "root"], mode="project_host")
    (tmp_path / "root").mkdir()
    with pytest.raises(PermissionError, match="outside allowed roots"):
        await mgr.start("echo x", tmp_path, pol, approved=True)
    assert mgr.sessions == {}


async def test_ap001_default_roots_exclude_data_dir_but_include_scratch(env):
    """AP-001 regression.

    Before the fix, `_roots()` fell back to [settings.data_dir] whenever the
    owner had not configured `terminal.roots` — the SAME directory that holds
    bcc.db and the UI's plaintext auth token. An agent granted terminal.run with
    no owner-configured roots could `cat` that token and call the API directly
    (e.g. PATCH /api/agents to self-grant permissions) with no approval gate at
    all. Bad case: the default root must never be data_dir itself.
    """
    roots = await tt._roots(env.svc)
    assert env.settings.data_dir not in roots, roots
    assert not any(str(r) == str(env.settings.data_dir) for r in roots), roots
    # Legitimate case: the default is still usable — it resolves to a real,
    # writable subdirectory of data_dir (scratch), not "nowhere at all".
    assert len(roots) == 1
    assert roots[0].is_relative_to(env.settings.data_dir)
    assert roots[0].name == "scratch"
    roots[0].mkdir(parents=True, exist_ok=True)
    assert roots[0].is_dir()


async def test_ap001_owner_configured_roots_still_take_priority(env):
    """Legitimate case: once an owner explicitly configures terminal.roots (the
    intended way to grant a real project directory), that configuration is used
    as-is — AP-001 only changes the un-configured fallback, not the override
    path owners rely on."""
    import json as _json

    import sqlalchemy as sa
    from bcc.db import settings_kv

    configured = "/some/owner/configured/project/root"
    async with env.svc.db.session() as s:
        await s.execute(sa.insert(settings_kv).values(
            key=tt.ROOTS_KEY,
            value_enc=env.svc.vault.encrypt(_json.dumps([configured])),
        ))
        await s.commit()
    roots = await tt._roots(env.svc)
    assert [str(r) for r in roots] == [configured]


def test_docker_runtime_proof_marker():
    """Контейнерная часть (RW bind-mount только разрешённых корней) требует docker.
    На этом хосте демон недоступен → NOT_TESTED_ON_THIS_HOST, а не PASS."""
    have_docker = shutil.which("docker") is not None and os.system("docker info >/dev/null 2>&1") == 0
    if not have_docker:
        pytest.skip("NOT_TESTED_ON_THIS_HOST: docker daemon unavailable — F-009 container "
                    "mount proof deferred to RunPod/owner host")
