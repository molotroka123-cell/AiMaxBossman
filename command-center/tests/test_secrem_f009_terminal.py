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
import subprocess
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


def _docker_available():
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        return subprocess.run([docker, "info"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def test_docker_runtime_proof_marker():
    """Контейнерная часть (RW bind-mount только разрешённых корней) требует docker.
    На этом хосте демон недоступен → NOT_TESTED_ON_THIS_HOST, а не PASS."""
    have_docker = _docker_available()
    if not have_docker:
        pytest.skip("NOT_TESTED_ON_THIS_HOST: docker daemon unavailable — F-009 container "
                    "mount proof deferred to RunPod/owner host")


# --------------------------------------------------------------------------
# AP-001, exfiltration half.
#
# The tests above prove the ROUTE is refused: a cwd outside the owner's roots,
# a `../` climb, a symlink pointing out. What they never did was plant a real
# secret and check that it does not come back. Those are different claims: a
# refusal that still echoes the file's contents in its own error message has
# refused nothing.
#
# So this plants a token outside the authorized scratch root and, for every
# escape shape, requires three things at once — the call is refused, and the
# token appears in NEITHER the model-visible result NOR anywhere the process
# wrote. A tool result is the model's evidence; if the secret is in it, it has
# been exfiltrated regardless of the exit code.
# --------------------------------------------------------------------------

TOKEN = "AP001-CANARY-9f2c41d7e8b64a05-DO-NOT-LEAK"


def _plant(tmp_path):
    """A token OUTSIDE every authorized root, in a file with an ORDINARY name.

    Deliberately not `credentials.env`: a separate policy already refuses
    credential-shaped filenames outright, and planting one would let these tests
    pass on that rule while proving nothing about path containment. `notes.txt`
    can only be refused by the boundary this test is about.
    """
    vault = tmp_path / "ap001_vault"
    vault.mkdir(exist_ok=True)
    secret = vault / "notes.txt"
    secret.write_text(f"API_KEY={TOKEN}\n", encoding="utf-8")
    return vault, secret


def _clean(text) -> bool:
    return TOKEN not in (text or "")


async def test_ap001_a_planted_token_outside_the_roots_is_never_exfiltrated(
        env, tmp_path):
    """Every escape shape: refused, and the token never reaches the model."""
    vault, secret = _plant(tmp_path)
    scratch = (await tt._roots(env.svc))[0]
    scratch.mkdir(parents=True, exist_ok=True)
    stack = await make_stack(env.client)
    ctx = _ctx(env, task_id=stack["task"]["id"], agent=stack["agent"])

    # a symlink planted INSIDE the authorized root, pointing at the vault
    link = scratch / "totally-innocent"
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(vault, link, target_is_directory=True)

    attempts = [
        # read it by naming its directory as cwd
        {"command": f"cat {secret.name}", "cwd": str(vault), "mode": "project_host"},
        # climb out of the authorized root
        {"command": "cat notes.txt",
         "cwd": str(scratch / ".." / ".." / vault.name), "mode": "project_host"},
        # follow a symlink that starts inside the root
        {"command": "cat notes.txt", "cwd": str(link), "mode": "project_host"},
        # sandbox mode must not be a way around any of it
        {"command": f"cat {secret}", "cwd": str(vault), "mode": "sandbox"},
    ]
    try:
        for args in attempts:
            res = await tt._tool_run({**args, "timeout": 20}, ctx)
            assert res.error is True, (args, res.content)
            assert _clean(res.content), f"token reached the model: {args}"
            assert _clean(getattr(res, "detail", "")), args
            # and nothing was started that could still be writing it
            assert not _live(tt._mgr(env.svc)), args
    finally:
        if link.exists() or link.is_symlink():
            link.unlink()
        shutil.rmtree(vault)


async def test_ap001_an_absolute_read_from_an_allowed_cwd_needs_the_container(
        env, tmp_path):
    """The subtle case, and an honest statement of where it is decided.

    cwd is legitimate; the PATH is not. `project_host` deliberately runs on the
    host, so what stops an absolute read there is the approval gate and the
    command policy — not the filesystem. The filesystem boundary is the
    container's RW bind-mount of allowed roots only, and this host has no docker
    daemon, exactly as `test_docker_runtime_proof_marker` already records.

    So this is NOT_TESTED_ON_THIS_HOST rather than a pass. Asserting containment
    here without the runtime that provides it would turn a missing proof into a
    green line, which is the one thing the AP-001 row must not become.
    """
    have_docker = _docker_available()
    if not have_docker:
        pytest.skip("NOT_TESTED_ON_THIS_HOST: docker daemon unavailable — the "
                    "absolute-path containment proof belongs to the container "
                    "mount and is deferred to the owner host")
    vault, secret = _plant(tmp_path)                         # pragma: no cover
    stack = await make_stack(env.client)
    ctx = _ctx(env, task_id=stack["task"]["id"], agent=stack["agent"])
    try:
        # The container itself must be usable: a missing image, dead daemon or
        # blanket refusal cannot masquerade as an isolation proof.
        owned, _ = await tt._resolve_cwd(ctx, {"cwd": "scratch"})
        owned.mkdir(parents=True, exist_ok=True)
        (owned / "container-control.txt").write_text(TOKEN, encoding="utf-8")
        positive = await tt._tool_run(
            {"command": "cat container-control.txt", "cwd": "scratch",
             "mode": "sandbox", "timeout": 20}, ctx)
        assert positive.error is False and TOKEN in positive.content, positive.content
        res = await tt._tool_run(
            {"command": f"cat {secret}", "cwd": "scratch",
             "mode": "sandbox", "timeout": 20}, ctx)
        # terminal.run's contract (tools_terminal._tool_run): a non-zero exit is
        # DATA, error=True only when the command could not run at all. The
        # containment proof is therefore the failed `cat` inside a usable
        # container plus the absent token — not the tool-level error flag.
        assert res.error is False, res.content
        assert res.data.get("exit_code") not in (0, None), "outside file must be inaccessible"
        assert _clean(res.content), "an absolute read exfiltrated the token"
        assert _clean(getattr(res, "detail", ""))
    finally:
        shutil.rmtree(vault)


async def test_ap001_the_canary_is_readable_when_it_is_legitimately_in_scope(
        env, tmp_path):
    """Negative control for the three tests above.

    A terminal that refuses everything, or that strips the token from all
    output, would pass them while being either useless or a liar. Inside the
    authorized root the same file IS readable and the same token DOES come
    back — which is what makes its absence elsewhere evidence of containment
    rather than of blanket filtering.
    """
    scratch = (await tt._roots(env.svc))[0]
    scratch.mkdir(parents=True, exist_ok=True)
    stack = await make_stack(env.client)
    ctx = _ctx(env, task_id=stack["task"]["id"], agent=stack["agent"])
    # Where "scratch" actually resolves is the terminal's business, not this
    # test's guess: ask it, then plant the canary in the directory it named.
    located = await tt._tool_run({"command": "pwd", "cwd": "scratch",
                                  "mode": "project_host", "timeout": 20}, ctx)
    assert located.error is False, located.content
    owned = Path(located.content.strip().splitlines()[-1].strip())
    assert owned.is_dir(), located.content
    (owned / "mine.txt").write_text(f"API_KEY={TOKEN}\n", encoding="utf-8")

    res = await tt._tool_run({"command": "cat mine.txt", "cwd": "scratch",
                              "mode": "project_host", "timeout": 20}, ctx)
    assert res.error is False, res.content
    assert TOKEN in res.content, (
        "the canary is not readable even in scope — the other AP-001 tests would "
        "then prove filtering, not containment")
