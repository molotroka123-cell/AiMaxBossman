"""authored_by_lane (opsplug): bossman.apprentice._bootstrap - repo-root bootstrap + lazy learning accessors."""
import json
from pathlib import Path

import pytest

from bossman.apprentice import _bootstrap as bs


def test_repo_root_is_this_checkout_and_has_the_shared_learning_package():
    root = bs.repo_root()
    assert root == Path(bs.__file__).resolve().parents[3]
    assert (root / "bossman-core" / "bossman" / "apprentice" / "_bootstrap.py").is_file()
    assert (root / "learning").is_dir()


def test_importing_never_leaves_an_unrelated_checkout_in_front_for_learning():
    trace = bs.trace()
    assert Path(trace.__file__).resolve().parent.name == "learning"
    for name in ("redact_obj", "has_secret", "validate", "LearningStore"):
        assert hasattr(trace, name), name


def test_schema_dir_points_at_the_checkout_schemas_and_paths_are_derived_from_it():
    assert bs.SCHEMA_DIR == bs.repo_root() / "schemas"
    assert bs.ACTION_RECORD_SCHEMA_PATH.name == "apprentice_action_record.schema.json"
    assert bs.ACTION_RECORD_SCHEMA_PATH.parent == bs.SCHEMA_DIR
    assert bs.SKILL_SCHEMA_PATH.parent == bs.SCHEMA_DIR


def test_the_shipped_schemas_load_as_json_objects():
    for p in (bs.ACTION_RECORD_SCHEMA_PATH, bs.SKILL_SCHEMA_PATH):
        assert p.is_file(), p
        schema = bs.load_json(p)
        assert isinstance(schema, dict) and schema.get("type") == "object"


def test_load_json_roundtrip_with_non_ascii_and_missing_file_raises(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"k": "значение"}, ensure_ascii=False), encoding="utf-8")
    assert bs.load_json(p) == {"k": "значение"}
    with pytest.raises(FileNotFoundError):
        bs.load_json(tmp_path / "missing.json")
