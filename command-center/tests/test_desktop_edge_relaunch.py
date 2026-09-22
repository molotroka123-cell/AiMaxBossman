"""DESK-EDGE-RELAUNCH: браузер, перезапускающий сам себя (msedge.exe у владельца).

Порождённый лаунчером процесс сразу выходит кодом 0, а «окно» держит отдельный
отсоединённый процесс с тем же ``--user-data-dir``. Всё на настоящих процессах,
код под тестом не подменяется.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from bcc import desktop

# Держатель профиля: argv несёт --user-data-dir (или профиль в env — для проверки lockfile),
# держит <профиль>/lockfile открытым, пишет свой pid и живёт заданное время.
HOLDER = textwrap.dedent("""
    import os, sys, time, pathlib
    life = float(sys.argv[1]); pidfile = sys.argv[2]
    flags = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--user-data-dir=")]
    prof = pathlib.Path(flags[0] if flags else os.environ["FAKE_PROFILE"])
    prof.mkdir(parents=True, exist_ok=True)
    pathlib.Path(pidfile).write_text(str(os.getpid()))
    with open(prof / "lockfile", "w") as fh:
        fh.write("x"); fh.flush()
        time.sleep(life)
""")

# Запускаемый «браузер»: отсоединённо стартует держателя (сразу или через delay,
# через промежуточный процесс без профиля в argv) и выходит с кодом exit_code.
LAUNCHER = textwrap.dedent("""
    import json, os, subprocess, sys
    life, delay, exit_code, mode, holder, pidfile = sys.argv[1:7]
    rest = sys.argv[7:]
    flags = (0x00000008 | 0x00000200) if os.name == "nt" else 0
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              close_fds=True, creationflags=flags, start_new_session=(os.name != "nt"))
    if float(life) > 0:
        prof = [a.split("=", 1)[1] for a in rest if a.startswith("--user-data-dir=")][0]
        env = dict(os.environ, FAKE_PROFILE=prof)
        argv = [sys.executable, holder, life, pidfile]
        if mode == "argv":
            argv += rest
        if float(delay) > 0:
            # Промежуточный процесс без профиля в argv: argv держателя идёт через env.
            env["FAKE_ARGV"] = json.dumps(argv)
            code = ("import json, os, subprocess, time; time.sleep(%s); "
                    "subprocess.Popen(json.loads(os.environ['FAKE_ARGV']))" % delay)
            subprocess.Popen([sys.executable, "-c", code], env=env, **kw)
        else:
            subprocess.Popen(argv, env=env, **kw)
    sys.exit(int(exit_code))
""")


def _fake_browser(tmp: Path, *, life: float, delay: float = 0.0, exit_code: int = 0,
                  mode: str = "argv") -> tuple[str, Path]:
    (tmp / "holder.py").write_text(HOLDER, encoding="utf-8")
    (tmp / "launcher.py").write_text(LAUNCHER, encoding="utf-8")
    pidfile = tmp / "holder.pid"
    head = [life, delay, exit_code, mode, tmp / "holder.py", pidfile]
    if os.name == "nt":
        script = tmp / "fakebrowser.cmd"
        args = " ".join(f'"{a}"' for a in head)
        script.write_text(f'@"{sys.executable}" "{tmp / "launcher.py"}" {args} %*\r\n', encoding="ascii")
    else:
        script = tmp / "fakebrowser.sh"
        args = " ".join(f"'{a}'" for a in head)
        script.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{tmp / 'launcher.py'}' {args} \"$@\"\n",
                          encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), pidfile


def _kill_pidfile(pidfile: Path) -> None:
    import psutil

    try:
        psutil.Process(int(pidfile.read_text())).kill()
    except (OSError, ValueError, psutil.Error):
        pass


def _launch(browser: str, profile: Path, **kw) -> tuple[int, float, list[str]]:
    lines: list[str] = []
    t0 = time.monotonic()
    code = desktop.launch_window(browser, "http://127.0.0.1:9/", profile, log=lines.append, **kw)
    return code, time.monotonic() - t0, lines


def test_survivor_on_same_profile_is_waited_for(tmp_path):
    browser, pidfile = _fake_browser(tmp_path, life=6.0)
    try:
        code, took, lines = _launch(browser, tmp_path / "prof")
    finally:
        _kill_pidfile(pidfile)
    assert code == 0
    assert took >= 5.0, f"вернулись через {took:.1f} c, пока наследник держал профиль"
    assert took < 30.0
    assert any("browser-self-relaunch takeover" in ln for ln in lines), lines
    assert any("browser-self-relaunch released" in ln for ln in lines), lines


def test_no_survivor_returns_quickly_with_old_behaviour(tmp_path):
    browser, _ = _fake_browser(tmp_path, life=0.0)
    code, took, lines = _launch(browser, tmp_path / "prof")
    assert code == 0
    assert took < desktop.RELAUNCH_DISCOVERY_S + 4.0, f"никто не держит профиль, а ждали {took:.1f} c"
    assert any("browser-self-relaunch none" in ln for ln in lines), lines


def test_foreign_browser_on_other_profile_is_not_adopted_nor_killed(tmp_path):
    other = tmp_path / "prof-other"          # общий префикс с нашим профилем — не совпадение
    (tmp_path / "holder.py").write_text(HOLDER, encoding="utf-8")
    foreign = subprocess.Popen([sys.executable, str(tmp_path / "holder.py"), "30",
                                str(tmp_path / "foreign.pid"), f"--user-data-dir={other}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        browser, _ = _fake_browser(tmp_path, life=0.0)
        code, took, lines = _launch(browser, tmp_path / "prof")
        assert code == 0
        assert took < desktop.RELAUNCH_DISCOVERY_S + 4.0
        assert not any("takeover" in ln for ln in lines), lines
        assert foreign.poll() is None, "браузер на чужом профиле трогать нельзя"
    finally:
        foreign.kill()
        foreign.wait()


def test_discovery_is_bounded_when_the_browser_appears_too_late(tmp_path):
    """Наследник, появившийся после окна поиска, не подвешивает лаунчер."""
    browser, pidfile = _fake_browser(tmp_path, life=20.0, delay=desktop.RELAUNCH_DISCOVERY_S + 5.0)
    try:
        code, took, lines = _launch(browser, tmp_path / "prof")
    finally:
        time.sleep(0.1)
    assert code == 0
    assert took < desktop.RELAUNCH_DISCOVERY_S + 4.0, f"поиск наследника не ограничен: {took:.1f} c"
    # Убрать запоздавший процесс, когда он появится.
    deadline = time.monotonic() + desktop.RELAUNCH_DISCOVERY_S + 15
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.2)
    _kill_pidfile(pidfile)


def test_nonzero_exit_is_never_adopted(tmp_path):
    """Краш с кодом != 0 — это «не открылось», даже если профиль кем-то занят."""
    browser, pidfile = _fake_browser(tmp_path, life=20.0, exit_code=3)
    try:
        code, took, lines = _launch(browser, tmp_path / "prof")
    finally:
        _kill_pidfile(pidfile)
    assert code == 3
    assert took < 5.0
    assert not lines


@pytest.mark.skipif(os.name != "nt", reason="lockfile Chromium на Windows")
def test_held_lockfile_alone_marks_the_profile_as_busy(tmp_path):
    """Командная строка наследника недоступна (профиль не в argv) — хватает занятого lockfile."""
    browser, pidfile = _fake_browser(tmp_path, life=5.0, mode="env")
    try:
        code, took, lines = _launch(browser, tmp_path / "prof")
    finally:
        _kill_pidfile(pidfile)
    assert code == 0
    assert took >= 4.0, f"занятый lockfile проигнорирован: {took:.1f} c"
    assert any("lockfile=held" in ln for ln in lines), lines


@pytest.mark.skipif(os.name != "nt", reason="lockfile Chromium на Windows")
def test_stale_lockfile_is_not_a_holder(tmp_path):
    profile = tmp_path / "prof"
    profile.mkdir()
    (profile / "lockfile").write_text("x", encoding="utf-8")   # остался от прошлого краша
    assert desktop._profile_lock_held(profile) is False
    assert (profile / "lockfile").exists(), "проба замка не имеет права удалять файл"


def test_unready_takeover_window_is_closed_only_on_our_profile(tmp_path):
    other = tmp_path / "prof-other"
    (tmp_path / "holder.py").write_text(HOLDER, encoding="utf-8")
    foreign = subprocess.Popen([sys.executable, str(tmp_path / "holder.py"), "30",
                                str(tmp_path / "foreign.pid"), f"--user-data-dir={other}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser, pidfile = _fake_browser(tmp_path, life=30.0)
    try:
        code, took, _ = _launch(browser, tmp_path / "prof", timeout=4.0, ready=lambda: False)
        assert code == desktop.STARTUP_FAILED_CODE
        import psutil

        holder = int(pidfile.read_text())
        assert not psutil.pid_exists(holder) or psutil.Process(holder).status() == "zombie"
        assert foreign.poll() is None
    finally:
        _kill_pidfile(pidfile)
        foreign.kill()
        foreign.wait()


def test_profile_argument_forms_are_normalised(tmp_path):
    prof = tmp_path / "Prof"
    scanner = desktop._ProfileScanner(prof)
    variants = [
        [f"--user-data-dir={prof}"],
        [f'--user-data-dir="{prof}"'],
        [f'"--user-data-dir={prof}"'],
        ["--user-data-dir", str(prof)],
        [f'x.exe --app=u --user-data-dir="{prof}" --no-first-run'],
        [f"--user-data-dir={prof}{os.sep}"],
    ]
    if os.name == "nt":
        variants.append([f"--USER-DATA-DIR={str(prof).upper()}"])
    for argv in variants:
        assert any(scanner._matches(v) for v in desktop._profile_args(argv)), argv
    for argv in ([f"--user-data-dir={tmp_path / 'Prof2'}"], [f"--user-data-dir={tmp_path}"], ["--app=x"]):
        assert not any(scanner._matches(v) for v in desktop._profile_args(argv)), argv


def test_takeover_line_reaches_desktop_run_log(tmp_path, monkeypatch):
    """Через run(): перехват окна виден в desktop-run.log, сервер живёт до закрытия окна."""
    from bcc.config import settings

    data = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{data / 'bcc.db'}")
    monkeypatch.setattr(desktop, "identify_server", lambda *a, **k: None)
    monkeypatch.setattr(desktop, "port_busy", lambda *a, **k: False)
    stopped: list[float] = []

    class _FakeServer:
        def __init__(self, host, port):
            pass

        def start(self, url):
            return True

        def stop(self):
            stopped.append(time.monotonic())

    monkeypatch.setattr(desktop, "_BackgroundServer", _FakeServer)
    # Дольше 10 c: иначе run() честно печатает прежний совет про «быстро закрытое окно».
    browser, pidfile = _fake_browser(tmp_path, life=11.0)
    import io

    out = io.StringIO()
    t0 = time.monotonic()
    try:
        code = desktop.run(["--port", "18931", "--browser", browser, "--profile", str(tmp_path / "prof"),
                            "--no-show-token"], out=out)
    finally:
        _kill_pidfile(pidfile)
    assert code == 0
    assert stopped and stopped[0] - t0 >= 10.0, "сервер остановлен, пока окно было открыто"
    log = (data / "desktop-run.log").read_text(encoding="utf-8")
    assert "browser-self-relaunch takeover" in log
    assert "вероятно, краш" not in out.getvalue()
