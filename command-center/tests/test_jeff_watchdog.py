"""Jeff 1.1 survivability: watchdog policy (fake clock, fake children) and real
kernel-lock behaviour of the poller (kill -> restart -> exactly one poller).
No Telegram, no Ollama, no network, no wall-clock races."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import bcc
from bcc.pit import heartbeat as hb
from bcc.pit.heartbeat import Watchdog, backoff_delay
from bcc.pit.runtime import STOP_FLAG


# -- backoff -------------------------------------------------------------------------
def test_backoff_grows_exponentially_is_capped_and_jittered():
    lows = [backoff_delay(n, rng=lambda: 0.0) for n in range(12)]
    highs = [backoff_delay(n, rng=lambda: 1.0) for n in range(12)]
    assert lows[0] == 1.0 and highs[0] == 2.0
    assert lows[1] == 2.0 and highs[1] == 4.0
    assert all(b >= a for a, b in zip(lows, lows[1:]))
    assert max(highs) == hb.BACKOFF_CAP_SECONDS
    assert backoff_delay(500, rng=lambda: 1.0) == hb.BACKOFF_CAP_SECONDS
    assert backoff_delay(3, rng=lambda: 0.0) < backoff_delay(3, rng=lambda: 0.9)


# -- fake world ---------------------------------------------------------------------------
class World:
    def __init__(self, home: Path):
        self.now = 1000.0
        self.home = home
        self.children: list[FakeChild] = []
        self.plan: list[dict] = []           # one entry per spawn: lifetime/code/healthy
        self.max_alive = 0
        self.foreign_lock = False
        self.sleeps: list[float] = []
        self.on_sleep = None

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(seconds, 0.001)
        if self.on_sleep:
            self.on_sleep(self)

    def alive(self) -> list["FakeChild"]:
        return [c for c in self.children if c.poll() is None]

    def is_running(self, _home) -> bool:
        return self.foreign_lock or bool(self.alive())

    def spawn(self) -> "FakeChild":
        spec = self.plan.pop(0) if self.plan else {"lifetime": 10 ** 9, "code": 0, "healthy": True}
        child = FakeChild(self, 4000 + len(self.children), spec)
        self.children.append(child)
        self.max_alive = max(self.max_alive, len(self.alive()))
        return child

    def beat(self, _home):
        for child in self.alive():
            if child.spec.get("healthy", True):
                return {"pid": child.pid, "availability": "up"}
        return {"pid": -1, "availability": "stale"}


class FakeChild:
    def __init__(self, world: World, pid: int, spec: dict):
        self.world, self.pid, self.spec = world, pid, spec
        self.born = world.now
        self.killed: str | None = None
        self.exited: int | None = None
        self.stop_seen_exit = spec.get("exit_on_stop", True)

    def poll(self):
        if self.exited is None:
            if self.killed:
                self.exited = -15 if self.killed == "terminate" else -9
            elif self.world.now >= self.born + self.spec["lifetime"]:
                self.exited = self.spec["code"]
            elif self.stop_seen_exit and (self.world.home / STOP_FLAG).exists():
                self.exited = 0               # the real poller exits itself on stop.flag
        return self.exited

    def terminate(self):
        self.killed = "terminate"

    def kill(self):
        self.killed = "kill"


def make_dog(tmp_path, world: World, **kw) -> Watchdog:
    kw.setdefault("rng", lambda: 0.0)
    return Watchdog(tmp_path, world.spawn, is_running=world.is_running, beat_reader=world.beat,
                    clock=world.clock, sleep=world.sleep, **kw)


def test_crashed_poller_is_restarted_with_growing_backoff_and_never_two_at_once(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 5, "code": 1}, {"lifetime": 5, "code": 1},
                  {"lifetime": 5, "code": 1}, {"lifetime": 10 ** 9, "code": 0}]
    dog = make_dog(tmp_path, world)

    def stop_after_fourth(w: World):
        if len(w.children) == 4 and w.now > w.children[3].born + 30:
            (tmp_path / STOP_FLAG).write_text("x")

    world.on_sleep = stop_after_fourth
    assert dog.run() == "stopped"
    assert len(world.children) == 4 and dog.restarts == 3
    assert world.max_alive == 1
    assert dog.delays == [1.0, 2.0, 4.0]                  # rng=0 -> lower bound of each ceiling
    assert not world.alive() and not (tmp_path / STOP_FLAG).exists()


def test_backoff_resets_after_a_stable_run(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 5, "code": 1}, {"lifetime": 500, "code": 1},
                  {"lifetime": 5, "code": 1}, {"lifetime": 10 ** 9, "code": 0}]
    dog = make_dog(tmp_path, world)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if len(w.children) == 4 and w.now > w.children[3].born + 10 else None
    assert dog.run() == "stopped"
    assert dog.delays == [1.0, 1.0, 2.0]                  # the stable 500 s run restarted the ladder


def test_hung_poller_with_stale_heartbeat_is_killed_and_replaced(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 10 ** 9, "code": 0, "healthy": False}]
    dog = make_dog(tmp_path, world, startup_grace=30)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if len(w.children) == 2 and w.now > w.children[1].born + 10 else None
    assert dog.run() == "stopped"
    assert world.children[0].killed in {"terminate", "kill"}
    assert len(world.children) == 2 and world.max_alive == 1


def test_healthy_poller_is_left_alone_after_the_startup_grace(tmp_path):
    world = World(tmp_path)
    dog = make_dog(tmp_path, world, startup_grace=30)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if w.now > 1000 + 600 else None
    assert dog.run() == "stopped"
    assert len(world.children) == 1 and dog.restarts == 0


def test_stop_flag_ends_watchdog_and_poller_without_restart(tmp_path):
    world = World(tmp_path)
    dog = make_dog(tmp_path, world)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if w.now > 1010 else None
    assert dog.run() == "stopped"
    assert len(world.children) == 1 and not world.alive()
    assert json.loads((tmp_path / "watchdog" / "state.json").read_text())["state"] == "stopped"


def test_stop_escalates_to_terminate_when_poller_ignores_the_flag(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 10 ** 9, "code": 0, "exit_on_stop": False}]
    dog = make_dog(tmp_path, world, stop_grace=5)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if w.now > 1010 else None
    assert dog.run() == "stopped"
    assert world.children[0].killed == "terminate" and not world.alive()


def test_clean_exit_is_not_restarted(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 5, "code": 0}]
    assert make_dog(tmp_path, world).run() == "exited"
    assert len(world.children) == 1


def test_foreign_lock_holder_is_never_duplicated(tmp_path):
    world = World(tmp_path)
    world.foreign_lock = True
    dog = make_dog(tmp_path, world)
    world.on_sleep = lambda w: (tmp_path / STOP_FLAG).write_text("x") if w.now > 1100 else None
    assert dog.run() == "stopped"
    assert world.children == []


def test_permanent_config_failure_gives_up_instead_of_looping(tmp_path):
    world = World(tmp_path)
    world.plan = [{"lifetime": 1, "code": 2} for _ in range(20)]
    dog = make_dog(tmp_path, world, max_fatal=3)
    assert dog.run() == "fatal"
    assert len(world.children) == 3


def test_stale_stop_flag_from_a_dead_session_does_not_block_start(tmp_path):
    (tmp_path / STOP_FLAG).write_text("old")
    world = World(tmp_path)
    world.plan = [{"lifetime": 5, "code": 0}]
    assert make_dog(tmp_path, world).run() == "exited"
    assert len(world.children) == 1


def test_watchdog_does_not_orphan_the_poller_on_interrupt(tmp_path):
    world = World(tmp_path)
    dog = make_dog(tmp_path, world, stop_grace=3)

    def boom(w: World):
        if w.now > 1010:
            w.on_sleep = None
            raise KeyboardInterrupt

    world.on_sleep = boom
    with pytest.raises(KeyboardInterrupt):
        dog.run()
    assert not world.alive()


def test_cmd_stop_reaches_a_watchdog_that_is_between_poller_restarts(tmp_path):
    from bcc.pit import cli
    from bcc.pit.config import config_path, pit_home
    from bcc.telegram_companion.store import single_instance

    home = pit_home(tmp_path)
    with single_instance(home / hb.WATCHDOG_DIR):
        assert cli.cmd_stop(config_path(tmp_path)) == 0
        assert (home / STOP_FLAG).exists()
    (home / STOP_FLAG).unlink()
    assert cli.cmd_stop(config_path(tmp_path)) == 0
    assert not (home / STOP_FLAG).exists()


# -- real kernel locks: kill -> restart -> exactly one poller ---------------------------------
HOLDER = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    from bcc.telegram_companion.store import single_instance
    from bcc.telegram_companion.config import CompanionError
    home = Path(sys.argv[1])
    try:
        with single_instance(home):
            print("READY", flush=True)
            sys.stdin.read()
    except CompanionError as exc:
        print("REFUSED " + str(exc), flush=True)
        sys.exit(3)
''')


def _env() -> dict:
    root = Path(bcc.__file__).resolve().parents[1]
    core = root.parent / "bossman-core"
    extra = os.pathsep.join(str(p) for p in (root, core) if p.exists())
    return {**os.environ, "PYTHONPATH": extra + os.pathsep + os.environ.get("PYTHONPATH", "")}


def _holder(home: Path, script: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(script), str(home)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=_env())


def _first_line(proc: subprocess.Popen) -> str:
    return (proc.stdout.readline() or "").strip()


@pytest.fixture()
def holder_script(tmp_path):
    script = tmp_path / "holder.py"
    script.write_text(HOLDER, encoding="utf-8")
    return script


def test_second_poller_is_refused_while_the_first_lives(tmp_path, holder_script):
    home = tmp_path / "pit"
    first = _holder(home, holder_script)
    try:
        assert _first_line(first) == "READY"
        second = _holder(home, holder_script)
        assert _first_line(second).startswith("REFUSED")
        assert second.wait(timeout=30) == 3
    finally:
        first.kill()
        first.wait(timeout=30)


def test_killed_poller_leaves_no_stale_lock_and_restart_yields_exactly_one(tmp_path, holder_script):
    from bcc.pit.cli import _is_running

    home = tmp_path / "pit"
    first = _holder(home, holder_script)
    assert _first_line(first) == "READY"
    assert (home / "poller.json").exists() and _is_running(home)
    first.kill()                                   # crash: no cleanup code runs
    first.wait(timeout=30)
    deadline = time.monotonic() + 30
    while _is_running(home) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _is_running(home)                   # stale poller.json/lock file did not stick
    replacement = _holder(home, holder_script)
    intruder = None
    try:
        assert _first_line(replacement) == "READY"
        intruder = _holder(home, holder_script)
        assert _first_line(intruder).startswith("REFUSED")
        assert intruder.wait(timeout=30) == 3
        assert _is_running(home)
    finally:
        for proc in (replacement, intruder):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=30)


def test_token_lock_is_released_by_a_killed_poller(tmp_path, monkeypatch):
    from bcc.pit.bot_guard import token_poller_lock
    from bcc.telegram_companion.config import CompanionError

    monkeypatch.setenv("BOSSMAN_TELEGRAM_POLLER_LOCK_DIR", str(tmp_path / "locks"))
    token = "fake-" + "token-for-lock-test"
    script = tmp_path / "tok.py"
    script.write_text(textwrap.dedent('''
        import os, sys
        from bcc.pit.bot_guard import token_poller_lock
        with token_poller_lock(sys.argv[1]):
            print("READY", flush=True)
            sys.stdin.read()
    '''), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), token], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True,
                            env={**_env(), "BOSSMAN_TELEGRAM_POLLER_LOCK_DIR": str(tmp_path / "locks")})
    try:
        assert _first_line(proc) == "READY"
        with pytest.raises(CompanionError, match="ANOTHER_POLLER"):
            with token_poller_lock(token):
                pass
    finally:
        proc.kill()
        proc.wait(timeout=30)
    deadline = time.monotonic() + 30
    while True:
        try:
            with token_poller_lock(token):
                break
        except CompanionError:
            assert time.monotonic() < deadline
            time.sleep(0.05)


LOOPER = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    from bcc.telegram_companion.store import single_instance
    from bcc.telegram_companion.config import CompanionError
    home = Path(sys.argv[1])
    try:
        with single_instance(home):
            while not (home / "stop.flag").exists():
                time.sleep(0.05)
    except CompanionError:
        sys.exit(3)
''')


def test_real_processes_kill_then_watchdog_restarts_exactly_one_poller(tmp_path):
    import threading

    home = tmp_path / "pit"
    home.mkdir()
    script = tmp_path / "looper.py"
    script.write_text(LOOPER, encoding="utf-8")
    procs: list[subprocess.Popen] = []

    def spawn():
        proc = subprocess.Popen([sys.executable, str(script), str(home)], env=_env())
        procs.append(proc)
        return proc

    dog = Watchdog(home, spawn, poll_interval=0.05, startup_grace=10 ** 6, rng=lambda: 0.0)
    result: list[str] = []
    thread = threading.Thread(target=lambda: result.append(dog.run()), daemon=True)

    def holder_pid(after: int | None = None) -> int:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                pid = json.loads((home / "poller.json").read_text())["pid"]
                if pid != after:
                    return pid
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.05)
        raise AssertionError("no poller came up")

    thread.start()
    try:
        first = holder_pid()
        next(p for p in procs if p.pid == first).kill()          # crash the poller
        second = holder_pid(after=first)
        assert second != first
        live = [p for p in procs if p.poll() is None]
        assert [p.pid for p in live] == [second]                 # exactly one poller
    finally:
        (home / "stop.flag").write_text("x")
        thread.join(timeout=60)
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    assert result == ["stopped"]
    assert all(p.poll() is not None for p in procs)
