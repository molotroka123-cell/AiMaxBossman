"""Watch Direct Generator jobs + Jeff liveness; one stdout line per event (built for Claude Code's Monitor tool).

Structure only: job id prefix, kind, status, progress counters, short system error text. It never prints prompts, results,
chat text or photos (owner rule 21.10: no reading local chats/data/photos).

  python tools/gen_watch.py [--interval 60] [--stall-minutes 10] [--model-match gemma4-31b]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

LIVE = ("queued", "loading", "generating", "running")
DONE = ("completed", "failed", "stopped", "cancelled")


class Watcher:
    """Pure event detector: feed it observations, get back the lines to print."""

    def __init__(self, stall_s: float = 600.0, model_match: str = "gemma4-31b"):
        self.stall_s, self.model_match = stall_s, model_match
        self.seen: dict[str, str] = {}
        self.prog: dict[str, tuple] = {}
        self.flags: set[str] = set()
        self.first = True

    def _edge(self, flag: str, on: bool, down_msg: str, up_msg: str, out: list[str]) -> None:
        if on and flag not in self.flags:
            out.append(down_msg)
            self.flags.add(flag)
        elif not on and flag in self.flags:
            out.append(up_msg)
            self.flags.discard(flag)

    def step(self, jobs: list[dict], loaded_models: list[str] | None, jeff_alive: bool, now: float) -> list[str]:
        out: list[str] = []
        live = [j for j in jobs if j.get("status") in LIVE]
        for j in jobs:
            jid, st = str(j.get("job_id") or j.get("id") or ""), j.get("status")
            prev = self.seen.get(jid)
            if not self.first and prev not in (None, st) and st in DONE:
                err = j.get("error")
                err = (err.get("message") if isinstance(err, dict) else err) or ""
                out.append(f"JOB {jid[:6]} {j.get('kind')} -> {st} {str(err)[:100]} | live left {len(live)}")
            self.seen[jid] = st
            if st in ("loading", "generating"):
                p = json.dumps(j.get("progress"), sort_keys=True)
                last = self.prog.get(jid)
                if last is None or last[0] != p or last[1] != st:
                    self.prog[jid] = (p, st, now)
                    self.flags.discard("stall:" + jid)
                elif now - last[2] > self.stall_s and "stall:" + jid not in self.flags:
                    out.append(f"STALL {jid[:6]} {st} no progress {int((now - last[2]) / 60)} min: {p[:80]}")
                    self.flags.add("stall:" + jid)
        if not self.first and not live and "empty" not in self.flags:
            out.append("QUEUE EMPTY: all generations finished")
            self.flags.add("empty")
        elif live:
            self.flags.discard("empty")
        self._edge("jeff", not jeff_alive, "JEFF DOWN (process not running)", "JEFF back up", out)
        if loaded_models is not None:
            gone = not any(self.model_match in m for m in loaded_models)
            self._edge("model", gone, f"JEFF MODEL UNLOADED from Ollama (loaded: {loaded_models})",
                       "JEFF MODEL loaded again", out)
        self.first = False
        return out


def data_dir() -> Path:
    env = os.environ.get("BCC_DATA_DIR")
    return Path(env) if env else Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter"


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def jeff_alive(dd: Path) -> bool:
    try:
        return pid_alive(int((dd / "one-bossman" / "jeff.pid").read_text().strip()))
    except (OSError, ValueError):
        return False


def _get(url: str, headers: dict | None = None, timeout: float = 20.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--stall-minutes", type=float, default=10.0)
    ap.add_argument("--model-match", default="gemma4-31b")
    ap.add_argument("--backend", default="http://127.0.0.1:8801")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    a = ap.parse_args(argv)
    dd = data_dir()
    headers = {"X-BCC-Token": (dd / "token").read_text(encoding="utf-8").strip(), "X-Participant": "owner"}
    w, down = Watcher(a.stall_minutes * 60, a.model_match), False
    while True:
        try:
            jobs = _get(f"{a.backend}/api/direct-gen/jobs?limit=60", headers)
            jobs = jobs.get("items") or jobs.get("jobs") or jobs
            down = False
        except Exception as e:  # noqa: BLE001
            if not down:
                print(time.strftime("%H:%M ") + f"BACKEND DOWN: {str(e)[:120]}", flush=True)
                down = True
            time.sleep(a.interval)
            continue
        try:
            models = [m["name"] for m in _get(f"{a.ollama}/api/ps").get("models", [])]
        except Exception:  # noqa: BLE001
            models = None
        for line in w.step(jobs, models, jeff_alive(dd), time.time()):
            print(time.strftime("%H:%M ") + line, flush=True)
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
