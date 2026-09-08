"""Run the V7 convergence gates and report what they returned.

The night's objectives were verified by hand the first time: three scripts and
a dozen pytest invocations, in an order you had to already know. That is not a
result anybody else can reproduce, and an unreproducible result is the same
class of claim as a report written from merged code rather than from a run.

So this is a driver, not a judge. Every objective maps to checks that already
exist — a suite, a script, a number inside an evidence file — and its verdict
is whatever those returned. Nothing here knows how to make an objective pass;
there is no table of expected outcomes and no place to write one. An objective
whose checks could not run at all reports NOT_RUN with the reason, because
"we could not look" and "we looked and it failed" are different findings and
collapsing them is how a red run gets reported green.

    python -m bossman.v7.nightly_run --objectives B2,B3,B4,B5,P0 --timeout 8h
    python -m bossman.v7.nightly_run --list
    python -m bossman.v7.nightly_run --dry-run

Exit: 0 when every selected objective passed, 1 otherwise (NOT_RUN included —
an unverified objective is not a passed one).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"

#: Where the driver writes its own summary. The gates keep writing their own
#: evidence where they always did; this file only records what was run.
DEFAULT_OUT = "docs/testing/v7-nightly-run.json"

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.I)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """`8h`, `90m`, `600s`, `600`. A budget nobody can express is a budget
    nobody sets, and an unbudgeted nightly run is one that hangs until morning."""
    match = _DURATION.match(str(text))
    if not match:
        raise argparse.ArgumentTypeError(
            f"не срок: {text!r} (ожидается 8h / 90m / 600s / 600)")
    value = float(match.group(1)) * _UNITS[match.group(2).lower()]
    if value <= 0:
        raise argparse.ArgumentTypeError(f"срок должен быть положительным: {text!r}")
    return value


def repo_root(start: Path | None = None) -> Path:
    """The checkout, found by what it contains rather than by how deep we are."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "command-center").is_dir() and (candidate / "bossman-core").is_dir():
            return candidate
    raise RuntimeError("не найден корень репозитория (нет command-center/ рядом с bossman-core/)")


# --------------------------------------------------------------------- gates

@dataclass
class Result:
    name: str
    status: str
    detail: str
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"gate": self.name, "status": self.status, "detail": self.detail,
                "seconds": round(self.seconds, 2)}


@dataclass
class Gate:
    """One check. `argv` runs it; `verify` reads whatever it wrote."""
    name: str
    argv: Sequence[str] = ()
    cwd: str = "command-center"
    verify: Callable[[Path], tuple[str, str]] | None = None
    #: A script that measures several things reports one overall verdict, and
    #: that verdict is not this gate's question: the P0 gate asks whether the
    #: deadlock rate is zero, not whether every other benchmark in the same
    #: file also passed. Where the verifier judges a specific number, the exit
    #: code stops being the answer — but a crash still has to be caught, which
    #: `fresh` below does.
    judge_exit: bool = True
    #: Evidence this gate is expected to (re)write. A file left over from an
    #: earlier run reads exactly like a successful one, so a stale file is
    #: treated as no file at all.
    fresh: str = ""

    def run(self, root: Path, budget: float) -> Result:
        started = time.time()
        if budget <= 0:
            return Result(self.name, NOT_RUN, "бюджет времени исчерпан до запуска")
        detail = ""
        if self.argv:
            try:
                proc = subprocess.run([sys.executable, *self.argv], cwd=root / self.cwd,
                                      capture_output=True, text=True, timeout=budget)
            except subprocess.TimeoutExpired:
                return Result(self.name, FAIL, f"не уложился в {budget:.0f}s",
                              time.time() - started)
            except OSError as exc:
                return Result(self.name, NOT_RUN, f"не запустилось: {exc}",
                              time.time() - started)
            detail = _tail(proc.stdout) or _tail(proc.stderr)
            if proc.returncode != 0 and (self.judge_exit or _could_not_look(proc)):
                # An import error is "we could not look"; a failing assertion is
                # "we looked and it failed". Reporting both as FAIL would hide
                # a missing dependency behind a red result nobody can act on.
                status = NOT_RUN if _could_not_look(proc) else FAIL
                return Result(self.name, status, f"exit={proc.returncode}: {detail}",
                              time.time() - started)
            if self.fresh and not _rewritten_since(root / self.fresh, started):
                return Result(self.name, NOT_RUN,
                              f"{self.fresh} не обновлён этим прогоном — числа старые",
                              time.time() - started)
        if self.verify is not None:
            try:
                status, detail = self.verify(root)
            except FileNotFoundError as exc:
                status, detail = NOT_RUN, f"нет файла с доказательством: {exc}"
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                status, detail = FAIL, f"доказательство нечитаемо: {type(exc).__name__}: {exc}"
            return Result(self.name, status, detail, time.time() - started)
        return Result(self.name, PASS, detail or "ok", time.time() - started)


def _tail(text: str, limit: int = 400) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return (" | ".join(lines[-3:]))[-limit:]


def _rewritten_since(path: Path, started: float) -> bool:
    """Did this run actually produce the file, or is it yesterday's?"""
    try:
        return path.stat().st_mtime >= started - 1.0
    except OSError:
        return False


def _could_not_look(proc: subprocess.CompletedProcess) -> bool:
    """pytest exits 2/3/4 on collection and usage errors, 1 on real failures."""
    blob = f"{proc.stdout}\n{proc.stderr}"
    return proc.returncode in (2, 3, 4) or "ModuleNotFoundError" in blob


def pytest_gate(name: str, *paths: str) -> Gate:
    return Gate(name, ("-m", "pytest", "-q", "-p", "no:cacheprovider", *paths))


def _evidence(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.exists():
        raise FileNotFoundError(relative)
    return json.loads(path.read_text(encoding="utf-8"))


def _benchmark(root: Path, name: str) -> dict[str, Any]:
    for row in _evidence(root, "docs/testing/convergence-metrics-20260908.json")["benchmarks"]:
        if row["benchmark"] == name:
            return row
    raise KeyError(name)


# ------------------------------------------------------------------ verifiers

def _verify_deadlock_rate(root: Path) -> tuple[str, str]:
    row = _benchmark(root, "review_deadlock")
    rate = row["review_deadlock_rate"]
    return (PASS if rate == 0 else FAIL,
            f"reproduced={row['reproduced']} deadlocked_after_sweep="
            f"{row['deadlocked_after_sweep']} rate={rate}")


def _verify_three_system(root: Path) -> tuple[str, str]:
    ev = _evidence(root, "docs/testing/three-system-qa-20260908.json")
    answered = [s["system"] for s in ev["systems"] if s["status"] == "ok"]
    ok = (ev["verdict"] == "PASS" and ev["negative_controls_pass"]
          and ev["manual_interventions_total"] == 0)
    return (PASS if ok else FAIL,
            f"{len(answered)}/{len(ev['systems'])} систем ответили, "
            f"negative_controls={ev['negative_controls_pass']}, "
            f"manual_interventions={ev['manual_interventions_total']}")


def _verify_approvals(root: Path) -> tuple[str, str]:
    rows = [_benchmark(root, f"doc_edit/{shape}")
            for shape in ("read_modify_verify", "single_write")]
    ok = all(r["approvals_pass"] and not r["same_effect_asked_twice"] for r in rows)
    shown = ", ".join(f"{r['benchmark'].split('/')[1]}={r['approvals_asked']}"
                      f"({'+'.join(r['distinct_effect_classes'])})" for r in rows)
    return PASS if ok else FAIL, f"{shown}; корпус спрашивал 60"


def _verify_tokens(root: Path) -> tuple[str, str]:
    rows = [_benchmark(root, f"doc_edit/{shape}")
            for shape in ("read_modify_verify", "single_write")]
    worst = max(r["tokens_total"] for r in rows)
    ok = all(r["tokens_pass"] and r["completed"] for r in rows)
    return PASS if ok else FAIL, f"худший прогон {worst} токенов; корпус сжёг 1 295 189"


def _verify_recovery(root: Path) -> tuple[str, str]:
    row = _benchmark(root, "recovery_on_expired_key")
    ok = row["pass"] and row["escalated_to_owner"]
    return (PASS if ok else FAIL,
            f"provider_calls={row['provider_calls']} из бюджета {row['retry_budget']}, "
            f"escalated_to_owner={row['escalated_to_owner']}")


# ------------------------------------------------------------------ objectives

@dataclass
class Objective:
    key: str
    title: str
    gates: list[Gate] = field(default_factory=list)


#: Objectives keyed as the mission brief names them. Order is the order the
#: brief puts them in: the P0 first, because a deadlocked queue makes every
#: later measurement a measurement of the deadlock.
OBJECTIVES: dict[str, Objective] = {
    "P0": Objective("P0", "review/escalation deadlock", [
        pytest_gate("suite: review deadlock", "tests/test_p0_review_deadlock.py"),
        Gate("metric: review_deadlock_rate == 0",
             ("-m", "scripts.convergence_metrics",
              "--out", "../docs/testing/convergence-metrics-20260908.json"),
             verify=_verify_deadlock_rate, judge_exit=False,
             fresh="docs/testing/convergence-metrics-20260908.json"),
        Gate("metric: escalation to the owner, not to the retry budget",
             verify=_verify_recovery),
    ]),
    "STORM": Objective("STORM", "approval storm / coalescing", [
        pytest_gate("suite: approval scope and leases", "tests/test_approval_scope.py"),
        Gate("metric: one question per distinct real effect", verify=_verify_approvals),
    ]),
    "TOKENS": Objective("TOKENS", "token and review loop guards", [
        pytest_gate("suite: mission budget", "tests/test_mission_budget.py"),
        Gate("metric: a documentation edit under 100k tokens", verify=_verify_tokens),
    ]),
    "B3": Objective("B3", "Apps control without the hidden env ritual", [
        pytest_gate("suite: apps control policy", "tests/test_apps_control.py"),
    ]),
    "B4": Objective("B4", "unified streaming layer", [
        pytest_gate("suite: streaming contract", "tests/test_streaming_contract.py"),
    ]),
    "B5": Objective("B5", "honest model health and fallback", [
        pytest_gate("suite: model health", "tests/test_model_health.py"),
    ]),
    "B2": Objective("B2", "secure local cloud-QA relay", [
        pytest_gate("suite: relay contract", "tests/test_qa_relay.py"),
        Gate("3-system QA over the relay, negative controls included",
             ("-m", "scripts.three_system_qa",
              "--out", "../docs/testing/three-system-qa-20260908.json"),
             verify=_verify_three_system,
             fresh="docs/testing/three-system-qa-20260908.json"),
    ]),
    "GOLDEN": Objective("GOLDEN", "202-event golden trace replay", [
        pytest_gate("suite: golden trace replay", "tests/test_golden_trace_replay.py"),
    ]),
    "V7": Objective("V7", "reality compiler, world state, recovery, shadow telemetry", [
        pytest_gate("suite: V7 reality core", "tests/test_v7_reality.py"),
    ]),
    "UX": Objective("UX", "navigation consolidation", [
        pytest_gate("suite: navigation shape", "tests/test_ux_navigation_shape.py"),
    ]),
}

#: What `--objectives all` means. Explicit rather than `OBJECTIVES.keys()` so
#: adding an objective is a decision about the nightly run, not a side effect.
ALL = ["P0", "STORM", "TOKENS", "B3", "B4", "B5", "B2", "GOLDEN", "V7", "UX"]


def select(raw: str) -> list[str]:
    if raw.strip().lower() in ("", "all"):
        return list(ALL)
    keys, unknown = [], []
    for part in raw.split(","):
        key = part.strip().upper()
        if not key:
            continue
        (keys if key in OBJECTIVES else unknown).append(key)
    if unknown:
        raise argparse.ArgumentTypeError(
            f"неизвестные цели: {', '.join(unknown)}; известны: {', '.join(ALL)}")
    if not keys:
        return list(ALL)
    # Canonical order, not the order they were typed: the P0 runs first because
    # a deadlocked queue turns every later measurement into a measurement of
    # the deadlock. `--objectives B2,P0` must not silently mean "measure the
    # relay while the queue is still jammed".
    return sorted(set(keys), key=ALL.index)


def roll_up(results: list[Result]) -> str:
    """An objective is only as verified as its least verified gate."""
    if any(r.status == FAIL for r in results):
        return FAIL
    if any(r.status == NOT_RUN for r in results) or not results:
        return NOT_RUN
    return PASS


def run(keys: Sequence[str], *, root: Path, budget: float,
        echo: Callable[[str], None] = print) -> dict[str, Any]:
    started = time.time()
    objectives: list[dict[str, Any]] = []
    for key in keys:
        objective = OBJECTIVES[key]
        echo(f"\n[{key}] {objective.title}")
        results: list[Result] = []
        for gate in objective.gates:
            remaining = budget - (time.time() - started)
            result = gate.run(root, remaining)
            results.append(result)
            echo(f"  {result.status:8s} {gate.name}  ({result.seconds:.1f}s)"
                 + (f"\n           {result.detail}" if result.detail else ""))
        objectives.append({"objective": key, "title": objective.title,
                           "status": roll_up(results),
                           "gates": [r.to_dict() for r in results]})
    passed = sum(1 for o in objectives if o["status"] == PASS)
    return {
        "run": "v7_nightly_run",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_seconds": round(time.time() - started, 2),
        "budget_seconds": budget,
        "objectives": objectives,
        "passed": passed,
        "of": len(objectives),
        # NOT_RUN counts against the verdict on purpose: an objective nobody
        # could check is not an objective that holds.
        "verdict": PASS if objectives and passed == len(objectives) else FAIL,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bossman.v7.nightly_run",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--objectives", default="all",
                        help="через запятую, например B2,B3,B4,B5,P0 (по умолчанию все)")
    parser.add_argument("--timeout", default="8h", type=parse_duration,
                        help="общий бюджет времени на весь прогон (8h / 90m / 600s)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="куда положить сводку")
    parser.add_argument("--list", action="store_true", help="показать цели и выйти")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать, что будет запущено, и не запускать")
    args = parser.parse_args(argv)

    if args.list:
        for key in ALL:
            print(f"{key:7s} {OBJECTIVES[key].title}")
        return 0

    keys = select(args.objectives)
    root = repo_root()
    if args.dry_run:
        for key in keys:
            print(f"[{key}] {OBJECTIVES[key].title}")
            for gate in OBJECTIVES[key].gates:
                shown = " ".join(gate.argv) if gate.argv else "(только проверка доказательства)"
                print(f"    {gate.name}: {shown}")
        return 0

    summary = run(keys, root=root, budget=args.timeout)
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{summary['verdict']}: {summary['passed']}/{summary['of']} целей за "
          f"{summary['wall_seconds']}s → {out}")
    for row in summary["objectives"]:
        if row["status"] != PASS:
            print(f"  {row['status']:8s} {row['objective']}: {row['title']}")
    return 0 if summary["verdict"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
