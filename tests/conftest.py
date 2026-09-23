"""Root test harness settings shared by the root suites."""
from __future__ import annotations

import os
import pathlib

import pytest

# Suites that build real git repositories and hand-written LF patches. On
# Windows ``Path.write_text`` translates "\n" into "\r\n", so a fixture file no
# longer matches its own LF patch (DIFF_DOES_NOT_APPLY / NO_DIFF on
# windows-latest) — something a git checkout of LF sources never produces.
_LF_SUITES = ("test_evolution_", "test_self_improve_lab")


@pytest.fixture(autouse=True)
def _fixture_files_are_lf(request, monkeypatch):
    if os.name != "nt" or not request.node.fspath.basename.startswith(_LF_SUITES):
        yield
        return
    original = pathlib.Path.write_text

    def write_text(self, data, encoding=None, errors=None, newline=None):
        return original(self, data, encoding=encoding, errors=errors,
                        newline="\n" if newline is None else newline)

    monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    yield
