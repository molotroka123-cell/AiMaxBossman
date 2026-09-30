"""`bossman autonomy ...` - the autonomy control plane in Bossman CMD.

    bossman autonomy status [--json]
    bossman autonomy goals [--state S] [--json]
    bossman autonomy journal verify [--json]
    bossman autonomy constitution status [--json]
    bossman autonomy constitution pin            (the user, in an interactive terminal only)
    bossman autonomy stop [--reason R]           emergency STOP of the loop (also kills the worker trees)
    bossman autonomy resume                      clear the autonomy STOP (not the owner's global STOP)
    bossman autonomy plan [--redteam-model M]    propose ONE goal from metrics / JUnit / backlog / verified lessons
                                                 (PROPOSED only; it never runs a goal)
    bossman autonomy run [--max-cycles N] [--max-hours H] [--interval-s S] [--dry]
                                                 bounded supervisor (default: 1 cycle, 2 hours); stops at the owner gate
    bossman autonomy lesson list|verify ID|withdraw ID   experience memory (verify: owner, interactive terminal)
    bossman autonomy skills list|revoke NAME VERSION     skill proposals and skills

Same data dir as the backend (``<data dir>/autonomy``: BCC_DATA_DIR or the
installed default), so the terminal, the dashboard and the loop see the same
goals, journal and lease. Exit codes: 0 ok, 1 verification failure, 2 usage,
5 BLOCKED / refused, 6 stopped, 9 not found. Experience saved by the loop is
retrieval context: WEIGHTS_UNCHANGED, never training.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from ..terminal_cli.records import EXIT_BLOCKED, EXIT_FAIL, EXIT_NOT_FOUND, EXIT_OK, EXIT_USAGE
from . import constitution as const
from .service import LEARNING_KIND, WEIGHTS, AutonomyService


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
    st = sub.add_parser("status", help="constitution, level, lease, goals, journal, STOP, budget, mode")
    g = sub.add_parser("goals", help="list goals")
    g.add_argument("--state", help="only goals in this state")
    j = sub.add_parser("journal", help="journal operations")
    j.add_argument("action", choices=("verify",))
    c = sub.add_parser("constitution", help="constitution status / owner pin (pin: interactive terminal only)")
    c.add_argument("action", choices=("status", "pin"))
    sp_stop = sub.add_parser("stop", help="emergency STOP of the autonomy loop")
    sp_stop.add_argument("--reason", default="", help="why (kept in the STOP file and the journal)")
    sp_resume = sub.add_parser("resume", help="clear the autonomy STOP (the owner's global STOP stays)")
    pl = sub.add_parser("plan", help="propose ONE goal (PROPOSED; never runs it)")
    pl.add_argument("--redteam-model", default="", help="model for the identity red-team metric "
                                                          "(default: BOSSMAN_AUTONOMY_JEFF_MODEL; none = NOT_RUN)")
    pl.add_argument("--redteam-endpoint", default="", help="OpenAI-compatible endpoint (default: local Ollama)")
    pl.add_argument("--repo", default="", help="repository whose code the red team measures (default: this checkout)")
    pl.add_argument("--remote-planner", action="store_true",
                    help="let the free Nemotron model choose among candidates (needs a live price 0/0 check)")
    rn = sub.add_parser("run", help="bounded supervisor: run the loop with hard limits")
    rn.add_argument("--max-cycles", type=int, default=1, help="cycles to run (default 1, at most 25)")
    rn.add_argument("--max-hours", type=float, default=2.0, help="wall-clock limit (default 2, at most 24)")
    rn.add_argument("--interval-s", type=float, default=0.0, help="wait this long when idle (0 = single sweep)")
    rn.add_argument("--dry", action="store_true", help="preflight and show what would run; starts nothing")
    rn.add_argument("--repo", default="", help="git checkout the writer clones (default: this checkout)")
    rn.add_argument("--seed", default="0", help="seed of the Claude/Codex writer draw")
    rn.add_argument("--claude-model", default="", help="Claude CLI model alias (for example haiku)")
    rn.add_argument("--codex-model", default="", help="Codex CLI model")
    rn.add_argument("--max-cli-turns", type=int, default=0, help="claude -p --max-turns N (0 = CLI default)")
    ls = sub.add_parser("lesson", help="experience memory: list / verify / withdraw")
    ls.add_argument("action", choices=("list", "verify", "withdraw"))
    ls.add_argument("lesson_id", nargs="?", default="")
    ls.add_argument("--reason", default="withdrawn by the owner")
    sk = sub.add_parser("skills", help="skill proposals and skills: list / revoke")
    sk.add_argument("action", choices=("list", "revoke"))
    sk.add_argument("name", nargs="?", default="")
    sk.add_argument("version", nargs="?", type=int, default=0)
    sk.add_argument("--reason", default="revoked by the owner")
    for sp in (st, g, j, c, sp_stop, sp_resume, pl, rn, ls, sk):     # options accepted before or after the subcommand
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
        print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    else:
        print(text)


def _repo(args) -> Path:
    return Path(args.repo).resolve() if getattr(args, "repo", "") else const.repo_root()


def _status_text(s: dict) -> str:
    lease = s["lease"]
    mode, stop, promo = s["mode"], s["stop"], s["promotion"]
    b = s["budget"]
    return (f"loop: {s['loop']}" + (f" ({s['reason']})" if s["reason"] else "") +
            f"\nlevel: {s['level']}   autonomous apply: {mode['autonomous_apply']} (cap {mode['max_level']})"
            f"\nSTOP: " + ("ACTIVE " + ", ".join(sorted(stop["sources"])) if stop["active"] else "none") +
            "\nlease: " +
            (f"{lease['holder']} on {lease['goal_id']}" + (f" (stale: {lease['stale_reason']})"
                                                           if lease.get("stale_reason") else "")
             if lease else "free") +
            f"\ngoals: {s['goals_total']} " + json.dumps(s["goals"], ensure_ascii=False) +
            f"\njournal: {'OK' if s['journal']['ok'] else 'BROKEN'} ({s['journal']['entries']} entries)"
            f"\ndaily budget ({b['day']}): cycles {b['used']['cycles']}/{b['limits']['cycles_per_day']}, "
            f"turns {b['used']['turns']}/{b['limits']['turns_per_day']}, usd {b['used']['usd']}/{b['limits']['usd_per_day']}"
            f"\npromotion: {promo['clean_cycles']}/{promo['required']} clean cycles (read-only, never changes the level)"
            f"\nexperience: {WEIGHTS} ({LEARNING_KIND})")


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
        _emit(args, s, _status_text(s))
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
    if args.cmd == "stop":
        res = auto.request_stop(by="owner-cli", reason=args.reason)
        _emit(args, res, f"STOP set ({res['stop']['at']}); killed worker processes: {res['killed'] or 'none'}\n"
                         "clear it with: bossman autonomy resume")
        return EXIT_OK
    if args.cmd == "resume":
        res = auto.clear_stop(by="owner-cli")
        _emit(args, res, ("autonomy STOP cleared" if res["cleared"] else "no autonomy STOP was set")
              + (f"\n{res['note']}" if res["note"] else ""))
        return EXIT_BLOCKED if res["state"]["active"] else EXIT_OK
    if args.cmd == "plan":
        return _plan(args, auto)
    if args.cmd == "run":
        return _run(args, auto)
    if args.cmd == "lesson":
        return _lesson(args, auto)
    if args.cmd == "skills":
        return _skills(args, auto)
    parser.print_help()
    return EXIT_USAGE


# ------------------------------------------------------------------ plan


def _plan(args, auto: AutonomyService) -> int:
    from .experience import ExperienceWriter
    from .goals import GoalError
    from .planner import Planner, PlannerBudget, build_plan_inputs, plan_goal

    model = args.redteam_model or os.environ.get("BOSSMAN_AUTONOMY_JEFF_MODEL", "").strip()
    endpoint = args.redteam_endpoint or os.environ.get("BOSSMAN_AUTONOMY_JEFF_ENDPOINT", "http://127.0.0.1:11434/v1")
    metrics = None
    redteam = "NOT_RUN (no model: pass --redteam-model or set BOSSMAN_AUTONOMY_JEFF_MODEL)"
    if model:
        from .identity_task import probe_in_checkout
        metrics = asyncio.run(probe_in_checkout(_repo(args), model=model, endpoint=endpoint))
        redteam = "measured" if metrics else "NOT_RUN (the red-team suite could not be measured)"
    lessons = ExperienceWriter(auto.root, journal=auto.journal).verified()
    inp = build_plan_inputs(auto.root, redteam_metrics=metrics, verified_lessons=lessons)
    planner = Planner(budget=PlannerBudget(), journal=auto.journal)
    if args.remote_planner:                                  # pragma: no cover - needs the live free-model price check
        from .cycle import openrouter_nemotron
        nemo, facts, why = asyncio.run(openrouter_nemotron())
        if nemo is not None:
            planner = Planner(remote_chat=lambda _model, messages: nemo.chat(messages), remote_facts=facts,
                              budget=PlannerBudget(), journal=auto.journal)
    res = asyncio.run(plan_goal(auto.root, inp, planner=planner))
    out = {"created": None, "state": None, "source": res.source, "candidates": res.candidates, "reason": res.reason,
           "red_team": redteam, "inputs": {"metrics": inp.metrics, "junit_failures": len(inp.logs),
                                           "backlog": len(inp.backlog), "verified_lessons": len(inp.lessons)}}
    if res.goal is None:
        _emit(args, out, f"no goal proposed: {res.reason}\nred-team: {redteam}")
        return EXIT_OK
    try:
        auto.goals.create(res.goal)
        out.update(created=res.goal.goal_id, state="PROPOSED")
        auto.journal.append("plan.proposed", {"goal_id": res.goal.goal_id, "source": res.source,
                                              "candidates": res.candidates, "reason": res.reason[:300]})
        text = f"proposed {res.goal.goal_id} (PROPOSED, chosen by {res.source}); it is NOT running"
    except GoalError as exc:
        out.update(created=None, state="EXISTS", error=str(exc)[:300])
        text = f"not created: {exc}"
    _emit(args, out, text + f"\nred-team: {redteam}\nnext: bossman autonomy run --dry, then run")
    return EXIT_OK


# ------------------------------------------------------------------ run


def _run(args, auto: AutonomyService) -> int:
    from .supervisor import EXIT_BY_STATUS, Supervisor, SupervisorConfig

    cfg = SupervisorConfig(max_cycles=args.max_cycles, max_hours=args.max_hours, interval_s=args.interval_s,
                           dry=args.dry)

    def make_cycle():                                          # pragma: no cover - real CLIs, owner machine
        from .cycle import AutonomyCycle, CycleConfig, openrouter_nemotron, real_deps
        deps = real_deps(auto.root, _repo(args), data_dir=auto.data_dir, constitution_path=args.constitution,
                         pin_path=args.pin_path)
        if os.environ.get("OPENROUTER_API_KEY"):
            deps.nemotron, deps.nemotron_facts, _why = asyncio.run(openrouter_nemotron())
        models = {k: v for k, v in (("claude", args.claude_model), ("codex", args.codex_model)) if v}
        # level: the constitution starts the system at L2 at most; there is no flag that raises it
        return AutonomyCycle(deps, CycleConfig(level=2, seed=args.seed, models=models,
                                               max_cli_turns=args.max_cli_turns or None))

    rep = asyncio.run(Supervisor(auto, make_cycle, cfg).run())
    _emit(args, rep.as_dict(), f"supervisor: {rep.status}" + (f" ({rep.reason})" if rep.reason else "") +
          f"\ncycles: {rep.cycles}\nheartbeat: {auto.root / 'heartbeat.json'}")
    return EXIT_BY_STATUS.get(rep.status, EXIT_FAIL)


# ------------------------------------------------------------------ lessons / skills


def _lesson(args, auto: AutonomyService) -> int:
    from .experience import ExperienceWriter, owner_gate

    xp = ExperienceWriter(auto.root, journal=auto.journal)
    if args.action == "list":
        rows = xp.listing()
        _emit(args, {"items": rows, "weights": WEIGHTS, "learning_kind": LEARNING_KIND},
              "\n".join(f"{r['lesson_id']}  {r['status']:<10} x{r['occurrences']}  {r['correction'][:90]}"
                        for r in rows) or "no lessons")
        return EXIT_OK
    if not args.lesson_id:
        print("lesson id required", file=sys.stderr)
        return EXIT_USAGE
    try:
        if args.action == "withdraw":
            xp.withdraw(args.lesson_id, by="owner-cli", reason=args.reason)
            _emit(args, {"ok": True, "lesson_id": args.lesson_id, "status": "withdrawn"}, "withdrawn")
            return EXIT_OK
        ok, why = owner_gate(args.lesson_id.rsplit(":", 1)[-1])
        if not ok:
            _emit(args, {"ok": False, "reason": why}, f"REFUSED: {why}")
            return EXIT_BLOCKED
        xp.verify(args.lesson_id, by="owner")
        _emit(args, {"ok": True, "lesson_id": args.lesson_id, "status": "verified", "weights": WEIGHTS},
              f"verified {args.lesson_id} ({WEIGHTS}: retrieval context only)")
        return EXIT_OK
    except KeyError:
        _emit(args, {"ok": False, "reason": "unknown lesson"}, "unknown lesson")
        return EXIT_NOT_FOUND
    except Exception as exc:  # noqa: BLE001 - LessonError / store invariants: show the reason
        _emit(args, {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}, f"REFUSED: {exc}")
        return EXIT_BLOCKED


def _skills(args, auto: AutonomyService) -> int:
    from .skills import SkillStore

    store = SkillStore(auto.root, journal=auto.journal)
    if args.action == "list":
        active = [{"name": s.name, "version": s.version, "confidence": s.confidence, "weights": s.weights}
                  for s in store.all_latest()]
        props = [{"artifact_hash": p["artifact_hash"], "name": p["spec"]["name"], "status": p["status"]}
                 for p in store.proposals()]
        _emit(args, {"skills": active, "proposals": props, "weights": WEIGHTS, "learning_kind": LEARNING_KIND},
              "skills: " + (", ".join(f"{a['name']} v{a['version']}" for a in active) or "none") +
              "\nproposals: " + (", ".join(f"{p['name']} {p['artifact_hash'][:12]}" for p in props) or "none"))
        return EXIT_OK
    if not args.name or not args.version:
        print("usage: skills revoke NAME VERSION [--reason R]", file=sys.stderr)
        return EXIT_USAGE
    try:
        store.revoke(args.name, args.version, args.reason)
    except KeyError:
        _emit(args, {"ok": False, "reason": "unknown skill"}, "unknown skill")
        return EXIT_NOT_FOUND
    _emit(args, {"ok": True}, f"revoked {args.name} v{args.version}")
    return EXIT_OK


__all__ = ["build_parser", "main"]
