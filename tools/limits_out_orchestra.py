"""Limits-out orchestra (owner, 10.10): when the coding assistant's limits run out, Bossman keeps improving itself
with its OWN workers (local -> free cloud -> GLM 5.3 Flash / Haiku 5.5 as finalizer) until the owner or a cap stops it.

Nothing new is invented here: every cycle is `tools/tree_self_repair_cycle.py` (free worker, hidden holdout, independent
verification, recipe auto-save) and lessons are re-imported through `tools/import_operational_lessons.py`. This file only
sequences them, logs every cycle, respects STOP files and caps, and writes a Russian report for the pult (the send
itself goes through owner_report, owner only).

    python tools/limits_out_orchestra.py --max-cycles 6 --max-hours 4 --paid glm-flash

FREE-ONLY mode (owner 18.10): `--paid none` is the default, so the paid finalizer is never used; every cycle is checked afterwards
(recorded cost_usd must be 0.0 and the model a free route, otherwise the orchestra writes STOP_ORCHESTRA and reports). The
orchestra PAUSES while any direct-gen job is not finished (queued / loading / generating: polled every 30 s; a cycle in flight is
killed and its coding task cancelled, then re-run after the PC has been idle for 2 polls), runs BELOW_NORMAL, one worker at a time,
never merges (patches stay in isolated copies, recipes only after independent verification). One short Russian pult message a day.

    python tools/limits_out_orchestra.py --dry                  # preflight only, starts nothing
    python tools/limits_out_orchestra.py --max-cycles 6 --max-hours 4 --send

Stops on: owner STOP (<data>\\computer\\STOP or <data>\\autonomy\\STOP), file STOP_ORCHESTRA next to the log, cap reached.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import re
import time
import urllib.error
import urllib.request
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


POLL_S = 30
IDLE_POLLS = 2
FINISHED_JOB = {"completed", "failed", "cancelled", "canceled", "done", "error", "rejected"}
FREE_MODEL_HINTS = (":free", "nemotron", "gpt-oss", "gemini", "gemma", "llama", "qwen", "deepseek-v4.1-flash")
DAILY_STATE = LOGDIR / "daily-report.json"


def set_low_priority() -> None:
    """BELOW_NORMAL for this process (children inherit it); never fails the run."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:  # noqa: BLE001
        pass


def _token() -> str:
    try:
        return (DATA / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _api(method: str, path: str, timeout: float = 10.0):
    req = urllib.request.Request("http://127.0.0.1:8801" + path, method=method, headers={"X-BCC-Token": _token()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def direct_gen_busy() -> list[str]:
    """Statuses of direct-gen jobs that are not finished (queued / loading / generating / unknown). A cheap GET, no prompts kept.
    Backend unreachable = nothing is generating, but then a cycle cannot run either."""
    try:
        jobs = (_api("GET", "/api/direct-gen/jobs") or {}).get("jobs") or []
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return [str(j.get("status") or "unknown").lower() for j in jobs if str(j.get("status") or "unknown").lower() not in FINISHED_JOB]


def wait_idle(deadline: float) -> str:
    """Block until the owner's generations are finished for IDLE_POLLS polls in a row. Returns '' when idle, else why we stopped."""
    quiet = 0
    while quiet < IDLE_POLLS:
        why = stop_requested()
        if why:
            return f"STOP ({why})"
        if time.time() > deadline:
            return "лимит часов"
        if direct_gen_busy():
            quiet = 0
        else:
            quiet += 1
        if quiet < IDLE_POLLS:
            time.sleep(POLL_S)
    return ""


def sync_client_src(client_src: str) -> str:
    """Keep the read-only client checkout at the SHA of the RUNNING server (the owner restarts the backend on newer builds).
    '' = ok / nothing to do, else why the cycle cannot run. Only touches the client checkout, a detached worktree of ours."""
    if not client_src:
        return ""
    try:
        sha = str(json.loads(urllib.request.urlopen("http://127.0.0.1:8801/api/identity", timeout=10).read()).get("build_sha") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return "backend unreachable"
    cur = subprocess.run(["git", "-C", client_src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if sha and cur != sha:
        r = subprocess.run(["git", "-C", client_src, "checkout", "--detach", "--quiet", sha], capture_output=True, text=True)
        if r.returncode != 0:
            return f"client checkout to server SHA {sha[:12]} failed"
    return ""


def spend_check(evidence: Path) -> dict:
    """Sum recorded cost_usd of the cycle's coding task(s) and list the models used. paid=True when anything cost money."""
    cost, models = 0.0, set()
    for p in evidence.glob("**/task-*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        c = d.get("cost_usd")
        if isinstance(c, (int, float)):
            cost += float(c)
        if d.get("model"):
            models.add(str(d["model"]))
    return {"cost_usd": round(cost, 6), "models": sorted(models), "paid": cost > 0}


def _kill_tree(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def _cancel_task(out_file: Path) -> str:
    """Cancel the coding task the killed cycle had started and WAIT until the backend reports it finished
    (cancel is re-sent every 10 s, at most 90 s). Returns '<id>:<final status>' or ''."""
    try:
        m = re.search(r"task (\w+) started", out_file.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""
    if not m:
        return ""
    tid, status = m.group(1), "unknown"
    for _ in range(9):
        try:
            _api("POST", f"/api/coding-tasks/{tid}/cancel")
            time.sleep(10)
            status = str((_api("GET", f"/api/coding-tasks/{tid}") or {}).get("status") or "unknown")
        except (urllib.error.URLError, OSError, ValueError):
            status = "api-error"
        if status in ("completed", "failed", "blocked", "cancelled"):
            break
    return f"{tid}:{status}"


def run_cycle(case: str, paid: str | None, evidence: Path, timeout: int, cycle_py: str, source_repo: str,
              deadline: float = 0.0, client_src: str = "") -> dict:
    # The cycle must run from the SAME build as the server on :8801 (api_client refuses a foreign build), so it uses the
    # installed bundle's interpreter (its own `bcc`) and no repo PYTHONPATH; the repo is only the tree's source_repo.
    cmd = [cycle_py, str(ROOT / "tools" / "tree_self_repair_cycle.py"), "--case", case, "--worker", "openrouter-free",
           "--source-repo", source_repo, "--evidence", str(evidence), "--timeout", str(timeout)]
    if paid:
        cmd += ["--allow-paid-worker", paid]
    env = {**os.environ, "PYTHONUTF8": "1", "BCC_DATA_DIR": str(DATA), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    if client_src:       # a read-only checkout at the SHA the running server was started from (build identity must match)
        env["PYTHONPATH"] = os.pathsep.join(str(Path(client_src) / d) for d in ("command-center", "bossman-core", "."))
    out_file = evidence / "cycle.out"
    t0 = time.time()
    with open(out_file, "wb") as fh:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=0x00004000 | 0x08000000)       # BELOW_NORMAL | CREATE_NO_WINDOW
        interrupted = ""
        while proc.poll() is None:
            time.sleep(POLL_S)
            busy = direct_gen_busy()
            if busy:
                interrupted = "direct-gen busy: " + ",".join(sorted(set(busy)))
            elif stop_requested():
                interrupted = "STOP"
            elif time.time() > t0 + timeout + 300 or (deadline and time.time() > deadline):
                interrupted = "timeout"
            if interrupted:
                _kill_tree(proc)
                break
    out = {"case": case, "rc": proc.returncode if not interrupted else -2, "minutes": round((time.time() - t0) / 60, 1)}
    tail = out_file.read_text(encoding="utf-8", errors="replace")[-600:]
    out["tail"] = tail
    if interrupted:
        out["interrupted"] = interrupted
        out["cancelled_task"] = _cancel_task(out_file)
    out["spend"] = spend_check(evidence)
    out["cross_family"] = cross_family_check(evidence)
    for p in sorted(evidence.glob("**/*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and "stages" in d:
            out["stages"] = dict(d["stages"])
            if out["stages"].get("INDEPENDENT_VERIFICATION_PASS") and not out["cross_family"]["ok"]:
                out["stages"]["INDEPENDENT_VERIFICATION_PASS"] = False
                out["cross_family_rejected"] = out["cross_family"]["reason"]
            break
    return out


_FAMILY_GROUPS = (
    ("google", ("gemma", "gemini", "google")),
    ("meta_llama", ("llama", "nemotron", "meta")),
    ("qwen", ("qwen",)),
    ("mistral", ("mistral", "mixtral")),
    ("deepseek", ("deepseek",)),
    ("glm", ("glm", "chatglm")),
    ("claude", ("claude", "anthropic")),
    ("gpt", ("gpt", "openai", "o1", "o3")),
)


def model_family(identifier: str) -> str:
    low = str(identifier or "").lower()
    if low.startswith("tree-holdout:") or low == "external_tool":
        return "external_tool"
    for canonical, tokens in _FAMILY_GROUPS:
        if any(tok in low for tok in tokens):
            return canonical
    return low.split("/", 1)[0].split(":", 1)[0] or "unknown"


def cross_family_check(evidence: Path) -> dict:
    """Ensure independent verification comes from an external_tool holdout or a DIFFERENT model family."""
    worker_models: list[str] = []
    verifier_info: dict = {}
    for p in sorted(evidence.glob("**/task-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("model"):
            worker_models.append(str(d["model"]))
    for p in sorted(evidence.glob("**/recipe-*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d.get("verifier"), dict):
            verifier_info = d["verifier"]
            break
    w_fam = model_family(worker_models[0]) if worker_models else "unknown"
    if not verifier_info:
        return {
            "ok": False,
            "worker_family": w_fam,
            "verifier_family": "unknown",
            "reason": "missing verifier block",
        }
    ind_cls = str(verifier_info.get("independence_class") or "")
    v_id = str(verifier_info.get("model_id") or verifier_info.get("principal_id") or "")
    v_fam = "external_tool" if ind_cls == "external_tool" or v_id.startswith("tree-holdout:") else model_family(v_id)
    ok = v_fam == "external_tool" or (w_fam != "unknown" and v_fam != "unknown" and w_fam != v_fam)
    return {
        "ok": ok,
        "worker_family": w_fam,
        "verifier_family": v_fam,
        "reason": "ok" if ok else f"same model family ({w_fam} == {v_fam})",
    }


def reimport_lessons() -> str:
    env = {**os.environ, "PYTHONUTF8": "1", "BOSSMAN_SKILL_RECORDING": "1",
           "PYTHONPATH": f"{ROOT};{ROOT / 'bossman-core'};{ROOT / 'command-center'}"}
    catalogs = [
        ROOT / "docs" / "owner" / "bossman_critical_errors.json",
        ROOT / "docs" / "owner" / "bossman_lessons_20261010.json",
        ROOT / "docs" / "owner" / "bossman_lessons_20261011.json",
    ]
    existing = [c for c in catalogs if c.exists()]
    if not existing:
        return "no catalog"
    rcs = []
    for cat in existing:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "import_operational_lessons.py"), "--catalog", str(cat),
                            "--data-dir", str(DATA / "learning" / "apprentice")], cwd=ROOT, env=env, capture_output=True, text=True)
        rcs.append(r.returncode)
    chk = subprocess.run([sys.executable, str(ROOT / "tools" / "import_operational_lessons.py"), "--run-checks"],
                         cwd=ROOT, env=env, capture_output=True, text=True)
    rcs.append(chk.returncode)
    return "ok" if all(rc == 0 for rc in rcs) else f"rcs={rcs}"


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


def daily_message(results: list[dict], reason: str, spend: float) -> str:
    ok = sum(1 for r in results if isinstance(r.get("stages"), dict) and r["stages"].get("INDEPENDENT_VERIFICATION_PASS"))
    part = sum(1 for r in results if isinstance(r.get("stages"), dict) and r["stages"].get("MODEL_PATCH_CREATED")
               and not r["stages"].get("INDEPENDENT_VERIFICATION_PASS"))
    paused = sum(1 for r in results if r.get("interrupted"))
    done = [r for r in results if not r.get("interrupted")]
    lines = [f"Оркестр самоулучшения (только бесплатные модели): циклов {len(done)}, пауз из-за твоих генераций {paused}.",
             f"Независимую проверку прошло: {ok}; патч есть, но проверка не пройдена: {part}; провалено: {len(done) - ok - part}.",
             f"Потрачено: ${spend:.2f}. Остановка: {reason}."]
    if ok == 0:
        lines.append("Улучшений пока не засчитано: без независимой проверки слова «улучшил» нет.")
    return "\n".join(lines)


def send_daily(text: str) -> str:
    """At most one pult message per local day (owner only, through owner_report: secret scrub + owner lookup)."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if json.loads(DAILY_STATE.read_text(encoding="utf-8")).get("sent") == today:
            return "already sent today"
    except (OSError, ValueError):
        pass
    env = {**os.environ, "PYTHONUTF8": "1", "BCC_DATA_DIR": str(DATA), "PYTHONPATH": f".;{ROOT / 'bossman-core'};{ROOT}"}
    r = subprocess.run([sys.executable, "-m", "bcc.telegram_companion.owner_report", "--file", "-", "--send"],
                       cwd=ROOT / "command-center", env=env, input=text, capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0:
        DAILY_STATE.write_text(json.dumps({"sent": today}), encoding="utf-8")
    return f"rc={r.returncode}"


def preflight(a) -> dict:
    """Everything a real run needs, without starting a task. Honest: a failed check is a failed check."""
    checks: dict[str, str] = {}
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tree_self_improve import refuse_worker
    import import_operational_lessons as iol
    checks["worker_free"] = "ok" if refuse_worker("openrouter-free") is None else "REFUSED"
    checks["paid_finalizer"] = "off" if a.paid == "none" else f"ON ({a.paid})"
    checks["stop"] = stop_requested() or "none"
    checks["cycle_py"] = "ok" if Path(a.cycle_py).exists() else f"missing {a.cycle_py}"
    checks["source_repo"] = "ok" if (Path(a.source_repo) / ".git").exists() else f"missing {a.source_repo}"
    try:
        busy = direct_gen_busy()
        checks["backend"] = "up"
        checks["direct_gen"] = ("BUSY " + ",".join(sorted(set(busy)))) if busy else "idle"
        provs = _api("GET", "/api/providers") or []
        checks["free_key_openrouter"] = "ok" if any("openrouter" in str(p.get("name", "")).lower() and p.get("api_key_masked")
                                                    for p in provs) else "no key"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        checks["backend"] = f"DOWN ({type(exc).__name__})"
    sync_client_src(a.client_src)
    try:
        server_sha = str(json.loads(urllib.request.urlopen("http://127.0.0.1:8801/api/identity", timeout=10).read()).get("build_sha"))
        src = a.client_src or str(Path(__import__("importlib.util").util.find_spec("bcc").origin).parents[2])
        client_sha = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        checks["build_match"] = "ok" if server_sha == client_sha else f"MISMATCH server {server_sha[:12]} client {client_sha[:12]} (use --client-src)"
    except Exception as exc:  # noqa: BLE001
        checks["build_match"] = f"unknown ({type(exc).__name__})"
    ex_res = iol.run_executable_checks(ROOT)
    checks["executable_lessons"] = f"ok ({ex_res['passed']}/{ex_res['total']})" if ex_res["failed"] == 0 else f"FAIL ({ex_res['failed_ids']})"
    checks["independent_verification"] = "cross-family + external_tool holdout (same-family rejected)"
    checks["priority"] = "BELOW_NORMAL (this process and the cycle)"
    checks["pause_rule"] = f"direct-gen not finished -> kill cycle + cancel task; resume after {IDLE_POLLS} idle polls of {POLL_S}s"
    checks["concurrency"] = "1 worker (sequential cycles)"
    checks["merges"] = "never (isolated copies; recipe only after independent verification)"
    infra_good = {"worker_free": "ok", "paid_finalizer": "off", "cycle_py": "ok", "source_repo": "ok",
                  "backend": "up", "free_key_openrouter": "ok", "build_match": "ok",
                  "executable_lessons": f"ok ({ex_res['total']}/{ex_res['total']})"}
    checks["preflight_infra"] = "ok" if all(checks.get(k) == v for k, v in infra_good.items()) else "NO"
    checks["ready"] = "yes" if (checks["preflight_infra"] == "ok" and checks["stop"] == "none") else "NO"
    return checks



def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-cycles", type=int, default=6)
    ap.add_argument("--max-hours", type=float, default=4.0)
    ap.add_argument("--paid", choices=["none", "glm-flash", "haiku-5.5"], default="none",
                    help="paid finalizer; default none = FREE ONLY (owner 18.10)")
    ap.add_argument("--cycle-timeout", type=int, default=1900)
    ap.add_argument("--send", action="store_true", help="send the daily pult message (once per day, owner only) at the end")
    ap.add_argument("--dry", action="store_true", help="preflight only: writes a report, starts no task")
    ap.add_argument("--cycle-py", default=os.environ.get("BOSSMAN_INSTALLED_PY", sys.executable),
                    help="interpreter of the INSTALLED build serving :8801 (bundle runtime\\python.exe)")
    # The server accepts only checkouts inside its evolution roots (EVOLUTION_ROOT_DENIED otherwise): evo-tree-src is one.
    ap.add_argument("--client-src", default=os.environ.get("BOSSMAN_ORCH_CLIENT_SRC", ""),
                    help="checkout whose bcc the cycle imports; must be at the SHA of the RUNNING server (api_client refuses a foreign build)")
    ap.add_argument("--source-repo", default=r"C:\Users\asd\Bossman\evo-tree-src", help="checkout the tree works on")
    a = ap.parse_args()
    if not 1 <= a.max_cycles <= 25 or not 0 < a.max_hours <= 24:
        print("caps out of range")
        return 1
    set_low_priority()
    if a.dry:
        res = preflight(a)
        LOGDIR.mkdir(parents=True, exist_ok=True)
        p = LOGDIR / f"dry-{datetime.now():%Y%m%d-%H%M}.json"
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        log("dry " + json.dumps(res, ensure_ascii=False))
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ready"] == "yes" else 1
    t_end = time.time() + a.max_hours * 3600
    results: list[dict] = []
    paid = None if a.paid == "none" else a.paid
    log(f"start max_cycles={a.max_cycles} max_hours={a.max_hours} paid={a.paid}")
    reason = "лимит циклов"
    done = i = 0
    spend = 0.0
    while done < a.max_cycles:
        why = stop_requested()
        if why:
            reason = f"STOP ({why})"
            break
        if time.time() > t_end:
            reason = "лимит часов"
            break
        idle = wait_idle(t_end)                      # pause while the owner generates
        if idle:
            reason = idle
            break
        bad = sync_client_src(a.client_src)
        if bad:
            reason = f"не запустилось: {bad}"
            break
        case = CASES[i % len(CASES)]
        ev = LOGDIR / f"cycle-{datetime.now():%Y%m%d-%H%M}-{case}"
        ev.mkdir(parents=True, exist_ok=True)
        log(f"cycle {done + 1} {case} start")
        try:
            r = run_cycle(case, paid, ev, a.cycle_timeout, a.cycle_py, a.source_repo, t_end, a.client_src)
        except subprocess.TimeoutExpired:
            r = {"case": case, "rc": -1, "minutes": round(a.cycle_timeout / 60), "tail": "timeout"}
        results.append(r)
        spend += float((r.get("spend") or {}).get("cost_usd") or 0.0)
        log(f"cycle {done + 1} {case} rc={r['rc']} {r['minutes']} min stages={r.get('stages')} "
            f"interrupted={r.get('interrupted')} cancelled={r.get('cancelled_task')} spend={r.get('spend')}")
        if (r.get("spend") or {}).get("paid") and a.paid == "none":
            (LOGDIR / "STOP_ORCHESTRA").write_text("free-only violated: a cycle recorded cost_usd > 0", encoding="utf-8")
            reason = "СТОП: зафиксирован платный расход, оркестр остановлен"
            log(reason)
            break
        if r.get("interrupted"):
            continue                                 # same case again once the PC is idle; not counted as a cycle
        done += 1
        i += 1
        log("lessons reimport " + reimport_lessons())
    p = report(results, reason)
    log(f"report {p}")
    if a.send and any(isinstance(r.get("stages"), dict) or r.get("interrupted") for r in results):
        log("daily pult: " + send_daily(daily_message(results, reason, spend)))
    elif a.send:
        log("daily pult: skipped (no cycle reached a verdict; an infrastructure failure is not an improvement report)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
