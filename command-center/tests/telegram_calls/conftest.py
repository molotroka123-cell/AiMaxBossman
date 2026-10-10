"""Test plumbing of the calls module.

The REAL worker runs as ``python -I -m bcc.telegram_calls`` (isolated: no PYTHONPATH, no user site). In a git worktree whose
editable install points at ANOTHER checkout, that child would import the other checkout's ``bcc`` and the tests would exercise
code that is not the code under test. When the parent process imports ``bcc`` from a place the child would not find, the tests
start the worker WITHOUT ``-I`` so that the inherited ``PYTHONPATH`` (this checkout) wins. A normal install is unaffected.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _child_bcc_file() -> str:
    out = subprocess.run([sys.executable, "-I", "-c", "import bcc, sys; sys.stdout.write(bcc.__file__)"],
                         capture_output=True, text=True, timeout=60, check=False)
    return os.path.normcase(os.path.realpath(out.stdout.strip())) if out.returncode == 0 else ""


@pytest.fixture(autouse=True, scope="session")
def _isolated_worker_imports_this_checkout():
    import bcc
    from bcc.telegram_calls.call import manager

    same = _child_bcc_file() == os.path.normcase(os.path.realpath(bcc.__file__))
    original_init = manager.CallsManager.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if not same and self.argv == list(manager.DEFAULT_ARGV):          # only the default command; DEFAULT_ARGV itself stays as shipped
            self.argv = [a for a in self.argv if a != "-I"]

    manager.CallsManager.__init__ = init
    yield
    manager.CallsManager.__init__ = original_init
