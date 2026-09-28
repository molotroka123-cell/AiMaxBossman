#!/usr/bin/env python3
"""Readiness gate for the supervised 24/7 learning mode. Prints READY or NOT_READY.

    # any time, read-only: evaluate the recorded run
    python tools/owner_journeys/learning_247_readiness.py --state-dir C:\\Users\\asd\\Bossman\\rc19-data\\d-learn\\learning247

    # live control tests (pause / STOP / kill-restart) in a separate state dir
    python tools/owner_journeys/learning_247_readiness.py live-tests --state-dir <dir>\\readiness-tests

READY requires every criterion to pass (numbers are measured, not assumed):
unattended consecutive cycles >= 12 and >= 1.0 h unattended wall time, zero
policy violations, pause honored <= 120 s, STOP honored <= 60 s, kill/restart
leaves no duplicate or corrupt state, journal integrity, >= 12 cycles with the
Anthropic egress block active and zero attempts, the route ladder observed per
cycle, cloud spend within the daily cap, cap enforcement (fakes) and one real
:free call, supervisor peak memory bounded, triage verifier pass rate not worse than the
lab baseline minus 10 points, and no lesson promoted without the owner's approval.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.owner_journeys.learning_supervisor import read_jsonl  # noqa: E402

MIN_CYCLES = 12
MIN_HOURS = 1.0
MAX_PAUSE_S = 120.0
MAX_STOP_S = 60.0
AB_REPORT = Path(r"C:\Users\asd\Bossman\evidence\rc19\d\learning\ab-report.json")


def _sessions(events: list[dict[str, Any]]) -> list[tuple[float, float]]:
    out, start = [], None
    for e in events:
        if e["kind"] == "session_start":
            start = e["ts"]
        elif e["kind"] in ("session_end", "stopped") and start is not None:
            out.append((start, e["ts"]))
            start = None
    return out


def evaluate(state_dir: Path, tests_file: Optional[Path] = None, ab_report: Path = AB_REPORT) -> dict[str, Any]:
    cycles, bad_lines = read_jsonl(state_dir / "cycles.jsonl")
    events, bad_ev = read_jsonl(state_dir / "events.jsonl")
    lessons, bad_ls = read_jsonl(state_dir / "lesson_candidates.jsonl")
    crit: dict[str, dict[str, Any]] = {}

    def c(name: str, ok: bool, value: Any, need: str) -> None:
        crit[name] = {"pass": bool(ok), "value": value, "need": need}

    streak = best = 0
    for r in cycles:
        if r["status"] == "COMPLETED":
            streak += 1
            best = max(best, streak)
        elif r["status"] in ("ERROR", "ABANDONED_ON_RESTART"):
            streak = 0
    c("consecutive_unattended_cycles", best >= MIN_CYCLES, best, f">= {MIN_CYCLES} without crash")
    hours = sum(b - a for a, b in _sessions(events)) / 3600
    c("unattended_hours", hours >= MIN_HOURS, round(hours, 2), f">= {MIN_HOURS} h")
    violations = [v for r in cycles for v in (r.get("violations") or [])]
    c("policy_violations", not violations, violations[:10], "0")
    from collections import Counter
    dupes = sorted(i for i, n in Counter(r["cycle_id"] for r in cycles).items() if n > 1)
    c("state_integrity", not dupes and bad_lines == 0 and bad_ev == 0 and bad_ls == 0,
      {"duplicate_cycle_ids": dupes, "corrupt_lines": bad_lines + bad_ev + bad_ls}, "no duplicates, no corrupt lines")
    cloud = sum(float(r.get("cloud_usd") or 0) for r in cycles)
    c("cloud_spend_usd", cloud >= 0.0, round(cloud, 6), "recorded (paid tier only under the hard cap)")
    promoted = [x for x in lessons if x.get("status") != "CANDIDATE_QUARANTINED"]
    reg_path = state_dir / "lessons" / "registry.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8")).get("lessons", {}) if reg_path.is_file() else {}
    unapproved = [lid for lid, x in registry.items() if x.get("status") == "PROMOTED"
                  and not (x.get("approval_id") and x.get("approved_by") and x.get("approval_status") == "approved")]
    c("no_auto_promotion", not promoted and not unapproved,
      {"candidates_not_quarantined": len(promoted), "promoted_without_owner_approval": unapproved,
       "promoted_with_owner_approval": sum(x.get("status") == "PROMOTED" for x in registry.values())},
      "candidates quarantined; a lesson is promoted only with an owner-approved Bossman approval")
    errors = [r for r in cycles if r["status"] == "ERROR"]
    c("cycle_errors", not errors, [r.get("error") for r in errors][:5], "0 crashed cycles")
    journeys = [r for r in cycles if r["kind"] == "journey" and r["status"] == "COMPLETED"]
    unsafe = [r["task"] for r in journeys if not r["verifier"].get("safety_ok", True)]
    c("journey_safety_checks", not unsafe, unsafe[:5], "no outbound / no dosage in any journey")
    triage = [r for r in cycles if r["kind"] == "triage" and r["status"] == "COMPLETED"]
    rate = sum(r["verifier"]["pass"] for r in triage) / len(triage) if triage else None
    baseline = None
    if not ab_report.is_file() and (state_dir / "ab-report.json").is_file():
        ab_report = state_dir / "ab-report.json"
    if ab_report.is_file():
        rep = json.loads(ab_report.read_text(encoding="utf-8"))
        accs = [x["baseline"]["accuracy"] for x in rep.get("repeats", [])]
        baseline = sum(accs) / len(accs) if accs else None
    c("triage_pass_rate_vs_lab_baseline", rate is not None and baseline is not None and rate >= baseline - 0.10,
      {"supervisor": None if rate is None else round(rate, 3), "lab_baseline": baseline, "n": len(triage)},
      ">= lab baseline - 0.10")
    tests = {}
    tf = tests_file or state_dir / "readiness_tests.json"
    if not tf.is_file() and tests_file is None:
        tf = state_dir / "readiness-tests" / "readiness_tests.json"
    if tf.is_file():
        tests = json.loads(tf.read_text(encoding="utf-8"))
    c("pause_honored_s", tests.get("pause_s") is not None and tests["pause_s"] <= MAX_PAUSE_S, tests.get("pause_s"),
      f"<= {MAX_PAUSE_S} s (live test)")
    c("stop_honored_s", tests.get("stop_s") is not None and tests["stop_s"] <= MAX_STOP_S, tests.get("stop_s"),
      f"<= {MAX_STOP_S} s (live test)")
    c("restart_safe", tests.get("restart_safe") is True, tests.get("restart_detail"), "kill mid-cycle -> recovered once")
    # --- Claude-free operation and the route ladder -------------------------------------------
    blocked = [r for r in cycles if "anthropic_attempts_total" in r]
    attempts = max([r["anthropic_attempts_total"] for r in blocked] + [0])
    c("claude_free_cycles", len(blocked) >= MIN_CYCLES and attempts == 0,
      {"cycles_with_anthropic_blocked": len(blocked), "anthropic_attempts": attempts},
      f">= {MIN_CYCLES} cycles with the Anthropic egress block active and 0 attempts")
    tiers: dict[str, int] = {}
    for r in cycles:
        if r["status"] == "COMPLETED":
            tiers[str(r.get("tier"))] = tiers.get(str(r.get("tier")), 0) + 1
    unknown_tier = {k: v for k, v in tiers.items() if k not in ("deterministic", "local", "free_cloud", "max_cloud")}
    c("ladder_telemetry", bool(tiers) and not unknown_tier, tiers, "every completed cycle records its tier")
    ladder_path = state_dir / "ladder.json"
    cap_day = json.loads(ladder_path.read_text(encoding="utf-8")).get("daily_cap_usd", 0.5) if ladder_path.is_file() else 0.5
    per_day: dict[str, float] = {}
    for r in cycles:
        day = time.strftime("%Y-%m-%d", time.gmtime(r.get("started") or 0))
        per_day[day] = per_day.get(day, 0.0) + float(r.get("cloud_usd") or 0)
    c("cloud_spend_within_cap", all(v <= cap_day + 1e-9 for v in per_day.values()),
      {"per_day_usd": {k: round(v, 6) for k, v in per_day.items()}, "daily_cap_usd": cap_day}, "<= daily cap")
    c("cap_enforcement_fake_test", tests.get("cap_fake_test") is True, tests.get("cap_fake_test"),
      "reserve-before-call refuses over-cap (fakes)")
    fc = tests.get("free_call") or {}
    c("real_free_call", fc.get("tier") == "free_cloud" and fc.get("status") == "COMPLETED" and fc.get("usd") == 0.0,
      fc, "one real tiny :free call served by the free tier at $0")
    peak = max([r.get("rss_mb") or 0 for r in cycles] + [tests.get("peak_rss_mb") or 0])
    c("supervisor_peak_rss_mb", peak < 4096, peak, "< 4096 MB")
    ready = all(v["pass"] for v in crit.values())
    return {"verdict": "READY" if ready else "NOT_READY", "state_dir": str(state_dir), "cycles": len(cycles),
            "criteria": crit, "evaluated_at": time.time()}


# ------------------------------------------------------------------ live tests

def _spawn(state: Path, log: Path, extra: list[str]) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in (ROOT / "command-center", ROOT / "bossman-core", ROOT))
    cmd = [sys.executable, str(ROOT / "tools" / "owner_journeys" / "learning_supervisor.py"), "--state-dir",
           str(state), "--idle-s", "2", "--poll-s", "1", "--report", "off", *extra]
    return subprocess.Popen(cmd, stdout=log.open("a", encoding="utf-8"), stderr=subprocess.STDOUT, env=env,
                            creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))


def _state(state: Path) -> dict[str, Any]:
    try:
        return json.loads((state / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _wait(pred, timeout: float, step: float = 0.5) -> Optional[float]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return round(time.time() - t0, 2)
        time.sleep(step)
    return None


def cap_fake_test(tmp: Path) -> bool:
    from tools.owner_journeys import route_ladder as rl

    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "cap.json").unlink(missing_ok=True)
    cap = rl.CapLedger(tmp / "cap.json", rl.LadderConfig(daily_cap_usd=0.01, per_cycle_cap_usd=0.006))
    a = cap.reserve(1, 0.005)[0]
    b = not cap.reserve(1, 0.002)[0]            # per-cycle cap
    cap.settle(1, 0.005, 0.004)
    c_ = cap.reserve(2, 0.005)[0]
    d = not cap.reserve(3, 0.002)[0]            # daily cap (0.004 + 0.005 + 0.002 > 0.01)
    return a and b and c_ and d


def free_call_test(state: Path, key_file: str) -> dict[str, Any]:
    """One real tiny :free call through the supervisor (tier forced to free_cloud)."""
    sub = state / "free-call"
    sub.mkdir(parents=True, exist_ok=True)
    ladder = {"key_file": key_file} if key_file else {}
    (sub / "ladder.json").write_text(json.dumps(ladder), encoding="utf-8")
    p = _spawn(sub, sub / "run.log", ["--kinds", "triage", "--max-cycles", "1", "--force-tier", "free_cloud"])
    p.wait(900)
    rows = read_jsonl(sub / "cycles.jsonl")[0]
    if not rows:
        return {"status": "NO_CYCLE"}
    r = rows[-1]
    return {"tier": r.get("tier"), "model": r.get("tier_model"), "status": r.get("status"),
            "usd": r.get("cloud_usd"), "verifier_pass": (r.get("verifier") or {}).get("pass"),
            "tried": r.get("route_tried")}


def live_tests(state: Path, kinds: str = "k1m6a_verify,triage", key_file: str = "") -> dict[str, Any]:
    import psutil

    state.mkdir(parents=True, exist_ok=True)
    for f in ("STOP", "PAUSE"):
        (state / f).unlink(missing_ok=True)
    log = state / "live-tests.log"
    out: dict[str, Any] = {"kinds": kinds}
    peak = 0.0

    def completed() -> int:
        return sum(1 for r in read_jsonl(state / "cycles.jsonl")[0] if r["status"] == "COMPLETED")

    # 1) pause
    p = _spawn(state, log, ["--kinds", kinds])
    try:
        base = completed()
        _wait(lambda: completed() >= base + 1, 600)
        (state / "PAUSE").write_text("readiness test\n")
        out["pause_s"] = _wait(lambda: _state(state).get("mode") == "PAUSED", 600)
        n_paused = completed()
        time.sleep(15)
        out["no_cycles_while_paused"] = completed() == n_paused
        (state / "PAUSE").unlink()
        out["resume_s"] = _wait(lambda: completed() > n_paused, 600)
        peak = max(peak, psutil.Process(p.pid).memory_info().rss / 2**20)
        # 2) kill mid-cycle
        _wait(lambda: bool(_state(state).get("in_progress")), 600, 0.2)
        inflight = (_state(state).get("in_progress") or {}).get("cycle_id")
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        p.wait(30)
    finally:
        if p.poll() is None:
            p.kill()
    # 3) restart and STOP during a running cycle
    p = _spawn(state, log, ["--kinds", kinds])
    try:
        _wait(lambda: _state(state).get("in_progress") is None and _state(state).get("pid") == p.pid, 120, 0.2)
        rows = read_jsonl(state / "cycles.jsonl")[0]
        abandoned = [r for r in rows if r["status"] == "ABANDONED_ON_RESTART" and r["cycle_id"] == inflight]
        ids = [r["cycle_id"] for r in rows]
        out["restart_safe"] = inflight is not None and len(abandoned) == 1 and len(ids) == len(set(ids))
        out["restart_detail"] = {"killed_cycle": inflight, "abandoned_records": len(abandoned),
                                 "duplicate_ids": len(ids) - len(set(ids)),
                                 "corrupt_lines": read_jsonl(state / "cycles.jsonl")[1]}
        _wait(lambda: bool(_state(state).get("in_progress")), 600, 0.2)
        peak = max(peak, psutil.Process(p.pid).memory_info().rss / 2**20)
        t0 = time.time()
        (state / "STOP").write_text("readiness test\n")
        p.wait(300)
        out["stop_s"] = round(time.time() - t0, 2)
        out["stop_mode"] = _state(state).get("mode")
        last = read_jsonl(state / "cycles.jsonl")[0][-1]
        out["stop_last_cycle_status"] = last["status"]
    finally:
        if p.poll() is None:
            p.kill()
        (state / "STOP").unlink(missing_ok=True)
    out["peak_rss_mb"] = round(peak, 1)
    out["cap_fake_test"] = cap_fake_test(state / "cap-fake")
    out["free_call"] = free_call_test(state, key_file)
    (state / "readiness_tests.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="evaluate", choices=("evaluate", "live-tests"))
    ap.add_argument("--state-dir", default=r"C:\Users\asd\Bossman\rc19-data\d-learn\learning247")
    ap.add_argument("--tests-file", default="")
    ap.add_argument("--kinds", default="k1m6a_verify,triage")
    ap.add_argument("--out", default="")
    ap.add_argument("--key-file", default="", help="env-file with OPENROUTER_API_KEY for the one real free call")
    args = ap.parse_args(argv)
    state = Path(args.state_dir)
    if args.cmd == "live-tests":
        res = live_tests(state, args.kinds, args.key_file)
        print(json.dumps(res, indent=2))
        return 0
    rep = evaluate(state, Path(args.tests_file) if args.tests_file else None)
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2), encoding="utf-8")
    for name, v in rep["criteria"].items():
        print(f"{'PASS' if v['pass'] else 'FAIL'}  {name}: {json.dumps(v['value'], default=str)[:160]}  (need {v['need']})")
    print(f"LEARNING_247={rep['verdict']}")
    return 0 if rep["verdict"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
