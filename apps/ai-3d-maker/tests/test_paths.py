"""Filename and path safety: nothing may be written outside the job sandbox."""

from __future__ import annotations

import pytest

from ai_3d_maker.errors import UnsafePathError
from ai_3d_maker.paths import dir_size_bytes, resolve_within, safe_artifact_name, safe_job_id


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "..",
        "../",
        "/etc/shadow",
        "..\\..\\windows\\system32",
        "....//....//x",
        "job/../../escape",
        "‮/etc/passwd",
    ],
)
def test_traversal_attempts_never_yield_a_separator(raw):
    try:
        cleaned = safe_job_id(raw)
    except UnsafePathError:
        return
    assert "/" not in cleaned and "\\" not in cleaned
    assert ".." not in cleaned


@pytest.mark.parametrize("raw", ["../", "..", "///", "!!!", "", "   ", "..."])
def test_names_with_nothing_safe_are_refused(raw):
    with pytest.raises(UnsafePathError):
        safe_job_id(raw)


def test_reserved_device_names_are_refused():
    with pytest.raises(UnsafePathError):
        safe_artifact_name("CON.stl")
    with pytest.raises(UnsafePathError):
        safe_artifact_name("lpt1")


def test_ordinary_names_survive():
    assert safe_job_id("bracket-v2_final") == "bracket-v2_final"
    assert safe_artifact_name("model.stl") == "model.stl"


def test_long_names_are_truncated():
    assert len(safe_job_id("a" * 500)) == 80


def test_resolve_within_keeps_paths_inside_the_root(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    assert resolve_within(root, "job1", "model.stl").is_relative_to(root.resolve())


def test_resolve_within_refuses_escape(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    with pytest.raises(UnsafePathError):
        resolve_within(root, "..", "outside.stl")


def test_resolve_within_refuses_symlink_escape(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError):
        resolve_within(root, "link", "evil.stl")


def test_dir_size_counts_only_real_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 50)
    assert dir_size_bytes(tmp_path) == 150
    assert dir_size_bytes(tmp_path / "missing") == 0
