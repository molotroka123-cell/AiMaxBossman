#!/usr/bin/env python3
"""Unattended day plan for 08.10: ONE guarded step per call, started by Task Scheduler (BossmanTomorrow-*).

    python tools/bossman_tomorrow.py <step> [--dry]
    steps: selfrepair | replay | ux | publish | dossier | report

This is NOT a scheduler (Windows Task Scheduler stays the clock; Bossman's own queue, budgets and approvals do the
work). It is a thin guard in front of existing tools, so that a step runs only inside Bossman's own limits:

  * STOP: the owner's global STOP (<data>/computer/STOP), the autonomy STOP and <day>/STOP end the step before and
    between sub-jobs (exit 6). Nothing here clears a STOP.
  * serial: heavy steps take <day>/heavy.lock (pid inside, stale locks are ignored); a second heavy step exits 7
    instead of overlapping. Subprocesses run at BELOW_NORMAL priority and are killed (process tree) at the step deadline.
  * other sessions: a heavy sub-job waits while any coding task on the owner's Bossman is queued/running.
  * self-repair ladder per case: local -> openrouter-free -> nemotron-ultra-free -> nvidia-nim -> glm-flash. Only the
    LAST rung is paid, only after every free rung failed, and only while the day ledger (<day>/spend.json, a
    conservative estimate per paid cycle) stays under the hard cap of $1.00. Nothing here changes keys or roots.
  * every sub-job writes its output under <day>/ ; the step summary is <day>/step-<name>.json (evidence for the dossier).
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
DAY = Path(os.environ.get("BOSSMAN_TOMORROW_DAY", r"C:\Users\asd\Bossman\handoff\tomorrow-20261008"))
DATA = Path(os.environ.get("BCC_DATA_DIR", r"C:\Users\asd\AppData\Local\Bossman\CommandCenter"))
URL = os.environ.get("BOSSMAN_TOMORROW_URL", "http://127.0.0.1:8801")
PY = os.environ.get("BOSSMAN_TOMORROW_PY", sys.executable)
CAP_USD = 1.00
PAID_ESTIMATE_USD = 0.10          # conservative upper bound per paid cycle (GLM 5.3 Flash costs cents)
MAX_PAID_PER_CASE = 2
CASES = ("goal-budget", "atomic-json")          # 'discovery' already passed (cycle 23)
# owner 07.10: product code is written ONLY by free or local models; the paid GLM rung is gone (paid models audit, they do not code)
LADDER = ("local", "openrouter-free", "nemotron-ultra-free", "nvidia-nim")
RUNG_TIMEOUT = {"local": 1500, "openrouter-free": 1200, "nemotron-ultra-free": 1200, "nvidia-nim": 1200, "glm-flash": 1200}
ZONES = ("plugins", "skills", "ops", "jeff", "memapps", "mediaux", "agcloud")
HEAVY = {"selfrepair", "replay", "ux"}
DEADLINE_MIN = {"selfrepair": 130, "replay": 90, "ux": 60, "publish": 30, "dossier": 10, "report": 5}
BELOW_NORMAL = 0x00004000
NO_WINDOW = 0x08000000


class Stop(Exception):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"[{now()}] {msg}"
    print(line, flush=True)
    DAY.mkdir(parents=True, exist_ok=True)
    with (DAY / "tomorrow.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def stop_files() -> list[Path]:
    return [p for p in (DATA / "computer" / "STOP", DATA / "autonomy" / "STOP", DAY / "STOP") if p.exists()]


def check_stop() -> None:
    hit = stop_files()
    if hit:
        raise Stop(f"STOP set: {hit[0]}")


def pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    return str(pid) in out


class HeavyLock:
    path = DAY / "heavy.lock"

    def __enter__(self):
        DAY.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}\n".encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    owner = int(self.path.read_text().split()[0])
                except (OSError, ValueError, IndexError):
                    owner = 0
                if owner and pid_alive(owner):
                    raise RuntimeError(f"another heavy step holds {self.path} (pid {owner})")
                self.path.unlink(missing_ok=True)
        raise RuntimeError("cannot take the heavy lock")

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def run(cmd: list[str], out: Path, deadline: float, cwd: Path = ROOT, env_extra: dict | None = None) -> int:
    """Run below-normal, tee to a file, kill the whole tree at the deadline or on STOP. Returns exit code (124 = timeout)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **(env_extra or {})}
    with out.open("wb") as fh:
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT, env=env,
                                creationflags=BELOW_NORMAL | NO_WINDOW)
        while proc.poll() is None:
            time.sleep(5)
            if time.monotonic() > deadline or stop_files():
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
                proc.wait()
                return 124 if time.monotonic() > deadline else 6
    return proc.returncode


def client():
    sys.path[:0] = [str(ROOT / "command-center"), str(ROOT / "bossman-core"), str(ROOT)]
    from bcc.terminal_cli.api_client import Client, discover
    return Client(discover(URL, str(DATA)), timeout=30.0)


def backend_busy() -> bool | None:
    """True when a coding task is queued/running on the owner's Bossman (another session's cycle), None when unreachable."""
    try:
        with client() as c:
            data = c.get("/api/coding-tasks")
    except Exception:  # noqa: BLE001
        return None
    items = data if isinstance(data, list) else data.get("items", [])
    return any(str(t.get("status")) in ("queued", "running", "pending", "starting") for t in items)


def wait_idle(deadline: float, limit_s: int = 1800) -> None:
    t0 = time.monotonic()
    while True:
        check_stop()
        busy = backend_busy()
        if busy is False:
            return
        if busy is None:
            raise RuntimeError("Bossman is not reachable on " + URL)
        if time.monotonic() - t0 > limit_s or time.monotonic() > deadline:
            raise RuntimeError("Bossman stayed busy with another coding task; step skipped, not overlapped")
        time.sleep(30)


def ledger() -> dict:
    p = DAY / "spend.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cap_usd": CAP_USD, "estimated_usd": 0.0, "paid_cycles": []}


def ledger_add(case: str) -> None:
    led = ledger()
    led["estimated_usd"] = round(led["estimated_usd"] + PAID_ESTIMATE_USD, 4)
    led["paid_cycles"].append({"case": case, "at": now(), "estimate_usd": PAID_ESTIMATE_USD})
    (DAY / "spend.json").write_text(json.dumps(led, indent=1), encoding="utf-8")


def step_selfrepair(dry: bool, deadline: float) -> dict:
    res: dict = {"cases": {}}
    for case in CASES:
        paid = 0
        res["cases"][case] = {"rungs": [], "result": "NOT_PASSED"}
        for worker in LADDER:
            check_stop()
            if time.monotonic() > deadline:
                res["cases"][case]["rungs"].append({"worker": worker, "skipped": "step deadline"})
                break
            cmd = [PY, "-X", "utf8", str(ROOT / "tools" / "tree_self_repair_cycle.py"), "--case", case, "--worker", worker,
                   "--evidence", str(DAY / "selfrepair" / f"{case}-{worker}"), "--timeout", str(RUNG_TIMEOUT[worker]),
                   "--url", URL, "--data-dir", str(DATA)]
            if worker == "glm-flash":
                if ledger()["estimated_usd"] + PAID_ESTIMATE_USD > CAP_USD or paid >= MAX_PAID_PER_CASE:
                    res["cases"][case]["rungs"].append({"worker": worker, "skipped": "paid cap ($1/day) or per-case limit"})
                    break
                cmd += ["--allow-paid-worker", "glm-flash"]
            if dry:
                res["cases"][case]["rungs"].append({"worker": worker, "would_run": cmd})
                continue
            wait_idle(deadline)
            out = DAY / "selfrepair" / f"{case}-{worker}.log"
            rung_deadline = min(deadline, time.monotonic() + RUNG_TIMEOUT[worker] + 600)
            code = run(cmd, out, rung_deadline)
            if worker == "glm-flash":
                paid += 1
                ledger_add(case)
            tail = out.read_text(encoding="utf-8", errors="replace")
            verdict = next((ln.split("=", 1)[1].strip() for ln in tail.splitlines() if ln.startswith("SELF_REPAIR=")), "NO_VERDICT")
            res["cases"][case]["rungs"].append({"worker": worker, "exit": code, "verdict": verdict, "log": str(out)})
            log(f"selfrepair {case} {worker}: {verdict} (exit {code})")
            if code == 6:
                raise Stop("STOP during a cycle")
            if verdict in ("INDEPENDENT_VERIFICATION_PASS", "TRANSFER_PASS"):
                res["cases"][case]["result"] = verdict
                break
    return res


def step_replay(dry: bool, deadline: float) -> dict:
    res: dict = {"zones": {}}
    for zone in ZONES:
        check_stop()
        if time.monotonic() > deadline:
            res["zones"][zone] = {"skipped": "step deadline"}
            continue
        cmd = [PY, "-X", "utf8", str(ROOT / "tools" / "tree_proof" / "installed_rerun.py"), "--zone", zone, "--workers", "1", "--limit", "60"]
        if dry:
            res["zones"][zone] = {"would_run": cmd}
            continue
        out = DAY / "replay" / f"{zone}.log"
        code = run(cmd, out, deadline)
        res["zones"][zone] = {"exit": code, "log": str(out)}
        log(f"replay {zone}: exit {code}")
        if code == 6:
            raise Stop("STOP during replay")
    return res


def step_ux(dry: bool, deadline: float) -> dict:
    cmd = [PY, "-X", "utf8", str(ROOT / "tools" / "tree_proof" / "ux_sweep.py"), "--viewport", "both", "--budget-min", "45",
           "--port", "8893", "--json", str(DAY / "ux-sweep.json")]
    if dry:
        return {"would_run": cmd}
    code = run(cmd, DAY / "ux-sweep.log", deadline)
    log(f"ux sweep: exit {code}")
    return {"exit": code, "log": str(DAY / "ux-sweep.log"), "json": str(DAY / "ux-sweep.json")}


def step_publish(dry: bool, deadline: float) -> dict:
    res: dict = {}
    apply_cmd = [PY, "-X", "utf8", str(ROOT / "tools" / "tree_apply_evidence.py")]
    site_ok = (ROOT / "tools" / "tree_publish_site.py").is_file()
    pub = [PY, "-X", "utf8", str(ROOT / "tools" / "tree_publish_site.py")]
    if dry:
        return {"would_run": [apply_cmd, pub + ["--push"]]}
    res["apply_exit"] = run(apply_cmd, DAY / "tree-apply.log", deadline)
    if res["apply_exit"] != 0 or not site_ok:
        res["publish"] = "SKIPPED: evidence apply failed"
        return res
    check_stop()
    # tree_publish_site itself refuses to push unless the site tests pass, branch is main and the clone is clean.
    res["publish_exit"] = run(pub + ["--push"], DAY / "tree-publish.log", deadline)
    log(f"tree apply {res['apply_exit']}, publish {res['publish_exit']}")
    return res


def step_dossier(dry: bool, deadline: float) -> dict:
    cmd = [PY, "-X", "utf8", str(ROOT / "tools" / "proof_dossier.py"), "--extra-selfrepair", str(DAY / "selfrepair"),
           "--extra-evidence", str(DAY)]
    if dry:
        return {"would_run": cmd}
    code = run(cmd, DAY / "dossier.log", deadline)
    return {"exit": code, "log": str(DAY / "dossier.log")}


def step_report(dry: bool, deadline: float) -> dict:
    text = DAY / "morning-report.md"
    dossiers = sorted((DAY.parent).glob("proof-dossier-*/proof-dossier.md"))
    body = ["Bossman: утренний отчёт плана на 08.10", ""]
    for name in ("selfrepair", "replay", "ux", "publish", "dossier"):
        f = DAY / f"step-{name}.json"
        body.append(f"- {name}: " + ("есть файл " + f.name if f.is_file() else "НЕТ ДОКАЗАТЕЛЬСТВА (шаг не завершился)"))
    led = ledger()
    body.append(f"- платные циклы GLM Flash (оценка): ${led['estimated_usd']:.2f} из ${led['cap_usd']:.2f}")
    if dossiers:
        body.append(f"- досье: {dossiers[-1]}")
    text.write_text("\n".join(body) + "\n", encoding="utf-8")
    cmd = [PY, "-X", "utf8", "-m", "bcc.telegram_companion.owner_report", "--file", str(text)] + ([] if dry else ["--send"])
    cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion" / "config.json"
    if cfg.is_file():
        cmd += ["--config", str(cfg)]
    code = run(cmd, DAY / "report.log", deadline, env_extra={"PYTHONPATH": os.pathsep.join(
        [str(ROOT / "command-center"), str(ROOT / "bossman-core"), str(ROOT)])})
    return {"exit": code, "sent": not dry and code == 0, "note": "exit 3 = Пульт not configured, nothing sent"}


STEPS = {"selfrepair": step_selfrepair, "replay": step_replay, "ux": step_ux, "publish": step_publish,
         "dossier": step_dossier, "report": step_report}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("step", choices=sorted(STEPS))
    ap.add_argument("--dry", action="store_true", help="print the plan, run nothing")
    args = ap.parse_args(argv)
    DAY.mkdir(parents=True, exist_ok=True)
    started = now()
    deadline = time.monotonic() + DEADLINE_MIN[args.step] * 60
    status, detail = "DONE", {}
    try:
        check_stop()
        if args.step in HEAVY and not args.dry:
            with HeavyLock():
                detail = STEPS[args.step](args.dry, deadline)
        else:
            detail = STEPS[args.step](args.dry, deadline)
    except Stop as exc:
        status, detail = "STOPPED", {"reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a step failure is evidence, never a crash loop
        status, detail = "SKIPPED_OR_FAILED", {"reason": str(exc)[:500]}
    summary = {"step": args.step, "status": status, "started": started, "finished": now(), "dry": args.dry, "detail": detail}
    (DAY / f"step-{args.step}{'-dry' if args.dry else ''}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log(f"step {args.step}: {status}")
    return {"DONE": 0, "STOPPED": 6, "SKIPPED_OR_FAILED": 7}[status]


if __name__ == "__main__":
    sys.exit(main())
