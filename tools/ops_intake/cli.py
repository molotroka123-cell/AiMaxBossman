"""CLI: ``python -m ops_intake.cli run --input X --out DIR [--window-min 1440]`` (run from the ``tools`` directory).

Local only: reads one file, writes files into --out. No network, no model call, no sending, no CRM write."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

from . import SCHEMA, __version__, report, table
from .dedupe import dedupe
from .extract import Taxonomy, detect_and_load, extract, default_taxonomy, mark_answered, parse_ts

ASSUMPTIONS = [
    "Timestamps of CSV/JSON leads without a time zone are read as UTC.",
    "Telegram time comes from date_unixtime (UTC); relative deadlines are resolved against the message's local date.",
    "A bare 9/10-digit phone has no country and is flagged, never completed.",
    "A thousands-or-decimal amount like 5.000 / 2,500 is flagged, never guessed.",
    "Amounts of different currencies are reported per currency and never summed or converted.",
    "Senders are the anonymised S-labels of exchange_volume.parse; display names are not read.",
]
LIMITS = [
    "No CRM connector and no sending: notifications are DRAFT text files for a human.",
    "Split requests (name in one message, phone in the next) are separate rows, flagged for review, not stitched.",
    "Service detection is a keyword taxonomy (RU/EN/CS words); it does not understand negation or context.",
    "Answered status needs Telegram reply links (or a status column); otherwise it is reported as unknown.",
    "Duplicate matching is rule based and conservative: uncertain pairs are flagged, not merged.",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(input_path, out_dir, *, window_min: int = 1440, lang: str = "ru", as_of=None, staff=(),
        require=("service", "contact"), services_path=None, sla_hours: float = 24.0, tz_offset_min: int = 0,
        xlsx: bool = True, min_confidence: str = "medium") -> dict:
    t0 = time.perf_counter()
    src = Path(input_path)
    out = Path(out_dir)
    if lang not in ("ru", "en"):
        raise ValueError("--lang must be ru or en")
    if window_min < 0:
        raise ValueError("--window-min must be >= 0")
    as_of_dt = None
    if as_of:
        as_of_dt = parse_ts(as_of)
        if as_of_dt is None:
            raise ValueError(f"--as-of {as_of!r} is not an ISO timestamp")
    sha = _sha256(src)
    tax = Taxonomy.from_file(services_path) if services_path else default_taxonomy()

    t = time.perf_counter()
    load = detect_and_load(src, staff)
    t_load = time.perf_counter() - t

    t = time.perf_counter()
    extracted = [extract(i, tax) for i in load.items]
    mark_answered(extracted, load)
    t_extract = time.perf_counter() - t

    t = time.perf_counter()
    requests = [e for e in extracted if e.is_request]
    skipped = [e for e in extracted if not e.is_request]
    result = dedupe(requests, window_min=window_min, min_confidence=min_confidence)
    t_dedupe = time.perf_counter() - t

    t = time.perf_counter()
    require = tuple(require)
    rows = table.build_rows(result, require)
    dup_rows = table.build_duplicate_rows(result, rows)
    skipped_rows = table.build_skipped_rows(skipped, load.unparsed)
    review_rows = [r for r in rows if r["needs_review"] == "yes"]
    params = {"window_min": window_min, "lang": lang, "as_of": as_of_dt, "sla_hours": sla_hours,
              "tz_offset_min": tz_offset_min, "input_sha256": sha, "input_name": src.name}
    summary = report.summarize(load=load, extracted=extracted, result=result, rows=rows, params=params)
    t_tables = time.perf_counter() - t

    t = time.perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    table.write_csv(out / "table.csv", table.COLUMNS, rows)
    table.write_csv(out / "duplicates.csv", table.DUPLICATE_COLUMNS, dup_rows)
    table.write_csv(out / "needs_review.csv", table.COLUMNS, review_rows)
    table.write_csv(out / "skipped.csv", table.SKIPPED_COLUMNS, skipped_rows)
    xlsx_info = {"written": False, "reason": "disabled by --no-xlsx", "sheets": []}
    if xlsx:
        xlsx_info = table.write_xlsx(out / "table.xlsx", {
            "requests": (table.COLUMNS, rows), "duplicates": (table.DUPLICATE_COLUMNS, dup_rows),
            "needs_review": (table.COLUMNS, review_rows), "skipped": (table.SKIPPED_COLUMNS, skipped_rows)})
    (out / "daily_report.md").write_text(report.render_report(summary, rows, load, params, lang), encoding="utf-8")
    (out / "notifications_draft.md").write_text(report.render_notifications(summary, rows, lang), encoding="utf-8")
    t_write = time.perf_counter() - t

    by_reason = collections.Counter(r for row in review_rows for r in row["review_reasons"].split("; ") if r)
    outputs = {}
    for f in sorted(out.iterdir()):
        if f.is_file() and f.name != "run.json":
            outputs[f.name] = {"bytes": f.stat().st_size, "sha256": _sha256(f)}
    total = time.perf_counter() - t0
    run_info = {
        "schema": SCHEMA, "tool_version": __version__,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "input": {"path": str(src), "name": src.name, "sha256": sha, "bytes": src.stat().st_size,
                  "type": load.source_type, "items": load.total_items, "meta": load.meta,
                  "ignored": load.ignored},
        "params": {"window_min": window_min, "lang": lang, "as_of": as_of, "staff": list(staff),
                   "require": list(require), "services_file": str(services_path) if services_path else None,
                   "sla_hours": sla_hours, "tz_offset_min": tz_offset_min, "min_confidence": min_confidence},
        "counts": summary["counts"], "reconciliation": summary["reconciliation"], "ids": summary["ids"],
        "skipped_by_reason": summary["skipped_by_reason"], "unparsed": load.unparsed,
        "warnings": summary["warnings"],
        "manual_review": {"rows": len(review_rows), "of_unique_requests": len(rows),
                          "share": round(len(review_rows) / len(rows), 3) if rows else 0.0,
                          "by_reason": dict(by_reason.most_common())},
        "timings_s": {"load": round(t_load, 4), "extract": round(t_extract, 4), "dedupe": round(t_dedupe, 4),
                      "tables_and_summary": round(t_tables, 4), "write_outputs": round(t_write, 4),
                      "total": round(total, 4)},
        "machine": {"python": platform.python_version(), "platform": platform.platform(),
                    "processor": platform.processor()},
        "xlsx": xlsx_info, "outputs": outputs, "assumptions": ASSUMPTIONS, "limits": LIMITS,
        "no_network_no_model_no_send": True,
    }
    (out / "run.json").write_text(json.dumps(run_info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return run_info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ops_intake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="extract, de-duplicate, tabulate, report")
    r.add_argument("--input", required=True, help="Telegram result.json / messages.html, or CSV / JSONL / JSON leads")
    r.add_argument("--out", required=True, help="output directory (created)")
    r.add_argument("--window-min", type=int, default=1440, help="duplicate window from the first request, minutes")
    r.add_argument("--lang", default="ru", choices=["ru", "en"], help="language of report and drafts")
    r.add_argument("--as-of", help="ISO timestamp (UTC) for SLA ages; default: latest message in the input")
    r.add_argument("--staff", action="append", default=[],
                   help="Telegram from_id or display name of a staff sender (repeatable); result.json only")
    r.add_argument("--require", default="service,contact",
                   help="fields whose absence sends a row to review: " + ",".join(table.REQUIRE_CHOICES))
    r.add_argument("--services", help="JSON taxonomy file {service: [regex...]} or {service: {strong, weak}}")
    r.add_argument("--sla-hours", type=float, default=24.0)
    r.add_argument("--tz-offset-min", type=int, default=0, help="offset used only to bucket the per-day table")
    r.add_argument("--min-confidence", default="medium", choices=["high", "medium"],
                   help="weakest duplicate rule that is merged (weaker ones are only flagged)")
    r.add_argument("--no-xlsx", action="store_true")
    args = ap.parse_args(argv)
    try:
        info = run(args.input, args.out, window_min=args.window_min, lang=args.lang, as_of=args.as_of,
                   staff=args.staff, require=tuple(x.strip() for x in args.require.split(",") if x.strip()),
                   services_path=args.services, sla_hours=args.sla_hours, tz_offset_min=args.tz_offset_min,
                   xlsx=not args.no_xlsx, min_confidence=args.min_confidence)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)   # ASCII: any console
        return 2
    c = info["counts"]
    print(json.dumps({"input_items": c["input_items"], "unique_requests": c["unique_requests"],
                      "duplicates_merged": c["duplicates_merged"], "needs_review": c["needs_review"],
                      "unparsed": c["unparsed"], "skipped": c["skipped"], "out": str(args.out),
                      "elapsed_s": info["timings_s"]["total"], "xlsx": info["xlsx"]["written"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
