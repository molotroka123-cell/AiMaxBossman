"""CLI: ``python -m botlab arena|learn|trainer-ui|profiles|gen-preflop``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .profiles import STUDENT, TRAINING_OPPONENTS, Profile, get_profile

APP_ROOT = Path(__file__).resolve().parents[1]
RUNS = APP_ROOT / "runs"


def _load_profile(spec: str) -> Profile:
    path = Path(spec)
    if path.suffix == ".json" and path.is_file():
        return Profile.load(path)
    return get_profile(spec)


def cmd_profiles(_: argparse.Namespace) -> int:
    for p in (*TRAINING_OPPONENTS.values(), STUDENT):
        tag = "training opponent" if p.training_opponent else "learner start"
        print(f"{p.name:<18} [{tag}] vpip={p.vpip:.2f} pfr={p.pfr:.2f} value={p.value_threshold:.2f} "
              f"aggr={p.aggression:.2f} bluff={p.bluff:.2f} slack={p.call_slack:+.2f} size={p.bet_size:.2f}  {p.description}")
    return 0


def _rake_from(args: argparse.Namespace, big_blind: int):
    from .engine import Rake

    if not getattr(args, "rake_pct", 0):
        return None
    return Rake(pct=args.rake_pct, cap=int(round(args.rake_cap_bb * big_blind)))


def cmd_arena(args: argparse.Namespace) -> int:
    from .arena import run_arena

    profiles = [_load_profile(s) for s in args.bots.split(",")]
    res = run_arena(profiles, hands=args.hands, seed=args.seed, big_blind=args.big_blind,
                    stack_bb=args.stack_bb, duplicate=not args.no_duplicate, keep_samples=args.samples,
                    rake=_rake_from(args, args.big_blind))
    print(res.table())
    print(f"\nhands={res.hands} deals={res.deals} seed={res.seed} duplicate={res.duplicate} "
          f"chip_conservation={res.to_dict()['chip_conservation']}")
    for line in res.sample_hands:
        print(line)
    if args.out:
        Path(args.out).write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from .learn import LearnConfig, learn

    cfg = LearnConfig(generations=args.generations, population=args.population,
                      deals_per_candidate=args.deals, test_deals=args.test_deals, seed=args.seed)
    start = _load_profile(args.start) if args.start else STUDENT
    report = learn(cfg, start=start, out_root=Path(args.runs_dir))
    print(json.dumps({k: report[k] for k in ("verdict", "improvement_supported_on_unseen", "elapsed_sec", "total_hands_simulated")},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    import time

    from . import report as rp

    started = time.time()
    out = Path(args.out) if args.out else Path(args.runs_dir) / ("report-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    if args.history:
        path = Path(args.history)
        if not path.is_file():
            print(f"history file not found: {path}", file=sys.stderr)
            return 2
        hands, meta = rp.load_history(path, default_bb=args.big_blind or None)
        if not hands:
            print("no hands could be parsed from the file", file=sys.stderr)
            return 3
        body = rp.report_from_hands(hands, meta, args.big_blind or None)
        rep = rp.build_report("history", body, meta, None, started)
    else:
        opps = [o for o in args.opponents.split(",") if o]
        body = rp.report_from_simulation(args.hero, opps, args.hands, args.seed, args.big_blind or 2, args.rake_pct,
                                         args.rake_cap_bb, _load_profile)
        meta = {"hero": args.hero, "opponents": opps, "hands_requested": args.hands, "seed": args.seed,
                "big_blind": args.big_blind or 2}
        rake = {"pct": args.rake_pct, "cap_bb": args.rake_cap_bb, "no_flop_no_drop": True} if args.rake_pct > 0 else None
        rep = rp.build_report("simulation", body, meta, rake, started)
    j, m = rp.write_report(rep, out)
    print(f"report: {m}")
    print(f"json:   {j}")
    return 0


def cmd_trainer_ui(args: argparse.Namespace) -> int:
    from .trainer_ui import NotLoopbackError, TrainerSession

    profile = _load_profile(args.profile) if args.profile else STUDENT
    out = Path(args.runs_dir) / ("trainer-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    try:
        session = TrainerSession(args.url, profile, out, table=args.table, dry_run=args.dry_run,
                                 headless=not args.headed, screenshots=args.screenshots, seed=args.seed)
    except NotLoopbackError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    summary = session.run(args.hands)
    print(json.dumps({k: v for k, v in summary.items() if k != "hand_results"}, indent=2, ensure_ascii=False))
    print(f"log: {out / 'decisions.jsonl'}")
    return 0


def cmd_gen_preflop(args: argparse.Namespace) -> int:
    from .equity import generate_preflop_table, write_preflop_table

    table = generate_preflop_table(args.sims)
    write_preflop_table(table, args.sims)
    print(f"wrote preflop table ({len(table)} classes, {args.sims} sims each)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="botlab", description="Poker bot training lab (local engine + local trainer only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profiles", help="list training opponents")
    p.set_defaults(fn=cmd_profiles)

    p = sub.add_parser("arena", help="play N hands between bots and print stats")
    p.add_argument("--bots", default="Student (start),Rock,TAG Trainer,Calling Station,Maniac,Scared Money",
                   help="comma-separated profile names or learned_profile.json paths (2-6)")
    p.add_argument("--hands", type=int, default=3000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--big-blind", type=int, default=2)
    p.add_argument("--stack-bb", type=int, default=100)
    p.add_argument("--no-duplicate", action="store_true", help="disable seat-rotation duplicate dealing")
    p.add_argument("--samples", type=int, default=0, help="print N sample hand summaries")
    p.add_argument("--rake-pct", type=float, default=0.0, help="cash rake as a fraction of the contested pot (0 = none)")
    p.add_argument("--rake-cap-bb", type=float, default=3.0, help="rake cap in big blinds (used with --rake-pct)")
    p.add_argument("--out", help="write JSON result here")
    p.set_defaults(fn=cmd_arena)

    p = sub.add_parser("learn", help="cross-entropy search vs TRAIN pool, evaluate on UNSEEN pool")
    p.add_argument("--generations", type=int, default=10)
    p.add_argument("--population", type=int, default=16)
    p.add_argument("--deals", type=int, default=60, help="deals per candidate (x6 hands)")
    p.add_argument("--test-deals", type=int, default=600, help="held-out deals per pool for the final test (x6 hands)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--start", help="start profile name or JSON (default: Student)")
    p.add_argument("--runs-dir", default=str(RUNS))
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("trainer-ui", help="play the owner's LOCAL trainer (loopback only) via Playwright")
    p.add_argument("--url", default="http://127.0.0.1:8925/")
    p.add_argument("--hands", type=int, default=5)
    p.add_argument("--table", default="NL10", choices=["NL2", "NL5", "NL10", "NL25"])
    p.add_argument("--profile", help="profile name or learned_profile.json (default: Student)")
    p.add_argument("--dry-run", action="store_true", help="read state and decide, never click a poker action")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--screenshots", type=int, default=6)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--runs-dir", default=str(RUNS))
    p.set_defaults(fn=cmd_trainer_ui)

    p = sub.add_parser("report", help="training report from a local hand history (--history) or a seeded simulation")
    p.add_argument("--history", help="Poker Train session export JSON or hands JSONL (local file)")
    p.add_argument("--hero", default="Student (start)", help="simulation: hero profile name or learned_profile.json")
    p.add_argument("--opponents", default="Rock,TAG Trainer,Calling Station,Maniac,Scared Money")
    p.add_argument("--hands", type=int, default=3000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--big-blind", type=int, default=0, help="chips per big blind (history: fallback; simulation: default 2)")
    p.add_argument("--rake-pct", type=float, default=0.0)
    p.add_argument("--rake-cap-bb", type=float, default=3.0)
    p.add_argument("--out", help="output directory (default runs/report-<time>)")
    p.add_argument("--runs-dir", default=str(RUNS))
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("gen-preflop", help="regenerate data/preflop_equity.json")
    p.add_argument("--sims", type=int, default=4000)
    p.set_defaults(fn=cmd_gen_preflop)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
