#!/usr/bin/env python3
"""Validate and inspect Bossman's curated OpenClaw skill shortlist.

This tool is intentionally read-only. It never downloads or installs community
skills. Community code must pass Bossman's normal source review, provenance,
negative-test, approval and rollback gates before promotion.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "integrations" / "openclaw" / "curated-skills.json"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_MODES = {"candidate", "reference"}
_ALLOWED_RISKS = {"low", "medium", "high"}
_REQUIRED_POLICY_GATES = {
    "manual_source_review",
    "provenance_pin",
    "hostile_tests",
    "owner_approval",
    "rollback_plan",
}


class CatalogError(ValueError):
    """Raised when the curated catalog violates Bossman's admission contract."""


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read catalog: {exc}") from exc
    validate_catalog(data)
    return data


def validate_catalog(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1:
        raise CatalogError("schema_version must be 1")

    source = data.get("source")
    if not isinstance(source, dict):
        raise CatalogError("source must be an object")
    if source.get("repository") != "VoltAgent/awesome-openclaw-skills":
        raise CatalogError("unexpected upstream repository")
    if not _SHA40.fullmatch(str(source.get("commit", ""))):
        raise CatalogError("source.commit must be a pinned 40-char git SHA")
    if source.get("security_status") != "curated_not_audited":
        raise CatalogError("upstream security status must remain explicit")

    policy = data.get("policy")
    if not isinstance(policy, dict):
        raise CatalogError("policy must be an object")
    if policy.get("auto_install") is not False:
        raise CatalogError("community skills must never be auto-installed")
    if policy.get("default_network") != "deny":
        raise CatalogError("default_network must be deny")
    if policy.get("write_actions") != "ask":
        raise CatalogError("write_actions must be ask")
    if policy.get("secret_access") != "deny":
        raise CatalogError("secret_access must be deny")
    promotion = set(policy.get("promotion_requires") or ())
    if not _REQUIRED_POLICY_GATES.issubset(promotion):
        missing = sorted(_REQUIRED_POLICY_GATES - promotion)
        raise CatalogError(f"missing promotion gates: {missing}")

    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise CatalogError("skills must be a non-empty list")

    ranks: set[int] = set()
    slugs: set[str] = set()
    urls: set[str] = set()
    expected_rank = 1
    previous_score = float("inf")

    for item in skills:
        if not isinstance(item, dict):
            raise CatalogError("each skill must be an object")
        rank = item.get("rank")
        slug = item.get("slug")
        url = item.get("registry_url")
        score = item.get("fit_score")
        mode = item.get("mode")
        risk = item.get("risk")
        gates = set(item.get("required_gates") or ())

        if rank != expected_rank:
            raise CatalogError(f"ranks must be contiguous; expected {expected_rank}, got {rank!r}")
        expected_rank += 1
        if rank in ranks:
            raise CatalogError(f"duplicate rank {rank}")
        ranks.add(rank)

        if not isinstance(slug, str) or not slug or slug in slugs:
            raise CatalogError(f"invalid or duplicate slug {slug!r}")
        slugs.add(slug)

        if not isinstance(url, str) or not url.startswith("https://clawskills.sh/skills/") or url in urls:
            raise CatalogError(f"invalid or duplicate registry_url for {slug}")
        urls.add(url)

        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= float(score) <= 10:
            raise CatalogError(f"invalid fit_score for {slug}")
        if float(score) > previous_score:
            raise CatalogError("skills must be sorted by descending fit_score")
        previous_score = float(score)

        if mode not in _ALLOWED_MODES:
            raise CatalogError(f"invalid mode for {slug}")
        if risk not in _ALLOWED_RISKS:
            raise CatalogError(f"invalid risk for {slug}")
        if not {"manual_source_review", "provenance_pin", "negative_tests"}.issubset(gates):
            raise CatalogError(f"{slug} is missing mandatory per-skill gates")


def _print_table(data: dict[str, Any]) -> None:
    print(f"source={data['source']['repository']}@{data['source']['commit']}")
    print("auto_install=false; this is a review queue, not an installation list")
    for item in data["skills"]:
        print(
            f"{item['rank']:>2}. {item['slug']:<28} "
            f"score={item['fit_score']:.1f} mode={item['mode']:<9} "
            f"risk={item['risk']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--check", action="store_true", help="validate and exit")
    parser.add_argument("--json", action="store_true", help="print validated JSON")
    args = parser.parse_args()

    try:
        data = load_catalog(args.catalog)
    except CatalogError as exc:
        print(f"OPENCLAW_CURATED=FAIL: {exc}")
        return 2

    if args.check:
        print(f"OPENCLAW_CURATED=PASS skills={len(data['skills'])} auto_install=false")
    elif args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_table(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
