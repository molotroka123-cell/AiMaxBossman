"""Repo-root bootstrap (same pattern as bossman/_shared.py) + lazy accessors for
learning/trace.py primitives. Never crashes an import: callers degrade."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path and (_ROOT / "learning").is_dir():
    sys.path.insert(0, str(_ROOT))

SCHEMA_DIR = _ROOT / "schemas"
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
