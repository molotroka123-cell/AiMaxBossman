"""Real child-process checks; no arbitrary owner-process cleanup."""
import io
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from tools.pytest_watchdog import run_watchdog, redact, _cleanup


def run(code, **kw):
    output = io.StringIO()
    status = run_watchdog([sys.executable, '-u', '-c', code], output=output, **kw)
    return status, output.getvalue()


def test_real_exit_code_wins_over_printed_success():
    code, text = run("print('=== 100 passed ==='); raise SystemExit(7)")
    assert code == 7
    assert 'exit=7' in text


def test_summary_followed_by_hang_is_not_pass():
    code, text = run("import time; print('=== 100 passed ==='); time.sleep(20)", timeout_s=.3)
    assert code == 124
    assert 'TIMEOUT' in text


def test_quiet_running_test_is_not_assumed_stuck():
    code, text = run("import time; time.sleep(.2); print('finished')", timeout_s=3)
    assert code == 0
    assert 'finished' in text


def test_log_redacts_and_is_not_overwritten(tmp_path):
    log = tmp_path / 'run.log'
    code, text = run("print('api_key=testvalue password=privatevalue')", log_path=str(log))
    assert code == 0
    assert 'testvalue' not in log.read_text()
    assert 'privatevalue' not in text
    with pytest.raises(FileExistsError):
        run("print('must not execute')", log_path=str(log))


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_invalid_timeout_denied(value):
    with pytest.raises(ValueError):
        run('pass', timeout_s=value)


def test_long_line_and_bounded_tail():
    code, text = run("print('x'*40000); [print('line'+str(i)) for i in range(500)]")
    assert code == 0
    assert len(text) < 20000
    assert 'line499' in text


def test_timeout_does_not_kill_unrelated_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    child_pid = tmp_path / 'child.pid'
    script = ("import subprocess,sys,time; from pathlib import Path; "
              "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
              f"Path({str(child_pid)!r}).write_text(str(p.pid)); time.sleep(30)")
    try:
        code, text = run(script, timeout_s=1)
        assert code == 124
        assert unrelated.poll() is None
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        try:
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass
        assert 'residual_pids=[]' in text
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_stale_identity_is_not_signalled():
    class ReusedPid:
        pid = os.getpid() + 10000
        def is_running(self):
            return False
        def terminate(self):
            raise AssertionError('PID identity mismatch must not be signalled')
    assert _cleanup({1: ReusedPid()}, 1) == []


def test_redaction_handles_bearer():
    assert 'testvalue' not in redact('Authorization: Bearer testvalue\n')
