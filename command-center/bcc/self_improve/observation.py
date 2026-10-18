"""Observation record: one row per finished task run, the only thing that feeds the loop.

Fields (all required; ``N/A`` where the source does not carry the datum - never invented):
task_id, source_sha (git SHA of the code that ran), instruction_hash + instruction_version,
expected_criterion, outcome (PASS/FAIL/BLOCKED/NOT_RUN/N/A), error_class, duration_s, cost_usd,
evidence_source (path or API locator) + evidence_sha256.

``outcome`` is never PASS just because the engine said ``completed``: PASS needs a criterion that
was checked by something other than the run itself. Without it the outcome is ``N/A``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .limits import BLOCKED, FAIL, NA, NOT_RUN, PASS
from .redact import assert_clean, sanitize

SCHEMA_VERSION = 1
OUTCOMES = (PASS, FAIL, BLOCKED, NOT_RUN, NA)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{7,40}$")
REQUIRED = ("task_id", "source_sha", "instruction_hash", "instruction_version", "expected_criterion", "outcome",
            "error_class", "duration_s", "cost_usd", "evidence_source", "evidence_sha256", "agent_id")


@dataclass(frozen=True)
class Observation:
    task_id: str
    agent_id: str
    source_sha: str            # git sha or N/A
    instruction_hash: str      # sha256 of the instruction text, or N/A
    instruction_version: str   # human label, e.g. "agent-12@<first 12 of hash>"
    expected_criterion: str    # short statement or N/A
    outcome: str
    error_class: str           # "none" for a clean outcome
    duration_s: float | str    # float or N/A
    cost_usd: float | str
    evidence_source: str
    evidence_sha256: str
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_observation(rec: Any) -> list[str]:
    """Return a list of problems; empty means valid."""
    if not isinstance(rec, dict):
        return ["not an object"]
    errs = [f"missing field: {k}" for k in REQUIRED if k not in rec]
    if errs:
        return errs
    if rec["outcome"] not in OUTCOMES:
        errs.append(f"outcome must be one of {OUTCOMES}")
    if rec["source_sha"] != NA and not _GIT.match(str(rec["source_sha"])):
        errs.append("source_sha must be a git sha or N/A")
    for k in ("instruction_hash", "evidence_sha256"):
        if rec[k] != NA and not _SHA.match(str(rec[k])):
            errs.append(f"{k} must be 64 hex chars or N/A")
    for k in ("duration_s", "cost_usd"):
        v = rec[k]
        if v != NA and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0):
            errs.append(f"{k} must be a non-negative number or N/A")
    for k in ("task_id", "agent_id", "instruction_version", "expected_criterion", "error_class", "evidence_source"):
        if not isinstance(rec[k], str) or not rec[k].strip() or len(rec[k]) > 300:
            errs.append(f"{k} must be a non-empty string up to 300 chars")
    if rec["outcome"] == PASS and rec["expected_criterion"] == NA:
        errs.append("PASS requires a checked expected_criterion (engine 'completed' alone is not PASS)")
    if rec["outcome"] in (FAIL, BLOCKED) and rec["error_class"] == "none":
        errs.append("a FAIL/BLOCKED outcome needs an error_class")
    if not errs:
        try:
            assert_clean(rec)
        except ValueError as exc:
            errs.append(str(exc))
    return errs


_CLASS_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("timeout", re.compile(r"time ?out|timed out|deadline|слишком долго", re.I)),
    ("provider_down", re.compile(r"provider_down|connection|нет связи|unreachable|refused|offline", re.I)),
    ("budget", re.compile(r"budget|cap\b|quota|лимит|402|429|rate.?limit", re.I)),
    ("action_contract", re.compile(r"action_contract", re.I)),
    ("tool_error", re.compile(r"tool|инструмент|permission|denied|forbidden", re.I)),
    ("verification", re.compile(r"verif|test(s)? fail|assert", re.I)),
    ("parse", re.compile(r"json|parse|schema|decode", re.I)),
)


def classify_error(raw: str | None) -> str:
    """Stable, low-cardinality class so that repeated failures can be counted. Raw text is never kept."""
    if not raw or not str(raw).strip():
        return "unknown"
    text = str(raw)
    for name, rx in _CLASS_RULES:
        if rx.search(text):
            return name
    head = re.split(r"[/:\s]", text.strip(), maxsplit=1)[0].lower()
    head = re.sub(r"[^a-z_]", "", head)
    return head[:40] or "other"


_STATUS_MAP = {"failed": FAIL, "blocked": BLOCKED, "stopped": NOT_RUN, "cancelled": NOT_RUN}


def observations_from_api(tasks: Iterable[dict], agents: Iterable[dict], *, source_sha: str = NA,
                          evidence_source: str = "bcc-api:/api/tasks") -> list[dict]:
    """Turn an ``/api/tasks`` snapshot (each task carries ``last_run``) into validated, sanitized rows.
    Prompts, results and titles are NOT copied; only hashes and classes."""
    by_id = {str(a.get("id")): a for a in agents}
    out: list[dict] = []
    for t in tasks:
        run = t.get("last_run") or {}
        st = str(t.get("status") or "")
        if st in ("draft", "queued", "running", "paused", "waiting_approval") or not isinstance(run, dict):
            continue
        agent = by_id.get(str(t.get("agent_id")), {})
        instr = str(agent.get("system_prompt") or "")
        ihash = sha256_text(instr) if instr else NA
        outcome = _STATUS_MAP.get(st, NA)
        cls = classify_error(run.get("error")) if outcome in (FAIL, BLOCKED) else "none"
        dur: float | str = NA
        if run.get("started_at") and run.get("finished_at"):
            dur = _seconds(run["started_at"], run["finished_at"])
        cost = run.get("cost_usd")
        rec_src = json.dumps({k: run.get(k) for k in ("id", "task_id", "status", "error", "started_at", "finished_at",
                                                      "cost_usd", "tokens_in", "tokens_out", "model_alias")},
                             sort_keys=True, ensure_ascii=False)
        rec = {
            "task_id": f"task-{t.get('id')}", "agent_id": str(t.get("agent_id") or NA),
            "source_sha": source_sha, "instruction_hash": ihash,
            "instruction_version": f"agent-{t.get('agent_id')}@{ihash[:12]}" if ihash != NA else NA,
            "expected_criterion": NA, "outcome": outcome, "error_class": cls,
            "duration_s": dur, "cost_usd": float(cost) if isinstance(cost, (int, float)) else NA,
            "evidence_source": f"{evidence_source}#task={t.get('id')}/run={run.get('id', NA)}",
            "evidence_sha256": sha256_text(rec_src), "schema_version": SCHEMA_VERSION,
        }
        rec = sanitize(rec) | {"agent_id": rec["agent_id"]}
        if not validate_observation(rec):
            out.append(rec)
    return out


def _seconds(a: str, b: str) -> float | str:
    from datetime import datetime
    try:
        d = (datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds()
        return round(max(d, 0.0), 3)
    except ValueError:
        return NA
