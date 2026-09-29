"""Speed report of one Master Parser run: analysis and delivery, per participant and overall.

Contains numbers and status codes only; never message text. ``public_report`` drops
every id/label and orders participants as P1..Pn, so it is safe to commit.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA = "bossman.jeff.master-parse.speed.v1"
PUBLIC_SCHEMA = "bossman.jeff.master-parse.speed.public.v1"
PHASES = ("collect", "map", "reduce", "write")


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 3)


def person_speed(*, messages: int, chars: int, stats, analyze_seconds: float,
                 timings: dict | None, narrative_started_at: float | None = None,
                 run_started_at: float = 0.0) -> dict:
    """One participant's speed block. ``stats`` is the merged ``ScopeStats`` list."""
    phases = {name: round(float((timings or {}).get(name, 0.0)), 3) for name in PHASES}
    active = sum(phases.values()) if timings else float(analyze_seconds)
    latencies = [value for st in stats for value in st.latencies]
    ttfp = round(float(timings.get("first_paragraph", 0.0)), 3) if timings and timings.get(
        "first_paragraph") else None
    return {
        "messages": messages, "chars": chars,
        "llm_calls": sum(st.calls for st in stats),
        "empty_answers": sum(st.empty_answers for st in stats),
        "empty_answer_recoveries": sum(st.recoveries for st in stats),
        "phase_seconds": phases, "analyze_seconds": round(float(analyze_seconds), 3),
        "active_seconds": round(active, 3),
        "messages_per_second": round(messages / active, 2) if active > 0 else 0.0,
        "chars_per_second": round(chars / active, 1) if active > 0 else 0.0,
        "latency_p50_seconds": percentile(latencies, 50),
        "latency_p95_seconds": percentile(latencies, 95),
        "time_to_first_paragraph_seconds": ttfp,
        "first_paragraph_at_run_seconds": (
            round(narrative_started_at - run_started_at + ttfp, 3)
            if ttfp is not None and narrative_started_at is not None else None),
        "delivery_seconds": phases["write"],
    }


def build_report(rows: list[dict], all_latencies: list[float], *, run_id: str, model: str,
                 wall_seconds: float, recoveries_unloaded: int) -> dict:
    """``rows`` are participant rows that carry ``speed`` (and ``status``/``narrative``)."""
    blocks = [row["speed"] for row in rows if row.get("speed")]
    phase_totals = {name: round(sum(b["phase_seconds"][name] for b in blocks), 3) for name in PHASES}
    messages = sum(b["messages"] for b in blocks)
    chars = sum(b["chars"] for b in blocks)
    firsts = [b["first_paragraph_at_run_seconds"] for b in blocks
              if b["first_paragraph_at_run_seconds"] is not None]
    active = sum(b["active_seconds"] for b in blocks)
    return {
        "schema": SCHEMA, "run_id": run_id, "model": model,
        "participants": [{"label": row["label"], "status": row["status"],
                          "narrative_status": (row.get("narrative") or {}).get("status", "NONE"),
                          **row["speed"]} for row in rows if row.get("speed")],
        "overall": {
            "participants": len(blocks), "messages": messages, "chars": chars,
            "llm_calls": sum(b["llm_calls"] for b in blocks),
            "empty_answers": sum(b["empty_answers"] for b in blocks),
            "empty_answer_recoveries": sum(b["empty_answer_recoveries"] for b in blocks),
            "runner_unloads": recoveries_unloaded,
            "phase_seconds": phase_totals, "wall_seconds": round(wall_seconds, 3),
            "messages_per_second": round(messages / wall_seconds, 2) if wall_seconds > 0 else 0.0,
            "chars_per_second": round(chars / wall_seconds, 1) if wall_seconds > 0 else 0.0,
            "latency_p50_seconds": percentile(all_latencies, 50),
            "latency_p95_seconds": percentile(all_latencies, 95),
            "time_to_first_paragraph_seconds": min(firsts) if firsts else None,
            "delivery_seconds": phase_totals["write"],
            "sum_participant_active_seconds": round(active, 3),
        },
    }


def public_report(report: dict) -> dict:
    """Ordinal P1..Pn, no ids, no labels, no text, no dates: safe to commit."""
    people = [{"participant": f"P{index}", **{k: v for k, v in person.items() if k != "label"}}
              for index, person in enumerate(report["participants"], 1)]
    return {"schema": PUBLIC_SCHEMA, "model": report["model"], "overall": report["overall"],
            "participants": people}


def table_ru(report: dict) -> str:
    head = "Участник  сообщ  символов  вызовов  пустых/восст  сбор  map   reduce  запись  сообщ/с  симв/с  p50   p95   1-й абзац"
    lines = [head]
    for index, p in enumerate(report["participants"], 1):
        ph = p["phase_seconds"]
        first = p["time_to_first_paragraph_seconds"]
        lines.append(
            f"P{index:<8} {p['messages']:<6} {p['chars']:<9} {p['llm_calls']:<8} "
            f"{p['empty_answers']}/{p['empty_answer_recoveries']:<11} {ph['collect']:<5} {ph['map']:<5} "
            f"{ph['reduce']:<7} {ph['write']:<7} {p['messages_per_second']:<8} {p['chars_per_second']:<7} "
            f"{p['latency_p50_seconds']:<5} {p['latency_p95_seconds']:<5} {first if first is not None else '-'}")
    o = report["overall"]
    lines.append(
        f"Итого     {o['messages']:<6} {o['chars']:<9} {o['llm_calls']:<8} "
        f"{o['empty_answers']}/{o['empty_answer_recoveries']:<11} {o['phase_seconds']['collect']:<5} "
        f"{o['phase_seconds']['map']:<5} {o['phase_seconds']['reduce']:<7} {o['phase_seconds']['write']:<7} "
        f"{o['messages_per_second']:<8} {o['chars_per_second']:<7} {o['latency_p50_seconds']:<5} "
        f"{o['latency_p95_seconds']:<5} {o['time_to_first_paragraph_seconds'] if o['time_to_first_paragraph_seconds'] is not None else '-'}")
    lines.append(f"Всего {o['wall_seconds']} с; запись (доставка) {o['delivery_seconds']} с; "
                 f"выгрузок модели {o['runner_unloads']}.")
    return "\n".join(lines)


def write_reports(path: Path, report: dict) -> tuple[Path, Path]:
    """Full report at ``path``; sanitized ``speed_report_public.json`` beside it."""
    from bcc.auth import _restrict_to_owner

    from ..vault import _atomic_json
    path = Path(path)
    public = path.with_name("speed_report_public.json")
    _atomic_json(path, report)
    _atomic_json(public, public_report(report))
    try:
        _restrict_to_owner(path)
    except Exception:  # noqa: BLE001 — best effort; the public file is meant to be shareable
        pass
    return path, public


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
