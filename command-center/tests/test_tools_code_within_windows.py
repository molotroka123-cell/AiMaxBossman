"""tools_code._within on Windows (audit 28.10, CMP-UP-3): the shared confinement helper now also refuses what resolve() accepts.

Found by comparing the old check with win_paths on a real tree: `resolve()` already catches junctions, `..` and a sibling
that shares a text prefix, but it called these paths «inside the root»: `ok.txt:hidden` (alternate data stream), `NUL`
(device) and `folder.` (a trailing dot Windows drops). Real temp directories and a real junction.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bcc.features.tools_code import _within

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows path rules")


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "project_root"
    outside = tmp_path / "outside_dir"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (root / "src" / "ok.txt").write_text("in", encoding="utf-8")
    (outside / "secret.txt").write_text("out", encoding="utf-8")
    return root, outside


def test_normal_paths_behave_as_before(tree):
    root, outside = tree
    assert _within(root / "src" / "ok.txt", [root]) is True
    assert _within(root / "src" / "new" / "file.txt", [root]) is True
    assert _within(root, [root]) is True
    assert _within(outside / "secret.txt", [root]) is False
    assert _within(root / "src" / ".." / ".." / "outside_dir" / "secret.txt", [root]) is False
    assert _within(Path(str(root) + "_evil") / "a.txt", [root]) is False


def test_junction_escape_is_still_refused(tree):
    root, outside = tree
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "link"), str(outside)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot create a junction here")
    assert _within(root / "link" / "secret.txt", [root]) is False


@pytest.mark.parametrize("name", ["ok.txt:hidden", "ok.txt::$DATA", "NUL", "con.txt", "COM1", "folder.", "name "])
def test_streams_devices_and_trailing_dots_are_no_longer_inside(tree, name):
    root, _ = tree
    assert _within(root / "src" / name, [root]) is False


def test_the_gap_was_real_resolve_alone_accepts_these(tree):
    root, _ = tree
    for name in ("ok.txt:hidden", "NUL", "folder."):
        resolved = (root / "src" / name).resolve()
        assert root.resolve() in resolved.parents            # resolve() alone would have called it inside
        assert _within(root / "src" / name, [root]) is False
