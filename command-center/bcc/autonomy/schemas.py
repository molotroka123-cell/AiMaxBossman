"""JSON Schemas of the autonomy wire formats and a small, dependency-free validator.

The schemas live in ``schemas/autonomy/`` at the repository root (task, action,
review, result). ``jsonschema`` is not a runtime dependency of the Command
Center, so this module validates the subset of Draft 2020-12 the schemas use:
type, enum, const, required, properties, additionalProperties, pattern,
minLength/maxLength, minimum/maximum, items, minItems/maxItems, uniqueItems.
An unknown keyword is an error (fail closed), never silently ignored.
"""
from __future__ import annotations

import dataclasses
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .types import Budget, Goal, HandRequest, HandResult, Review

SCHEMA_NAMES = ("task", "action", "review", "result")
_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_KNOWN = _ANNOTATIONS | {"type", "enum", "const", "required", "properties", "additionalProperties", "pattern",
                         "minLength", "maxLength", "minimum", "maximum", "items", "minItems", "maxItems",
                         "uniqueItems"}


class SchemaError(ValueError):
    def __init__(self, name: str, errors: list[str]):
        super().__init__(f"{name}: " + "; ".join(errors))
        self.name = name
        self.errors = errors


def schema_dir() -> Path:
    # <repo>/command-center/bcc/autonomy/schemas.py -> <repo>/schemas/autonomy
    return Path(__file__).resolve().parents[3] / "schemas" / "autonomy"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    path = schema_dir() / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"unsupported schema type {expected!r}")


def _check(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    unknown = set(schema) - _KNOWN
    if unknown:
        errors.append(f"{path}: schema uses unsupported keywords {sorted(unknown)}")
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in value]
            if len(seen) != len(set(seen)):
                errors.append(f"{path}: items are not unique")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{i}]", errors)
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(f"{path}: non-string key {key!r}")
            elif key in props:
                _check(item, props[key], f"{path}.{key}", errors)
            elif extra is False:
                errors.append(f"{path}.{key}: not allowed")
            elif isinstance(extra, dict):
                _check(item, extra, f"{path}.{key}", errors)


def errors_for(name: str, value: Any) -> list[str]:
    errors: list[str] = []
    _check(value, load(name), "$", errors)
    return errors


def validate(name: str, value: Any) -> None:
    errors = errors_for(name, value)
    if errors:
        raise SchemaError(name, errors)


def to_json(obj: Any) -> Any:
    """Dataclass -> plain JSON value (tuples become lists)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): to_json(v) for k, v in obj.items()}
    return obj


def goal_from_json(data: Any) -> Goal:
    validate("task", data)
    b = data["budget"]
    return Goal(goal_id=data["goal_id"], problem=data["problem"], desired_result=data["desired_result"],
                constraints=tuple(data["constraints"]), acceptance_tests=tuple(data["acceptance_tests"]),
                budget=Budget(max_minutes=b["max_minutes"], max_agent_turns=b["max_agent_turns"],
                              max_cost_usd=float(b["max_cost_usd"])),
                risk_tier=data["risk_tier"], target_metric=data["target_metric"],
                protected_metrics=tuple(data["protected_metrics"]))


def hand_request_from_json(data: Any) -> HandRequest:
    validate("action", data)
    return HandRequest(goal_id=data["goal_id"], requested_by=data["requested_by"], action=data["action"],
                       target=data["target"], arguments=dict(data["arguments"]),
                       expected_evidence=tuple(data["expected_evidence"]), risk_class=data["risk_class"],
                       timeout_s=data["timeout_s"], rollback=data["rollback"])


def review_from_json(data: Any) -> Review:
    validate("review", data)
    return Review(**data)


def hand_result_from_json(data: Any) -> HandResult:
    validate("result", data)
    return HandResult(**{**data, "artifacts": dict(data["artifacts"])})


__all__ = ["SCHEMA_NAMES", "SchemaError", "schema_dir", "load", "errors_for", "validate", "to_json",
           "goal_from_json", "hand_request_from_json", "review_from_json", "hand_result_from_json"]
