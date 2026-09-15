"""The disposable UI sweep must not leak managed application processes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

spec = importlib.util.spec_from_file_location('installed_ui_sweep',
    Path(__file__).resolve().parents[1] / 'tools' / 'installed_ui_sweep.py')
sweep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sweep)


def test_stop_owned_tree_releases_child_working_directory(tmp_path):
    working = tmp_path / 'managed app with spaces'
    working.mkdir()
    parent = subprocess.Popen([sys.executable, '-c',
        'import subprocess,sys,time; sys.stdin.readline(); '
        'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
        'print(p.pid,flush=True); time.sleep(60)'],
        cwd=working, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    if psutil.Process(parent.pid).cmdline()[1:] != parent.args[1:]:
        parent.terminate()
        parent.wait(timeout=5)
        if os.name == 'nt':
            pytest.fail('Native Windows runner must expose actual child process identity')
        pytest.skip('Managed execution remaps Popen PIDs; native process-tree test required')
    parent.stdin.write('start\n')
    parent.stdin.flush()
    child = psutil.Process(int(parent.stdout.readline()))
    # This unrelated process must remain untouched by the scoped cleanup.
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    def stop_parent(process):
        process.terminate()
        process.wait(timeout=5)
    try:
        sweep.stop_owned_process_tree(parent, stop_parent)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert unrelated.poll() is None
        working.rmdir()  # Windows rejects this while the child owns its cwd.
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
        unrelated.kill()
        unrelated.wait(timeout=5)
