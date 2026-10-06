#!/usr/bin/env python3
"""Move a redacted, owner-attested retention verdict to exact-SHA CI.

The full owner-PC measurement stays outside GitHub. This tool never posts it.
Only the repository owner's commit comment can supply the public summary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.intelligence_preservation_gate import GateConfig, MODES, REQUIRED_METRICS, evaluate  # noqa: E402

MARKER = "BOSSMAN_INTELLIGENCE_EVIDENCE_V1\n"
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_COMMENT = 60000


class TransportError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json(data: bytes | str) -> dict:
    value = json.loads(data, object_pairs_hook=_unique_pairs)
    if not isinstance(value, dict):
        raise TransportError("evidence must be a JSON object")
    return value


def _check_items(raw: dict) -> None:
    """Recount private paired rows before publishing their aggregate scores."""
    corpus = raw.get("corpus") or {}
    tasks, rows = corpus.get("tasks"), raw.get("items")
    if not isinstance(tasks, list) or not isinstance(rows, list) or not tasks:
        raise TransportError("private task and paired-item rows are required")
    if corpus.get("tasks_sha256") != _digest(_canonical(tasks)):
        raise TransportError("private task digest differs")
    by_id = {t.get("task_id"): t.get("metric") for t in tasks if isinstance(t, dict)}
    if len(by_id) != len(tasks) or len(rows) != len(tasks):
        raise TransportError("private task/item identity mismatch")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("task_id") in seen:
            raise TransportError("duplicate or invalid paired item")
        task_id, metric = row.get("task_id"), row.get("metric")
        if by_id.get(task_id) != metric or metric not in REQUIRED_METRICS:
            raise TransportError("paired item does not match the private corpus")
        if any(type(row.get(mode)) is not bool for mode in MODES):
            raise TransportError("paired item lacks a Boolean lane outcome")
        seen.add(task_id)
    if seen != set(by_id):
        raise TransportError("private paired items are incomplete")
    for metric in REQUIRED_METRICS:
        group = [row for row in rows if row["metric"] == metric]
        if not group:
            raise TransportError(f"unmeasured metric: {metric}")
        for mode in MODES:
            item = raw["modes"][mode][metric]
            passed = sum(row[mode] for row in group)
            expected = (len(group) - passed if metric == "hallucination_rate" else passed) / len(group)
            if item.get("samples") != len(group) or abs(item.get("score", -1) - expected) > 0.00000051:
                raise TransportError(f"private score differs from paired items: {mode}/{metric}")
            if mode != "raw":
                pair = item.get("paired") or {}
                lost = sum(row["raw"] and not row[mode] for row in group)
                gained = sum(not row["raw"] and row[mode] for row in group)
                if pair != {"lost": lost, "gained": gained}:
                    raise TransportError(f"private discordance differs: {mode}/{metric}")


def prepare(raw_bytes: bytes, expected_sha: str) -> dict:
    if not SHA.fullmatch(expected_sha):
        raise TransportError("expected SHA must be 40 lowercase hex characters")
    raw = _json(raw_bytes)
    if raw.get("diagnostic_only") is not False:
        raise TransportError("diagnostic measurement cannot be submitted")
    source = raw.get("source") or {}
    if source.get("evaluated_sha") != expected_sha or type(source.get("tracked_files_verified")) is not int or source["tracked_files_verified"] < 1:
        raise TransportError("private source verification does not match the commit")
    _check_items(raw)
    if evaluate(raw, GateConfig(expect_sha=expected_sha))["status"] != "PASS":
        raise TransportError("unchanged retention gate did not PASS on private evidence")
    observed = raw["lanes"]["full"]["observed"]
    traces = (raw.get("traces") or {}).get("full")
    if not isinstance(traces, dict) or set(traces) != {row["task_id"] for row in raw["items"]}:
        raise TransportError("private FULL traces are incomplete")
    if sum(len(t.get("executed_tools", [])) for t in traces.values()) != observed["executed"]:
        raise TransportError("observed tool calls differ from private traces")
    if sum(t.get("turns", 0) for t in traces.values()) != observed["model_turns"]:
        raise TransportError("observed model turns differ from private traces")
    if sum(bool(t.get("executed_tools")) for t in traces.values()) != observed["items_with_executed_tool_call"]:
        raise TransportError("observed tool-using items differ from private traces")
    if sum(len(t.get("declined_tools", [])) for t in traces.values()) != observed["declined"]:
        raise TransportError("observed declined calls differ from private traces")
    summary = {
        "model": "sha256:" + _digest(raw["model"].encode("utf-8")),
        "dataset_id": "sha256:" + _digest(raw["dataset_id"].encode("utf-8")),
        "evaluated_sha": expected_sha,
        "modes": {mode: {name: raw["modes"][mode][name] for name in REQUIRED_METRICS} for mode in MODES},
        "lanes": {"full": {"kind": "production_execution_loop", "executes_tools": True,
                           "observed": {key: observed[key] for key in ("model_turns", "executed", "declined", "items_with_executed_tool_call")}}},
        "provenance": {"private_measurement_sha256": _digest(raw_bytes),
                       "corpus_sha256": raw["corpus"]["file_sha256"],
                       "paired_items_sha256": _digest(_canonical(raw["items"])),
                       "model_identity_sha256": _digest(_canonical(raw["model_identity"])),
                       "tracked_files_verified": source["tracked_files_verified"]},
    }
    validate_summary(summary, expected_sha)
    body = MARKER + _canonical(summary).decode("utf-8")
    if len(body.encode("utf-8")) > MAX_COMMENT:
        raise TransportError("redacted summary exceeds the commit-comment limit")
    return {"body": body}


def validate_summary(summary: dict, expected_sha: str) -> None:
    if set(summary) != {"model", "dataset_id", "evaluated_sha", "modes", "lanes", "provenance"}:
        raise TransportError("public summary fields are not allowlisted")
    if summary["evaluated_sha"] != expected_sha:
        raise TransportError("stale commit comment")
    for name in ("model", "dataset_id"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(summary[name])):
            raise TransportError("public identity is not hashed")
    provenance = summary["provenance"]
    if set(provenance) != {"private_measurement_sha256", "corpus_sha256", "paired_items_sha256", "model_identity_sha256", "tracked_files_verified"}:
        raise TransportError("public provenance fields are not allowlisted")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(provenance[k])) for k in provenance if k != "tracked_files_verified"):
        raise TransportError("invalid private evidence digest")
    if type(provenance["tracked_files_verified"]) is not int or provenance["tracked_files_verified"] < 1:
        raise TransportError("missing source verification")
    if set(summary["lanes"]) != {"full"} or set(summary["lanes"]["full"]) != {"kind", "executes_tools", "observed"}:
        raise TransportError("public lane fields are not allowlisted")
    observed = summary["lanes"]["full"]["observed"]
    if set(observed) != {"model_turns", "executed", "declined", "items_with_executed_tool_call"} or any(
            type(value) is not int or value < 0 for value in observed.values()):
        raise TransportError("public observation fields are not allowlisted")
    if set(summary["modes"]) != set(MODES) or any(set(summary["modes"][m]) != set(REQUIRED_METRICS) for m in MODES):
        raise TransportError("public metric fields are not allowlisted")
    for mode in MODES:
        for name in REQUIRED_METRICS:
            allowed = {"score", "samples"} | ({"paired"} if mode != "raw" else set())
            if set(summary["modes"][mode][name]) != allowed:
                raise TransportError("public metric contains non-allowlisted fields")
            if mode != "raw" and set(summary["modes"][mode][name]["paired"]) != {"lost", "gained"}:
                raise TransportError("public paired counts contain non-allowlisted fields")
    if evaluate(summary, GateConfig(expect_sha=expected_sha))["status"] != "PASS":
        raise TransportError("unchanged gate does not PASS on public summary")


def select_comment(comments: list[dict], owner: str, sha: str) -> dict:
    matches = [c for c in comments if isinstance(c, dict) and
               (c.get("user") or {}).get("login", "").casefold() == owner.casefold() and
               c.get("commit_id") == sha and str(c.get("body", "")).startswith(MARKER)]
    if not matches:
        raise TransportError("no owner-authored evidence comment on this exact commit")
    comment = max(matches, key=lambda c: c.get("id", -1))
    body = comment["body"]
    if len(body.encode("utf-8")) > MAX_COMMENT:
        raise TransportError("owner evidence comment exceeds the public summary limit")
    summary = _json(body[len(MARKER):])
    validate_summary(summary, sha)
    return summary


def fetch(repo: str, sha: str, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not SHA.fullmatch(sha) or not token:
        raise TransportError("invalid repository, commit or CI token")
    owner = repo.split("/", 1)[0]
    comments = []
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{repo}/commits/{sha}/comments?per_page=100&page={page}"
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                       "Authorization": f"Bearer {token}",
                                                       "X-GitHub-Api-Version": "2022-11-28"})
        with urllib.request.urlopen(request, timeout=20) as response:
            part = json.load(response)
        if not isinstance(part, list):
            raise TransportError("GitHub did not return commit comments")
        comments.extend(part)
        if len(part) < 100:
            return select_comment(comments, owner, sha)
    raise TransportError("commit-comment pagination exceeded safe limit")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("measurement", type=Path)
    prep.add_argument("--expect-sha", required=True)
    prep.add_argument("--out", type=Path, required=True)
    get = sub.add_parser("fetch")
    get.add_argument("--repo", required=True)
    get.add_argument("--expect-sha", required=True)
    get.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.resolve().is_relative_to(ROOT.resolve()):
            raise TransportError("write owner evidence outside the source checkout")
        if args.action == "prepare":
            current = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                     text=True, check=True, timeout=20).stdout.strip()
            if current != args.expect_sha:
                raise TransportError("exporter checkout is not the measured commit")
            result = prepare(args.measurement.read_bytes(), args.expect_sha)
        else:
            result = fetch(args.repo, args.expect_sha, os.environ.get("GITHUB_TOKEN", ""))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("exact-SHA redacted evidence prepared" if args.action == "prepare" else "exact-SHA owner evidence fetched")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
