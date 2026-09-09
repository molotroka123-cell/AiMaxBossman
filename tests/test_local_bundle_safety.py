"""A packaging typo may fail; it must never erase source code or owner files."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "build_local_bundle", Path(__file__).resolve().parents[1] / "tools" / "build_local_bundle.py")
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)


def test_output_refuses_nonempty_directory_without_mutation(tmp_path):
    valuable = tmp_path / "owner-data.txt"
    valuable.write_text("irreplaceable")
    with pytest.raises(ValueError, match="not an empty directory"):
        bundle.prepare_output(tmp_path)
    assert valuable.read_text() == "irreplaceable"


def test_output_refuses_file_without_mutation(tmp_path):
    valuable = tmp_path / "owner-data.txt"
    valuable.write_text("irreplaceable")
    with pytest.raises(ValueError):
        bundle.prepare_output(valuable)
    assert valuable.read_text() == "irreplaceable"


def test_output_refuses_symlink(tmp_path):
    target = tmp_path / "owner-folder"
    target.mkdir()
    link = tmp_path / "bundle-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"host cannot create symlinks: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        bundle.prepare_output(link)
    assert target.is_dir()


def test_output_accepts_new_directory(tmp_path):
    dest = tmp_path / "new" / "bundle"
    assert bundle.prepare_output(dest) == dest
    assert dest.is_dir()
