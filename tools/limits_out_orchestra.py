"""Limits-out orchestra (owner, 10.10): when the coding assistant's limits run out, Bossman keeps improving itself
with its OWN workers (local -> free cloud -> GLM 5.3 Flash / Haiku 5.5 as finalizer) until the owner or a cap stops it.

Nothing new is invented here: every cycle is `tools/tree_self_repair_cycle.py` (free worker, hidden holdout, independent
verification, recipe auto-save) and lessons are re-imported through `tools/import_operational_lessons.py`. This file only
sequences them, logs every cycle, respects STOP files and caps, and writes a Russian report for the pult (the send
itself goes through owner_report, owner only).

    python tools/limits_out_orchestra.py --max-cycles 6 --max-hours 4 --paid glm-flash

Stops on: owner STOP (<data>\\computer\\STOP or <data>\\autonomy\\STOP), file STOP_ORCHESTRA next to the log, cap reached.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BCC_DATA_DIR") or (Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"))
LOGDIR = ROOT / "docs" / "evidence" / "limits-out-orchestra"
CASES = ["discovery", "goal-budget", "atomic-json"]


def log(line: str) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(LOGDIR / "orchestra.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


def stop_requested() -> str:
    for p in (DATA / "computer" / "STOP", DATA / "autonomy" / "STOP", LOGDIR / "STOP_ORCHESTRA"):
        if p.exists():
            return str(p)
    return ""


def run_cycle(case: str, paid: str | None, evidence: Path, timeout: int) -> dict:
    cmd = [sys.executable, str(ROOT / "tools" / "tree_self_repair_cycle.py"), "--case", case, "--worker", "openrouter-free",
           "--evidence", str(evidence), "--timeout", str(timeout)]
    if paid:
        cmd += ["--allow-paid-worker", paid]
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": f"{ROOT};{ROOT / 'bossman-core'};{ROOT / 'command-center'}"}
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout + 300)
    out = {"case": case, "rc": r.returncode, "minutes": round((time.time() - t0) / 60, 1), "tail": (r.stdout or r.stderr)[-600:]}
    for p in sorted(evidence.glob("**/*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "stages" in d:
            out["stages"] = d["stages"]
            break
    return out


def reimport_lessons() -> str:
    cat = ROOT / "docs" / "owner" / "bossman_lessons_20261010.json"
    if not cat.exists():
        return "no catalog"
    env = {**os.environ, "PYTHONUTF8": "1", "BOSSMAN_SKILL_RECORDING": "1",
           "PYTHONPATH": f"{ROOT};{ROOT / 'bossman-core'};{ROOT / 'command-center'}"}
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "import_operational_lessons.py"), "--catalog", str(cat),
                        "--data-dir", str(DATA / "learning" / "apprentice")], cwd=ROOT, env=env, capture_output=True, text=True)
    return "ok" if r.returncode == 0 else f"rc={r.returncode}"


def report(results: list[dict], reason: str) -> Path:
    lines = [f"# Оркестр без Claude — отчёт {datetime.now():%d.%m %H:%M}", f"Остановка: {reason}", "",
             "| Цикл | Случай | Минут | Код | Стадии |", "|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        st = r.get("stages")
        st_s = ", ".join(f"{k}={v}" for k, v in st.items()) if isinstance(st, dict) else "нет файла стадий"
        lines.append(f"| {i} | {r['case']} | {r['minutes']} | {r['rc']} | {st_s} |")
    lines += ["", "Правила: работник — бесплатная облачная модель; платный финализатор только разрешённый владельцем; "
                  "применение патчей в рантайм — только после Apply владельца. Уроки дня переимпортированы в память."]
    p = LOGDIR / f"report-{datetime.now():%Y%m%d-%H%M}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-cycles", type=int, default=6)
    ap.add_argument("--max-hours", type=float, default=4.0)
    ap.add_argument("--paid", choices=["glm-flash", "haiku-5.5"], default="glm-flash")
    ap.add_argument("--cycle-timeout", type=int, default=1900)
    ap.add_argument("--send", action="store_true", help="send the report to the pult (owner only) at the end")
    a = ap.parse_args()
    if not 1 <= a.max_cycles <= 25 or not 0 < a.max_hours <= 24:
        print("caps out of range")
        return 1
    t_end = time.time() + a.max_hours * 3600
    results: list[dict] = []
    log(f"start max_cycles={a.max_cycles} max_hours={a.max_hours} paid={a.paid}")
    reason = "лимит циклов"
    for i in range(a.max_cycles):
        why = stop_requested()
        if why:
            reason = f"STOP ({why})"
            break
        if time.time() > t_end:
            reason = "лимит часов"
            break
        case = CASES[i % len(CASES)]
        ev = LOGDIR / f"cycle-{datetime.now():%Y%m%d-%H%M}-{case}"
        ev.mkdir(parents=True, exist_ok=True)
        log(f"cycle {i + 1} {case} start")
        try:
            r = run_cycle(case, a.paid, ev, a.cycle_timeout)
        except subprocess.TimeoutExpired:
            r = {"case": case, "rc": -1, "minutes": round(a.cycle_timeout / 60), "tail": "timeout"}
        results.append(r)
        log(f"cycle {i + 1} {case} rc={r['rc']} {r['minutes']} min stages={r.get('stages')}")
        log("lessons reimport " + reimport_lessons())
    p = report(results, reason)
    log(f"report {p}")
    if a.send:
        env = {**os.environ, "PYTHONUTF8": "1", "BCC_DATA_DIR": str(DATA),
               "PYTHONPATH": f".;{ROOT / 'bossman-core'};{ROOT}"}
        subprocess.run([sys.executable, "-m", "bcc.telegram_companion.owner_report", "--file", str(p), "--send"],
                       cwd=ROOT / "command-center", env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
