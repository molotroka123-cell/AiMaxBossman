"""Gem Search (h100envy/gem-search, MIT) as a Bossman RESEARCH-ONLY sidecar (owner 31.10).

    python tools/gem_search_research.py start [--port 8851]   # fetch pinned source, harden, run the local engine
    python tools/gem_search_research.py status [--port 8851]

What it is: a local engine + Chrome extension that turns visible X posts into research leads (mentions, authors,
growth pace) and runs four rule checks (lookout / maker / skeptic / runner) with evidence links.

What Bossman deliberately does NOT take: the same repository also auto-launches memecoins on Solana / pump.fun from
those leads (its own SOL budget, a treasury wallet, SPIDER_ALLOW_LAUNCH). Bossman never runs that part: launches stay
paused by the project's own switch (`data/launch-paused`), LAUNCH_MODE is forced to dry_run, no wallet / Pinata /
paid-X / Grok key is passed, and `start` REFUSES when a wallet or launch credential is present. The source is not
vendored into this repo; it is fetched at a pinned commit into D:\\Bossman\\gem-search\\src.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = "https://github.com/h100envy/gem-search.git"
PIN = "a04fae4"
HOME = Path(os.environ.get("GEM_SEARCH_HOME") or r"D:\Bossman\gem-search")
SRC = HOME / "src"

# Everything that could enable a token launch or a paid / cloud call is blanked or forced off.
RESEARCH_ENV = {
    "LAUNCH_MODE": "dry_run", "SPIDER_ALLOW_LAUNCH": "0", "PINATA_JWT": "", "ENABLE_PAID_X": "0",
    "X_BEARER_TOKEN": "", "GROK_ENABLED": "0", "XAI_API_KEY": "", "LAUNCH_PROVIDER": "pumpportal",
    "LAUNCH_BRIDGE_URL": "", "SOLANA_KEYPAIR_PATH": str(HOME / "NO_WALLET" / "none.json"),
}
LAUNCH_CREDENTIAL_VARS = ("PINATA_JWT", "LAUNCH_BRIDGE_URL", "SPIDER_ALLOW_LAUNCH")

FCNTL_SHIM = '''try:
    import fcntl
except ImportError:  # Windows (Bossman research-only port): same exclusive non-blocking lock via msvcrt
    import msvcrt

    class _FcntlShim:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

        @staticmethod
        def flock(f, op):
            f.seek(0)
            if op & _FcntlShim.LOCK_UN:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    fcntl = _FcntlShim()
'''


def refusal(src: Path, environ: dict) -> str | None:
    """Why the engine must NOT start (a wallet or a launch credential is present), or None."""
    wallets = src / "data" / "wallets"
    if wallets.exists() and any(wallets.iterdir()):
        return f"wallet files present in {wallets}: Bossman runs Gem Search research-only and will not start next to a wallet"
    if (src / ".env").exists():
        return f"{src / '.env'} exists: launch/Grok credentials are not allowed for the Bossman research-only run"
    for var in LAUNCH_CREDENTIAL_VARS:
        value = environ.get(var, "")
        if var == "SPIDER_ALLOW_LAUNCH" and value not in ("", "0"):
            return "SPIDER_ALLOW_LAUNCH is set in the environment"
        if var in ("PINATA_JWT", "LAUNCH_BRIDGE_URL") and value:
            return f"{var} is set in the environment"
    return None


def research_env(environ: dict) -> dict:
    env = {k: v for k, v in environ.items() if k not in RESEARCH_ENV}
    env.update(RESEARCH_ENV)
    env["PYTHONUTF8"] = "1"
    return env


def harden(src: Path) -> None:
    """Windows lock shim (the project imports Unix-only fcntl) and the project's own launch pause switch."""
    auto = src / "automation.py"
    text = auto.read_text(encoding="utf-8")
    if "_FcntlShim" not in text and "import fcntl\n" in text:
        auto.write_text(text.replace("import fcntl\n", FCNTL_SHIM, 1), encoding="utf-8")
    (src / "data").mkdir(exist_ok=True)
    (src / "data" / "launch-paused").write_text("Bossman research-only: token launches disabled by owner policy\n",
                                               encoding="utf-8")


def fetch() -> None:
    if not (SRC / ".git").exists():
        HOME.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", REPO, str(SRC)], check=True)
    subprocess.run(["git", "-C", str(SRC), "fetch", "-q", "origin"], check=False)
    subprocess.run(["git", "-C", str(SRC), "checkout", "-q", "--", "automation.py"], check=False)
    subprocess.run(["git", "-C", str(SRC), "checkout", "-q", PIN], check=True)


def state(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
        return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["start", "status"])
    ap.add_argument("--port", type=int, default=8851)
    a = ap.parse_args(argv)
    if a.action == "status":
        auto = state(a.port).get("automation") or {}
        print(json.dumps({"engine": "up", "launch_enabled": auto.get("enabled"), "mode": auto.get("mode")}))
        return 0 if auto.get("enabled") is False else 2
    fetch()
    why = refusal(SRC, dict(os.environ))
    if why:
        print("REFUSED:", why)
        return 3
    harden(SRC)
    log = open(HOME / "engine.log", "ab")
    subprocess.Popen([sys.executable, "-X", "utf8", "app.py", "--port", str(a.port)], cwd=SRC,
                     env=research_env(dict(os.environ)), stdout=log, stderr=log,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(30):
        try:
            auto = state(a.port).get("automation") or {}
            print(json.dumps({"engine": f"http://127.0.0.1:{a.port}", "launch_enabled": auto.get("enabled"),
                              "mode": auto.get("mode"), "pin": PIN}))
            return 0 if auto.get("enabled") is False and auto.get("mode") == "dry_run" else 2
        except OSError:
            time.sleep(1)
    print("engine did not come up; see", HOME / "engine.log")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
