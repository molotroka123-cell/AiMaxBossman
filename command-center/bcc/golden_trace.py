"""§9 — the 202-event acceptance trace as a regression corpus, not a souvenir.

`docs/testing/acceptance-run-20260906/traces-20260908/tasks-trace.jsonl` is the
only record of how this system behaved under a real owner-run acceptance. Left
as a document it is read once and forgotten; the pathologies it caught then
come back and nobody notices until the next manual run.

This module turns it into scenarios a test can assert against. It does NOT
replay the events through the engine — the trace records an OBSERVER's view
(task ids, statuses, interventions), not a deterministic input tape, and
pretending otherwise would build a replay that proves whatever the extractor
was written to find. What it does instead is extract, from the raw evidence,
the six pathologies §9 names, so a regression test can assert two separate
things:

  1. the corpus still contains the pathology it was chosen for — otherwise the
     test is asserting against an empty set and would pass forever; and
  2. current code makes that shape impossible.

The raw file is immutable. Everything here is derived and re-derived on every
run, so a scenario cannot silently drift away from its evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: Repository-relative, so the corpus travels with the tests that need it.
TRACE_PATH = ("docs/testing/acceptance-run-20260906/traces-20260908/tasks-trace.jsonl")
EXPECTED_EVENTS = 202


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for parent in [here, *here.parents]:
        if (parent / TRACE_PATH).exists():
            return parent
    raise FileNotFoundError(f"golden corpus not found above {here}")


def load(path: Path | None = None) -> list[dict[str, Any]]:
    """Events, oldest first. The file is UTF-8 with a BOM — written on Windows
    by the acceptance run, and read as-is rather than rewritten, because the
    raw evidence stays immutable."""
    target = path or (repo_root() / TRACE_PATH)
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


@dataclass
class Scenario:
    """One extracted pathology.

    `evidence` holds the raw events it was derived from, so a failing
    regression can print what the corpus actually said instead of a summary
    somebody wrote by hand."""
    name: str
    description: str
    count: int
    evidence: list[dict[str, Any]] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.count > 0


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str).lower()


# --------------------------------------------------------------- extraction

def review_deadlocks(events: Iterable[dict[str, Any]]) -> Scenario:
    """`waiting_approval` recorded alongside zero pending approvals.

    Matched on the observer's own words rather than on a status field alone:
    the corpus states the pathology explicitly ("deadlocked in waiting_approval
    with 0 pending approvals", "0 pending approvals; review_escalated"), and a
    status match by itself would also catch every healthy park."""
    hits: list[dict[str, Any]] = []
    for event in events:
        payload = _payload(event)
        blob = _text(payload)
        if "waiting_approval" not in blob:
            continue
        # Two independent signals, because the observer wrote the same finding
        # two ways: the explicit count for T1/T2, and the word "deadlock" for
        # T3, whose verdict says "review_escalated -> waiting_approval deadlock
        # (4th deadlock of session)" without repeating the count. Matching only
        # the first shape would silently under-count the corpus and make the
        # regression weaker than the evidence it is built from.
        zero_pending = (payload.get("pending_approvals") == 0
                        or "0 pending approvals" in blob
                        or "deadlock" in blob)
        if zero_pending:
            hits.append(event)
    tasks = sorted({str(e.get("task_id")) for e in hits})
    return Scenario(
        "review_deadlock",
        "task parked in waiting_approval with no approval anyone could decide",
        len(hits), hits, {"tasks": tasks})


def approval_storm(events: Iterable[dict[str, Any]]) -> Scenario:
    """Owner confirmations, and how they piled onto individual tasks."""
    grants = [e for e in events
              if e.get("phase") == "intervention"
              and _payload(e).get("type") == "approval_granted"]
    per_task: dict[str, int] = {}
    for event in grants:
        key = str(event.get("task_id"))
        per_task[key] = per_task.get(key, 0) + 1
    worst = max(per_task.items(), key=lambda kv: kv[1], default=("", 0))
    return Scenario(
        "approval_storm",
        "owner confirmations for a single acceptance session",
        len(grants), grants,
        {"per_task": per_task, "worst_task": worst[0], "worst_count": worst[1]})


def token_burn(events: Iterable[dict[str, Any]]) -> Scenario:
    """The largest token and cost totals any single task reached."""
    peak_tokens, peak_cost, worst_task = 0, 0.0, ""
    hits: list[dict[str, Any]] = []
    for event in events:
        payload = _payload(event)
        tokens_in = payload.get("tokens_in")
        if not isinstance(tokens_in, (int, float)):
            continue
        total = int(tokens_in) + int(payload.get("tokens_out") or 0)
        hits.append(event)
        if total > peak_tokens:
            peak_tokens, worst_task = total, str(event.get("task_id"))
        cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)):
            peak_cost = max(peak_cost, float(cost))
    return Scenario(
        "token_burn", "peak tokens and cost reached by one task",
        len(hits), hits,
        {"peak_tokens": peak_tokens, "peak_cost_usd": round(peak_cost, 6),
         "worst_task": worst_task})


def provider_down(events: Iterable[dict[str, Any]]) -> Scenario:
    """The negative control the session already contains: an expired provider
    key. Valuable precisely because the system behaved correctly — it failed
    rather than inventing success — and that must not regress either."""
    hits = [e for e in events
            if e.get("phase") in ("run_error_observed", "task_failed")
            and ("401" in _text(_payload(e)) or "expired" in _text(_payload(e)))]
    return Scenario("provider_down",
                    "provider rejected the key; the task failed honestly",
                    len(hits), hits)


def owner_interventions(events: Iterable[dict[str, Any]]) -> Scenario:
    """Every point a human had to touch the run, by kind. `/stop` appearing
    here at all is the finding: it was the only escape from the deadlock."""
    hits = [e for e in events if e.get("phase") == "intervention"]
    kinds: dict[str, int] = {}
    for event in hits:
        kind = str(_payload(event).get("type") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    return Scenario("owner_interventions",
                    "points where a human had to act for the run to continue",
                    len(hits), hits, {"kinds": kinds})


def repeated_reviews(events: Iterable[dict[str, Any]]) -> Scenario:
    """Review verdicts the corpus itself calls false negatives, plus the
    phantom `file:example.com` obligation that caused two of them."""
    hits: list[dict[str, Any]] = []
    phantom = 0
    for event in events:
        blob = _text(_payload(event))
        if "false-negative" in blob or "false negative" in blob:
            hits.append(event)
        if "example.com" in blob and "file" in blob:
            phantom += 1
    return Scenario("repeated_reviews",
                    "reviews that failed work which was actually correct",
                    len(hits), hits, {"phantom_obligation_events": phantom})


def cloud_routed_success(events: Iterable[dict[str, Any]]) -> Scenario:
    """Tasks that did reach a terminal verdict, so the corpus is not only
    failures — a regression suite built solely from pathologies cannot tell
    "we fixed it" from "we broke everything"."""
    hits = [e for e in events if e.get("phase") in ("final_verdict", "terminal_status")]
    verdicts: dict[str, int] = {}
    for event in hits:
        verdict = str(_payload(event).get("verdict") or _payload(event).get("status") or "")
        if verdict:
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
    return Scenario("cloud_routed_tasks", "tasks that reached a terminal verdict",
                    len(hits), hits, {"verdicts": verdicts})


EXTRACTORS = {
    "review_deadlock": review_deadlocks,
    "approval_storm": approval_storm,
    "token_burn": token_burn,
    "provider_down": provider_down,
    "owner_interventions": owner_interventions,
    "repeated_reviews": repeated_reviews,
    "cloud_routed_tasks": cloud_routed_success,
}


def scenarios(events: Iterable[dict[str, Any]] | None = None) -> dict[str, Scenario]:
    rows = list(events) if events is not None else load()
    return {name: fn(rows) for name, fn in EXTRACTORS.items()}


def summary(events: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A compact report of what the corpus proves, for the acceptance record."""
    rows = list(events) if events is not None else load()
    found = scenarios(rows)
    return {"events": len(rows),
            "scenarios": {name: {"count": s.count, **s.detail}
                          for name, s in found.items()}}
