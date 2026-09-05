#!/usr/bin/env python3
"""README Live OS Scorecard — детерминированный updater (BOSS-README-LIVE-SCORECARD-001).

Источник истины: docs/benchmark/current-scorecard.json. README — только проекция:
скрипт заменяет текст между маркерами и ничего больше.

    python scripts/update_readme_scorecard.py          # перерисовать блок в README + .md
    python scripts/update_readme_scorecard.py --check  # CI: блок совпадает с данными, маркеры ровно один раз

Никаких платных вызовов: только чтение JSON, git rev-parse и рендер текста.
Правила схемы: ровно 10 канонических категорий, score ∈ [0,10], статус из словаря,
10.0 только при ATTESTED, UNPROVEN/NOT_RUN никогда не рендерятся как PASS.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORECARD_JSON = ROOT / "docs" / "benchmark" / "current-scorecard.json"
SCORECARD_MD = ROOT / "docs" / "benchmark" / "current-scorecard.md"
README = ROOT / "README.md"
START, END = "<!-- BOSSMAN_LIVE_SCORECARD_START -->", "<!-- BOSSMAN_LIVE_SCORECARD_END -->"

CATEGORIES = (
    "Execution Truth", "Security", "Tooling / OS Integration", "Organization Layer", "Fleet & Resources",
    "Memory / Context", "Testing / CI", "Observability / CEO Control", "Treasury / Cost",
    "Mission UX / Command Center",
)
STATUSES = {"DESIGNED", "IMPLEMENTED", "INTEGRATED", "VERIFIED", "ATTESTED", "PARTIAL", "BLOCKED", "UNPROVEN"}
CONFIDENCES = {"LOW", "MEDIUM", "HIGH"}
ATTESTATIONS = {"PASS", "PENDING", "NOT_REQUIRED", "FAIL", "UNPROVEN"}
CI_VALUES = {"PASS", "FAIL", "NOT_RUN", "NOT_APPLICABLE", "UNPROVEN"}
# Hard fail → какие категории теряют VERIFIED/ATTESTED и потолок оценки
HARD_FAIL_IMPACT = {
    "false_success": ("Execution Truth", "Organization Layer"),
    "duplicate_side_effect": ("Execution Truth", "Fleet & Resources"),
    "privacy_violation": ("Security", "Fleet & Resources"),
    "permission_bypass": ("Security",),
    "parent_success_with_failed_child": ("Organization Layer", "Execution Truth"),
    "stale_evidence_accepted": ("Execution Truth",),
    "review_bypass": ("Organization Layer",),
    "scope_leak": ("Memory / Context", "Security"),
    "treasury_overrun": ("Treasury / Cost",),
    "ci_regression": ("Testing / CI",),
}
HARD_FAIL_CAP = 6.0


class ScorecardError(ValueError):
    pass


# ------------------------------------------------------------------ schema

def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ScorecardError("scorecard must be an object")
    cats = data.get("categories")
    if not isinstance(cats, list):
        raise ScorecardError("categories must be a list")
    names = [c.get("category") if isinstance(c, dict) else None for c in cats]
    if sorted(n for n in names if n) != sorted(CATEGORIES) or len(names) != len(CATEGORIES):
        dup = {n for n in names if names.count(n) > 1}
        missing = set(CATEGORIES) - set(names)
        extra = set(names) - set(CATEGORIES)
        raise ScorecardError(f"categories must be exactly the 10 canonical axes; duplicate={sorted(map(str, dup))} "
                             f"missing={sorted(missing)} extra={sorted(map(str, extra))}")
    for c in cats:
        name = c["category"]
        score = c.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0.0 <= float(score) <= 10.0:
            raise ScorecardError(f"{name}: score must be a number in [0, 10]")
        status = c.get("status")
        if status not in STATUSES:
            raise ScorecardError(f"{name}: unknown status {status!r}")
        if float(score) >= 10.0 and status != "ATTESTED":
            raise ScorecardError(f"{name}: 10.0 requires status ATTESTED")
        if c.get("confidence") not in CONFIDENCES:
            raise ScorecardError(f"{name}: unknown confidence {c.get('confidence')!r}")
        if c.get("live_attestation", "NOT_REQUIRED") not in ATTESTATIONS:
            raise ScorecardError(f"{name}: unknown live_attestation")
        if status == "ATTESTED" and c.get("live_attestation") == "PENDING":
            raise ScorecardError(f"{name}: ATTESTED with live attestation PENDING is a contradiction")
        for key in ("evidence", "blockers", "tests"):
            if not isinstance(c.get(key, []), list) or not all(isinstance(x, str) for x in c.get(key, [])):
                raise ScorecardError(f"{name}: {key} must be a list of strings")
        if status in ("VERIFIED", "ATTESTED") and not c.get("evidence"):
            raise ScorecardError(f"{name}: {status} requires non-empty evidence")
        if not isinstance(c.get("last_verified_sha", ""), str):
            raise ScorecardError(f"{name}: last_verified_sha must be a string")
    for key in ("current_bottleneck", "next_highest_value_fix", "last_evidence_sha", "last_update"):
        if not isinstance(data.get(key), str) or not data.get(key):
            raise ScorecardError(f"top-level {key} is required")
    if data.get("exact_sha_ci", "UNPROVEN") not in CI_VALUES:
        raise ScorecardError("exact_sha_ci must be one of " + ", ".join(sorted(CI_VALUES)))
    hf = data.get("benchmark_hard_failures", [])
    if not isinstance(hf, list):
        raise ScorecardError("benchmark_hard_failures must be a list")
    if data.get("live_hardware_attestation", "PENDING") not in ATTESTATIONS:
        raise ScorecardError("live_hardware_attestation invalid")
    return data


def apply_hard_fails(data: dict, hard_fails: list[str]) -> dict:
    """Hard fail понижает связанные категории: снимает VERIFIED/ATTESTED и режет
    оценку до HARD_FAIL_CAP. Оценки могут падать — это требование, не сбой."""
    impacted: dict[str, list[str]] = {}
    for hf in hard_fails:
        for cat in HARD_FAIL_IMPACT.get(hf, ()):
            impacted.setdefault(cat, []).append(hf)
    for c in data["categories"]:
        hits = impacted.get(c["category"])
        if not hits:
            continue
        if c["status"] in ("VERIFIED", "ATTESTED"):
            c["status"] = "PARTIAL"
        c["score"] = min(float(c["score"]), HARD_FAIL_CAP)
        c["live_attestation"] = "FAIL" if c.get("live_attestation") == "PASS" else c.get("live_attestation", "NOT_REQUIRED")
        c.setdefault("blockers", []).extend(f"hard fail: {h}" for h in hits if f"hard fail: {h}" not in c.get("blockers", []))
    data["benchmark_hard_failures"] = sorted(set(data.get("benchmark_hard_failures", [])) | set(hard_fails))
    return data


# ------------------------------------------------------------------ render

def head_sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "UNPROVEN"


def freshness(data: dict, head: str) -> str:
    ev = data.get("last_evidence_sha", "")
    if not ev or ev == "UNPROVEN" or head == "UNPROVEN":
        return "UNPROVEN"
    return "FRESH" if head.startswith(ev) or ev.startswith(head) else "PARTIALLY_STALE"


def render(data: dict, head: str) -> str:
    lines = ["| # | Ось системы | Оценка | Статус | Уверенность | Улики |", "|---|---|---:|---|---|---|"]
    for i, c in enumerate(data["categories"], 1):
        ev = "; ".join(c.get("evidence") or ["UNPROVEN"])
        lines.append(f"| {i} | {c['category']} | {float(c['score']):.1f}/10 | {c['status']} | {c['confidence']} | {ev} |")
    scores = [float(c["score"]) for c in data["categories"]]
    lines += [
        "",
        f"- **Current bottleneck:** {data['current_bottleneck']}",
        f"- **Next highest-value fix:** {data['next_highest_value_fix']}",
        f"- **Last evidence SHA:** `{data['last_evidence_sha']}` · **Current HEAD SHA:** `{head[:12]}` · "
        f"**Evidence freshness:** {freshness(data, head)}",
        f"- **Last scorecard update:** {data['last_update']}",
        f"- **Benchmark hard failures:** {', '.join(data.get('benchmark_hard_failures') or []) or 'none observed'}",
        f"- **Live hardware attestation:** {data.get('live_hardware_attestation', 'PENDING')}",
        f"- **Exact-SHA CI:** {data.get('exact_sha_ci', 'UNPROVEN')}",
        "",
        f"_Среднее (вторично, не авторитетно): {sum(scores) / len(scores):.1f}/10. 10.0 = ATTESTED; "
        f"ни одна ось не ATTESTED без живой аттестации железа._",
    ]
    return "\n".join(lines)


def render_md(data: dict, head: str) -> str:
    out = ["# Current scorecard (rendered from current-scorecard.json)", "", render(data, head), ""]
    for c in data["categories"]:
        out += [f"## {c['category']} — {float(c['score']):.1f} · {c['status']}", ""]
        out += [f"- evidence: {e}" for e in c.get("evidence", [])]
        out += [f"- blocker: {b}" for b in c.get("blockers", [])]
        out += [f"- tests: {t}" for t in c.get("tests", [])]
        out += [f"- last_verified_sha: `{c.get('last_verified_sha', '')}` · last_verified_at: {c.get('last_verified_at', 'UNPROVEN')} · "
                f"live_attestation: {c.get('live_attestation', 'NOT_REQUIRED')} · regression_delta: {c.get('regression_delta', 0.0):+.1f}", ""]
    counters = data.get("deterministic_counters") or {}
    if counters:
        out += ["## Deterministic counters", ""] + [f"- {k}: {v}" for k, v in counters.items()] + [""]
    return "\n".join(out)


def splice(readme: str, block: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ScorecardError(f"README must contain exactly one {START} and one {END}")
    s, e = readme.index(START), readme.index(END)
    if e < s:
        raise ScorecardError("README scorecard END marker precedes START")
    return readme[: s + len(START)] + "\n" + block + "\n" + readme[e:]


def current_block(readme: str) -> str:
    s, e = readme.index(START) + len(START), readme.index(END)
    return readme[s:e].strip("\n")


# -------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--scorecard", default=str(SCORECARD_JSON))
    ap.add_argument("--readme", default=str(README))
    ap.add_argument("--md", default=str(SCORECARD_MD))
    a = ap.parse_args(argv)
    try:
        data = validate(json.loads(Path(a.scorecard).read_text(encoding="utf-8")))
        head = head_sha()
        block = render(data, head)
        readme = Path(a.readme).read_text(encoding="utf-8")
        if a.check:
            if readme.count(START) != 1 or readme.count(END) != 1:
                raise ScorecardError("README markers missing or duplicated")
            # freshness зависит от HEAD и пересчитывается при каждом коммите; в --check
            # сравнивается всё, кроме строки с HEAD/freshness
            def _stable(text: str) -> str:
                return "\n".join(l for l in text.splitlines() if "Current HEAD SHA" not in l)
            if _stable(current_block(readme)) != _stable(block):
                print("README_SCORECARD_CURRENT=FAIL (rendered block differs from current-scorecard.json)", file=sys.stderr)
                return 1
            print("README_SCORECARD_CURRENT=PASS")
            return 0
        Path(a.readme).write_text(splice(readme, block), encoding="utf-8")
        Path(a.md).write_text(render_md(data, head), encoding="utf-8")
        print(f"README scorecard updated (HEAD {head[:12]}, freshness {freshness(data, head)})")
        return 0
    except ScorecardError as exc:
        print(f"scorecard error: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"scorecard error: malformed input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
