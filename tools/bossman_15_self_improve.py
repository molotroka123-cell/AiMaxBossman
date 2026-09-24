#!/usr/bin/env python3
"""Bossman 1.5 self-improvement bootstrap.

Goal: start Bossman's OWN bounded improvement loop and make Codex/Claude/Aster
supervisors rather than routine workers.

The bootstrap does not promote to stable and does not create cloud accounts.
It combines:
1) provider-capacity preflight,
2) Jev/Bossman coding readiness,
3) free-first YouTube/economy learning,
4) the existing bounded evolution loop through Bossman's coding path,
5) optional compilation of independently VERIFIED unseen-transfer lessons.

Aster's job after launch is audit/coordination only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT), str(ROOT / "bossman-core")]

from v15_provider_pool import load as load_provider_pool, status as provider_status  # noqa: E402
from bossman_v3.self_improvement.attempts import CommandCenterApi, resolve_token  # noqa: E402

_INSTALLED_CONFIG = HERE / "config" / "v1.5" / "self-improvement.json"
DEFAULT_CONFIG = (
    _INSTALLED_CONFIG if _INSTALLED_CONFIG.is_file()
    else ROOT / "config" / "v1.5" / "self-improvement.json"
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str, cwd: Path = ROOT) -> str:
    p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=60,
                       encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-1000:])
    return p.stdout.strip()


def api(data_dir: Path, base_url: str) -> CommandCenterApi:
    token = resolve_token(data_dir=str(data_dir))
    return CommandCenterApi(base_url, token)


def api_get(client: CommandCenterApi, path: str) -> dict[str, Any]:
    code, body = client.get(path)
    return {"http": code, "body": body}


def preflight(cfg: dict[str, Any], *, repo: Path, data_dir: Path, api_url: str) -> dict[str, Any]:
    source_sha = git("rev-parse", "HEAD", cwd=repo)
    dirty = git("status", "--porcelain", "--untracked-files=normal", cwd=repo)
    providers = provider_status(load_provider_pool())
    out: dict[str, Any] = {
        "schema": "bossman.v1.5.self-improve-preflight/1",
        "source_sha": source_sha,
        "repo": str(repo),
        "clean": not bool(dirty),
        "provider_pool": providers,
        "openrouter_key_present": bool(
            os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("BOSSMAN_OPENROUTER_API_KEY")
        ),
        "coding": {"http": 0, "body": {"available": False}},
        "jev": {"http": 0, "body": {"wired": False}},
        "economy": {"http": 0, "body": {}},
    }
    if dirty:
        out["blocker"] = "SOURCE_DIRTY"
        return out
    try:
        client = api(data_dir, api_url)
        out["coding"] = api_get(client, "/api/coding-tasks/readiness")
        out["jev"] = api_get(client, "/api/jev/status")
        out["economy"] = api_get(client, "/api/v15/economy/status")
    except Exception as exc:  # loopback product is allowed to be offline during status
        out["api_error"] = type(exc).__name__ + ": " + str(exc)[:300]
    ready = bool((out["coding"].get("body") or {}).get("available"))
    out["self_improve_startable"] = bool(out["clean"] and ready)
    if not ready and "blocker" not in out:
        out["blocker"] = "BOSSMAN_CODING_PATH_NOT_READY"
    return out


def run_cmd(argv: list[str], *, cwd: Path, timeout: int, log: Path) -> dict[str, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    log.write_text(proc.stdout + "\n--- STDERR ---\n" + proc.stderr, encoding="utf-8")
    return {
        "argv": argv,
        "returncode": proc.returncode,
        "seconds": round(time.time() - started, 2),
        "stdout_tail": proc.stdout[-3000:],
        "stderr_tail": proc.stderr[-3000:],
        "log": str(log),
    }


def evolution_command(cfg: dict[str, Any], *, repo: Path, data_dir: Path, api_url: str,
                      work: Path, student_model: str | None, cycles: int | None) -> list[str]:
    evo = cfg["evolution"]
    suite = ROOT / evo["suite"] if not (HERE / evo["suite"]).is_file() else HERE / evo["suite"]
    script = HERE / "bossman_evolve.py"
    if not script.is_file():
        script = ROOT / "tools" / "bossman_evolve.py"
    argv = [
        sys.executable, str(script), "loop",
        "--work", str(work),
        "--repo", str(repo),
        "--suite", str(suite),
        "--backend", "bossman_coding",
        "--executor", "host",
        "--data-dir", str(data_dir),
        "--api-url", api_url,
        "--project-id", "bossman-1.5-self-improve",
        "--max-cycles", str(cycles or evo["max_cycles"]),
        "--attempt-minutes", str(evo["attempt_minutes"]),
        "--total-hours", str(evo["total_hours"]),
        "--max-retries", str(evo["max_retries_per_task"]),
        "--max-consecutive-failures", str(evo["max_consecutive_failures"]),
        "--max-disk-mb", str(evo["max_disk_mb"]),
        "--max-rss-mb", str(evo["max_rss_mb"]),
        "--min-free-ram-mb", str(evo["min_free_ram_mb"]),
        "--max-usd", str(evo["max_usd"]),
        "--attempt-usd", str(evo["attempt_usd"]),
        "--add-root",
    ]
    if student_model:
        argv += ["--model", student_model]
    return argv


def owner_required_onboarding(pre: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for p in pre["provider_pool"]["providers"]:
        if p["account_state"] == "OWNER_REQUIRED":
            rows.append({
                "provider": p["id"],
                "signup_url": p["signup_url"],
                "key_env": p["key_env"],
                "free_confirmation_env": p["free_confirmation_env"],
                "reason": "external account/ToS/API-key creation must be performed by the owner",
            })
        elif p["account_state"] == "OWNER_CONFIRM_FREE_TIER":
            rows.append({
                "provider": p["id"],
                "key_env": p["key_env"],
                "free_confirmation_env": p["free_confirmation_env"],
                "reason": "key exists; owner must confirm this account is intended for zero-cost capacity",
            })
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument(
        "--data-dir",
        default=str(Path(os.environ.get("LOCALAPPDATA", ".")) / "Bossman" / "CommandCenter"),
    )
    ap.add_argument("--api-url", default="http://127.0.0.1:8800")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    s = sub.add_parser("start")
    s.add_argument("--work")
    s.add_argument("--cycles", type=int)
    s.add_argument("--student-model")
    s.add_argument("--skip-economy", action="store_true")
    s.add_argument("--allow-glm", action="store_true")
    s.add_argument("--youtube-url", default=os.environ.get("BOSSMAN_K1MBA_YOUTUBE_URL", ""))

    x = sub.add_parser("stop")
    x.add_argument("--work", required=True)

    c = sub.add_parser("compile-verified")
    c.add_argument("--input", required=True)
    c.add_argument("--out", required=True)

    ns = ap.parse_args(argv)
    cfg = read_json(Path(ns.config))
    repo, data_dir = Path(ns.repo).resolve(), Path(ns.data_dir).resolve()
    if ns.cmd == "status":
        pre = preflight(cfg, repo=repo, data_dir=data_dir, api_url=ns.api_url)
        pre["owner_required_onboarding"] = owner_required_onboarding(pre)
        print(json.dumps(pre, ensure_ascii=False, indent=2))
        return 0 if pre.get("self_improve_startable") else 3

    if ns.cmd == "stop":
        work = Path(ns.work).resolve()
        script = HERE / "bossman_evolve.py"
        if not script.is_file():
            script = ROOT / "tools" / "bossman_evolve.py"
        return subprocess.call([sys.executable, str(script), "stop", "--work", str(work)], cwd=repo)

    if ns.cmd == "compile-verified":
        script = HERE / "bossman_15_learning_compile.py"
        if not script.is_file():
            script = ROOT / "tools" / "bossman_15_learning_compile.py"
        return subprocess.call([
            sys.executable, str(script),
            "--input", ns.input,
            "--data-dir", str(data_dir),
            "--out", ns.out,
        ], cwd=repo)

    pre = preflight(cfg, repo=repo, data_dir=data_dir, api_url=ns.api_url)
    if not pre.get("self_improve_startable"):
        print(json.dumps({
            "status": "OWNER_OR_ENVIRONMENT_REQUIRED",
            "preflight": pre,
            "onboarding": owner_required_onboarding(pre),
        }, ensure_ascii=False, indent=2))
        return 3

    source_sha = pre["source_sha"]
    work = Path(ns.work).resolve() if ns.work else (
        data_dir / "self-improvement" / ("v1.5-" + source_sha[:12])
    )
    work.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema": "bossman.v1.5.self-improve-bootstrap/1",
        "source_sha": source_sha,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "preflight": pre,
        "onboarding": owner_required_onboarding(pre),
        "economy": None,
        "evolution": None,
        "weights_changed": False,
        "stable_written": False,
        "external_auditor": "ASTER_ONLY",
    }

    if not ns.skip_economy:
        economy = HERE / "bossman_15_economy_run.py"
        if not economy.is_file():
            economy = ROOT / "tools" / "bossman_15_economy_run.py"
        cmd = [sys.executable, str(economy), "--out", str(work / "economy")]
        if ns.youtube_url:
            cmd += ["--channel", ns.youtube_url]
        if ns.allow_glm:
            cmd += ["--allow-glm"]
        try:
            report["economy"] = run_cmd(
                cmd, cwd=repo, timeout=8 * 3600, log=work / "economy-run.log"
            )
        except subprocess.TimeoutExpired:
            report["economy"] = {"returncode": 124, "status": "TIMEOUT"}

    preferred = ns.student_model or cfg["student"]["preferred_model"]
    cmd = evolution_command(
        cfg, repo=repo, data_dir=data_dir, api_url=ns.api_url,
        work=work / "evolution", student_model=preferred, cycles=ns.cycles
    )
    try:
        report["evolution"] = run_cmd(
            cmd, cwd=repo, timeout=int(float(cfg["evolution"]["total_hours"]) * 3600 + 900),
            log=work / "evolution-run.log"
        )
    except subprocess.TimeoutExpired:
        report["evolution"] = {"returncode": 124, "status": "TIMEOUT"}

    state_path = work / "evolution" / "loop-state.json"
    if state_path.is_file():
        try:
            report["evolution_state"] = read_json(state_path)
        except Exception as exc:
            report["evolution_state"] = {"status": "UNREADABLE", "error": type(exc).__name__}

    state = report.get("evolution_state") or {}
    report["status"] = (
        "SELF_IMPROVEMENT_PROCESS_STARTED"
        if state_path.exists()
        else "SELF_IMPROVEMENT_BLOCKED"
    )
    report["gain_claim"] = "UNMEASURED_UNTIL_UNSEEN_TRANSFER"
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (work / "bootstrap-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "source_sha": source_sha,
        "work": str(work),
        "evolution_status": state.get("status"),
        "gain_claim": report["gain_claim"],
    }, ensure_ascii=False))
    return 0 if report["status"] == "SELF_IMPROVEMENT_PROCESS_STARTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
