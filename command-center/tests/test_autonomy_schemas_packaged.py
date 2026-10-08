"""Autonomy schemas must ship INSIDE the bcc package: the installed bundle has no <repo>/schemas (07.10, stage 1)."""
from pathlib import Path

from bcc.autonomy import schemas

REPO = Path(__file__).resolve().parents[2]


def test_schemas_are_package_data_and_identical_to_the_repo_copy():
    pkg = Path(schemas.__file__).resolve().parent / "schema_data"
    for name in schemas.SCHEMA_NAMES:
        packaged = pkg / f"{name}.schema.json"
        assert packaged.is_file(), packaged
        assert packaged.read_bytes() == (REPO / "schemas" / "autonomy" / f"{name}.schema.json").read_bytes(), name


def test_schema_dir_prefers_the_packaged_copy():
    assert schemas.schema_dir() == Path(schemas.__file__).resolve().parent / "schema_data"
    assert schemas.load("task")


def test_pyproject_ships_the_schemas():
    text = (REPO / "command-center" / "pyproject.toml").read_text(encoding="utf-8")
    assert "autonomy/schema_data/*.json" in text
