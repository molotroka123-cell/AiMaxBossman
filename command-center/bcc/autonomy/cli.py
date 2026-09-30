"""`bossman autonomy ...` - the autonomy control plane in Bossman CMD.

    bossman autonomy status [--json]
    bossman autonomy goals [--state S] [--json]
    bossman autonomy journal verify [--json]
    bossman autonomy constitution status [--json]
    bossman autonomy constitution pin            (the user, in an interactive terminal only)

Same data dir as the backend (``<data dir>/autonomy``: BCC_DATA_DIR or the
installed default), so the terminal, the dashboard and the loop see the same
goals, journal and lease. Exit codes: 0 ok, 2 usage, 5 BLOCKED / refused,
9 not found, 1 verification failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..terminal_cli.records import EXIT_BLOCKED, EXIT_FAIL, EXIT_OK, EXIT_USAGE
from . import constitution as const
from .service import AutonomyService


GLOBAL = (("--data-dir", "Bossman data dir (default: BCC_DATA_DIR or the installed default)"),
          ("--constitution", "constitution file (default: docs/constitution/BOSSMAN_CONSTITUTION.md)"),
          ("--pin-path", r"pin file (default: %%LOCALAPPDATA%%\Bossman\autonomy\constitution.sha256)"))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bossman autonomy",
                                description="Bossman autonomy: constitution, goals, journal (same data as the backend).")
    for flag, text in GLOBAL:
        p.add_argument(flag, help=text)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd")
    st = sub.add_parser("status", help="constitution, level, lease, goals, journal")
    g = sub.add_parser("goals", help="list goals")
    g.add_argument("--state", help="only goals in this state")
    j = sub.add_parser("journal", help="journal operations")
    j.add_argument("action", choices=("verify",))
    c = sub.add_parser("constitution", help="constitution status / owner pin (pin: interactive terminal only)")
    c.add_argument("action", choices=("status", "pin"))
    for sp in (st, g, j, c):                  # options accepted before or after the subcommand
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        for flag, _ in GLOBAL:
            sp.add_argument(flag, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    return p


def _data_dir(args) -> Path:
    if getattr(args, "data_dir", None):
        return Path(args.data_dir)
    from ..config import _data_dir as default
    return default()


def _emit(args, obj: dict, text: str) -> None:
    if args.json:
        print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True))
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.cmd is None:
        parser.print_help()
        return EXIT_USAGE

    if args.cmd == "constitution":
        if args.action == "pin":
            auto = AutonomyService(_data_dir(args), constitution_path=args.constitution,
                                   pin_path=args.pin_path)
            res = const.pin(args.constitution, args.pin_path, journal=auto.journal)
            _emit(args, {"ok": res.ok, "sha": res.sha, "reason": res.reason},
                  f"{'PINNED' if res.ok else 'REFUSED'}: {res.reason}" + (f" ({res.sha})" if res.sha else ""))
            return EXIT_OK if res.ok else EXIT_BLOCKED
        st = const.verify(args.constitution, args.pin_path)
        _emit(args, st.as_dict(), f"{st.status}: {st.reason}\n  sha:    {st.sha or '-'}\n"
                                  f"  pinned: {st.pinned_sha or '-'}\n  pin:    {st.pin_path}")
        return EXIT_OK if st.ok else EXIT_BLOCKED

    auto = AutonomyService(_data_dir(args), constitution_path=args.constitution, pin_path=args.pin_path)
    if args.cmd == "status":
        s = auto.status()
        lease = s["lease"]
        text = (f"loop: {s['loop']}" + (f" ({s['reason']})" if s["reason"] else "") +
                f"\nlevel: {s['level']}\nlease: " +
                (f"{lease['holder']} on {lease['goal_id']}" + (f" (stale: {lease['stale_reason']})"
                                                               if lease.get("stale_reason") else "")
                 if lease else "free") +
                f"\ngoals: {s['goals_total']} " + json.dumps(s["goals"], ensure_ascii=False) +
                f"\njournal: {'OK' if s['journal']['ok'] else 'BROKEN'} ({s['journal']['entries']} entries)")
        _emit(args, s, text)
        return EXIT_OK if s["loop"] == "READY" else EXIT_BLOCKED
    if args.cmd == "goals":
        items = [g for g in auto.list_goals() if not args.state or g["state"] == args.state]
        lines = [f"{g['goal_id']:<24} {g['state']:<14} {g['risk_tier']:<30} {g['sha'][:10] or '-':<10} "
                 f"{','.join(g['approvals']) or '-'}" + (f"  BLOCKED: {g['blocked_reason']}"
                                                         if g["state"] == "BLOCKED" else "") for g in items]
        _emit(args, {"items": items}, "\n".join(lines) if lines else "no goals")
        return EXIT_OK
    if args.cmd == "journal":
        v = auto.journal.verify()
        _emit(args, {"ok": v.ok, "entries": v.entries, "head": v.head, "reason": v.reason, "bad_seq": v.bad_seq},
              f"{'OK' if v.ok else 'TAMPERED'}: {v.entries} entries, head {v.head[:16]}"
              + (f" - {v.reason} (seq {v.bad_seq})" if not v.ok else ""))
        return EXIT_OK if v.ok else EXIT_FAIL
    parser.print_help()
    return EXIT_USAGE


__all__ = ["build_parser", "main"]
