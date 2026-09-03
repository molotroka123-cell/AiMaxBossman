"""A fact without a passport is not stored."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


REQUIRED = ("source", "url", "observed_at", "method", "license", "confidence")
METHODS = frozenset({"official_api", "http_get", "sparql", "manual", "registry"})


class PassportError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_passport(
    *,
    source: str,
    url: str,
    method: str,
    license: str,
    confidence: float,
    observed_at: str | None = None,
    extra: dict | None = None,
) -> dict:
    if method not in METHODS:
        raise PassportError(f"unknown method {method}")
    conf = float(confidence)
    if conf < 0 or conf > 1:
        raise PassportError("confidence must be 0..1")
    if not source or not url or not license:
        raise PassportError("source, url, license required")
    p = {
        "source": source,
        "url": url,
        "observed_at": observed_at or _now(),
        "method": method,
        "license": license,
        "confidence": conf,
    }
    if extra:
        p["extra"] = extra
    return p


def validate_fact(fact: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fact, dict):
        raise PassportError("fact must be an object")
    passport = fact.get("passport")
    if not isinstance(passport, dict):
        raise PassportError("fact without origin is discarded")
    for key in REQUIRED:
        if passport.get(key) in (None, ""):
            raise PassportError(f"passport.{key} missing")
    if passport.get("method") not in METHODS:
        raise PassportError("passport.method unknown")
    try:
        conf = float(passport["confidence"])
    except (TypeError, ValueError) as e:
        raise PassportError("passport.confidence invalid") from e
    if conf < 0 or conf > 1:
        raise PassportError("passport.confidence out of range")
    if not fact.get("subject") or not fact.get("predicate") or "object" not in fact:
        raise PassportError("subject, predicate, object required")
    return fact
