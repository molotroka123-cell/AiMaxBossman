"""Repo-root bootstrap (same pattern as bossman/_shared.py) + lazy accessors for
learning/trace.py primitives. Never crashes an import: callers degrade."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
try:                                   # installed bossman-shared distribution first
    import learning as _installed_learning  # noqa: F401
except Exception:  # noqa: BLE001
    if str(_ROOT) not in sys.path and (_ROOT / "learning").is_dir():
        sys.path.insert(0, str(_ROOT))


def _schema_dir() -> Path:
    local = _ROOT / "schemas"
    if local.is_dir():
        return local
    try:
        from importlib.util import find_spec  # noqa: WPS433
        spec = find_spec("bossman_schemas")          # namespace package from the bossman-shared wheel
        locs = list(spec.submodule_search_locations or []) if spec else []
        if locs:
            return Path(locs[0])
        raise ImportError("bossman_schemas not installed")
    except Exception:  # noqa: BLE001
        return local


SCHEMA_DIR = _schema_dir()
ACTION_RECORD_SCHEMA_PATH = SCHEMA_DIR / "apprentice_action_record.schema.json"
SKILL_SCHEMA_PATH = SCHEMA_DIR / "apprentice_skill.schema.json"


def repo_root() -> Path:
    return _ROOT


def trace():
    """learning.trace module (redact_obj, has_secret, validate, LearningStore)."""
    from learning import trace as _trace  # noqa: WPS433 — lazy on purpose
    return _trace


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
