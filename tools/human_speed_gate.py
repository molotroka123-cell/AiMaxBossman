"""Measured latency gates. A component PASS never activates V5/N0.

UI evidence is supplied by the independent visible-user run, not synthesized by
these microbenchmarks. This validator checks provenance fields, not the honesty
of an arbitrary producer; retain the original trace and trusted run identity.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"


def finite_nonnegative(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def latency_summary(samples_ms: list[float], *, limit_ms: float,
                    minimum: int = 100, percentile: int = 95,
                    max_isolated_stalls: int = 0,
                    body_percentile: int = 95,
                    body_margin: float = 0.5) -> dict[str, Any]:
    """Nearest-rank percentile, retaining outliers and every timed attempt.

    Ни один замер не выбрасывается: `outliers_removed` всегда 0, а все замеры
    сверх предела перечисляются в отчёте.

    `max_isolated_stalls` существует ради одного конкретного случая, который
    иначе путают с регрессией. На общем CI-раннере запись на диск изредка
    попадает в чужой fsync: измеренный p100 разово подскакивал до 78 мс при
    пределе 10 мс, тогда как локально 300 записей подряд дают p50 около 1.2 мс
    и НИ ОДНОГО замера ≥10 мс. Это стоимость хозяйского хранилища, а не
    стоимость операции.

    Прощение срыва обставлено так, чтобы за ним нельзя было спрятать
    настоящее замедление:

    * срывов должно быть не больше объявленного числа (по умолчанию ноль,
      то есть поведение прежних вызовов не меняется);
    * тело распределения обязано лежать ГЛУБОКО внутри предела
      (p`body_percentile` < `limit_ms` * `body_margin`).

    Систематическое замедление двигает всё тело распределения и проваливает
    гейт, сколько бы срывов ни было разрешено. Требование к человеческой
    скорости не ослаблено: оно предъявлено к операции, а не к худшей секунде
    чужого диска.
    """
    if (type(samples_ms) is not list or not finite_nonnegative(limit_ms)
            or limit_ms == 0 or type(minimum) is not int or minimum < 1
            or type(percentile) is not int or not 1 <= percentile <= 100
            or type(max_isolated_stalls) is not int or max_isolated_stalls < 0
            or type(body_percentile) is not int or not 1 <= body_percentile <= 100
            or not finite_nonnegative(body_margin) or not 0 < body_margin <= 1):
        raise ValueError("invalid latency-gate configuration")
    if any(not finite_nonnegative(x) for x in samples_ms):
        return {"status": FAIL, "reason": "invalid_sample", "n": len(samples_ms)}
    if len(samples_ms) < minimum:
        return {"status": INSUFFICIENT, "reason": "sample_count", "n": len(samples_ms)}
    ordered = sorted(samples_ms)
    value = ordered[math.ceil(len(ordered) * percentile / 100) - 1]
    over = [x for x in ordered if x >= limit_ms]
    body = ordered[math.ceil(len(ordered) * body_percentile / 100) - 1]
    report = {"status": PASS if value < limit_ms else FAIL, "n": len(ordered),
              "percentile": percentile, "value_ms": value,
              "p50_ms": ordered[math.ceil(len(ordered) / 2) - 1],
              "max_ms": ordered[-1], "limit_ms": limit_ms,
              "comparison": "strictly_less_than", "outliers_removed": 0,
              "basis": "absolute_percentile", "over_limit": len(over),
              "stalls_ms": over[:5], "body_percentile": body_percentile,
              "body_ms": body, "max_isolated_stalls": max_isolated_stalls,
              "body_allowance_ms": limit_ms * body_margin}
    if report["status"] == FAIL and max_isolated_stalls:
        if len(over) <= max_isolated_stalls and body < limit_ms * body_margin:
            report["status"] = PASS
            report["basis"] = "isolated_host_stall_forgiven"
        else:
            report["reason"] = ("too_many_stalls" if len(over) > max_isolated_stalls
                                else "body_not_inside_limit")
    return report


def validate_ui_trace(trace: Any, *, expected_sha: str) -> dict[str, Any]:
    """Five sessions x 100 genuine input/visible-ack pairs, one browser clock.

    CLI/API/controller timings are rejected as UI input evidence. Missing ACKs
    fail instead of disappearing from the denominator. The owner run must keep
    original screenshot/trace artifacts; JSON alone is not hardware attestation.
    """
    def bad(reason: str, status: str = FAIL) -> dict[str, Any]:
        return {"status": status, "reason": reason, "n0_activation_authorized": False}

    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("full expected code SHA required")
    if trace is None:
        return bad("visible_input_trace_not_supplied", INSUFFICIENT)
    if (not isinstance(trace, dict) or type(trace.get("schema_version")) is not int
            or trace["schema_version"] != 1):
        return bad("trace_schema")
    if trace.get("code_sha") != expected_sha or trace.get("dirty") is not False:
        return bad("stale_or_dirty_source")
    if trace.get("tier") != "USER_UI_LIVE":
        return bad("wrong_evidence_tier")
    sessions = trace.get("sessions")
    if not isinstance(sessions, list) or len(sessions) < 5:
        return bad("need_five_sessions", INSUFFICIENT)
    ids: set[str] = set()
    summaries = []
    for session in sessions:
        if not isinstance(session, dict):
            return bad("session_schema")
        sid = session.get("session_id")
        if not isinstance(sid, str) or not sid or sid in ids:
            return bad("session_identity")
        ids.add(sid)
        if (not finite_nonnegative(session.get("time_origin_ms"))
                or not isinstance(session.get("trace_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", session["trace_sha256"])):
            return bad("clock_or_trace_provenance")
        events = session.get("events")
        if not isinstance(events, list):
            return bad("events_schema")
        seen: set[str] = set()
        samples: list[float] = []
        last_start = -1.0
        for event in events:
            if not isinstance(event, dict):
                return bad("event_schema")
            eid = event.get("event_id")
            if not isinstance(eid, str) or not eid or eid in seen:
                return bad("event_identity")
            seen.add(eid)
            if (event.get("kind") not in ("click", "key")
                    or event.get("trusted") is not True
                    or event.get("ack_visible") is not True):
                return bad("untrusted_or_unacknowledged_input")
            start, end = event.get("input_ms"), event.get("ack_ms")
            if (not finite_nonnegative(start) or not finite_nonnegative(end)
                    or end < start or start < last_start):
                return bad("invalid_clock_order")
            last_start = start
            samples.append(end - start)
        summaries.append(latency_summary(samples, limit_ms=100.0))
    status = (FAIL if any(s["status"] == FAIL for s in summaries) else
              INSUFFICIENT if any(s["status"] == INSUFFICIENT for s in summaries) else PASS)
    return {"status": status, "sessions": summaries,
            "scope": "visible_click_key_acknowledgement_only",
            "human_comparison": "NOT_RUN", "n0_activation_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-sha", required=True)
    parser.add_argument("--ui-trace", type=Path)
    args = parser.parse_args()
    try:
        trace = json.loads(args.ui_trace.read_text(encoding="utf-8")) if args.ui_trace else None
        result = validate_ui_trace(trace, expected_sha=args.expect_sha)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": FAIL, "reason": type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] == PASS else 2 if result["status"] == INSUFFICIENT else 1


if __name__ == "__main__":
    raise SystemExit(main())
