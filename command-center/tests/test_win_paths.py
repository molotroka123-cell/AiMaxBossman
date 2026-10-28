"""Windows path canonicalization (audit 28.10, CMP-UP-3): the confinement answer comes from the file system.

Real directories, a real junction (mklink /J needs no admin), real 8.3 names when the volume has them. Each test that
shows a text-comparison hole also states what the naive `abspath().startswith(root)` check says.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

import pytest

from bcc.security import win_paths as wp

pytestmark = pytest.mark.skipif(not wp.IS_WINDOWS, reason="Windows paths")


def naive_inside(root, path) -> bool:
    return os.path.abspath(path).lower().startswith(os.path.abspath(root).lower())


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "project_root"
    outside = tmp_path / "outside_secret_directory_with_a_long_name"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (root / "src" / "ok.txt").write_text("in", encoding="utf-8")
    (outside / "secret.txt").write_text("out", encoding="utf-8")
    return root, outside


def _junction(link: Path, target: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"cannot create a junction here: {r.stderr.strip()[:100]}")


def test_plain_children_are_inside_and_siblings_are_not(tree):
    root, outside = tree
    assert wp.is_inside(root, root / "src" / "ok.txt")
    assert wp.is_inside(root, root)
    assert not wp.is_inside(root, outside / "secret.txt")
    assert not wp.is_inside(root, str(root) + "_evil")            # sibling that merely shares the text prefix


def test_dotdot_traversal_is_not_inside(tree):
    root, outside = tree
    sneaky = root / "src" / ".." / ".." / outside.name / "secret.txt"
    assert not wp.is_inside(root, sneaky)


def test_a_junction_inside_the_root_that_points_outside_is_caught_where_text_matching_is_fooled(tree):
    root, outside = tree
    link = root / "innocent_looking_folder"
    _junction(link, outside)
    target = link / "secret.txt"
    assert target.is_file()
    assert naive_inside(root, target) is True, "the textual check should be fooled by the junction (documents the hole)"
    assert wp.is_inside(root, target) is False
    assert wp.canonical_path(target).lower().endswith("outside_secret_directory_with_a_long_name\\secret.txt")


def test_short_8_3_names_and_case_do_not_change_the_answer(tree):
    root, outside = tree
    buf = ctypes.create_unicode_buffer(520)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(outside), buf, 520)
    if not n or buf.value.lower() == str(outside).lower():
        pytest.skip("this volume has no 8.3 short names")
    short_secret = Path(buf.value) / "secret.txt"
    assert not wp.is_inside(root, short_secret)
    assert wp.canonical_path(short_secret).lower() == wp.canonical_path(outside / "secret.txt").lower()
    swapped = str(root / "src" / "ok.txt").swapcase()
    assert wp.is_inside(root, swapped)


def test_extended_length_prefix_is_understood(tree):
    root, outside = tree
    bs = chr(92)
    assert wp.is_inside(root, bs * 2 + "?" + bs + str(root / "src" / "ok.txt"))
    assert not wp.is_inside(root, bs * 2 + "?" + bs + str(outside / "secret.txt"))


@pytest.mark.parametrize("name", ["ok.txt:hidden", "ok.txt::$DATA", "NUL", "con.txt", "COM1", "folder.", "name ", "AUX"])
def test_streams_device_names_and_trailing_dots_are_refused(tree, name):
    root, _ = tree
    assert wp.is_inside(root, root / "src" / name) is False
    with pytest.raises(wp.PathRefused):
        wp.canonical_path(root / "src" / name)


@pytest.mark.parametrize("path", ["\\\\.\\PhysicalDrive0", "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1", "C:\\x\x00y"])
def test_device_namespace_object_manager_and_nul_paths_are_refused(tree, path):
    root, _ = tree
    assert wp.is_inside(root, path) is False


def test_a_file_that_does_not_exist_yet_is_judged_by_its_real_parent(tree):
    root, outside = tree
    assert wp.is_inside(root, root / "src" / "new" / "deeper" / "file.txt")
    link = root / "linked"
    _junction(link, outside)
    assert not wp.is_inside(root, link / "will_be_created.txt"), "a new file under an escaping junction must be refused"
