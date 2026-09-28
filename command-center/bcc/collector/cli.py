"""``bossman collect "<topic>" --sources <file> --max-pages N``

Dispatched the same way the existing ``bossman market`` command is (see
``bcc/terminal_cli/cli.py:cmd_market``): a standalone, in-process Bossman
command with its own audit ledger and STOP file — it does not need the
Command Center backend running, because there is no model call anywhere in
the pipeline to route through it. STOP/PAUSE are still honoured (see
``control.py``); everything the run does is written to
``<data-dir>/runs/<run-id>/ledger.jsonl`` for audit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import config
from .engine import CollectorConfig, run_collection


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bossman collect",
        description="Human-like data collector: reads owner-approved pages through Bossman's "
                    "own browser, one at a time, and writes a sourced fact bundle.")
    p.add_argument("topic", help="what to research, e.g. "
                                "\"AMD Ryzen AI Max+ 395 memory bandwidth\"")
    p.add_argument("--sources", required=True, metavar="FILE",
                  help="owner-approved source list: one http(s) URL per line, # comments allowed")
    p.add_argument("--max-pages", type=int, default=10,
                  help=f"pages to visit at most this run (hard ceiling {config.MAX_PAGES_CEILING})")
    p.add_argument("--data-dir", default=None, help="where the run bundle/ledger are written "
                                                    "(default: %LOCALAPPDATA%/Bossman/CommandCenter/collector)")
    p.add_argument("--min-delay", type=float, default=config.DEFAULT_DELAY_S,
                  help=f"minimum seconds between pages on the same domain "
                       f"(floor {config.MIN_DELAY_FLOOR_S}s)")
    p.add_argument("--daily-cap", type=int, default=config.DEFAULT_DAILY_CAP,
                  help=f"pages per domain per day at most (ceiling {config.DAILY_CAP_CEILING})")
    p.add_argument("--headed", action="store_true", help="show the browser window (debugging only)")
    p.add_argument("--attributes", default="", help="comma-separated attributes the owner wants "
                                                    "answered; unmatched ones are recorded UNKNOWN")
    p.add_argument("--memory", action="store_true",
                  help="also offer facts to Bossman's memory (bcc.v2.memory.facts.FactStore) "
                       "in an isolated DB under --data-dir; never touches the owner's live data")
    p.add_argument("--json", action="store_true", help="machine-readable summary on stdout")
    return p


async def _offer_to_memory(data_dir: Path, summary, topic: str) -> dict:
    """Best-effort: opens an isolated Services/DB under data_dir (the SAME
    code path the rest of the product uses to write memory facts —
    ``bcc.v2.memory.facts.FactStore`` — never a private table) and offers
    every non-UNKNOWN fact, in append mode. Never raises: a memory failure
    must not throw away a completed, saved collection run."""
    try:
        from ..api import Services
        from ..config import Settings
        from ..v2.memory.facts import FactStore
        from .memory_bridge import offer_facts_to_memory
    except Exception as exc:  # noqa: BLE001
        return {"offered": 0, "error": f"memory path unavailable: {type(exc).__name__}: {exc}"}
    memory_dir = Path(data_dir) / "memory"
    settings = Settings(data_dir=memory_dir,
                        database_url=f"sqlite+aiosqlite:///{memory_dir / 'bcc.db'}")
    try:
        svc = Services(settings, start_workers=False, announce_token=False)
        await svc.start()
        try:
            store = FactStore(svc)
            written = await offer_facts_to_memory(store, summary.facts, topic=topic)
            ok = [row for row in written if not row.get("skipped")]
            skipped = [row for row in written if row.get("skipped")]
            result = {"offered": len(ok)}
            if skipped:
                result["skipped"] = len(skipped)
            return result
        finally:
            await svc.stop()
    except Exception as exc:  # noqa: BLE001
        return {"offered": 0, "error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    data_dir = Path(args.data_dir) if args.data_dir else config.default_data_dir()
    attributes = tuple(a.strip() for a in args.attributes.split(",") if a.strip())
    cfg = CollectorConfig(topic=args.topic, sources_path=Path(args.sources),
                          max_pages=args.max_pages, data_dir=data_dir,
                          min_delay_s=args.min_delay, daily_cap=args.daily_cap,
                          headless=not args.headed, attributes=attributes)
    try:
        summary = asyncio.run(run_collection(cfg))
    except Exception as exc:  # noqa: BLE001 -- CLI boundary: report, do not traceback
        print(f"bossman collect: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    memory_result = None
    if args.memory:
        memory_result = asyncio.run(_offer_to_memory(data_dir, summary, args.topic))
    run_dir = data_dir / "runs" / summary.run_id
    if args.json:
        print(json.dumps({"run_id": summary.run_id, "pages": len(summary.pages),
                          "facts": len([f for f in summary.facts if not f.unknown]),
                          "unknown": len([f for f in summary.facts if f.unknown]),
                          "run_dir": str(run_dir), "memory": memory_result},
                         ensure_ascii=False))
    else:
        print(f"bossman collect: run {summary.run_id} — {len(summary.pages)} page(s), "
             f"{len([f for f in summary.facts if not f.unknown])} fact(s), "
             f"bundle at {run_dir}")
        if memory_result is not None:
            print(f"  memory: {memory_result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
