"""A hung host child must never hang Bossman forever or leave an orphan tree.

Open 1.0 defect "subprocess calls without a timeout / leaving orphans":

* video_factory/ffmpeg.py — render, ffprobe and the `ffmpeg -i` probe fallback
  awaited `proc.communicate()` with no timeout;
* projects/runner.py — a `kind: cmd` project tool (exec and the `sh -c` shell
  template) awaited `proc.communicate()` with no timeout;
* benchmark/engine.py — `git` queries, `git worktree add` and
  `git worktree remove` ran with no timeout;
* sandbox/netguard.py — `nft` ran with no timeout.

The children here are real processes: a Python fake stands in for the binary.
It starts a grandchild (the shape of Git's `cmd\\git.exe` launcher, of
`sh -c 'a | b'`, of ffmpeg helpers), and both write a heartbeat file every
50 ms. "The tree is dead" means neither heartbeat moves any more after the
call returned. The fake lives LIFETIME seconds on its own, so the unfixed code
does not hang the suite forever: it either trips the GUARD (async) or returns
only after LIFETIME (sync) — both fail the elapsed-time assertion.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from bossman import errors
from bossman.benchmark import engine as bench
from bossman.projects import plan as plan_mod
from bossman.projects import runner as pr
from bossman.projects.router import Route
from bossman.sandbox import netguard
from bossman.video_factory import ffmpeg as vf

# Timeout under test (production values are seconds to hours). Windows starts
# processes slower: the grandchild must be up before the timeout fires.
T = 2.0 if os.name == "nt" else 1.0
BOUND = T + (3.0 if os.name == "nt" else 1.5)   # timeout + kill + reap
GUARD = T + 6.0                                 # an unfixed async call trips this
LIFETIME = 9.0                                  # the fake gives up on its own

_BEAT = r'''
import pathlib, sys, time
d = pathlib.Path(sys.argv[1]); name = sys.argv[2]; stop = time.monotonic() + float(sys.argv[3])
d.mkdir(parents=True, exist_ok=True)
n = 0
while time.monotonic() < stop:
    try:
        (d / (name + ".beat")).write_text(str(n))
    except OSError:
        pass
    n += 1
    time.sleep(0.05)
'''

_HUNG_TREE = r'''
import subprocess, sys
beat, markers, lifetime = @BEAT@, @MARKERS@, @LIFETIME@
subprocess.Popen([sys.executable, "-c", beat, markers, "grandchild", lifetime])
sys.argv = ["beat", markers, "child", lifetime]
exec(compile(beat, "<beat>", "exec"), {"__name__": "__main__"})
'''


def hung_tree(tmp_path: Path, name: str = "fake") -> tuple[Path, Path]:
    """A script whose process starts a grandchild; both heartbeat for LIFETIME s."""
    markers = tmp_path / f"{name}-beats"
    script = tmp_path / f"{name}.py"
    script.write_text(_HUNG_TREE.replace("@BEAT@", repr(_BEAT))
                      .replace("@MARKERS@", repr(str(markers)))
                      .replace("@LIFETIME@", repr(str(LIFETIME))), encoding="utf-8")
    return script, markers


def quick(tmp_path: Path, name: str, body: str) -> Path:
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8")
    return script


def as_executable(script: Path) -> str:
    """One argv[0] that runs `script`. POSIX: a shebang file. Windows: a .cmd
    launcher (cmd.exe -> python), a real launcher tree like Git's cmd\\git.exe."""
    if os.name == "nt":
        launcher = script.with_suffix(".cmd")
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = script.with_suffix("")
        launcher.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv[0] = {str(script)!r}\n"
                            f"runpy.run_path({str(script)!r}, run_name='__main__')\n", encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


def redirect_popen(monkeypatch, route) -> None:
    """Swap ONE host binary for a Python fake at the Popen boundary. Everything
    else stays real: subprocess.run/check_output, run_tree, pipes, killpg or
    the Job Object. `route(argv)` returns the replacement argv or None."""
    real = subprocess.Popen

    class Redirected(real):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            if isinstance(args, (list, tuple)) and args:
                new = route([str(x) for x in args])
                if new is not None:
                    args = new
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Redirected)


def _read(p: Path) -> str | None:
    try:
        return p.read_text()
    except OSError:
        return None


def tree_alive(markers: Path) -> list[str]:
    beats = {n: markers / f"{n}.beat" for n in ("child", "grandchild")}
    missing = [n for n, p in beats.items() if not p.exists()]
    assert not missing, f"{missing} never started: the tree was not built before the timeout"
    first = {n: _read(p) for n, p in beats.items()}
    time.sleep(0.6)
    return [n for n, p in beats.items() if _read(p) != first[n]]


def assert_tree_dead(markers: Path) -> None:
    alive = tree_alive(markers)
    assert not alive, f"survived the timeout (orphans): {alive}"


# ---------------------------------------------------------------- video_factory/ffmpeg.py


async def test_video_factory_render_timeout_is_a_provider_failure_and_kills_the_tree(tmp_path, monkeypatch):
    script, beats = hung_tree(tmp_path)
    monkeypatch.setattr(vf, "ffmpeg_bin", lambda: as_executable(script))
    monkeypatch.setattr(vf, "RENDER_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    with pytest.raises(errors.VideoProviderFailed) as ei:
        await asyncio.wait_for(vf.run_testsrc(tmp_path / "take-001.mp4", 2.0), GUARD)
    elapsed = time.monotonic() - started

    assert "timeout" in ei.value.detail, ei.value.detail
    assert elapsed < BOUND, f"render held the scene for {elapsed:.1f}s"
    assert_tree_dead(beats)


async def test_video_factory_ffprobe_timeout_is_invalid_output_not_a_hang(tmp_path, monkeypatch):
    clip = tmp_path / "take-001.mp4"
    clip.write_bytes(b"\x00" * 64)
    script, beats = hung_tree(tmp_path)
    monkeypatch.setattr(vf, "ffprobe_bin", lambda: as_executable(script))
    monkeypatch.setattr(vf, "PROBE_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    with pytest.raises(errors.VideoInvalidOutput):
        await asyncio.wait_for(vf.validate_video_output(clip), GUARD)
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


async def test_video_factory_ffmpeg_probe_fallback_timeout_is_bounded(tmp_path, monkeypatch):
    clip = tmp_path / "take-001.mp4"
    clip.write_bytes(b"\x00" * 64)
    script, beats = hung_tree(tmp_path)
    monkeypatch.setattr(vf, "ffprobe_bin", lambda: None)
    monkeypatch.setattr(vf, "ffmpeg_bin", lambda: as_executable(script))
    monkeypatch.setattr(vf, "PROBE_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    assert await asyncio.wait_for(vf.probe_media(clip), GUARD) == (0.0, False)
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


async def test_video_factory_negative_control_fast_children_keep_their_results(tmp_path, monkeypatch):
    """Success path unchanged: a render that finishes, ffprobe JSON, `ffmpeg -i` text."""
    render = quick(tmp_path, "render", "import sys; open(sys.argv[-1], 'wb').write(b'x' * 32)\n")
    monkeypatch.setattr(vf, "ffmpeg_bin", lambda: as_executable(render))
    out = tmp_path / "scene" / "take-001.mp4"
    assert await vf.run_testsrc(out, 1.0) == str(out) and out.stat().st_size == 32

    probe = quick(tmp_path, "probe", "import json; print(json.dumps({'format': {'duration': '2.5'}, "
                                     "'streams': [{'codec_type': 'video'}]}))\n")
    monkeypatch.setattr(vf, "ffprobe_bin", lambda: as_executable(probe))
    assert await vf.probe_media(out) == (2.5, True)

    info = quick(tmp_path, "info", "import sys; sys.stderr.write('  Duration: 00:00:02.00, start: 0\\n"
                                   "    Stream #0:0: Video: h264\\n'); sys.exit(1)\n")
    monkeypatch.setattr(vf, "ffprobe_bin", lambda: None)
    monkeypatch.setattr(vf, "ffmpeg_bin", lambda: as_executable(info))
    assert await vf.probe_media(out) == (2.0, True)

    failing = quick(tmp_path, "failing", "import sys; sys.exit(3)\n")
    monkeypatch.setattr(vf, "ffmpeg_bin", lambda: as_executable(failing))
    with pytest.raises(errors.VideoProviderFailed, match="ffmpeg exit 3"):
        await vf.run_testsrc(tmp_path / "scene" / "take-002.mp4", 1.0)


# ---------------------------------------------------------------- projects/runner.py


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod.settings, "projects_dir", tmp_path / "projects")
    slug = "hung-tool"
    (tmp_path / "projects" / slug).mkdir(parents=True)

    async def no_db(*a, **k):
        return "OK"

    monkeypatch.setattr(pr.db, "execute", no_db)
    return slug


def _task(outputs=()) -> plan_mod.PlanTask:
    return plan_mod.PlanTask(id="1", name="clip", tool="t2v", stage="s1", outputs=list(outputs))


def _q(path) -> str:
    return shlex.quote(str(path))


async def test_projects_runner_cmd_timeout_fails_the_task_and_kills_the_tool_tree(tmp_path, monkeypatch, project):
    script, beats = hung_tree(tmp_path)
    monkeypatch.setattr(pr, "CMD_TIMEOUT_S", T, raising=False)
    route = Route("wan22_local", {"kind": "cmd", "where": "home",
                                  "cmd": f"{_q(sys.executable)} {_q(script)}"}, "test")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="тайм"):
        await asyncio.wait_for(pr._execute(project, _task(), route, None), GUARD)
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


async def test_projects_runner_shell_template_timeout_kills_the_shell_tree(tmp_path, monkeypatch, project):
    """The one `sh -c '<script>'` template (piper_local): shell -> tool -> helper."""
    script, beats = hung_tree(tmp_path)
    monkeypatch.setattr(pr, "CMD_TIMEOUT_S", T, raising=False)
    inner = f'"{sys.executable}" "{script}"'
    route = Route("piper_local", {"kind": "cmd", "where": "home", "cmd": f"sh -c '{inner}'"}, "test")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="тайм"):
        await asyncio.wait_for(pr._execute(project, _task(), route, None), GUARD)
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


async def test_projects_runner_stop_cancel_kills_the_tool_tree(tmp_path, project):
    """Owner STOP cancels the project task: the tool tree must not outlive it."""
    script, beats = hung_tree(tmp_path)
    route = Route("wan22_local", {"kind": "cmd", "where": "home",
                                  "cmd": f"{_q(sys.executable)} {_q(script)}"}, "test")
    task = asyncio.ensure_future(pr._execute(project, _task(), route, None))
    deadline = time.monotonic() + GUARD
    while not all((beats / f"{n}.beat").exists() for n in ("child", "grandchild")):
        assert time.monotonic() < deadline, "the tool tree never started"
        await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, GUARD)
    assert_tree_dead(beats)


async def test_projects_runner_negative_control_fast_tool_keeps_artifacts_and_exit_codes(tmp_path, project):
    writer = quick(tmp_path, "writer", "import pathlib, sys; p = pathlib.Path(sys.argv[1]); "
                                       "p.parent.mkdir(parents=True, exist_ok=True); p.write_text('ok')\n")
    route = Route("flux_local", {"kind": "cmd", "where": "home",
                                 "cmd": f"{_q(sys.executable)} {_q(writer)} {{out}}"}, "test")
    artifacts, cost = await pr._execute(project, _task(["assets/frame.png"]), route, None)
    assert artifacts == ["assets/frame.png"] and cost == 0.0

    failing = quick(tmp_path, "failing", "import sys; print('boom'); sys.exit(7)\n")
    route = Route("flux_local", {"kind": "cmd", "where": "home",
                                 "cmd": f"{_q(sys.executable)} {_q(failing)}"}, "test")
    with pytest.raises(RuntimeError, match="код 7: boom"):
        await pr._execute(project, _task(), route, None)


# ---------------------------------------------------------------- benchmark/engine.py


def _scratch_tempdir(tmp_path: Path, monkeypatch) -> Path:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return scratch


def _git_to(script: Path, when=lambda argv: True):
    def route(argv):
        if Path(argv[0]).stem.lower() == "git" and when(argv):
            return [sys.executable, str(script), *argv[1:]]
        return None
    return route


def test_benchmark_git_query_timeout_is_unknown_and_kills_git(tmp_path, monkeypatch):
    script, beats = hung_tree(tmp_path)
    redirect_popen(monkeypatch, _git_to(script))
    monkeypatch.setattr(bench, "GIT_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    assert bench._git("rev-parse", "HEAD") == "unknown"
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


def test_benchmark_isolated_worktree_add_timeout_raises_and_kills_git(tmp_path, monkeypatch):
    head = bench._git("rev-parse", "HEAD")
    assert len(head) == 40, "this suite runs from a git checkout"
    script, beats = hung_tree(tmp_path)
    redirect_popen(monkeypatch, _git_to(script, lambda a: a[1:3] == ["worktree", "add"]))
    monkeypatch.setattr(bench, "GIT_WORKTREE_TIMEOUT_S", T, raising=False)
    scratch = _scratch_tempdir(tmp_path, monkeypatch)

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bench.run_isolated(head, "smoke", tmp_path / "out")
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)
    assert not list(scratch.iterdir()), "the worktree temp dir leaked"


def test_benchmark_isolated_worktree_remove_timeout_is_bounded(tmp_path, monkeypatch):
    """`worktree add` succeeds (fake), the HEAD check refuses, and the cleanup
    `worktree remove` hangs: the refusal must still come back promptly."""
    head = bench._git("rev-parse", "HEAD")
    assert len(head) == 40, "this suite runs from a git checkout"
    add = quick(tmp_path, "add", "import pathlib, sys; wt = pathlib.Path(sys.argv[-2]); wt.mkdir(parents=True); "
                                 "(wt / '.git').write_text('gitdir: ' + str(wt / 'nowhere'))\n")
    script, beats = hung_tree(tmp_path)

    def route(argv):
        if Path(argv[0]).stem.lower() != "git" or argv[1:2] != ["worktree"]:
            return None
        return [sys.executable, str(add if argv[2] == "add" else script), *argv[1:]]

    redirect_popen(monkeypatch, route)
    monkeypatch.setattr(bench, "GIT_WORKTREE_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    with pytest.raises(bench.ShaMismatch):
        bench.run_isolated(head, "smoke", tmp_path / "out")
    assert time.monotonic() - started < BOUND
    assert_tree_dead(beats)


def test_benchmark_negative_control_real_git_answers_and_failures_stay_typed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                  "--allow-empty", "-m", "c"]):
        subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                         text=True).stdout.strip()
    assert bench._git("rev-parse", "HEAD", cwd=repo) == sha
    assert bench._git("rev-parse", "--verify", "nope^{commit}", cwd=repo) == "unknown"

    failing = quick(tmp_path, "failing", "import sys; sys.stderr.write('fatal: busy'); sys.exit(128)\n")
    redirect_popen(monkeypatch, _git_to(failing, lambda a: a[1:3] == ["worktree", "add"]))
    scratch = _scratch_tempdir(tmp_path, monkeypatch)
    with pytest.raises(subprocess.CalledProcessError) as ei:
        bench.run_isolated(bench._git("rev-parse", "HEAD"), "smoke", tmp_path / "out")
    assert ei.value.returncode == 128 and "busy" in ei.value.stderr
    assert not list(scratch.iterdir()), "the worktree temp dir leaked"


# ---------------------------------------------------------------- sandbox/netguard.py


def _nft_to(script: Path):
    def route(argv):
        if Path(argv[0]).stem.lower() == "nft":
            return [sys.executable, str(script), *argv[1:]]
        return None
    return route


def test_netguard_nft_timeout_is_a_failed_call_and_kills_nft(tmp_path, monkeypatch):
    script, beats = hung_tree(tmp_path)
    redirect_popen(monkeypatch, _nft_to(script))
    monkeypatch.setattr(netguard, "NFT_TIMEOUT_S", T, raising=False)

    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as ei:
        netguard._nft("add", "table", "inet", "bossman_sbx_probe")
    assert time.monotonic() - started < BOUND
    assert ei.value.returncode != 0
    assert_tree_dead(beats)


def test_netguard_remove_and_rules_return_when_nft_hangs(tmp_path, monkeypatch):
    script, beats = hung_tree(tmp_path)
    redirect_popen(monkeypatch, _nft_to(script))
    monkeypatch.setattr(netguard, "NFT_TIMEOUT_S", T, raising=False)
    lock = netguard.EgressLockdown("sbx_hung")
    lock.applied = True

    started = time.monotonic()
    lock.remove()
    assert time.monotonic() - started < BOUND and lock.applied is False
    assert_tree_dead(beats)

    started = time.monotonic()
    assert lock.rules() == ""
    assert time.monotonic() - started < BOUND


def test_netguard_negative_control_nft_output_and_failures_unchanged(tmp_path, monkeypatch):
    ok = quick(tmp_path, "ok", "import sys; print('table inet ' + sys.argv[-1])\n")
    redirect_popen(monkeypatch, _nft_to(ok))
    done = netguard._nft("list", "table", "inet", "bossman_sbx_x", check=False)
    assert done.returncode == 0 and done.stdout.strip() == "table inet bossman_sbx_x"

    bad = quick(tmp_path, "bad", "import sys; sys.stderr.write('Operation not permitted'); sys.exit(1)\n")
    redirect_popen(monkeypatch, _nft_to(bad))
    with pytest.raises(subprocess.CalledProcessError) as ei:
        netguard._nft("add", "table", "inet", "bossman_sbx_x")
    assert ei.value.returncode == 1 and "not permitted" in ei.value.stderr
