#!/usr/bin/env python3
"""Bossman 1.5 unified owner run.

One command starts the two long-running lanes the owner actually needs:
1) self-improvement/economy learning through Bossman's own coding/evolution path;
2) read-only Twitch market collection with verified Telegram notifications.

No external auditor is required for normal runtime. STOP is durable for both.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
IS_WIN = sys.platform == "win32"


def _default_data() -> Path:
    if os.environ.get("BCC_DATA_DIR"):
        return Path(os.environ["BCC_DATA_DIR"]).expanduser()
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "CommandCenter"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "bossman" / "command-center"


def _support(name: str) -> Path:
    for p in (HERE / name, ROOT / "tools" / name):
        if p.is_file():
            return p
    raise FileNotFoundError(name)


def _repo(explicit: str | None) -> Path | None:
    choices = [explicit, os.environ.get("BOSSMAN_SELF_IMPROVE_REPO")]
    if (ROOT / ".git").exists():
        choices.append(str(ROOT))
    for raw in choices:
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if (p / ".git").exists():
            return p
    return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False


def _spawn(argv: list[str], log: Path, cwd: Path | None = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    stream = log.open("ab", buffering=0)
    kw = {"cwd": str(cwd) if cwd else None, "stdin": subprocess.DEVNULL,
          "stdout": stream, "stderr": subprocess.STDOUT, "env": {**os.environ, "PYTHONUTF8": "1"}}
    if IS_WIN:
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kw)
    finally:
        stream.close()
    return int(proc.pid)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _market_status(market_root: Path) -> dict:
    path = market_root / "reports" / "collector-status.json"
    return _read(path) if path.is_file() else {"state": "NOT_STARTED"}


def _self_status(work: Path) -> dict:
    report = _read(work / "bootstrap-report.json")
    evo = _read(work / "evolution" / "loop-state.json")
    return {"bootstrap": report, "evolution": evo}


def status(root: Path) -> dict:
    state = _read(root / "state.json")
    market_root = Path(state.get("market_root") or root / "market")
    self_work = Path(state.get("self_work") or root / "self-improve")
    pids = state.get("pids") or {}
    market_alive = _alive(pids.get("market"))
    self_alive = _alive(pids.get("self_improve"))
    state["running"] = bool(market_alive or self_alive)
    state["processes"] = {
        "market": {"pid": pids.get("market"), "alive": market_alive},
        "self_improve": {"pid": pids.get("self_improve"), "alive": self_alive},
    }
    state["market"] = _market_status(market_root)
    state["self_improvement"] = _self_status(self_work)
    state["owner_input_path"] = str(root.parent.parent / "owner-input" / "requests.json")
    return state


def start(root: Path, *, data_dir: Path, repo: Path | None, cycles: int,
          allow_glm: bool, youtube_url: str, cadence: float) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    existing = status(root)
    if existing.get("running"):
        return {**existing, "status": "ALREADY_RUNNING"}

    stop_file = root / "STOP"
    stop_file.unlink(missing_ok=True)
    market_root = data_dir / "market-data" / "twitch" / "k1m6a"
    (market_root / "STOP").unlink(missing_ok=True)
    self_work = root / "self-improve"

    market_cmd = [
        sys.executable, "-m", "bcc.market.collector", "run",
        "--root", str(market_root), "--cadence", str(cadence),
    ]
    market_pid = _spawn(market_cmd, root / "market.log", ROOT if (ROOT / "command-center").exists() else None)

    self_pid = None
    self_blocker = None
    if repo is None:
        self_blocker = "OWNER_REQUIRED_REPO: set BOSSMAN_SELF_IMPROVE_REPO to a clean Bossman checkout"
    else:
        self_cmd = [
            sys.executable, str(_support("bossman_15_self_improve.py")),
            "--repo", str(repo), "--data-dir", str(data_dir),
            "start", "--work", str(self_work), "--cycles", str(cycles),
        ]
        if allow_glm:
            self_cmd.append("--allow-glm")
        if youtube_url:
            self_cmd += ["--youtube-url", youtube_url]
        self_pid = _spawn(self_cmd, root / "self-improve.log", repo)

    state = {
        "schema": "bossman.v1.5.owner-run/1",
        "status": "RUNNING" if self_pid else "PARTIAL_OWNER_REQUIRED",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": str(root), "data_dir": str(data_dir),
        "repo": str(repo) if repo else None,
        "market_root": str(market_root), "self_work": str(self_work),
        "pids": {"market": market_pid, "self_improve": self_pid},
        "self_improve_blocker": self_blocker,
        "policy": {
            "market_trading_execution": "OFF",
            "self_fix_target": "candidate/worktree only",
            "stable_write": False,
            "external_auditor_required_for_runtime": False,
            "owner_supervision": "Telegram/UX/CMD",
        },
    }
    _write(root / "state.json", state)
    return status(root)


def stop(root: Path) -> dict:
    state = _read(root / "state.json")
    (root / "STOP").write_text("owner stop\n", encoding="utf-8")
    market_root = Path(state.get("market_root") or root / "market")
    market_root.mkdir(parents=True, exist_ok=True)
    (market_root / "STOP").write_text("owner stop\n", encoding="utf-8")

    self_work = Path(state.get("self_work") or root / "self-improve")
    try:
        script = _support("bossman_15_self_improve.py")
        subprocess.run([sys.executable, str(script), "stop",
                        "--work", str(self_work / "evolution")],
                       capture_output=True, timeout=30)
    except Exception:
        pass
    state["status"] = "STOP_REQUESTED"
    state["stop_requested_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write(root / "state.json", state)
    return status(root)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bossman-1.5")
    ap.add_argument("--data-dir", default=str(_default_data()))
    ap.add_argument("--root")
    sub = ap.add_subparsers(dest="command", required=True)
    st = sub.add_parser("start")
    st.add_argument("--repo")
    st.add_argument("--cycles", type=int, default=8)
    st.add_argument("--allow-glm", action="store_true")
    st.add_argument("--youtube-url", default=os.environ.get("BOSSMAN_K1MBA_YOUTUBE_URL", ""))
    st.add_argument("--cadence", type=float, default=15.0)
    sub.add_parser("status")
    sub.add_parser("stop")
    ns = ap.parse_args(argv)
    data = Path(ns.data_dir).expanduser().resolve()
    root = Path(ns.root).expanduser().resolve() if ns.root else data / "v1.5" / "owner-run"
    if ns.command == "start":
        result = start(root, data_dir=data, repo=_repo(ns.repo), cycles=max(1, min(ns.cycles, 20)),
                       allow_glm=ns.allow_glm, youtube_url=ns.youtube_url, cadence=max(5.0, ns.cadence))
    elif ns.command == "stop":
        result = stop(root)
    else:
        result = status(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if ns.command == "start" and result.get("status") == "PARTIAL_OWNER_REQUIRED":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
